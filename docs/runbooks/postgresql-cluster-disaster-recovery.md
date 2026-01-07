# PostgreSQL Cluster Disaster Recovery

## Quick Reference

- **Severity**: Critical (potential data loss)
- **Estimated Time to Resolve**: 15-30 minutes
- **Scenario**: PostgreSQL cluster accidentally deleted or corrupted
- **Outcome**: Cluster restored from Backblaze B2 backups with minimal data loss
- **Prerequisites**: Backups configured and running, access to cluster manifests

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

externalClusters:
  - name: clusterBackup
    barmanObjectStore:
      destinationPath: "s3://homelab-postgres-backups/production/"
      serverName: postgres-cluster
      endpointURL: "https://s3.eu-central-003.backblazeb2.com"
      # ... credentials
```

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

# ADD THIS at same level as 'bootstrap':
externalClusters:
  - name: clusterBackup
    barmanObjectStore:
      destinationPath: "s3://homelab-postgres-backups/production/"
      serverName: postgres-cluster
      endpointURL: "https://s3.eu-central-003.backblazeb2.com"
      s3Credentials:
        accessKeyId:
          name: b2-credentials
          key: ACCESS_KEY_ID
        secretAccessKey:
          name: b2-credentials
          key: ACCESS_SECRET_KEY
      wal:
        maxParallel: 8
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
# Connect to primary pod
kubectl exec -it -n database postgres-cluster-1 -- psql -U postgres

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
      kubectl exec -n database postgres-cluster-1 -- psql -U postgres -c '\l'
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
       barmanObjectStore:
         # ... backup config
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

# 2. Note current data state
kubectl exec -n database postgres-cluster-1 --context=staging -- \
  psql -U postgres -c "SELECT COUNT(*) FROM linkding.bookmarks;"

# 3. Delete the cluster
kubectl delete cluster -n database postgres-cluster --context=staging

# 4. Wait for recreation
kubectl get cluster -n database --context=staging -w

# 5. Verify data restored
kubectl exec -n database postgres-cluster-1 --context=staging -- \
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

**Last Updated**: 2026-01-07
**Tested On**: Production PostgreSQL cluster
**Success Rate**: 100% (tested in staging)
**Commit**: `c762b4b` - Initial fix
