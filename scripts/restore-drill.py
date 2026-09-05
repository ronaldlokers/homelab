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
import copy
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

# How far past today's backup the point-in-time target sits, once backup_anchor()
# has found when that backup actually fired. Far enough that some real write
# almost certainly happened in between, so the restore has something to prove it
# stopped before; recent enough that the WAL between the backup and the target is
# minutes, not a whole day's worth.
PITR_MARGIN = timedelta(minutes=15)

# Fallback only, for a cluster whose ScheduledBackup backup_anchor() can't find —
# see its docstring for why "now" alone used to be the target, and why that was
# wrong often enough to matter.
PITR_BACK = timedelta(hours=1)

# What proves a database is populated rather than merely running, per cluster:
# the database to connect to, a query that counts something real, a query that
# finds the newest thing in it, and whether that newest-thing query is one
# this drill should trust to fail the run when it comes back stale.
#
# Every one of these was wrong when first written, and each was wrong in a way
# that would have passed review:
#
#   * immich's table is `asset`, not `assets`.
#   * nightscout's readings are not in a `nightscout` database and not in a
#     table anyone would guess. FerretDB stores them as DocumentDB collections
#     in the `postgres` database, under `documentdb_data.documents_<n>`, where
#     <n> is a collection id that changes if a collection is recreated — so the
#     count sums every documents_* table rather than naming one.
#   * the shared cluster's busiest table belongs to authentik's task log, which
#     churns; gatus writes a row every check, which makes its newest row a
#     genuine RPO signal rather than a number that happens to be large.
#   * immich's `asset.createdAt` looked like the same kind of signal and is
#     not: it is the newest *photo*, not the newest write, and this library
#     goes days between uploads — nine of them, the week this was found. The
#     drill read that gap as WAL replay stalling 66 hours behind and failed a
#     restore that had in fact reached the true end of the archive. The count
#     query still proves the restore is real; the freshness query is left in
#     for what it shows in the room's weekly post, but the fourth field below
#     keeps it from failing the drill on a photo library's silence.
#
# A count of zero is a failure: an empty restore reports Ready exactly as
# happily as a full one, and that is the failure this drill exists to catch.
# `count(*)` rather than `n_live_tup`, because a freshly restored cluster has
# never been analysed and its statistics are all zero — which would have
# reported every successful restore as empty.
TARGETS = {
    "postgres-cluster": (
        "gatus",
        "SELECT count(*) FROM endpoint_results",
        "SELECT max(timestamp) FROM endpoint_results",
        True,
    ),
    "immich-cluster": (
        "immich",
        'SELECT count(*) FROM asset',
        'SELECT max("createdAt") FROM asset',
        False,
    ),
    "nightscout-cluster": (
        "postgres",
        # Exact, and blind to which collection ids exist: query_to_xml runs the
        # count for each table and the sum is the total document count.
        """SELECT coalesce(sum((xpath('/row/c/text()',
             query_to_xml(format('SELECT count(*) AS c FROM %I.%I', schemaname, tablename),
                          false, true, '')))[1]::text::bigint), 0)
           FROM pg_tables
           WHERE schemaname = 'documentdb_data' AND tablename LIKE 'documents\\_%'""",
        None,
        False,
    ),
}


def kubectl(ctx, *args, check=True, timeout=180, stdin=None):
    prefix = ["kubectl"] + ([f"--context={ctx}"] if ctx else [])
    r = subprocess.run([*prefix, *args], capture_output=True, text=True,
                       timeout=timeout, input=stdin)
    if check and r.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def clusters(ctx):
    """Every live cluster's whole spec, which is what a faithful restore needs.

    An earlier version handed back three fields — the object store, the image,
    the postgresql block — and each production run found another one missing.
    First `imageName` (PostgreSQL 18 against a 17 data directory), then
    `postgresUID` (the DocumentDB image's postgres user is 999, and initdb
    refuses to run as a uid with no passwd entry). There is no reason to think
    that list was finished, so the drill now copies the spec and removes what
    must differ, which fails safe in the other direction.
    """
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
        yield name, spec, source


