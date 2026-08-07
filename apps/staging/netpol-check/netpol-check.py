#!/usr/bin/env python3
"""Check that NetworkPolicies do what they say, in the cluster they run in.

Two things are checked, in this order, because the second is meaningless
without the first.

1. Is the cluster enforcing at all? On every node, an already-running pod in a
   namespace that denies egress is told to reach something no policy permits.
   If it gets through, that node is not enforcing and every result below is
   meaningless. Staging enforced nothing for weeks while looking healthy.

2. Do the policies match intent? Denials matter as much as allows: a cluster
   enforcing nothing passes every allow-test.

Usage:
    scripts/netpol-check.py --context staging
    scripts/netpol-check.py --context production --expectations-only

Exit code is non-zero if any check fails, so it can gate a change.
"""

import argparse
import json
import subprocess
import sys
import time

# Ships python3, so the probe needs no network to start. An image that
# installs its tools at runtime cannot probe an egress-denied pod: the
# install is itself blocked, and the result reads as "no output" rather
# than as the denial it is.
PROBE_IMAGE = "python:3.12-alpine"
PROBE_CONTAINER = "netpol-probe"

# Destinations that no application policy grants. Reaching one means the
# calling pod is not being filtered. Chosen to be present in both clusters
# and to listen on the port given, so that a refusal is a policy decision
# rather than an empty socket.
FORBIDDEN_PROBES = [
    ("monitoring", "app.kubernetes.io/name=prometheus", 9090),
]

# (context, source namespace, source deployment, destination, port,
#  expect_allowed, why). The context matters: the two clusters do not run the
# same set of applications, and asserting production must do what only staging
# does produces a permanent failure that teaches people to ignore the output.
EXPECTATIONS = [
    (
        "*", "mealie", "mealie", "postgres-cluster-rw.database.svc.cluster.local", 5432,
        True, "mealie stores everything in postgres; without this it does not start",
    ),
    (
        "*", "tandoor", "tandoor", "postgres-cluster-rw.database.svc.cluster.local", 5432,
        True, "same for tandoor",
    ),
    (
        "*", "mealie", "mealie", "INGRESS", 443,
        True, "server-side half of the OIDC login: discovery and token exchange",
    ),
    (
        "staging", "tandoor", "tandoor", "INGRESS", 443,
        True, "same for tandoor, via allauth -- staging only, tandoor has no authentik client in production",
    ),
]

# The ingress address each cluster's public hostnames resolve to. The OIDC
# token exchange goes here, and it is inside the private range that the
# SSRF guard in allow-internet-egress excludes -- which is exactly why it
# needs naming rather than assuming.
INGRESS_IP = {"staging": "10.0.40.52", "production": "10.0.40.100"}


def kubectl(ctx, *args, check=True, timeout=120):
    # ctx is None in-cluster, where kubectl uses the ServiceAccount and there
    # is no context to name.
    prefix = ["kubectl"] + ([f"--context={ctx}"] if ctx else [])
    r = subprocess.run(
        [*prefix, *args],
        capture_output=True, text=True, timeout=timeout,
    )
    if check and r.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def probe_script(host, port):
    """Connect once, and say which of the three outcomes happened.

    A timeout and a refusal are both 'blocked', but they are different
    mechanisms -- a dropped packet versus an ICMP reject -- and which one a
    cluster produces is worth seeing when a result is surprising.
    """
    return (
        "import socket,sys\n"
        "s=socket.socket(); s.settimeout(8)\n"
        "try:\n"
        f"    s.connect(('{host}',{port})); print('ALLOWED')\n"
        "except socket.timeout: print('BLOCKED timeout')\n"
        "except ConnectionRefusedError: print('BLOCKED refused')\n"
        "except Exception as e: print('ERROR '+type(e).__name__)\n"
    )


def run_in_pod(ctx, ns, deploy, host, port):
    """Probe from an existing workload, so the real pod's policies apply."""
    try:
        out = kubectl(
            ctx, "exec", "-n", ns, f"deploy/{deploy}", "--",
            "python3", "-c", probe_script(host, port), check=False, timeout=90,
        )
    except subprocess.TimeoutExpired:
        return "ERROR probe-timeout"
    line = [l for l in out.splitlines() if l.startswith(("ALLOWED", "BLOCKED", "ERROR"))]
    return line[-1] if line else "ERROR no-output"


def nodes(ctx):
    d = json.loads(kubectl(ctx, "get", "nodes", "-o", "json"))
    return [n["metadata"]["name"] for n in d["items"]]


