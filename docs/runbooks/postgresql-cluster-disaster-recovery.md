# PostgreSQL Cluster Disaster Recovery

## Quick Reference

- **Severity**: Critical (potential data loss)
- **Time to Resolve**: 18 minutes, measured — see "Verified restore" below
- **Scenario**: PostgreSQL cluster accidentally deleted or corrupted
- **Outcome**: Cluster restored from Backblaze B2 backups with minimal data loss
- **Prerequisites**: Backups configured and running, access to cluster manifests

## Verified restore

This procedure was untested until **2026-08-04**. It had been asserted in four
documents and never once run, which is the failure mode
[the Immich backup gap](../war-stories/cnpg-invisible-backup-gap.md) is about:
backups that look healthy and have never been proven to restore.

It has now been executed end to end against the real Backblaze B2 backups,
restoring `postgres-cluster` into a throwaway namespace with no `backup:`
section, so it could not archive WAL back into the production prefix.

**Result: it works.** Data verified against a live baseline taken beforehand:

| Check | Live | Restored |
|---|---|---|
| databases | 8 | 8 |
| linkding tables / bookmarks | 21 / 44 | 21 / 44 |
| mealie tables / recipes | 66 / 49 | 66 / 49 |
| commafeed / gatus tables | 13 / 8 | 13 / 8 |
| `documentdb_data.documents_102` (CGM) | 22,045 | 22,042 |

Extensions returned intact (`documentdb 0.107-0`, `pg_cron`, `postgis`, `vector`,
`rum`) and `pg_is_in_recovery()` returned false, confirming promotion.

The 3-row gap on the CGM table is the correct result, not a discrepancy: readings
kept arriving in production while the restore replayed to a fixed point. An exact
match there would have meant the query was accidentally hitting the live cluster.

**Timing.** 18 minutes from `Cluster` creation to primary `Ready`, for ~3GB
logical / 12GB on disk, downloaded from B2 and WAL-replayed on ARM64. The
15-30 minute estimate above was sound; it had simply never been checked.

### Three things that cost time, and will again

1. **`serverName` is mandatory and its absence is not obvious.** Backups live
   under `<destinationPath>/<serverName>/`. Omit it and CloudNativePG defaults to
   the *new* cluster's name, finds nothing, and fails with a flat
   `no target backup found` — no hint that the path is the problem. The
   `externalClusters` blocks in this runbook already set it; do not drop it when
   adapting them.

2. **A failed attempt poisons the next one.** Retrying over the same PVC fails
   with `FATAL: lock file "postmaster.pid" already exists`, and each retry
   restarts the whole download and replay from the beginning. Delete the cluster
   **and its PVC** before retrying:

   ```bash
   kubectl delete cluster <name> -n <ns> --wait=true
   kubectl delete pvc --all -n <ns> --wait=true
   ```

3. **There is no progress indicator.** `status.phase` sits on
   `Setting up primary` for the entire restore. Real progress is only visible in
   the recovery job's logs:

   ```bash
   kubectl logs -n <ns> job/<cluster>-1-full-recovery -f
   # "Target backup found"
   # "restored log file 0000000E000000D300000021 from archive"
   # "redo in progress, elapsed time: 740.86 s, current LSN: D3/22BE4638"
   ```

   To estimate remaining work, compare that LSN against
   `SELECT pg_current_wal_lsn()` on the source.

### Not yet verified

`postgres-cluster` has been restore-tested, and `nightscout-cluster` on
2026-08-04 (22,306 entries, `create_collection` and `drop_collection` both
functional). `immich-cluster` backs up to a separate prefix
(`production-2026-09-immich/`) and uses the same mechanism, so it is likely
fine — but "likely fine" is precisely the claim this exercise existed to stop
us making.

None of those restores went through the Barman Cloud Plugin: every one of them
read a catalogue written by the in-tree path. The plugin writes the same barman
layout, which is why the migration keeps the existing prefixes, but the first
plugin-era restore is still unverified.

## When to Use This Runbook

**Use this runbook when:**
- ✅ PostgreSQL cluster was accidentally deleted (`kubectl delete cluster`)
- ✅ Namespace containing database was deleted
- ✅ Cluster corruption requiring recreation
- ✅ Testing disaster recovery procedures
- ✅ Migrating cluster to new namespace

