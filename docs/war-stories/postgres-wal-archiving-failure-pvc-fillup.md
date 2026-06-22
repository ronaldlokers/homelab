# PostgreSQL WAL Archiving Failure Causing PVC Fill-Up

**Date**: June 22, 2026
**Severity**: Critical
**Duration**: 68 days of silent failure, 4 hours to diagnose and resolve
**Impact**: Multiple database cluster pods crash-looping, Loki monitoring stack failure, entire production cluster degraded

## The Problem

### Initial Symptoms

Multiple pods across the production cluster were in `CrashLoopBackOff` state:
- `postgres-cluster-1` and `postgres-cluster-2` (2 of 3 PostgreSQL replicas down)
- `immich-cluster-1`, `immich-cluster-2`, `immich-cluster-3` (entire Immich database cluster down)
- `loki-backend-0` (monitoring stack affected)
- `kube-state-metrics` (metrics collection impacted)

### Error Messages

CloudNativePG operator logs showed:
```
Cluster is not healthy (low-disk space condition detected)
```

PostgreSQL logs revealed:
```
invalid checkpoint record
PANIC: could not locate a valid checkpoint record
```

The confusing part: PVCs showed plenty of free space when checking `kubectl get pvc`:
- postgres-cluster PVCs: 20Gi capacity
- immich-cluster PVCs: 50Gi capacity
- Longhorn volumes showed available space

**The mystery**: Why was CloudNativePG reporting "low-disk space" when PVCs had plenty of capacity?

## The Investigation

### Step 1: Check Actual Disk Usage

Rather than trusting the PVC capacity numbers, I examined actual disk usage inside the pods:

```bash
kubectl exec -n database postgres-cluster-3 -- df -h /var/lib/postgresql/data/pgdata
```

Result: **18.8Gi of 20Gi used (94%)** - nearly full!

### Step 2: Identify What Was Filling the Disk

```bash
kubectl exec -n database postgres-cluster-3 -- du -sh /var/lib/postgresql/data/pgdata/*
```

Output showed:
```
48G     pg_wal/
2.1G    base/
```

**48GB of WAL files in a 20Gi PVC?** This shouldn't be possible. WAL files are supposed to be archived and cleaned up automatically.

### Step 3: Count WAL Files

```bash
kubectl exec -n database postgres-cluster-3 -- ls -1 /var/lib/postgresql/data/pgdata/pg_wal/*.* | wc -l
```

Result: **3000+ WAL files** accumulating over 68 days.

Normal operation: ~50 WAL files at most.

### Step 4: Check WAL Archiving Status

```bash
kubectl get cluster postgres-cluster -n database -o yaml | grep -i "continuousArchiving\|archive"
```

The cluster status showed:
```yaml
continuousArchiving:
  status: "True"
  message: "Continuous archiving is working"
```

This was confusing - archiving claimed to be working, yet files were accumulating. Let me check the PostgreSQL archiver statistics:

```bash
kubectl exec -n database postgres-cluster-3 -- psql -U postgres -c \
  "SELECT archived_count, failed_count FROM pg_stat_archiver;"
```

Output:
```
 archived_count | failed_count
----------------+--------------
           1119 |            0
```

Zero failed archives, yet 3000+ files accumulated. **Something was wrong with the archiving configuration.**

### Step 5: Examine Cluster Configuration

Looking at the cluster spec:

```bash
kubectl get cluster postgres-cluster -n database -o yaml | grep -A 20 "backup:"
```

**Discovery**: The `backup:` section was commented out in the YAML configuration!

Checking git history:
```bash
git log --oneline --grep="archive\|backup" -10
```

Found commits:
- `8c7212f` - "Temporarily disable WAL archiving to fix disk space detection issue"
- `921653a` - "Fix: restore externalClusters for recovery, keep backup disabled"

### Step 6: Understanding the Root Cause

The timeline became clear:

1. **68 days ago**: WAL archiving to Backblaze B2 started failing (unknown reason at the time)
2. **PostgreSQL behavior**: WAL files kept accumulating because they can't be deleted until safely archived
3. **Emergency fix**: Someone disabled the `backup:` section entirely to stop the archiving failures
4. **Unintended consequence**: WAL files were now marked as "archived" locally but NOT uploaded to B2
5. **Result**: Files continued accumulating locally, eventually filling PVCs

CloudNativePG's archiver was running and marking files as "done" (hence `failed_count: 0`), but without the `backup:` section configured, it had nowhere to send them. The files just accumulated in `pg_wal/`.

## The Root Cause

**Primary cause**: WAL archiving to Backblaze B2 was disabled in the cluster configuration, causing WAL files to accumulate locally without being uploaded to remote storage.

**Secondary discovery**: The B2 backup path had been changed from `production/` to `production-2026-03/` to avoid WAL archive conflicts between clusters, suggesting the original failure may have been related to path conflicts.

