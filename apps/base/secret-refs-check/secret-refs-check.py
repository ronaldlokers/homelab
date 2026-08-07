#!/usr/bin/env python3
"""Check that every Secret a workload references exists, and that mirrored
credentials still match their source.

Two failures this repo has actually had:

1. A workload references a Secret that does not exist in its namespace. The pod
   sits in CreateContainerConfigError and nothing else reports it — the
   Deployment is applied, the Kustomization is Ready. This happened to five
   apps at once when the mirrors they had been pointed at were never
   authorised.

2. A copy of a credential drifts from its source. CloudNativePG regenerates the
   app password when a Cluster is created, and a namespace-local copy is not
   derived from it, so a recreate desynchronises them silently — Immich
   returned 500 for four hours that way (#268).

Usage:
    scripts/secret-refs-check.py --context production

Exit code is non-zero if any reference is missing or any mirror has drifted.
"""

import argparse
import base64
import hashlib
import json
import subprocess
import sys

# Where credentials originate. A Secret in one of these namespaces is treated
# as authoritative; copies elsewhere are compared against it.
SOURCE_NAMESPACE = "database"

# Keys worth comparing. Comparing whole Secrets is noisy — a mirror legitimately
# carries extra keys — so drift is judged on the credential itself.
COMPARED_KEYS = ("password",)


def kubectl(ctx, *args):
    # ctx is None in-cluster, where kubectl uses the ServiceAccount and there
    # is no context to name.
    prefix = ["kubectl"] + ([f"--context={ctx}"] if ctx else [])
    r = subprocess.run([*prefix, *args],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip())
    return r.stdout


def digest(b64):
    return hashlib.sha256(base64.b64decode(b64)).hexdigest()[:10]


def secret_refs(pod_spec):
    """Every Secret a pod spec requires.

    optional: true is skipped — the workload starts without it by design, so
    reporting it is noise. longhorn-manager references longhorn-grpc-tls that
    way and runs fine.
    """
    refs = set()
    for c in pod_spec.get("containers", []) + pod_spec.get("initContainers", []):
        for e in c.get("env", []):
            r = (e.get("valueFrom") or {}).get("secretKeyRef")
            if r and not r.get("optional"):
                refs.add(r["name"])
        for e in c.get("envFrom", []):
            r = e.get("secretRef")
            if r and not r.get("optional"):
                refs.add(r["name"])
    for v in pod_spec.get("volumes", []):
        s = v.get("secret")
        if s and s.get("secretName") and not s.get("optional"):
            refs.add(s["secretName"])
    return refs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--context")
    args = ap.parse_args()
    ctx = args.context
    failed = False

    secrets = json.loads(kubectl(ctx, "get", "secret", "-A", "-o", "json"))["items"]
    existing = {(s["metadata"]["namespace"], s["metadata"]["name"]) for s in secrets}

    sources = {}
    for s in secrets:
        if s["metadata"]["namespace"] != SOURCE_NAMESPACE:
            continue
        data = s.get("data") or {}
        sources[s["metadata"]["name"]] = {k: data[k] for k in COMPARED_KEYS if k in data}

    print(f"== every Secret referenced by a workload in {ctx or 'this cluster'} exists ==")
    workloads = []
    for kind in ("deployments", "statefulsets", "daemonsets"):
        workloads += json.loads(kubectl(ctx, "get", kind, "-A", "-o", "json"))["items"]

    missing = []
    checked = 0
    for w in workloads:
        ns = w["metadata"]["namespace"]
        for name in secret_refs(w["spec"]["template"]["spec"]):
            checked += 1
            if (ns, name) not in existing:
                missing.append((ns, w["metadata"]["name"], name))
    if missing:
        failed = True
        for ns, wl, name in missing:
            print(f"  MISSING  {ns}/{wl} references Secret {name}, which does not exist")
    else:
        print(f"  ok — {checked} references across {len(workloads)} workloads")

    print(f"\n== credentials outside {SOURCE_NAMESPACE} still match their source ==")
    compared = 0
    drifted = False
    for s in secrets:
        ns, name = s["metadata"]["namespace"], s["metadata"]["name"]
        if ns == SOURCE_NAMESPACE or name not in sources:
            continue
        src, here = sources[name], s.get("data") or {}
        for key in COMPARED_KEYS:
            if key not in src or key not in here:
                continue
            compared += 1
            if src[key] != here[key]:
                failed = drifted = True
                print(f"  DRIFTED  {ns}/{name} {key}={digest(here[key])} "
                      f"but {SOURCE_NAMESPACE}/{name} is {digest(src[key])}")
    if drifted:
        pass  # already reported above
    elif compared:
        print(f"  ok — {compared} mirrored credential(s) identical to source")
    else:
        print("  no mirrored credentials found — nothing to compare")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