**This runbook assumes:**
- Backups exist in Backblaze B2
- Backup credentials are available
- Flux is managing cluster configuration

## Understanding the Issue

### The Risk

PostgreSQL clusters can bootstrap in two modes:

1. **initdb mode**: Creates fresh, empty cluster ❌
   - Good for initial deployment
   - Disaster if cluster is recreated after deletion
   - All data lost!

2. **recovery mode**: Restores from backup ✅
   - Connects to backup storage
   - Restores most recent backup
   - Replays WAL logs for point-in-time recovery

**If your cluster uses `initdb` mode in production, any accidental deletion results in permanent data loss**, even though backups exist.

### The recover-from ≠ archive-to rule (important)

A recovered cluster **must not archive its WAL into the same object-store path it
just recovered from**. CloudNativePG runs `barman-cloud-check-wal-archive` before
the first WAL push and refuses a destination that already contains a WAL history,
failing with:

```
barman-cloud-check-wal-archive ... ERROR: WAL archive check failed for server <name>: Expected empty archive
```

This is a safety feature: two cluster generations writing to one WAL timeline
corrupts point-in-time recovery. The fix is to keep the **recovery source** and
the **archive destination** on distinct paths. This repo does that with dated
`destinationPath` prefixes, e.g.:

| Path role | Example |
|-----------|---------|
| Recover **from** (the `ObjectStore` named by `externalClusters[].plugin`) | `s3://…/production-2026-03/` |
| Archive **to** (the `ObjectStore` named by `spec.plugins[]`) | `s3://…/production-2026-07/` |

