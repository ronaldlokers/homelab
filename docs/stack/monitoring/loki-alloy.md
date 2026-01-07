# Loki + Alloy Logging Stack

Complete log aggregation and querying for Kubernetes.

**Components**:
- **Loki**: Log storage and indexing (Simple Scalable Deployment mode)
- **Grafana Alloy**: Log collection agent (replaces Promtail)
- **Grafana**: Log querying and visualization

## Loki

[Grafana Loki](https://grafana.com/docs/loki/latest/) is a horizontally-scalable log aggregation system.

**Version**: Chart 6.49.0 (Loki 3.6.3)

**Deployment Mode**: Simple Scalable Deployment (SSD)

### Architecture

**Components**:
- **Write**: Ingests log streams (3 replicas)
- **Read**: Queries logs (3 replicas)
- **Backend**: Compaction and retention (3 replicas)
- **Gateway**: HTTP gateway (NGINX)

**Storage**:
- **Staging**: Embedded MinIO (S3-compatible) running on local-path PVCs
- **Production**: External MinIO on NAS (http://10.0.40.10:9000), bucket `loki-production`

**Schema**: TSDB (v13) for efficient log indexing

### Configuration

#### Staging

**Location**: `monitoring/controllers/staging/loki/release.yaml`

**Key Settings**:
```yaml
loki:
  auth_enabled: false  # Single-tenant mode for staging
  commonConfig:
    replication_factor: 1  # Staging uses single replica writes
  schemaConfig:
    configs:
      - from: "2024-04-01"
        store: tsdb
        object_store: s3
        schema: v13
  limits_config:
    retention_period: 7d  # Logs kept for 7 days
    allow_structured_metadata: true
    volume_enabled: true
  compactor:
    retention_enabled: true  # Auto-delete old logs
    delete_request_store: s3
```

**Replica Counts**:
- Backend: 3
- Read: 3
- Write: 3

**Why 3 replicas?**
- Loki's hash ring requires multiple instances for proper operation
- SSD mode works best with 3+ replicas per component

#### Production

**Location**: `monitoring/controllers/production/loki/release.yaml`

**Key Differences from Staging**:
```yaml
loki:
  commonConfig:
    replication_factor: 2  # HA with 2 replicas
  limits_config:
    retention_period: 30d  # Longer retention for production
  storage:
    type: s3
    bucketNames:
      chunks: loki-production
      ruler: loki-production
      admin: loki-production
    s3:
      endpoint: http://10.0.40.10:9000  # External NAS MinIO
      region: us-east-1
      s3ForcePathStyle: true
      insecure: true  # Using HTTP not HTTPS

# Disable embedded MinIO - using external NAS S3
minio:
  enabled: false

# S3 credentials from SOPS-encrypted secret
backend:
  extraEnvFrom:
    - secretRef:
        name: loki-s3-secret
read:
  extraEnvFrom:
    - secretRef:
        name: loki-s3-secret
write:
  extraEnvFrom:
    - secretRef:
        name: loki-s3-secret
```

**Production-Specific Configuration**:
- **Replication Factor**: 2 (HA mode, can survive 1 node failure)
- **Retention**: 30 days (vs 7 days in staging)
- **Storage**: External MinIO on NAS at 10.0.40.10:9000
- **Bucket**: `loki-production` (dedicated bucket)
- **Credentials**: SOPS-encrypted secret with AWS access keys
- **SOPS Decryption**: Enabled in Flux Kustomization via `sops-age` secret

### Storage Retention

**Retention Period**:
- **Staging**: 7 days
- **Production**: 30 days

**Compaction**:
- Runs automatically on backend components
- Deletes logs older than retention period
- Prevents storage bloat

### Access

**Service URLs**:
- Gateway: `http://loki-gateway.monitoring.svc.cluster.local`
- Write endpoint: `http://loki-write.monitoring.svc.cluster.local:3100`
- Read endpoint: `http://loki-read.monitoring.svc.cluster.local:3100`

**Grafana Integration**:
- Pre-configured as Loki data source in Grafana
- **Staging**: https://grafana.staging.ronaldlokers.nl
- **Production**: https://grafana.ronaldlokers.nl

### Log Labels

Logs are automatically labeled with:
- `cluster`: Environment (staging/production)
- `namespace`: Kubernetes namespace
- `pod`: Pod name
- `container`: Container name
- `app`: Application name (from `app.kubernetes.io/name`)
- `job`: Namespace/container combination
- `stream`: stdout/stderr

## Grafana Alloy

[Grafana Alloy](https://grafana.com/docs/alloy/latest/) is the next-generation observability agent (replaces Promtail).

**Version**: Chart 1.5.0 (Alloy v1.12.0)

**Deployment**: DaemonSet (runs on every node)

### Configuration

**Locations**:
- **Staging**: `monitoring/controllers/staging/alloy/config.alloy`
- **Production**: `monitoring/controllers/production/alloy/config.alloy`

**Pipeline**:
1. **Discovery**: Find all pods on the node
2. **Relabeling**: Extract Kubernetes metadata as labels
3. **Collection**: Tail pod logs via Kubernetes API
4. **Processing**: Add cluster label (staging/production)
5. **Write**: Forward to Loki write endpoint

**Key Components**:
```alloy
// Discover pods on this node
discovery.kubernetes "pod" {
  role = "pod"
  selectors {
    role = "pod"
    field = "spec.nodeName=" + env("HOSTNAME")
  }
}

// Extract labels from Kubernetes metadata
discovery.relabel "pod_logs" {
  targets = discovery.kubernetes.pod.targets
  // Rules extract: namespace, pod, container, app, job
}

// Tail logs from pods
loki.source.kubernetes "pod_logs" {
  targets = discovery.relabel.pod_logs.output
  forward_to = [loki.process.pod_logs.receiver]
}

// Add cluster label (staging or production depending on environment)
loki.process "pod_logs" {
  stage.static_labels {
    values = {
      cluster = "staging"  # "production" in production config
    }
  }
  forward_to = [loki.write.default.receiver]
}

// Write to Loki
loki.write "default" {
  endpoint {
    url = "http://loki-write.monitoring.svc.cluster.local:3100/loki/api/v1/push"
  }
}
```

**Additional Features**:
- **Node logs**: Collects syslog from `/var/log/syslog`
- **Kubernetes events**: Streams cluster events to Loki
- **Config reload**: Automatically reloads on ConfigMap changes

### Labels Applied

Alloy automatically adds:
- `cluster`: "staging" (or "production")
- `namespace`: From pod metadata
- `pod`: Pod name
- `container`: Container name
- `app`: From `app.kubernetes.io/name` label
- `job`: Namespace/container combination
- `container_runtime`: Detected from pod (containerd/docker/cri-o)

## Loki Canary

Loki includes a **canary** component that continuously writes test logs to verify the logging pipeline is working.

**What it does**:
- Writes test data (repeated "p" characters) directly to Loki
- Validates end-to-end logging functionality
- Runs as a DaemonSet on each node

**Identifying canary logs**:
```logql
{app="loki-canary"}
```

**Note**: Canary logs don't have the `cluster` label because they bypass Alloy and write directly to Loki.

**Filtering out canary logs**:
```logql
{cluster="production"} != "pppppp"
# or
{cluster="production", app!="loki-canary"}
```

**Should you disable it?**
- **Recommended**: Keep it enabled - it's a lightweight health check
- It validates the entire logging pipeline is functional
- Minimal storage/performance impact

## Querying Logs in Grafana

**Access**:
- **Staging**: https://grafana.staging.ronaldlokers.nl → Explore → Select "Loki" data source
- **Production**: https://grafana.ronaldlokers.nl → Explore → Select "Loki" data source

**Example Queries**:

```logql
# All logs from staging cluster
{cluster="staging"}

# All logs from production cluster
{cluster="production"}

# Logs from monitoring namespace
{namespace="monitoring"}

# Logs from specific pod
{pod="linkding-848dd55574-djr4m"}

# Logs from all Loki components
{app="loki"}

# Error logs from any container (excluding canary)
{cluster="production", app!="loki-canary"} |= "error"

# Logs from nightscout excluding health checks
{namespace="nightscout"} != "health"

# Rate of errors in last 5 minutes
rate({namespace="monitoring"} |= "error" [5m])
```

**LogQL Resources**:
- [LogQL Documentation](https://grafana.com/docs/loki/latest/query/)
- [LogQL Examples](https://grafana.com/docs/loki/latest/query/examples/)

## Troubleshooting

### Check Alloy is Collecting Logs

```bash
# View Alloy logs
kubectl logs -n monitoring -l app.kubernetes.io/name=alloy --tail=100

# Check for send errors
kubectl logs -n monitoring -l app.kubernetes.io/name=alloy | grep -i error
```

### Check Loki is Receiving Logs

```bash
# Check write component logs
kubectl logs -n monitoring -l app.kubernetes.io/component=write --tail=50

# Query Loki API for labels
kubectl exec -n monitoring deploy/kube-prometheus-stack-grafana -c grafana -- \
  wget -qO- http://loki-gateway.monitoring.svc.cluster.local/loki/api/v1/label
```

### Ring Issues

If you see "too many unhealthy instances in the ring":

**Check replica health:**
```bash
kubectl get pods -n monitoring -l app.kubernetes.io/name=loki
```

**Common cause**: Replication factor mismatch with available replicas

**Solution**: Set `loki.commonConfig.replication_factor` to 1 for staging

### Storage Issues

**Check MinIO status:**
```bash
kubectl logs -n monitoring loki-minio-0
```

**Check disk space:**
```bash
kubectl exec -n monitoring loki-minio-0 -- df -h
```

### No Logs Appearing

**Checklist**:
1. Alloy pods running? `kubectl get pods -n monitoring -l app.kubernetes.io/name=alloy`
2. Loki write pods ready? `kubectl get pods -n monitoring -l app.kubernetes.io/component=write`
3. Correct endpoint in Alloy config? Should be `:3100/loki/api/v1/push`
4. Network connectivity? Test from Alloy pod to loki-write service
5. Check Grafana data source config in kube-prometheus-stack values

### Production-Specific Issues

**SOPS Decryption Failures** (S3 credentials not working):

Check if SOPS decryption is enabled:
```bash
kubectl get kustomization monitoring-controllers -n flux-system -o yaml | grep -A 3 decryption
```

Should show:
```yaml
decryption:
  provider: sops
  secretRef:
    name: sops-age
```

If missing, update `clusters/production/monitoring.yaml` to enable decryption.

**Verify secret is decrypted**:
```bash
# Should show plain text credentials, not ENC[...]
kubectl get secret loki-s3-secret -n monitoring -o jsonpath='{.data.AWS_ACCESS_KEY_ID}' | base64 -d
```

**inotify Limits Exhausted** ("too many open files"):

Increase limits on all production nodes:
```bash
# SSH to each node (kube-srv-1, kube-srv-2, kube-srv-3)
sudo nano /etc/sysctl.conf

# Add:
fs.inotify.max_user_watches=524288
fs.inotify.max_user_instances=512

# Apply:
sudo sysctl -p
```

Then restart affected pods:
```bash
kubectl delete pods -n monitoring -l app.kubernetes.io/name=loki
```

**S3 Connection Issues**:

Check S3 credentials are injected:
```bash
kubectl get statefulset loki-backend -n monitoring -o yaml | grep -A 5 extraEnvFrom
```

Test S3 connectivity from a pod:
```bash
kubectl run -it --rm debug --image=amazon/aws-cli --restart=Never -- \
  s3 ls --endpoint-url http://10.0.40.10:9000 s3://loki-production
```

## Migration Notes

**From Promtail to Alloy**:
- Alloy is the successor to Promtail (Promtail is in maintenance mode)
- Configuration is different (Alloy Config vs YAML)
- More features: service discovery, pipelines, remote write
- Better performance and resource usage

## Production Implementation

**Current Production Setup**:

1. ✅ **External Storage**: NAS MinIO at 10.0.40.10:9000, bucket `loki-production`
2. ✅ **Replication Factor**: 2 for high availability (can survive 1 node failure)
3. ✅ **Replica Counts**: 3 replicas per component (backend, read, write)
4. ✅ **Retention**: 30 days (vs 7 days in staging)
5. ✅ **Credentials**: SOPS-encrypted secret with AWS access keys
6. ✅ **SOPS Decryption**: Enabled in Flux Kustomization
7. ✅ **Cluster Label**: `cluster="production"` for log filtering
8. ⚠️ **Multi-tenancy**: Disabled (`auth_enabled: false`) - single-tenant mode
9. ⚠️ **Monitoring**: No ServiceMonitors yet - consider adding
10. ⚠️ **Alerting**: No alerts configured - consider adding for log ingestion failures

**Future Enhancements**:
- Add ServiceMonitors for Loki component metrics
- Configure alerts for:
  - Log ingestion failures
  - S3 storage issues
  - High error rates in logs
  - Disk space usage on NAS
- Consider increasing replication factor to 3 for better HA
- Enable multi-tenancy if multiple teams/applications need isolation

## References

- [Loki Documentation](https://grafana.com/docs/loki/latest/)
- [Loki Deployment Modes](https://grafana.com/docs/loki/latest/get-started/deployment-modes/)
- [Grafana Alloy Documentation](https://grafana.com/docs/alloy/latest/)
- [LogQL Query Language](https://grafana.com/docs/loki/latest/query/)