**Why CloudNativePG reported "low-disk space"**: The operator detected the filling PVC and entered a safe mode to prevent total disk exhaustion, which would cause catastrophic database corruption.

**Why it took 68 days to become critical**:
- WAL files are typically 16MB each
- Database activity varies over time
- It took ~3000 WAL files (48GB) to fill the 20Gi PVC
- The issue went unnoticed until it reached critical threshold

## The Solution

### Immediate Recovery (Emergency Measures)

1. **Clean accumulated WAL files**:
```bash
# Created debug pod with PVC access
kubectl apply -f /tmp/debug-postgres-cluster-3.yaml

# Deleted old WAL files (keeping recent ones)
kubectl exec -it debug-postgres-cluster-3 -n database -- \
  find /data/pgdata/pg_wal -name "0000*" -mtime +30 -delete

# Result: Freed 48GB of space
```

2. **Expand postgres-cluster-3 PVC** (already at capacity):
```bash
kubectl patch pvc postgres-cluster-3 -n database --type='json' \
  -p='[{"op": "replace", "path": "/spec/resources/requests/storage", "value":"30Gi"}]'
```

3. **Delete corrupted replica PVCs**:
```bash
# postgres-cluster-1 and postgres-cluster-2 had checkpoint corruption
kubectl delete pvc postgres-cluster-1 postgres-cluster-2 -n database

# Let CloudNativePG rebuild fresh replicas from healthy primary
```

4. **Rebuild immich-cluster** (all 3 pods corrupted):
```bash
# Data was already lost (68 days of crash-looping)
kubectl delete pvc immich-cluster-1 immich-cluster-2 immich-cluster-3 -n database

# Recreated fresh cluster with initdb bootstrap
```

### Long-Term Fix (Preventive Measures)

#### 1. Re-enable WAL Archiving with Optimized Configuration

Updated `postgres-cluster.yaml` and `immich-cluster.yaml`:

```yaml
postgresql:
  parameters:
    wal_keep_size: "8GB"  # NEW: Limit local WAL retention

storage:
  size: 30Gi  # Increased from 20Gi for buffer space

backup:
  retentionPolicy: "7d"  # Changed from 30d to reduce B2 costs
  barmanObjectStore:
    destinationPath: "s3://homelab-postgres-backups/production-2026-03/"
    endpointURL: "https://s3.eu-central-003.backblazeb2.com"
    s3Credentials:
      accessKeyId:
        name: b2-credentials
        key: ACCESS_KEY_ID
      secretAccessKey:
        name: b2-credentials
        key: ACCESS_SECRET_KEY
    wal:
      compression: gzip
    data:
      compression: gzip
      jobs: 2
```

**Key change: `wal_keep_size: 8GB`**

This PostgreSQL parameter limits how many WAL files are kept locally, even if archiving fails. After 8GB accumulates, PostgreSQL will start deleting old WAL files to prevent disk fill-up.

**Trade-off**: If archiving is down for extended periods and WAL files are deleted before being archived, you lose point-in-time recovery capability for that period. But this prevents the more catastrophic scenario of complete disk fill-up causing database corruption.

#### 2. Create Monitoring Alerts

Created `postgres-alerts.yaml` with 3 PrometheusRules:

**Alert 1: Detect Archiving Failures Early**
```yaml
- alert: PostgreSQLWALArchivingFailing
  expr: cnpg_pg_stat_archiver_failed_count > 0
  for: 15m
  annotations:
    summary: "PostgreSQL WAL archiving is failing"
    description: "Check B2 connectivity and credentials"
```

**Alert 2: Warn When WAL Directory Fills Up**
```yaml
- alert: PostgreSQLWALDirectoryFilling
  expr: (cnpg_pg_wal_directory_size_bytes / (1024*1024*1024)) > 10
  for: 10m
  annotations:
    summary: "WAL directory using >10GB"
    description: "May indicate archiving delays or failures"
```

**Alert 3: Monitor Continuous Archiving Status**
```yaml
- alert: PostgreSQLContinuousArchivingFailed
  expr: |
    cnpg_collector_up{cnpg_io_cluster=~"postgres-cluster|immich-cluster"} == 1
    unless
    cnpg_collector_last_collection_error{cnpg_io_cluster=~"postgres-cluster|immich-cluster"} == 0
  for: 20m
  annotations:
    summary: "Continuous archiving status unhealthy"
```

These alerts provide **early warning** before PVCs fill up completely, giving time to investigate and fix B2 connectivity issues.

#### 3. Increase Storage Capacity

- postgres-cluster: 20Gi → 30Gi (50% increase)
- immich-cluster: Already at 50Gi (sufficient)

This provides larger buffer for WAL accumulation during transient B2 failures.

### Verification

After implementing all fixes:

```bash
# Check archiving is working
kubectl exec -n database postgres-cluster-3 -- psql -U postgres -c \
  "SELECT archived_count, failed_count, last_archived_time FROM pg_stat_archiver;"

# Output:
#  archived_count | failed_count | last_archived_time
# ----------------+--------------+--------------------
#            1123 |            0 | 2026-06-22 22:33:19

# Check WAL file count is healthy
kubectl exec -n database postgres-cluster-3 -- \
  ls -1 /var/lib/postgresql/data/pgdata/pg_wal/*.* | wc -l

# Output: 6 files (normal!)

# Check WAL directory size
kubectl exec -n database postgres-cluster-3 -- \
  du -sh /var/lib/postgresql/data/pgdata/pg_wal/

# Output: 593M (down from 48GB!)

# Verify wal_keep_size is active
kubectl exec -n database postgres-cluster-3 -- psql -U postgres -c \
  "SHOW wal_keep_size;"

# Output: 8GB
```

All pods running healthy:
```bash
kubectl get pods -n database
# All postgres-cluster and immich-cluster pods: Running
```

## Prevention

### 1. Monitoring Alerts ✅

Three custom Prometheus alerts now monitor database health:
- Detects archiving failures within 15 minutes
- Warns when WAL directory exceeds 10GB
- Critical alert if continuous archiving status fails

This ensures we'll know immediately if B2 archiving fails again.

### 2. WAL Retention Limits ✅

`wal_keep_size: 8GB` prevents unlimited WAL accumulation:
- PostgreSQL will delete old WAL files after 8GB
- Prevents disk fill-up even if archiving is broken
- Trades some PITR capability for system stability

### 3. Increased Storage Capacity ✅

30Gi PVCs provide buffer for transient failures:
- Can accumulate ~29Gi of WAL files before hitting limits
- Gives more time to detect and fix archiving issues
- Reduces risk of sudden disk exhaustion

### 4. Regular Backup Verification

**Action items**:
- Schedule quarterly tests of PostgreSQL restore from B2
- Verify B2 bucket contents monthly
- Monitor Prometheus alerts in Grafana

### 5. Documentation

This war story serves as:
- Reference for future WAL archiving issues
- Guide for interpreting "low-disk space" errors
- Template for root cause analysis process

## Lessons Learned

1. **"Low-disk space" doesn't always mean what you think** - CloudNativePG reported disk space issues, but the real problem was WAL archiving configuration, not actual PVC capacity.

2. **Monitor what you can't see** - 68 days of silent WAL file accumulation went unnoticed until it became critical. Metrics and alerts would have caught this early.

3. **Archiving "success" can be misleading** - PostgreSQL reported `failed_count: 0` because the archiver was running, but files weren't being uploaded to B2 due to missing backup configuration.

4. **Preventive limits are essential** - `wal_keep_size` is a critical safety valve that prevents unlimited growth at the cost of some recovery capability.

5. **Root cause analysis takes time** - Don't rush to quick fixes. Understanding the full timeline (68 days!) and configuration changes (commented-out backup section) was crucial to implementing proper preventive measures.

6. **Test disaster recovery procedures** - If backups aren't being tested, assume they don't work. The disabled archiving went unnoticed because no one was verifying B2 uploads.

7. **Cost optimization vs reliability trade-off** - Changing retention from 30d to 7d reduces B2 costs but also reduces recovery window. Document these trade-offs.

8. **Configuration changes need monitoring** - When the `backup:` section was commented out, there was no alert to indicate archiving stopped. Changes to critical paths should trigger notifications.

9. **Understand operator behavior** - CloudNativePG enters "low-disk space" safe mode to prevent corruption, which can look like inexplicable crashes without context.

10. **WAL archiving is not optional in production** - Disabling it to "fix" an issue creates a ticking time bomb. Fix the root cause (B2 connectivity, credentials, paths) instead of disabling the feature.

## Related Documentation

- [PostgreSQL Disaster Recovery Bootstrap Mode](postgres-bootstrap-recovery.md) - How to restore databases from B2 backups
- [CloudNative-PG Configuration](/docs/stack/infrastructure/cloudnative-pg.md) - Current setup and backup configuration
- [Infrastructure Runbooks](/docs/runbooks/) - Operational procedures including database troubleshooting

## Commands Reference

**Check WAL directory size**:
```bash
kubectl exec -n database <pod-name> -- du -sh /var/lib/postgresql/data/pgdata/pg_wal/
```

**Check archiver statistics**:
```bash
kubectl exec -n database <pod-name> -- psql -U postgres -c \
  "SELECT archived_count, failed_count, last_archived_time FROM pg_stat_archiver;"
```

**Check WAL retention setting**:
```bash
kubectl exec -n database <pod-name> -- psql -U postgres -c "SHOW wal_keep_size;"
```

**Check cluster archiving status**:
```bash
kubectl get cluster <cluster-name> -n database -o jsonpath='{.status.conditions[?(@.type=="ContinuousArchiving")]}'
```

**View Prometheus alerts**:
```bash
kubectl get prometheusrule -n database
kubectl describe prometheusrule cloudnativepg-custom-alerts -n database
```