def backup_anchor(ctx, source):
    """When today's backup for this cluster actually fired, per the
    ScheduledBackup that owns it — `<source>-daily-backup`, the naming every
    ScheduledBackup in this repo follows.

    The point-in-time target used to be `datetime.now() - PITR_BACK`: an hour
    before whenever the drill happened to reach this cluster. That is wrong
    exactly as often as this CronJob's own schedule lands inside the backup
    window it was written to sit clear of. It was written to sit clear of it
    in Europe/Amsterdam local time, but the ScheduledBackups fire on a plain
    UTC cron with no timezone — so the hour of daylight saving that Amsterdam
    carries and UTC does not is a gap that opens and closes twice a year.
    In CEST this job's 05:00 local start is 03:00 UTC, which is *inside*
    02:45-03:30 UTC, not an hour clear of it. Whichever cluster the drill
    reaches while its target still lands before that day's backup gets no
    backup to restore from but yesterday's — 24 hours of WAL where the code
    below expects a quarter of an hour, and the ten-minute READY_TIMEOUT was
    never sized for that. nightscout-cluster and postgres-cluster hit exactly
    this on 2026-08-30: both PITR restores were still replaying WAL, making
    steady progress, when the timeout gave up on them.

    Anchoring on the ScheduledBackup's own record of when it last fired is
    right regardless of what wall-clock time the drill itself happens to run,
    which a fix to this job's own schedule would not be: the three clusters
    back up at three different times, and nothing keeps them a fixed distance
    from whatever time this CronJob is given next.
    """
    out = kubectl(ctx, "get", "scheduledbackup", f"{source}-daily-backup", "-n", "database",
                 "-o", "jsonpath={.status.lastScheduleTime}", check=False).strip()
    if not out:
        return None
    return datetime.fromisoformat(out.replace("Z", "+00:00"))


def drill_manifest(source_spec, source_name, name, target_time=None):
    """A scratch cluster that restores the same way the original would, and
    archives nothing.

    Built by copying the source's spec and removing what must differ, rather
    than by listing what to keep: every run that listed what to keep found
    another field it had not thought of.

    STRIPPED is the whole of the safety argument, and `backup` is the line that
    matters. A restored cluster that archives writes WAL into the prefix it just
    restored from, under the same serverName, and corrupts the catalogue of the
    thing being proved. The assertion below is deliberately redundant with the
    removal: this is the one mistake in this program that would be worse than
    not having it at all.
    """
    STRIPPED = (
        "backup",            # never, on pain of corrupting the source
        "replica",           # a designated-primary drill would follow a live cluster
        "affinity",          # written for three instances across three nodes
        "topologySpreadConstraints",
        "managed",           # roles and their Secrets are the live cluster's business
        "monitoring",        # no dashboards for something that lives ten minutes
        "serviceAccountTemplate",
        "projectedVolumeTemplate",
        "nodeMaintenanceWindow",
        "priorityClassName",
        "resources",         # replaced below with something small
        "storage",           # replaced below with something small
        "walStorage",
        "instances",
        "bootstrap",
    )
    spec = copy.deepcopy(source_spec)
    for key in STRIPPED:
        spec.pop(key, None)

    spec["instances"] = 1
    spec["storage"] = {"size": "5Gi", "storageClass": "longhorn"}
    spec["resources"] = {"requests": {"cpu": "100m", "memory": "512Mi"},
                         "limits": {"memory": "2Gi"}}
    spec["imagePullPolicy"] = "IfNotPresent"
    spec["bootstrap"] = {"recovery": {"source": source_name}}
    if target_time:
        # isoformat, not strftime("%z"): CNPG rejects "2026-08-14 13:27:03+0000"
        # as "The format of TargetTime is invalid" and wants the colon in the
        # offset. Caught by the first point-in-time restore this drill ran.
        spec["bootstrap"]["recovery"]["recoveryTarget"] = {
            "targetTime": target_time.isoformat(sep=" ", timespec="seconds"),
        }

    # Belt and braces, and worth the two lines: if a future field ever carries
    # archiving back in under another name, this is the last thing between the
    # rehearsal and the disaster.
    if "backup" in spec:
        raise RuntimeError("refusing to create a drill cluster that would archive")

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
    """Delete the cluster, and the PVC it leaves behind, all the way to the disk.

    CloudNativePG keeps PVCs after a Cluster is deleted, by design — a scratch
    cluster that left 5Gi of Longhorn behind every week would quietly fill a
    node in a couple of months. That comment used to be the whole story, and
    it was wrong: the `longhorn` StorageClass carries `reclaimPolicy: Retain`,
    on purpose, so a stray `kubectl delete pvc` on someone's real data can't
    take the backing volume with it. Deleting the PVC above never freed
    anything — it turned the PV `Released` and stopped there, because nothing
    reclaims a Retain volume automatically. This drill ran that way for three
    weeks before anyone read `kubectl get pv`: twenty-nine of them, going back
    21 days, one or two per cluster per run, every run, pass or fail.
    Retain is the right default for the cluster; it is the wrong default for
    a volume that exists for ten minutes and never holds anything but a
    restore rehearsal.
    So each PVC's PV is flipped to `Delete` right before the PVC goes — the
    only moment that is safe, since the PVC delete below is what triggers the
    reclaim, and by then the volume's one remaining purpose was to be
    destroyed by this call.
    """
    volumes = kubectl(ctx, "get", "pvc", "-n", "database",
                      "-l", f"cnpg.io/cluster={name}",
                      "-o", "jsonpath={.items[*].spec.volumeName}", check=False).split()
    for volume in volumes:
        kubectl(ctx, "patch", "pv", volume, "--type=merge",
                "-p", '{"spec":{"persistentVolumeReclaimPolicy":"Delete"}}',
                check=False)

    kubectl(ctx, "delete", "cluster", name, "-n", "database",
            "--ignore-not-found", "--wait=true", check=False, timeout=300)
    kubectl(ctx, "delete", "pvc", "-n", "database",
            "-l", f"cnpg.io/cluster={name}", "--ignore-not-found",
            check=False, timeout=300)


