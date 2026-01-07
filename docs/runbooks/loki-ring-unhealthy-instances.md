# Loki Ring Errors: Too Many Unhealthy Instances

## Quick Reference

- **Severity**: High (no log ingestion)
- **Estimated Time to Resolve**: 15 minutes
- **Symptoms**: Loki refusing to ingest logs, "too many unhealthy instances in the ring"
- **Affected Components**: Loki write components, log collection
- **Environment**: Typically staging/development clusters
- **Prerequisites**: Access to Loki configuration, Helm values

## Symptoms & Detection

### Error Messages

```
level=error msg="failed to write to ingester" err="too many unhealthy instances in the ring"
```

### Observable Behavior

- All Loki pods showing `Running` status
- No logs appearing in Grafana despite Alloy sending them
- Grafana queries fail with "too many unhealthy instances in the ring"
- Write component logs show ring errors
- All replicas (read/write/backend) appear healthy

### Monitoring Indicators

- Grafana Loki datasource unreachable or erroring
- No recent logs in LogQL queries
- Alloy/Promtail unable to push logs successfully

## Immediate Actions

**If you need logs RIGHT NOW:**

There's no workaround - you must fix the replication factor. However, this is a config-only fix (no data loss).

**Quick check**:

```bash
# Check Loki write logs for the error
kubectl logs -n monitoring -l app.kubernetes.io/component=write --tail=50 | grep -i ring

# If you see "too many unhealthy instances", this is your issue
```

## Diagnosis Steps

### 1. Verify all Loki pods are healthy

```bash
# Check pod status
kubectl get pods -n monitoring -l app.kubernetes.io/name=loki

# All should be Running
# NAME                     READY   STATUS    RESTARTS
# loki-backend-0           1/1     Running   0
# loki-backend-1           1/1     Running   0
# loki-backend-2           1/1     Running   0
# loki-read-xxxxx          1/1     Running   0
# loki-write-0             1/1     Running   0
# loki-write-1             1/1     Running   0
```

**If pods aren't healthy**, this is a different issue.

### 2. Check Loki replication factor

```bash
# Get Loki ConfigMap
kubectl get cm -n monitoring loki -o yaml | grep -A3 "commonConfig:"

# Or check HelmRelease values
kubectl get helmrelease loki -n monitoring -o yaml | grep -A5 "commonConfig:"
```

**Look for**:
```yaml
commonConfig:
  replication_factor: 3  # ← High for staging
```

### 3. Determine if this is staging or production

```bash
# Check context
kubectl config current-context
# staging or production?

# Or check cluster resources
kubectl get nodes
# staging typically has fewer/smaller nodes
```

### 4. Confirm diagnosis

**This is the right runbook if:**
- ✅ All Loki pods are Running
- ✅ Error mentions "too many unhealthy instances in the ring"
- ✅ `replication_factor` is set to 2 or 3
- ✅ This is a staging/development environment
- ✅ Logs are not appearing despite healthy pods

**This is NOT the right runbook if:**
- ❌ Loki pods are crashing or not running
- ❌ Different error message
- ❌ This is production (may need replication for HA)
- ❌ Network connectivity issues to Loki

## Resolution Steps

### Step 1: Determine appropriate replication factor

**For staging/development**:
```yaml
loki:
  commonConfig:
    replication_factor: 1  # No replication needed
```

**For production**:
```yaml
loki:
  commonConfig:
    replication_factor: 2  # Or 3 for high availability
```

**Rule of thumb**:
- `replication_factor: 1` - Single copy, no HA, simple debugging
- `replication_factor: 2` - Two copies, survives 1 failure
- `replication_factor: 3` - Three copies, survives 2 failures

### Step 2: Update Loki Helm values

Edit your Loki configuration:

```bash
# For staging
nano monitoring/controllers/staging/loki/release.yaml

# Or for production (only if appropriate)
nano monitoring/controllers/production/loki/release.yaml
```

Update the values:

```yaml
spec:
  values:
    loki:
      auth_enabled: false
      commonConfig:
        replication_factor: 1  # ✓ Change from 3 to 1 for staging
      schemaConfig:
        configs:
          - from: "2024-04-01"
            store: tsdb
            object_store: s3
            schema: v13
      # ... rest of config
```

### Step 3: Commit and reconcile

```bash
# Commit changes
git add monitoring/controllers/staging/loki/release.yaml
git commit -m "fix: set Loki replication_factor to 1 for staging"
git push

# Force Flux reconciliation
flux reconcile kustomization monitoring-controllers --context=staging

# Or reconcile specific HelmRelease
flux reconcile helmrelease loki -n monitoring --context=staging
```

### Step 4: Wait for pods to restart

```bash
# Watch Loki pods restart with new config
kubectl get pods -n monitoring -l app.kubernetes.io/name=loki --watch

# All pods should restart and return to Running
```

### Step 5: Verify logs flowing

