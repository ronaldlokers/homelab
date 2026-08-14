#!/usr/bin/env python3
"""Restore every PostgreSQL backup into a throwaway cluster and see what comes back.

The runbook claims an RTO of 20-30 minutes and an RPO of near-zero. Nothing has
ever tested either. `recovery-source-check` proves the *pointer* resolves to a
prefix holding a recent base backup — which is a catalogue listing, not a
restore. Between "barman says a backup exists" and "postgres starts and the rows
are there" sits every way a backup can be useless.

So each week this restores each cluster twice, into scratch clusters that are
deleted afterwards:

1. **Latest.** No recovery target, so CloudNativePG replays every WAL segment it
   can reach. Time to Ready is the RTO, measured. The age of the newest row is
   the RPO, measured — that gap is exactly what a real recovery would lose.

2. **A point in time.** Targeting an hour before the run, asserting the newest
   row is *older* than that target. This is the half nobody tests: replaying to
   the end proves the WAL is readable, not that you can stop where you aim,
   which is the thing you need on the day someone drops a table.

The scratch clusters carry **no `backup` stanza**. This is the one line in here
that must never change: a restored cluster that archives writes WAL into the
prefix it just restored from, under the same serverName, and corrupts the
catalogue of the thing being proved. The drill would then be the cause of the
disaster it exists to rehearse.

Usage:
    scripts/restore-drill.py --context production
    scripts/restore-drill.py --context production --only immich-cluster

Exit code is non-zero if any restore fails, comes back empty, or lands past the
point it was aimed at — which reaches the alerts room as KubeJobFailed.
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

# How long a restore may take before it is a failure rather than a slow day.
# The runbook claims 20-30 minutes for a whole cluster rebuild; a single 200MB
# database has no business taking a quarter of that.
READY_TIMEOUT = timedelta(minutes=10)
POLL_SECONDS = 10

# How far back the point-in-time restore aims. Far enough that WAL replay has to
# stop somewhere real, recent enough that the segments are certainly present.
PITR_BACK = timedelta(hours=1)

# What proves a database is populated rather than merely running. A count of
# zero here is a failure: an empty restore reports Ready just as happily as a
# full one, which is the failure mode this whole script exists to catch.
#
# The timestamp column is what makes the RPO measurable. Where a table has none,
# the row count still proves the data arrived.
TARGETS = {
    "postgres-cluster": ("linkding", "bookmarks_bookmark", "date_added"),
    "immich-cluster": ("immich", "assets", '"createdAt"'),
    "nightscout-cluster": ("nightscout", "entries", None),
}


def kubectl(ctx, *args, check=True, timeout=180, stdin=None):
    prefix = ["kubectl"] + ([f"--context={ctx}"] if ctx else [])
    r = subprocess.run([*prefix, *args], capture_output=True, text=True,
                       timeout=timeout, input=stdin)
    if check and r.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def clusters(ctx):
    """The live clusters, where each recovers from, and what it runs."""
    out = kubectl(ctx, "get", "cluster", "-n", "database", "-o", "json")
    for item in json.loads(out)["items"]:
        name = item["metadata"]["name"]
        spec = item["spec"]
        source = (spec.get("bootstrap") or {}).get("recovery", {}).get("source")
        ext = next((e for e in spec.get("externalClusters", []) if e["name"] == source), None)
        store = (ext or {}).get("barmanObjectStore")
        if not store:
            # The plugin spells this differently; when production moves to it,
            # this is the branch that has to learn the other spelling.
            print(f"  skip {name}: no barmanObjectStore on its recovery source")
            continue
        yield name, store, spec.get("imageName"), spec.get("postgresql")


def drill_manifest(name, store, image=None, postgresql=None, target_time=None):
    """A scratch cluster that restores and archives nothing.

    Note what is absent: `backup`. See this file's docstring — with it, the
    drill would write into the prefix it restored from.

    The image is inherited from the source cluster, and that is not a detail.
    The first run of this drill left it unset, so the operator used its own
    default — PostgreSQL 18 against a data directory written by 17 — and the
    restore died with "database files are incompatible with server" after
    downloading a perfectly good backup. Nightscout runs a DocumentDB image
    besides. A restore into the wrong image is exactly the mistake a hurried
    human makes at 3am, which is the argument for the drill inheriting rather
    than being told.
    """
    spec = {
        "instances": 1,
        "imagePullPolicy": "IfNotPresent",
        "storage": {"size": "5Gi", "storageClass": "longhorn"},
        "resources": {"requests": {"cpu": "100m", "memory": "512Mi"},
                      "limits": {"memory": "2Gi"}},
        "bootstrap": {"recovery": {"source": "origin"}},
        "externalClusters": [{"name": "origin", "barmanObjectStore": store}],
    }
    if image:
        spec["imageName"] = image
    if postgresql:
        # shared_preload_libraries and friends: a cluster that needs an
        # extension preloaded needs it preloaded to be restored, too.
        spec["postgresql"] = postgresql
    if target_time:
        spec["bootstrap"]["recovery"]["recoveryTarget"] = {
            "targetTime": target_time.strftime("%Y-%m-%d %H:%M:%S%z"),
        }
    return {
        "apiVersion": "postgresql.cnpg.io/v1",
        "kind": "Cluster",
        "metadata": {
            "name": name,
            "namespace": "database",
            "labels": {"homelab.io/restore-drill": "true"},
        },
        "spec": spec,
    }


def create(ctx, manifest):
    kubectl(ctx, "apply", "-f", "-", stdin=json.dumps(manifest))


def destroy(ctx, name):
    """Delete the cluster and the PVC it leaves behind.

    CloudNativePG keeps PVCs after a Cluster is deleted, by design — a scratch
    cluster that left 5Gi of Longhorn behind every week would quietly fill a
    node in a couple of months.
    """
    kubectl(ctx, "delete", "cluster", name, "-n", "database",
            "--ignore-not-found", "--wait=true", check=False, timeout=300)
    kubectl(ctx, "delete", "pvc", "-n", "database",
            "-l", f"cnpg.io/cluster={name}", "--ignore-not-found",
            check=False, timeout=300)


def why_stuck(ctx, name):
    """What the restore was complaining about, before the evidence is deleted.

    The first failure of this drill reported only "never reached a healthy
    state", and the cluster was already gone by the time anyone read it. The
    answer was in the recovery pod's log — a FATAL two lines long — so the
    weekly record now carries it and nobody has to reproduce the failure to
    find out what it was.
    """
    pods = kubectl(ctx, "get", "pods", "-n", "database",
                   "-l", f"cnpg.io/cluster={name}",
                   "-o", "jsonpath={.items[*].metadata.name}", check=False)
    for pod in pods.split():
        log = kubectl(ctx, "logs", "-n", "database", pod, "--all-containers",
                      "--tail=200", check=False, timeout=60)
        for line in reversed(log.splitlines()):
            if "FATAL" in line or "DETAIL" in line:
                # These are JSON log lines; the readable part is the message.
                try:
                    return json.loads(line).get("msg", line)[:300]
                except json.JSONDecodeError:
                    return line[:300]
    return "no FATAL in the recovery pod's log"


def wait_ready(ctx, name, deadline):
    """Poll until the operator says the cluster is up, or give up."""
    while datetime.now(timezone.utc) < deadline:
        out = kubectl(ctx, "get", "cluster", name, "-n", "database",
                      "-o", "jsonpath={.status.phase}", check=False)
        phase = out.strip()
        if phase == "Cluster in healthy state":
            return True
        if "fail" in phase.lower():
            print(f"       operator reports: {phase}")
            return False
        time.sleep(POLL_SECONDS)
    return False


def query(ctx, name, database, sql):
    pod = f"{name}-1"
    out = kubectl(ctx, "exec", "-n", "database", pod, "-c", "postgres", "--",
                  "psql", "-U", "postgres", "-d", database, "-tAc", sql,
                  check=False, timeout=120)
    return out.strip()


def newest_row(ctx, name, database, table, column):
    if not column:
        return None
    answer = query(ctx, name, database, f"SELECT max({column}) FROM {table};")
    if not answer:
        return None
    try:
        stamp = datetime.fromisoformat(answer.replace(" ", "T"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def drill(ctx, source, store, image, postgresql, findings):
    """Both restores for one cluster. Returns True if everything held."""
    database, table, column = TARGETS.get(source, (None, None, None))
    if not database:
        findings.append(f"{source}: no row-count target configured, so a restore "
                        f"could come back empty and pass")
        return False

    name = f"drill-{source}".replace("-cluster", "")[:30]
    ok = True

    # --- 1. the latest the backups can give us -----------------------------
    destroy(ctx, name)
    started = datetime.now(timezone.utc)
    create(ctx, drill_manifest(name, store, image, postgresql))
    if not wait_ready(ctx, name, started + READY_TIMEOUT):
        findings.append(f"{source}: restore never reached a healthy state within "
                        f"{int(READY_TIMEOUT.total_seconds() / 60)}m — {why_stuck(ctx, name)}")
        destroy(ctx, name)
        return False

    rto = datetime.now(timezone.utc) - started
    rows = query(ctx, name, database, f"SELECT count(*) FROM {table};")
    count = int(rows) if rows.isdigit() else 0
    newest = newest_row(ctx, name, database, table, column)

    if count == 0:
        ok = False
        findings.append(f"{source}: restored and came back empty — {table} has no rows")

    rpo = ""
    if newest:
        behind = datetime.now(timezone.utc) - newest
        rpo = f", newest row {int(behind.total_seconds() / 60)}m old"
        # A restore whose freshest row predates the last scheduled backup means
        # WAL replay is not reaching the end, which is an RPO of hours rather
        # than the "near-zero" the runbook claims.
        if behind > timedelta(hours=26):
            ok = False
            findings.append(f"{source}: restored to {int(behind.total_seconds() / 3600)}h "
                            f"ago, so WAL replay is not reaching the present")

    print(f"  ok   {source:<20} ready in {int(rto.total_seconds())}s, "
          f"{count} rows in {table}{rpo}")
    destroy(ctx, name)

    # --- 2. and a point in time we choose ----------------------------------
    target = datetime.now(timezone.utc) - PITR_BACK
    started = datetime.now(timezone.utc)
    create(ctx, drill_manifest(name, store, image, postgresql, target_time=target))
    if not wait_ready(ctx, name, started + READY_TIMEOUT):
        findings.append(f"{source}: point-in-time restore to {target:%H:%M} never "
                        f"reached a healthy state — {why_stuck(ctx, name)}")
        destroy(ctx, name)
        return False

    landed = newest_row(ctx, name, database, table, column)
    if landed and landed > target + timedelta(minutes=5):
        ok = False
        findings.append(f"{source}: aimed at {target:%H:%M} but the restore holds "
                        f"rows from {landed:%H:%M}, so it did not stop where told")
    else:
        where = f", newest row {landed:%H:%M}" if landed else ""
        print(f"  ok   {source:<20} point-in-time restore to {target:%H:%M} held"
              f"{where}")
    destroy(ctx, name)
    return ok


def post(url, html, timeout=15):
    request = urllib.request.Request(
        url, data=html.encode(), headers={"Content-Type": "text/html"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status


def report(url, lines, failed):
    """The weekly record, posted whether or not anything went wrong.

    A drill that only speaks up when it fails is one whose silence has to be
    trusted, and the whole point here is to stop trusting untested things.
    """
    mark = "⚠️" if failed else "✅"
    head = f"<div><strong>{mark} restore drill</strong></div>"
    body = "".join(f"<li>{line}</li>" for line in lines)
    return post(url, f"{head}<ul>{body}</ul>")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--context", default=None,
                    help="omit when running inside the cluster")
    ap.add_argument("--only", default=None,
                    help="drill one cluster by name instead of all of them")
    ap.add_argument("--report-to", default=os.environ.get("CAMPFIRE_URL"),
                    help="room URL for the weekly record; defaults to $CAMPFIRE_URL")
    args = ap.parse_args()
    ctx = args.context

    print(f"== restoring every backup in {ctx or 'this cluster'} ==")
    findings = []
    lines = []
    failed = False

    for source, store, image, postgresql in clusters(ctx):
        if args.only and source != args.only:
            continue
        before = len(findings)
        started = datetime.now(timezone.utc)
        try:
            drill(ctx, source, store, image, postgresql, findings)
        except Exception as error:  # noqa: BLE001 — one cluster failing is a finding
            findings.append(f"{source}: the drill itself failed ({error})")
        took = int((datetime.now(timezone.utc) - started).total_seconds())
        if len(findings) > before:
            failed = True
            lines.append(f"<strong>{source}</strong>: " + "; ".join(findings[before:]))
        else:
            lines.append(f"<strong>{source}</strong>: restored and verified in {took}s")

    if not lines:
        findings.append("no clusters were drilled at all")
        failed = True
        lines.append("nothing was drilled — no cluster had a recovery source to test")

    for finding in findings:
        print(f"  FAIL {finding}")

    if args.report_to:
        try:
            report(args.report_to, lines, failed)
            print("\n  filed the week's drill with the room")
        except Exception as error:  # noqa: BLE001
            print(f"\n  FAILED to file the record: {error}")
            failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