def why_stuck(ctx, name, phase=""):
    """What the restore was complaining about, before the evidence is deleted.

    The first failure of this drill reported only "never reached a healthy
    state", and the cluster was already gone by the time anyone read it. The
    answer was in the recovery pod's log — a FATAL two lines long — so the
    weekly record now carries it and nobody has to reproduce the failure to
    find out what it was.

    Both drills of August 2026 then came back with the fallback text and
    nothing else, on four separate restores. A log scan only answers when the
    recovery *complained*: one that is merely slow logs nothing alarming, and
    one whose pod has not been scheduled yet has no log to read at all. So the
    scan is now the first answer rather than the only one. Everything below it
    — the phase the operator was reporting when the clock ran out, the
    Cluster's own unmet conditions, events on the Cluster, the pod's waiting
    reason — was already there each week and thrown away.

    Ordered most specific first: an accusation beats a location, and a
    location beats silence.
    """
    pods = kubectl(ctx, "get", "pods", "-n", "database",
                   "-l", f"cnpg.io/cluster={name}",
                   "-o", "jsonpath={.items[*].metadata.name}", check=False).split()

    for pod in pods:
        log = kubectl(ctx, "logs", "-n", "database", pod, "--all-containers",
                      "--tail=200", check=False, timeout=60)
        for line in reversed(log.splitlines()):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            message = str(entry.get("msg", ""))
            error = str(entry.get("error", ""))
            # Not only FATAL: initdb reports "initdb: could not look up
            # effective user ID 26" and never uses the word, which is how the
            # second failure came back saying nothing at all.
            if "FATAL" in message or "DETAIL" in message or message.startswith("initdb:"):
                return message[:300]
            if error and "restore" in message.lower():
                return f"{message}: {error}"[:300]

    # Nothing accused anything. Say where it had got to, which for a WAL replay
    # that simply needs longer than READY_TIMEOUT is the whole answer.
    where = [f"operator phase {phase!r}"] if phase else []

    conditions = kubectl(ctx, "get", "cluster", name, "-n", "database",
                         "-o", "jsonpath={.status.conditions}", check=False)
    try:
        for condition in json.loads(conditions or "[]"):
            # ContinuousArchiving is False on every scratch cluster by design —
            # they carry no backup stanza, which is the one thing in this drill
            # that must not change. Reporting it every week would train the
            # reader to skip the line that matters.
            if condition.get("status") == "True" or condition.get("type") == "ContinuousArchiving":
                continue
            where.append(f"{condition.get('type')} not met"
                         f" ({condition.get('reason')}: {condition.get('message')})")
    except json.JSONDecodeError:
        pass

    # Events catch what neither the log nor the conditions can: a pod that is
    # unschedulable, an image that will not pull, a Longhorn volume that never
    # binds. None of those produce a postgres log line, and the first two mean
    # there is no pod to read one from.
    events = kubectl(ctx, "get", "events", "-n", "database",
                     "--field-selector", f"involvedObject.name={name}",
                     "--sort-by=.lastTimestamp",
                     "-o", "jsonpath={range .items[*]}{.reason}: {.message}\\n{end}",
                     check=False, timeout=60)
    recent = [line for line in events.splitlines() if line.strip()]
    if recent:
        where.append(f"last event {recent[-1].strip()}")

    for pod in pods:
        waiting = kubectl(ctx, "get", "pod", pod, "-n", "database", "-o",
                          "jsonpath={.status.containerStatuses[*].state.waiting.reason}"
                          "{.status.initContainerStatuses[*].state.waiting.reason}",
                          check=False).strip()
        if waiting:
            where.append(f"{pod} waiting: {waiting}")

    if where:
        return "; ".join(where)[:300]
    if not pods:
        return "no recovery pod was ever created"
    return "nothing in the recovery pod's log said why"