```bash
# Check write component logs
kubectl logs -n monitoring -l app.kubernetes.io/component=write --tail=20

# Should see:
# level=info msg="instance added to ring" ring=ingester
# No more "too many unhealthy instances" errors

# Test in Grafana
# Navigate to Explore → Select Loki data source
# Query: {namespace="monitoring"}
# Should see recent logs
```

## Verification

### Confirm resolution:

- [ ] No ring errors in write logs
      ```bash
      kubectl logs -n monitoring -l app.kubernetes.io/component=write --tail=50 | grep -i "unhealthy"
      # Should return no results
      ```

- [ ] Logs appearing in Grafana
      ```bash
      # Run a test query in Grafana Explore
      # {cluster="staging"} | last 5m
      # Should show recent logs
      ```

- [ ] Alloy successfully pushing logs
      ```bash
      kubectl logs -n monitoring -l app.kubernetes.io/name=alloy --tail=20
      # Check for successful push messages, no errors
      ```

- [ ] Loki accepting writes
      ```bash
      # Check Loki metrics
      kubectl port-forward -n monitoring svc/loki-write 3100:3100
      curl http://localhost:3100/metrics | grep loki_distributor_ingester_appends_total
      # Should show increasing counter
      ```

- [ ] Configuration persists across restarts
      ```bash
      kubectl delete pod -n monitoring loki-write-0
      # Wait for restart
      kubectl logs -n monitoring loki-write-0 | grep replication_factor
      # Should show: replication_factor=1
      ```

## Root Cause

### Understanding Loki's Hash Ring

**Replication factor** determines how many copies of each log entry are stored:

```
With replication_factor: 3
Write request arrives
    ↓
Distributed to 3 instances
    ↓
Instance 1: Write ✓
Instance 2: Write ✓
Instance 3: Write ✓
    ↓
Success (requires 3/3 healthy)
```

**If ring health check fails**: All writes are rejected.

### Replication Factor ≠ Replica Count

**Common confusion**:

- **Replica count**: How many instances of a component run
  ```yaml
  write:
    replicas: 3  # 3 write pods
  ```

- **Replication factor**: How many copies of each log entry
  ```yaml
  loki:
    commonConfig:
      replication_factor: 1  # Each log stored once
  ```

**You can have**:
- 3 write replicas (for load distribution)
- `replication_factor: 1` (each log stored once)
- This gives: Load distribution without replication overhead

### Why Staging Doesn't Need Replication

**Staging environment goals**:
- Quick feedback loops
- Easy debugging
- Lower resource usage
- HA not critical (can tolerate downtime)

**With `replication_factor: 1`**:
- Logs written once (faster)
- Simpler ring management
- Ring health checks more lenient
- Fewer moving parts = easier debugging

**Production needs**:
- High availability
- Survive instance failures
- Accept write/read overhead for reliability

## Prevention

### Configure Appropriately Per Environment

- [ ] Staging: `replication_factor: 1`
- [ ] Production: `replication_factor: 2` or `3`
- [ ] Document why in comments:
      ```yaml
      loki:
        commonConfig:
          replication_factor: 1  # Staging: no HA needed, simpler debugging
      ```

### Test Log Ingestion After Deployment

```bash
# Immediately after deploying Loki
echo "test log from $(hostname)" | kubectl exec -i -n monitoring deploy/alloy -- logger

# Check in Grafana within 1 minute
# Query: {job="systemd-journal"} |= "test log"

# If no logs appear, check write component logs immediately
kubectl logs -n monitoring -l app.kubernetes.io/component=write
```

### Review Helm Chart Defaults

When deploying from Helm charts:

- [ ] Check default `replication_factor` in chart values
- [ ] Override for non-production environments
- [ ] Don't assume defaults are appropriate for your use case

### Monitoring

Create alerts for log ingestion failures:

```yaml
# Prometheus alert
- alert: LokiNotIngesting
  expr: |
    rate(loki_distributor_ingester_appends_total[5m]) == 0
  for: 5m
  annotations:
    summary: "Loki not receiving logs - check for ring errors"
```

## Related Issues

- **Auth settings**: Combine `replication_factor: 1` with `auth_enabled: false` for staging
- **Storage config**: Ensure S3 storage configured correctly
- **Ring state**: Ring errors can persist after config fix (restart pods)

## Original War Story

For the complete investigation including understanding the difference between replica count and replication factor, see: [`docs/war-stories/loki-replication-factor-ring.md`](../war-stories/loki-replication-factor-ring.md)

## References

- [Loki Simple Scalable Deployment](https://grafana.com/docs/loki/latest/get-started/deployment-modes/#simple-scalable)
- [Loki Configuration Reference](https://grafana.com/docs/loki/latest/configure/)
- [Loki Hash Ring](https://grafana.com/docs/loki/latest/operations/consistent-hash-ring/)
- [Replication Factor Explained](https://grafana.com/docs/loki/latest/operations/scalability/#replication-factor)

---

**Last Updated**: 2026-01-07
**Tested On**: Staging k3d cluster
**Success Rate**: 100%
**Typical Cause**: Using production defaults in staging
