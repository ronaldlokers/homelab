#!/usr/bin/env python3
"""Check that every cluster's recovery source holds a backup worth recovering.

`bootstrap.recovery` only runs when a Cluster object is CREATED, so a wrong
`externalClusters` pointer is invisible until a rebuild — the one moment nobody
is in a position to notice. It has been wrong three times:

  #238  postgres-cluster recovered from a stale prefix for months, reporting
        healthy the whole time.
  #257  immich's pointer was six weeks stale; a rebuild would have restored an
        old photo library and reported Ready.
  #260  immich again, three days after #257, because the change advanced both
        prefixes by one generation and so left recovery pointed at the one that
        had just stopped receiving backups.

Every one of those passed review. What was missing is an assertion against
object storage rather than against the manifest.

The check asks barman itself, from a pod of the cluster in question, because
that is the tool a real restore would use — reimplementing the catalogue layout
here would test this script's idea of barman rather than barman.

Usage:
    scripts/recovery-source-check.py --context production

Exit code is non-zero if any recovery source is empty, unreadable, or behind
the prefix the cluster actually archives to.

With --report-to, findings are also filed with the campfire bridge. Until that
existed the output of this check went nowhere at all: a pod log, garbage
collected, for a check whose whole subject is a failure nobody notices.
"""

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

# Findings, in the words the room gets rather than the columns the log gets.
FINDINGS = []


def finding(subject, text):
    """Record a failure, for a room rather than for a pod log."""
    FINDINGS.append(f"{subject}: {text}")


def file_findings(url, check, cluster, findings, timeout=15):
    """File findings with the campfire bridge, which decides whether to say them.

    Every run reports, a clean one included: the bridge cannot tell a check
    that found nothing from a check that did not run, and only a run saying
    "nothing" can clear what an earlier run said.
    """
    body = json.dumps({
        "check": check,
        "cluster": cluster or "",
        "findings": findings,
    }).encode()
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status

# The invariant is NOT "the recovery source is recent". Absolute age misses the
# thing that actually went wrong: on the morning #260 was found, the stale
# source held a backup 3 days old against a 7d retention, so any sane age
# threshold called it healthy while a rebuild would have lost 4 days.
#
# What distinguishes a live prefix from an abandoned one is the archive target.
# If the cluster is writing newer backups somewhere else, the recovery pointer
# is behind, and it is behind on day one rather than after a fortnight.
#
# A day of slack: the two prefixes are usually the same object, but when they
# differ legitimately the nightly runs land minutes apart, never a day.
MAX_LAG = timedelta(days=1)