def wait_ready(ctx, name, deadline):
    """Poll until the operator says the cluster is up, or give up.

    Returns (ready, last_phase). The phase is half the diagnosis when the
    deadline is what ended it — "Creating a new replica" and "Setting up
    primary" are different failures — and the caller had no way to see it.
    """
    phase = ""
    while datetime.now(timezone.utc) < deadline:
        out = kubectl(ctx, "get", "cluster", name, "-n", "database",
                      "-o", "jsonpath={.status.phase}", check=False)
        phase = out.strip() or phase
        if phase == "Cluster in healthy state":
            return True, phase
        if "fail" in phase.lower():
            print(f"       operator reports: {phase}")
            return False, phase
        time.sleep(POLL_SECONDS)
    return False, phase


def query(ctx, name, database, sql):
    pod = f"{name}-1"
    out = kubectl(ctx, "exec", "-n", "database", pod, "-c", "postgres", "--",
                  "psql", "-U", "postgres", "-d", database, "-tAc", sql,
                  check=False, timeout=120)
    return out.strip()


def newest_row(ctx, name, database, sql):
    if not sql:
        return None
    answer = query(ctx, name, database, sql)
    if not answer:
        return None
    try:
        stamp = datetime.fromisoformat(answer.replace(" ", "T"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def drill(ctx, source, spec, origin, findings):
    """Both restores for one cluster. Returns True if everything held."""
    database, count_sql, newest_sql, rpo_reliable = TARGETS.get(source, (None, None, None, False))
    if not database:
        findings.append(f"{source}: no row-count target configured, so a restore "
                        f"could come back empty and pass")
        return False

    name = f"drill-{source}".replace("-cluster", "")[:30]
    ok = True

    # --- 1. the latest the backups can give us -----------------------------
    destroy(ctx, name)
    started = datetime.now(timezone.utc)
    create(ctx, drill_manifest(spec, origin, name))
    ready, phase = wait_ready(ctx, name, started + READY_TIMEOUT)
    if not ready:
        findings.append(f"{source}: restore never reached a healthy state within "
                        f"{int(READY_TIMEOUT.total_seconds() / 60)}m — "
                        f"{why_stuck(ctx, name, phase)}")
        destroy(ctx, name)
        return False

    rto = datetime.now(timezone.utc) - started
    rows = query(ctx, name, database, count_sql)
    count = int(rows) if rows.isdigit() else 0
    newest = newest_row(ctx, name, database, newest_sql)

    if count == 0:
        ok = False
        findings.append(f"{source}: restored and came back empty — "
                        f"nothing counted in {database}")

    rpo = ""
    if newest:
        behind = datetime.now(timezone.utc) - newest
        rpo = f", newest row {int(behind.total_seconds() / 60)}m old"
        # A restore whose freshest row predates the last scheduled backup means
        # WAL replay is not reaching the end, which is an RPO of hours rather
        # than the "near-zero" the runbook claims — for a table that is written
        # often enough for its silence to mean anything. rpo_reliable is what
        # keeps this from failing immich's restore over a quiet photo library;
        # see the comment on TARGETS.
        if rpo_reliable and behind > timedelta(hours=26):
            ok = False
            findings.append(f"{source}: restored to {int(behind.total_seconds() / 3600)}h "
                            f"ago, so WAL replay is not reaching the present")

    print(f"  ok   {source:<20} ready in {int(rto.total_seconds())}s, "
          f"{count} rows in {database}{rpo}")
    destroy(ctx, name)

    # --- 2. and a point in time we choose ----------------------------------
    anchor = backup_anchor(ctx, source)
    if anchor:
        # min() against "now" is the guard for the one case anchoring doesn't
        # cover on its own: a drill run close enough behind the backup that
        # anchor + PITR_MARGIN hasn't happened yet, which would hand CNPG a
        # recovery target in the future.
        target = min(anchor + PITR_MARGIN, datetime.now(timezone.utc) - timedelta(minutes=1))
    else:
        # No ScheduledBackup by the name this drill expects — fall back to the
        # old arithmetic rather than skip the check, but this is exactly the
        # situation backup_anchor() exists to avoid, so say so.
        print(f"  no {source}-daily-backup ScheduledBackup found; "
              f"aiming {PITR_BACK} back from now instead of from the backup")
        target = datetime.now(timezone.utc) - PITR_BACK
    started = datetime.now(timezone.utc)
    create(ctx, drill_manifest(spec, origin, name, target_time=target))
    ready, phase = wait_ready(ctx, name, started + READY_TIMEOUT)
    if not ready:
        findings.append(f"{source}: point-in-time restore to {target:%H:%M} never "
                        f"reached a healthy state — {why_stuck(ctx, name, phase)}")
        destroy(ctx, name)
        return False

    landed = newest_row(ctx, name, database, newest_sql)
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

    for source, spec, origin in clusters(ctx):
        if args.only and source != args.only:
            continue
        before = len(findings)
        started = datetime.now(timezone.utc)
        try:
            drill(ctx, source, spec, origin, findings)
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