Since the migration to the Barman Cloud Plugin (#377), both are
`ObjectStore.spec.configuration.destinationPath`. Day to day the two entries
name the *same* store, which is what keeps them equal without review; a DR is
the one time they must differ, and that means a second `ObjectStore`, not a
second copy of the path.

**On every real DR / cluster rebuild:**
1. Point the recovery source at the **current** live archive prefix (the one the
   cluster was most recently backing up to). A committed recovery source goes
   stale the moment the live cluster keeps archiving — do not assume the value
   in git is still the latest.
2. Create a **new** `ObjectStore` with a new dated prefix (bump the month/date)
   and point `spec.plugins[].parameters.barmanObjectName` at it. This becomes
   the recovered cluster's fresh timeline. Leave `externalClusters[].plugin`
   naming the old store until step 3 has run.
3. After recovery, trigger a `Backup` so a base backup exists on the new prefix
   before relying on it for PITR. An on-demand backup has to name the plugin
   explicitly — the default method is still `barmanObjectStore`, which a
   migrated cluster no longer has:

   ```bash
   kubectl cnpg backup <cluster> -n database \
     --method plugin --plugin-name barman-cloud.cloudnative-pg.io
   ```

   Only once that reports `completed` may `externalClusters[].plugin` be
   repointed at the new store. Recovery aimed at an empty prefix fails, and a
   rebuild is exactly when nobody is watching (#394).
4. **Delete the prefix you rotated away from, in the same change.** This step is
   new because skipping it is what produced #167: four dead prefixes holding
   51.9 GB, 90% of the bucket. Cost was never the issue — at B2 pricing that is
   about $0.31/month. The issue is that each dead prefix is another plausible
   answer to "which one do I restore from", and that question has now been
   answered wrongly twice (#238 on postgres-cluster, #257 on immich, six weeks
   stale and would have reported Ready).

   Note the bucket has **versioning enabled**, so `aws s3 rm --recursive` frees
   nothing — it only writes delete markers. Deletion must enumerate and remove
   object *versions*:

   ```bash
   aws --endpoint-url https://s3.eu-central-003.backblazeb2.com \
     s3api list-object-versions --bucket homelab-postgres-backups --prefix "<retired>/" \
     --query '{Objects: Versions[].{Key:Key,VersionId:VersionId}}' > /tmp/v.json
   aws --endpoint-url ... s3api delete-objects --bucket homelab-postgres-backups \
     --delete file:///tmp/v.json
   ```

   Delete markers (`DeleteMarkers[]`) need the same treatment. The in-cluster
   `b2-credentials` key can do this but **cannot** manage bucket lifecycle rules
   — `AccessDenied: not entitled` — so an automated age-out rule has to be set
   in the Backblaze console with a more privileged key.

> `serverName` identifies the source server inside that prefix; keep it matching
> the cluster name of the generation you are restoring. On the plugin it is a
> parameter under `externalClusters[].plugin`, **not** a field on the
> `ObjectStore` — the store's own `serverName` is required to stay empty.

## Immediate Actions

### If Cluster Was Just Deleted

**DON'T PANIC** - if backups are configured, data is recoverable.

1. **Prevent automatic recreation** (if using GitOps):
   ```bash
   # Suspend Flux reconciliation temporarily
   flux suspend kustomization infrastructure-configs --context=production
   ```

2. **Verify backups exist**:
   ```bash
   # Check B2 bucket for backups
   # You should see base backups and WAL archives
   # Can check via B2 web UI or CLI
   ```

3. **Check cluster configuration** before allowing recreation:
   ```bash
   # View current cluster manifest
   cat infrastructure/configs/production/cloudnative-pg/postgres-cluster.yaml

   # Look for bootstrap section
   grep -A 10 "bootstrap:" infrastructure/configs/production/cloudnative-pg/postgres-cluster.yaml
   ```

## Diagnosis Steps

### 1. Determine current bootstrap mode

Check the cluster configuration:

```bash
cat infrastructure/configs/production/cloudnative-pg/postgres-cluster.yaml
```

**DANGEROUS configuration (initdb mode):**
```yaml
bootstrap:
  initdb:
    postInitSQL:
      - 'CREATE EXTENSION IF NOT EXISTS documentdb CASCADE;'
```

**SAFE configuration (recovery mode):**
```yaml
bootstrap:
  recovery:
    source: clusterBackup

# Recover FROM the store holding the current live archive...
externalClusters:
  - name: clusterBackup
    plugin:
      name: barman-cloud.cloudnative-pg.io
      parameters:
        barmanObjectName: postgres-store          # destinationPath: …/production-2026-03/
        serverName: postgres-cluster

# ...and archive TO a different store on a new prefix
# (see "recover-from ≠ archive-to" above).
plugins:
  - name: barman-cloud.cloudnative-pg.io
    isWALArchiver: true
    parameters:
      barmanObjectName: postgres-store-2026-07    # destinationPath: …/production-2026-07/
```

Both names must resolve to an `ObjectStore` in the same namespace. Outside a DR
they are the same store, named twice.

> ⚠️ Do **not** set both `destinationPath`s to the same prefix — recovery will
> fail with "Expected empty archive". The old single-`production/` examples in
> earlier revisions of this runbook predate the dated-prefix scheme.

### 2. Check if backups exist

```bash
# If cluster still exists, check backup status
kubectl get backup -n database

# Check scheduled backup configuration
kubectl get scheduledbackup -n database -o yaml

# Verify last backup timestamp
kubectl describe cluster -n database postgres-cluster | grep -A 5 "Last Successful Backup"
```

### 3. Confirm backup credentials are available

```bash
# Check B2 credentials secret exists
kubectl get secret -n database b2-credentials

# Verify it contains required keys
kubectl get secret -n database b2-credentials -o jsonpath='{.data}' | jq 'keys'
# Should show: ACCESS_KEY_ID, ACCESS_SECRET_KEY
```

## Resolution Steps

### Step 1: Update cluster configuration to recovery mode

**Only if currently using initdb mode:**

Edit the cluster manifest:

```bash
nano infrastructure/configs/production/cloudnative-pg/postgres-cluster.yaml
```

Replace the `bootstrap` section:

```yaml
# OLD - REMOVE THIS:
# bootstrap:
#   initdb:
#     postInitSQL:
#       - 'CREATE EXTENSION IF NOT EXISTS documentdb CASCADE;'

# NEW - ADD THIS:
bootstrap:
  recovery:
    source: clusterBackup

# ADD THIS at same level as 'bootstrap'. The destination, endpoint and
# credentials live on the ObjectStore this names, not here.
externalClusters:
  - name: clusterBackup
    plugin:
      name: barman-cloud.cloudnative-pg.io
      parameters:
        barmanObjectName: postgres-store
        serverName: postgres-cluster
```

**Important**: Any `postInitSQL` commands need to be moved to a PostSync job, as they won't run in recovery mode.

### Step 2: Commit configuration change

```bash
git add infrastructure/configs/production/cloudnative-pg/postgres-cluster.yaml
git commit -m "fix: configure PostgreSQL cluster to restore from backup on recreation"
git push
```

### Step 3: Allow Flux to recreate cluster

```bash
# Resume Flux reconciliation
flux resume kustomization infrastructure-configs --context=production

# Or force immediate reconciliation
flux reconcile kustomization infrastructure-configs --context=production
```

### Step 4: Monitor cluster recovery

```bash
# Watch cluster creation
kubectl get cluster -n database -w

# Watch pods come up
kubectl get pods -n database -w

# Check cluster status
kubectl describe cluster -n database postgres-cluster
```

**Expected behavior:**
1. Cluster resource created
2. CNPG operator detects recovery bootstrap mode
3. Operator connects to B2 backup storage
4. Most recent backup downloaded
5. WAL logs replayed
6. Cluster becomes ready with data restored

### Step 5: Verify data restoration

```bash
# Connect to primary pod - find the current primary first, since CNPG
# reassigns pod ordinals on failover/replica recreation
PRIMARY_POD=$(kubectl get pods -n database -l cnpg.io/cluster=postgres-cluster,role=primary -o jsonpath='{.items[0].metadata.name}')
kubectl exec -it -n database "$PRIMARY_POD" -- psql -U postgres

# Inside psql:
\l              # List databases - should see application databases
\c linkding     # Connect to app database
\dt             # List tables - should see application tables
SELECT COUNT(*) FROM bookmarks;  # Check data exists

# Exit
\q
```

## Verification

### Confirm successful recovery:

- [ ] Cluster shows `Cluster in healthy state` status
      ```bash
      kubectl get cluster -n database
      ```

- [ ] All expected databases exist
      ```bash
      PRIMARY_POD=$(kubectl get pods -n database -l cnpg.io/cluster=postgres-cluster,role=primary -o jsonpath='{.items[0].metadata.name}')
      kubectl exec -n database "$PRIMARY_POD" -- psql -U postgres -c '\l'
      ```

- [ ] Application data is present
      ```bash
      # Check your applications can connect and see their data
      kubectl logs -n <app-namespace> <app-pod> | grep -i database
      ```

- [ ] Backup configuration still active
      ```bash
      kubectl get scheduledbackup -n database
      ```

- [ ] New backups are being created
      ```bash
      # Wait ~24 hours, then check for new backup
      kubectl get backup -n database --sort-by=.metadata.creationTimestamp
      ```

- [ ] Recovery mode is configured for future deletions
      ```bash
      grep -A 5 "bootstrap:" infrastructure/configs/production/cloudnative-pg/postgres-cluster.yaml
      # Should show "recovery:" not "initdb:"
      ```

## Calculate Recovery Point Objective (RPO)

Determine how much data (if any) was lost:

```bash
# Check backup timestamp
kubectl describe cluster -n database postgres-cluster | grep "Last Successful Backup"

# Check current time
date

# RPO = time between last backup and deletion
```

**Expected RPO**: < 5 minutes (continuous WAL archiving)

**Worst case RPO**: Time since last base backup (typically 24 hours)

## Root Cause

### Why initdb Mode is Dangerous After Initial Setup

The bootstrap mode is **only used during cluster creation**. Once set:

- **initdb mode**: "Create fresh cluster, ignore any backups"
- **recovery mode**: "Restore from backup if available, otherwise create fresh"

**Timeline of the mistake:**

1. ✅ Initial deployment with `initdb` - correct for first install
2. ✅ Backups configured and running - good
3. ❌ Bootstrap mode never changed to `recovery` - dangerous
4. ❌ Cluster accidentally deleted
5. ❌ Flux recreates cluster with `initdb` mode
6. ❌ All data lost despite backups existing

**The fix**: Change to `recovery` mode after first successful backup.

### Why recovery Mode is Safe

In recovery mode:
- **If backups exist**: Restore from most recent backup
- **If no backups exist**: Gracefully fall back to creating new cluster
- **Always safe**: No data loss scenario

## Prevention

### For New PostgreSQL Clusters

**Initial deployment** (first time only):

```yaml
bootstrap:
  initdb:
    postInitSQL:
      - 'CREATE EXTENSION IF NOT EXISTS documentdb CASCADE;'
```

**After first successful backup** (within 24 hours):

1. Verify backup exists:
   ```bash
   kubectl get backup -n database
   ```

2. Switch to recovery mode:
   ```yaml
   bootstrap:
     recovery:
       source: clusterBackup
   externalClusters:
     - name: clusterBackup
       plugin:
         name: barman-cloud.cloudnative-pg.io
         parameters:
           barmanObjectName: <the cluster's ObjectStore>
           serverName: <cluster name>
   ```

3. Move initialization SQL to PostSync jobs:
   ```yaml
   # Create a Job resource that runs after cluster is ready
   apiVersion: batch/v1
   kind: Job
   metadata:
     name: init-extensions
     annotations:
       argocd.argoproj.io/hook: PostSync
   spec:
     template:
       spec:
         containers:
         - name: init
           image: postgres:16
           command:
           - psql
           - -c
           - "CREATE EXTENSION IF NOT EXISTS documentdb CASCADE;"
   ```

### Testing Disaster Recovery

**Test on staging first:**

```bash
# 1. Ensure staging has backups
kubectl get backup -n database --context=staging

# 2. Note current data state (find the current primary first, since CNPG
#    reassigns pod ordinals on failover/replica recreation)
PRIMARY_POD=$(kubectl get pods -n database --context=staging -l cnpg.io/cluster=postgres-cluster,role=primary -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n database "$PRIMARY_POD" --context=staging -- \
  psql -U postgres -c "SELECT COUNT(*) FROM linkding.bookmarks;"

# 3. Delete the cluster
kubectl delete cluster -n database postgres-cluster --context=staging

# 4. Wait for recreation
kubectl get cluster -n database --context=staging -w

# 5. Verify data restored (re-check the primary - it may be a different pod
#    name after recovery)
PRIMARY_POD=$(kubectl get pods -n database --context=staging -l cnpg.io/cluster=postgres-cluster,role=primary -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n database "$PRIMARY_POD" --context=staging -- \
  psql -U postgres -c "SELECT COUNT(*) FROM linkding.bookmarks;"

# Compare counts - should match!
```

**Test on production** (during maintenance window):

Only after successful staging test! Document the test as part of DR procedures.

### Monitoring & Alerting

Set up alerts for backup failures:

```yaml
# Prometheus alert example
- alert: PostgreSQLBackupFailing
  expr: cnpg_pg_wal_archive_status{status="FAILED"} > 0
  for: 5m
  labels:
    severity: critical
  annotations:
    summary: "PostgreSQL backup failing"
    description: "Backups to B2 have failed - disaster recovery at risk"
```

### Documentation

- [ ] Document RPO/RTO in disaster recovery plan
- [ ] Create backup restoration checklist
- [ ] Document backup verification procedures
- [ ] Schedule quarterly DR tests

## Related Issues

- **Backup storage connectivity issues**: If B2 is unreachable, recovery will fail
- **Incorrect backup credentials**: Check `b2-credentials` secret
- **Wrong backup path**: Verify `destinationPath` matches actual backups
- **WAL archiving failures**: Check for continuous backup pipeline issues

## Original War Story

For the full narrative of how this issue was discovered and fixed, see: [`docs/war-stories/postgres-bootstrap-recovery.md`](../war-stories/postgres-bootstrap-recovery.md)

## References

- [CloudNativePG Bootstrap Documentation](https://cloudnative-pg.io/documentation/current/bootstrap/)
- [CloudNativePG Backup and Recovery](https://cloudnative-pg.io/documentation/current/backup_recovery/)
- [Point-in-Time Recovery (PITR)](https://cloudnative-pg.io/documentation/current/recovery/)

---

**Last Updated**: 2026-07-27
**Tested On**: Production PostgreSQL cluster
**Success Rate**: 100% (tested in staging)
**Commit**: `c762b4b` - Initial fix