def kubectl(ctx, *args, check=True, timeout=180):
    # ctx is None in-cluster, where kubectl uses the ServiceAccount and there
    # is no context to name.
    prefix = ["kubectl"] + ([f"--context={ctx}"] if ctx else [])
    r = subprocess.run([*prefix, *args], capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout if r.returncode == 0 else r.stdout + r.stderr


def secret_value(ctx, ns, name, key):
    out = kubectl(ctx, "get", "secret", name, "-n", ns, "-o", f"jsonpath={{.data.{key}}}")
    return base64.b64decode(out).decode()


def newest_backup(ctx, ns, pod, store, creds):
    """Ask barman for the catalogue, from a pod that could actually restore it."""
    env = [
        "env",
        f"AWS_ACCESS_KEY_ID={creds[0]}",
        f"AWS_SECRET_ACCESS_KEY={creds[1]}",
    ]
    cmd = [
        "barman-cloud-backup-list",
        "--cloud-provider", "aws-s3",
        "--endpoint-url", store["endpointURL"],
        store["destinationPath"], store["serverName"],
    ]
    out = kubectl(ctx, "exec", "-n", ns, pod, "-c", "postgres", "--",
                  *env, *cmd, check=False)
    stamps = []
    for line in out.splitlines():
        # "20260808T033001  2026-08-08 03:30:44  000000030000..."
        m = re.match(r"\s*\d{8}T\d{6}\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
        if m:
            stamps.append(datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                          .replace(tzinfo=timezone.utc))
    if stamps:
        return max(stamps), None
    return None, " ".join(out.split())[:120] or "no output"


def object_store(ctx, ns, name):
    """Resolve a Barman Cloud Plugin ObjectStore into the same shape as an
    in-tree barmanObjectStore, so both paths compare identically. Without this
    the check quietly stops comparing anything as #377 migrates clusters."""
    out = kubectl(ctx, "get", "objectstores.barmancloud.cnpg.io", name, "-n", ns,
                  "-o", "json", check=False)
    try:
        return json.loads(out)["spec"]["configuration"]
    except Exception:
        return None


def clusters(ctx):
    d = json.loads(kubectl(ctx, "get", "clusters.postgresql.cnpg.io", "-A", "-o", "json"))
    return d["items"]


def running_pod(ctx, ns, cluster):
    out = kubectl(ctx, "get", "pods", "-n", ns,
                  "-l", f"cnpg.io/cluster={cluster}",
                  "--field-selector=status.phase=Running",
                  "-o", "jsonpath={.items[0].metadata.name}", check=False)
    return out.strip() or None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--context")
    ap.add_argument("--report-to", default=os.environ.get("CHECK_BRIDGE_URL"),
                    help="bridge URL to file findings with; defaults to "
                         "$CHECK_BRIDGE_URL, and nothing is filed without one")
    ap.add_argument("--cluster", default=os.environ.get("CLUSTER"),
                    help="which cluster the findings are about, for a room that "
                         "serves more than one; defaults to $CLUSTER")
    args = ap.parse_args()
    ctx = args.context
    failed = False

    print(f"== every recovery source in {ctx or 'this cluster'} holds a recent backup ==")
    checked = 0
    for c in clusters(ctx):
        ns, name = c["metadata"]["namespace"], c["metadata"]["name"]
        spec = c["spec"]
        source = (spec.get("bootstrap") or {}).get("recovery", {}).get("source")
        if not source:
            continue

        ext = next((e for e in spec.get("externalClusters", []) if e["name"] == source), None)
        store = (ext or {}).get("barmanObjectStore")
        server = None
        if not store:
            # The plugin spells the same pointer differently: the destination
            # lives on an ObjectStore, and the external cluster names it. Both
            # forms have to resolve, or #377 would turn this check off one
            # cluster at a time while every one of them still reported ok.
            params = ((ext or {}).get("plugin") or {}).get("parameters") or {}
            if params.get("barmanObjectName"):
                store = object_store(ctx, ns, params["barmanObjectName"])
                # Unlike the in-tree form, serverName is a plugin parameter
                # rather than a field on the store — the ObjectStore's own
                # serverName is required to stay empty.
                server = params.get("serverName")
        if not store:
            failed = True
            finding(name, f"recovery source '{source}' resolves to no object store, "
                          f"so a rebuild would have nothing to restore from")
            print(f"  FAIL {name:<20} recovery source '{source}' resolves to no object "
                  f"store, so a rebuild would have nothing to restore from")
            continue

        store = dict(store)
        store.setdefault("serverName", server or name)
        pod = running_pod(ctx, ns, name)
        if not pod:
            failed = True
            finding(name, "no running pod to read the catalogue from, so the "
                          "recovery source could not be checked at all")
            print(f"  FAIL {name:<20} no running pod to read the catalogue from")
            continue

        cred = store["s3Credentials"]
        creds = (secret_value(ctx, ns, cred["accessKeyId"]["name"], cred["accessKeyId"]["key"]),
                 secret_value(ctx, ns, cred["secretAccessKey"]["name"], cred["secretAccessKey"]["key"]))

        newest, err = newest_backup(ctx, ns, pod, store, creds)
        prefix = store["destinationPath"].rstrip("/").split("/")[-1]
        checked += 1

        if newest is None:
            failed = True
            finding(name, f"{prefix}: no base backup found — a rebuild would have "
                          f"nothing to restore ({err})")
            print(f"  FAIL {name:<20} {prefix}: no base backup found — a rebuild would "
                  f"have nothing to restore ({err})")
            continue

        stamp = newest.strftime("%Y-%m-%d %H:%M")

        # Where the cluster actually writes. Same object in the common case, in
        # which case the comparison is trivially satisfied.
        archive = (spec.get("backup") or {}).get("barmanObjectStore")
        a_server = None
        if not archive:
            plugin = next((p for p in spec.get("plugins", [])
                           if p.get("parameters", {}).get("barmanObjectName")), None)
            if plugin:
                archive = object_store(
                    ctx, ns, plugin["parameters"]["barmanObjectName"])
                a_server = plugin["parameters"].get("serverName")
        if not archive:
            failed = True
            finding(name, f"{prefix}: cannot resolve where this cluster archives to, "
                          f"so the pointer cannot be compared")
            print(f"  FAIL {name:<20} {prefix}: cannot resolve where this cluster "
                  f"archives to, so the pointer cannot be compared")
            continue

        archive = dict(archive)
        archive.setdefault("serverName", a_server or name)
        if archive["destinationPath"] == store["destinationPath"]:
            print(f"  ok   {name:<20} {prefix}: newest base backup {stamp} "
                  f"(recovery reads the prefix it archives to)")
            continue

        a_newest, a_err = newest_backup(ctx, ns, pod, archive, creds)
        a_prefix = archive["destinationPath"].rstrip("/").split("/")[-1]
        if a_newest is None:
            print(f"  ok   {name:<20} {prefix}: newest base backup {stamp} "
                  f"(archive target {a_prefix} unreadable: {a_err})")
            continue

        lag = a_newest - newest
        if lag > MAX_LAG:
            failed = True
            finding(name, f"recovery reads {prefix} (newest {stamp}) but the cluster "
                          f"archives to {a_prefix} (newest "
                          f"{a_newest.strftime('%Y-%m-%d %H:%M')}) — {lag.days}d behind, "
                          f"and a rebuild would restore the older one")
            print(f"  FAIL {name:<20} recovery reads {prefix} (newest {stamp}) but the "
                  f"cluster archives to {a_prefix} (newest "
                  f"{a_newest.strftime('%Y-%m-%d %H:%M')}) — {lag.days}d behind, and "
                  f"a rebuild would restore the older one")
        else:
            print(f"  ok   {name:<20} {prefix}: newest base backup {stamp} "
                  f"(archive {a_prefix} within {MAX_LAG.days}d)")

    if not checked:
        print("  no cluster uses bootstrap.recovery — nothing to check")

    if args.report_to:
        try:
            status = file_findings(args.report_to, "recovery-source", args.cluster, FINDINGS)
            print(f"\n  filed {len(FINDINGS)} finding(s) with the bridge ({status})")
        except Exception as error:  # noqa: BLE001 — any failure to file is the same failure
            # A wrong recovery pointer that nobody is told about is exactly the
            # failure this check exists to catch, so a run that cannot file its
            # findings fails even when it found none. The Job's failure alerts.
            failed = True
            print(f"\n  FAILED to file findings with {args.report_to}: {error}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
