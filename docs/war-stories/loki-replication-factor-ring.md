# Loki Ring Errors: "Too Many Unhealthy Instances"

**Date**: December 2025
**Environment**: Staging cluster - Loki deployment
**Impact**: Loki refusing to ingest logs, write component failing

## The Problem

After deploying Loki in Simple Scalable Deployment (SSD) mode with 3 replicas, logs were not appearing in Grafana and Alloy was unable to send logs to Loki.

**Error in loki-write logs**:
```
level=error msg="failed to write to ingester" err="too many unhealthy instances in the ring"
```

**Symptoms**:
- Loki pods all running and healthy
- Grafana showing "Query error: too many unhealthy instances in the ring"
- No logs appearing despite Alloy sending them
- All 3 replicas of write/read/backend components running

## The Investigation

### Checking Pod Status

```bash
kubectl get pods -n monitoring -l app.kubernetes.io/name=loki
# NAME                           READY   STATUS    RESTARTS
# loki-backend-0                 1/1     Running   0
# loki-backend-1                 1/1     Running   0
# loki-backend-2                 1/1     Running   0
# loki-read-55f9c7d9f4-2vkxh     1/1     Running   0
# loki-read-55f9c7d9f4-7m8nl     1/1     Running   0
# loki-read-55f9c7d9f4-xp2k9     1/1     Running   0
# loki-write-0                   1/1     Running   0
# loki-write-1                   1/1     Running   0
# loki-write-2                   1/1     Running   0
```

All pods healthy. The problem is elsewhere.

### Checking Write Component Logs

```bash
kubectl logs -n monitoring loki-write-0 | grep -i ring
# level=warn msg="instance not found in ring" ring=ingester
# level=error msg="failed to write to ingester" err="too many unhealthy instances in the ring"
```

The ring is the key. Loki uses a hash ring to distribute data across instances.

### Understanding the Ring Configuration

```bash
kubectl get cm -n monitoring loki -o yaml | grep -A5 commonConfig
# commonConfig:
#   replication_factor: 3  # ❌ This is the problem!
```

Checked Loki documentation:
> The replication factor determines how many instances each log entry is replicated to. With replication_factor: 3, Loki requires at least 3 healthy instances in the ring to accept writes.

**But this is staging!** High availability isn't needed here.

### Testing with Lower Replication Factor

The issue: Loki was trying to replicate data 3 times, but the ring health check was failing even though all instances were running.

## The Root Cause

**Loki's replication factor must match the environment's HA requirements.**

For staging/development environments:
- `replication_factor: 1` is appropriate
- Data written to single instance
- No replication overhead
- Ring health checks pass with single healthy instance

For production environments:
- `replication_factor: 2-3` provides redundancy
- Survives instance failures
- Requires multiple healthy instances for writes
- More complex ring management

The default chart values use `replication_factor: 3` assuming production usage. In a staging environment with potentially unstable networking or resource constraints, this causes unnecessary complexity.

## The Solution

### Update Loki Configuration

Modified `monitoring/controllers/staging/loki/release.yaml`:

```yaml
loki:
  auth_enabled: false
  commonConfig:
    replication_factor: 1  # ✓ Changed from 3 to 1 for staging
  schemaConfig:
    configs:
      - from: "2024-04-01"
        store: tsdb
        object_store: s3
        schema: v13
```

### Apply and Verify

```bash
# Reconcile Helm release
flux reconcile helmrelease loki -n monitoring

# Wait for pods to restart
kubectl get pods -n monitoring -l app.kubernetes.io/name=loki --watch

# Check write logs
kubectl logs -n monitoring loki-write-0 | grep -i ring
# level=info msg="instance added to ring" ring=ingester instance=loki-write-0
# ✓ No more errors!

# Test log query in Grafana
# Navigate to Explore → Loki data source
# Query: {cluster="staging"}
# ✓ Logs appearing!
```

## How Loki's Hash Ring Works

### With replication_factor: 3

```
Write request arrives
    ↓
Ring distributes to 3 instances
    ↓
Instance 1: Write ✓
Instance 2: Write ✓
Instance 3: Write ✓
    ↓
Success (3/3 healthy required)
```

**If ring health check fails**: All writes rejected

### With replication_factor: 1

```
Write request arrives
    ↓
Ring assigns to 1 instance
    ↓
Instance 1: Write ✓
    ↓
Success (1/1 healthy required)
```

**Simpler, faster, sufficient for staging**

## Environment-Specific Configuration

### Staging (Single Replica)

```yaml
loki:
  commonConfig:
    replication_factor: 1  # No replication needed

backend:
  replicas: 3   # Still run 3 for hash ring distribution
read:
  replicas: 3   # Query load distribution
write:
  replicas: 3   # Ingest load distribution
```

**Why 3 replicas with replication_factor: 1?**
- Load distribution (not HA)
- Better performance with multiple components
- Each write only stored on 1 instance
- Ring still functions for request distribution

### Production (HA Setup)

```yaml
loki:
  commonConfig:
    replication_factor: 2-3  # Replicate for HA

backend:
  replicas: 3+
read:
  replicas: 3+
write:
  replicas: 3+
```

**Benefits**:
- Survives instance failures
- No data loss during restarts
- Better read performance (multiple copies)

## Lessons Learned

1. **Replication factor ≠ replica count**: These are different concepts
   - Replica count: How many instances of a component run
   - Replication factor: How many copies of each log entry to store

2. **Staging doesn't need production HA**: Use simpler configurations for development
   - Easier debugging
   - Fewer resources required
   - Faster iteration

3. **Ring errors are configuration issues**: Not infrastructure problems
   - All pods can be healthy
   - Ring checks fail due to misconfiguration
   - Read the replication factor documentation

4. **Default chart values assume production**: Always review defaults for your environment

## Prevention Checklist

When deploying Loki:

- [ ] Understand environment requirements (staging vs production)
- [ ] Set `replication_factor` appropriately (1 for staging, 2-3 for production)
- [ ] Review Loki deployment mode documentation
- [ ] Test log ingestion immediately after deployment
- [ ] Check write component logs for ring errors
- [ ] Document why specific values were chosen

## Related Configuration

### Auth Settings for Single-Tenant Staging

```yaml
loki:
  auth_enabled: false  # No multi-tenancy in staging
  commonConfig:
    replication_factor: 1
```

This combination works well for staging:
- No tenant isolation overhead
- Single-replica writes
- Simple troubleshooting

### Production Multi-Tenant Setup

```yaml
loki:
  auth_enabled: true  # Tenant isolation
  commonConfig:
    replication_factor: 3  # High availability
```

## Timeline

- **Loki deployed**: Initial deployment with default replication_factor: 3
- **Issue discovered**: No logs in Grafana, ring errors in write logs
- **Investigation**: Checked pods (healthy), reviewed logs (ring errors)
- **Research**: Read Loki documentation about replication factor
- **Solution**: Changed replication_factor to 1 for staging
- **Verification**: Logs flowing correctly, no ring errors
- **Time to fix**: ~15 minutes (after understanding the issue)

## References

- [Loki Simple Scalable Deployment](https://grafana.com/docs/loki/latest/get-started/deployment-modes/#simple-scalable)
- [Loki Configuration Reference](https://grafana.com/docs/loki/latest/configure/)
- [Loki Hash Ring](https://grafana.com/docs/loki/latest/operations/consistent-hash-ring/)