def guarded_pod_on(ctx, node, exclude_ns=()):
    """An existing pod on this node whose namespace denies egress by default.

    An existing pod, not one this script creates: new pods were getting
    firewall chains while hours-old ones were not, so a canary reported a
    healthy node that was enforcing nothing.

    Host-network pods are skipped (not subject to pod policy), as is the probe
    target's own namespace (reaching a neighbour there is a permission).
    """
    guarded = set()
    for p in json.loads(kubectl(ctx, "get", "networkpolicy", "-A", "-o", "json"))["items"]:
        spec = p.get("spec", {})
        if "Egress" in spec.get("policyTypes", []) and not spec.get("egress"):
            if not spec.get("podSelector"):  # applies to the whole namespace
                guarded.add(p["metadata"]["namespace"])

    pods = json.loads(kubectl(ctx, "get", "pods", "-A", "-o", "json"))["items"]
    for p in pods:
        if p["spec"].get("nodeName") != node:
            continue
        if p["spec"].get("hostNetwork"):
            continue
        if p["status"].get("phase") != "Running":
            continue
        if p["metadata"]["namespace"] not in guarded:
            continue
        if p["metadata"]["namespace"] in exclude_ns:
            continue
        # An ephemeral container cannot be replaced or removed, so each run
        # adds a new one rather than skipping pods it has probed before.
        # Skipping them instead exhausts the candidates: after a few runs a
        # node has no eligible pod left and reports INCONCLUSIVE, which reads
        # as a fault in the cluster rather than in the check.
        used = [c["name"] for c in p["spec"].get("ephemeralContainers", [])
                if c["name"].startswith(PROBE_CONTAINER)]
        return p["metadata"]["namespace"], p["metadata"]["name"], f"{PROBE_CONTAINER}-{len(used) + 1}"
    return None, None, None


def check_enforcement(ctx):
    """Is each node filtering at all? Returns list of (node, enforcing, detail)."""
    target = None
    for ns, selector, port in FORBIDDEN_PROBES:
        out = kubectl(
            ctx, "get", "pods", "-n", ns, "-l", selector,
            "-o", "jsonpath={.items[0].status.podIP}", check=False,
        )
        if out.strip():
            target = (out.strip(), port, ns)
            break
    if not target:
        return [("-", None, "no probe target found; cannot test enforcement")]

    host, port, target_ns = target
    results = []
    for node in nodes(ctx):
        ns, pod, container = guarded_pod_on(ctx, node, exclude_ns=(target_ns,))
        if not pod:
            results.append((node, None, "no egress-denied pod running here to probe from"))
            continue

        # An ephemeral container joins the target pod's network namespace, so
        # it is filtered by exactly the chains that pod's traffic is.
        subprocess.run(
            ["kubectl"] + ([f"--context={ctx}"] if ctx else []) + ["debug", "-n", ns, pod,
             f"--image={PROBE_IMAGE}", f"--container={container}", "-q", "--",
             "python3", "-c", probe_script(host, port)],
            capture_output=True, text=True, timeout=180,
        )

        verdict, deadline = "ERROR no-result", time.time() + 180
        while time.time() < deadline:
            out = kubectl(ctx, "logs", "-n", ns, pod, "-c", container, check=False)
            hit = [l for l in out.splitlines() if l.startswith(("ALLOWED", "BLOCKED", "ERROR"))]
            if hit:
                verdict = hit[-1]
                break
            time.sleep(5)

        # The source namespace denies egress by default and nothing grants
        # this destination, so anything other than BLOCKED means this node is
        # not applying the policy.
        if verdict.startswith("ERROR"):
            enforcing = None  # inconclusive, not a result
        else:
            enforcing = verdict.startswith("BLOCKED")
        results.append((node, enforcing, f"{verdict}, from {ns}/{pod}"))
    return results


def check_expectations(ctx):
    rows = []
    for want_ctx, ns, deploy, host, port, expect_allowed, why in EXPECTATIONS:
        if want_ctx != "*" and want_ctx != ctx:
            continue
        if host == "INGRESS":
            host = INGRESS_IP.get(ctx)
            if not host:
                rows.append((ns, "INGRESS", port, None, "no ingress IP known for this context", why))
                continue
        verdict = run_in_pod(ctx, ns, deploy, host, port)
        allowed = verdict.startswith("ALLOWED")
        ok = (allowed == expect_allowed)
        rows.append((ns, host, port, ok, verdict, why))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--context", default=None,
                    help="omit when running inside the cluster")
    ap.add_argument("--expectations-only", action="store_true",
                    help="skip the per-node enforcement canary")
    args = ap.parse_args()
    ctx = args.context
    failed = False

    if not args.expectations_only:
        print(f"== is {ctx} enforcing NetworkPolicy at all? ==")
        for node, enforcing, detail in check_enforcement(ctx):
            if enforcing:
                print(f"  {node:<24} enforcing        ({detail})")
            elif enforcing is None:
                failed = True
                print(f"  {node:<24} INCONCLUSIVE     ({detail})")
                print(f"  {'':<24} the probe produced no verdict; this is not evidence either way")
            else:
                failed = True
                print(f"  {node:<24} NOT ENFORCING    ({detail})")
                print(f"  {'':<24} every policy on this node is inert; results below mean nothing for it")
        print()

    print(f"== do the policies match intent in {ctx}? ==")
    for ns, host, port, ok, verdict, why in check_expectations(ctx):
        mark = "ok  " if ok else ("skip" if ok is None else "FAIL")
        if ok is False:
            failed = True
        short = host if len(host) < 34 else host[:31] + "..."
        print(f"  {mark} {ns:<10} -> {short:<34}:{port:<5} {verdict}")
        if not ok:
            print(f"       {why}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
