# The Photo Database That Had Backups — Except It Didn't

**Date**: July 6, 2026
**Severity**: Critical (latent) — no data was lost, but for 4 months none could have been recovered
**Duration**: ~4 months of silent unrecoverability, ~1 hour to diagnose and fix
**Impact**: `immich-cluster` (all Immich photo metadata) had zero recoverability from object storage; its metrics were never scraped; several database alerts could never fire

## The Problem

There were no symptoms. That is the problem.

Every dashboard was green. `ContinuousArchiving=True` on the cluster status. WAL
segments flowing to Backblaze B2. A `backup:` section in the Cluster manifest with
a retention policy. Everything *looked* like `immich-cluster` was backed up.

The gap surfaced only because a code review comment ("production immich-cluster
bootstraps with `initdb`") prompted the question: *is it initialized? It should
have backups active.* Checking the actual status:

```bash
kubectl get cluster immich-cluster -n database \
  -o jsonpath='{.status.firstRecoverabilityPoint} {.status.lastSuccessfulBackup}'
# → empty, empty
```

Empty `firstRecoverabilityPoint` means: **not recoverable, at all**. WAL archiving
without a base backup is a stream of diffs against a snapshot that doesn't exist.

## Root Causes (plural — they stacked)

### 1. `backup:` in the Cluster spec does not create backups

CNPG's `spec.backup.barmanObjectStore` configures *where* backups and WAL go, and
enables WAL archiving. Actual base backups only happen when something creates
`Backup` objects — normally a `ScheduledBackup` resource. Only one existed, and it
targeted `postgres-cluster`. When `immich-cluster` was re-created fresh in March
2026 (the `production-2026-03-immich/` backup path), nobody added a second
ScheduledBackup. WAL archived diligently into the void ever since.

### 2. The "daily" backups were actually hourly

The existing ScheduledBackup used `schedule: "0 3 * * *"` with a comment saying
"Daily at 3 AM". CNPG's ScheduledBackup cron has **six fields with leading
seconds** — so that expression means "second 0, minute 3, *every hour*". 2,217
Backup objects had accumulated. Wrong in the harmless direction, but wrong in a
way that shows the schedule was never verified against reality.

### 3. immich-cluster was never scraped by Prometheus

The `cloudnative-pg-clusters` PodMonitor selected pods with
`matchLabels: cnpg.io/cluster: postgres-cluster` — an exact match. immich-cluster
pods matched nothing, so the cluster holding the photos had **no metrics at all**.

### 4. The database alerts referencing immich could never fire

Two PrometheusRules filtered on `cnpg_io_cluster=~"postgres-cluster|immich-cluster"`
— a label that existed on **zero series**, because nothing (like
`podTargetLabels`) ever attached it to the scrape. The alerts written to watch the
database were watching an empty query result. Silently, of course.

## The Fix

1. **ScheduledBackup for immich-cluster** in both environments, with
   `immediate: true` — the first-ever base backup completed ~90 seconds after
   Flux applied it, and `firstRecoverabilityPoint` finally got a value.
2. **Six-field cron** on both schedules: `"0 0 3 * * *"` (and staggered
   `"0 30 3 * * *"` for immich), with a comment documenting the gotcha.
3. **PodMonitor** switched to `matchExpressions: cnpg.io/cluster Exists` plus
   `podTargetLabels: [cnpg.io/cluster]` — every CNPG cluster is scraped, and the
   `cnpg_io_cluster` label the alerts expect now actually exists.
4. **Two new alerts** that make this failure class impossible to miss again:
   `PostgreSQLBackupTooOld` (newest base backup > 26h) and
   `PostgreSQLBackupNeverTaken` (a cluster with no base backup at all). Note the
   metric detail: only the primary reports `cnpg_collector_last_available_backup_timestamp`
   non-zero, so both alerts aggregate `max by (cnpg_io_cluster)`.
5. ~2,500 stale Backup objects deleted across both clusters (deleting a Backup
   CR does not touch object-store data; retention policy owns that).

## Lessons Learned

1. **Green is not recoverable.** `ContinuousArchiving=True` measures the pipe,
   not the destination's usefulness. The only status field that means "you can
   restore this" is `firstRecoverabilityPoint` — check it, alert on it, and
   ideally restore-test it.
2. **Config that looks like backups isn't backups.** A `backup:` section without
   a `ScheduledBackup` is WAL archiving only. The two are separate resources and
   it's entirely possible to have either without the other.
3. **Verify alerts fire, not just that they load.** A PrometheusRule with a
   label selector matching zero series is indistinguishable from a healthy quiet
   alert. When writing rules, run the expression against live Prometheus first —
   and check the labels actually exist.
4. **Exact-match selectors rot.** The PodMonitor was correct when there was one
   cluster. The second cluster arrived; nothing failed; coverage silently halved.
   Prefer `Exists`/pattern selectors for infrastructure that enumerates peers.
5. **Read the cron dialect.** CNPG (Go `robfig/cron`) uses six fields with
   seconds. A five-field crontab habit produces a valid-but-wrong schedule.
6. **The best detection was a human asking "wait, is that right?"** — which is
   why this entire class now has machine detection (`PostgreSQLBackupNeverTaken`).

## Related

- [PostgreSQL WAL Archiving Failure Causing PVC Fill-Up](postgres-wal-archiving-failure-pvc-fillup.md)
  — the inverse failure: archiving broken while backups looked fine.
- [PostgreSQL Bootstrap Recovery](postgres-bootstrap-recovery.md) — the recovery
  procedure that would have been impossible for immich during this window.
