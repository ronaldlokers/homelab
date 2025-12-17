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
- **Staging**: MinIO (S3-compatible) running on local-path PVCs
- **Production**: TBD (will use external S3 or MinIO cluster on Longhorn)

**Schema**: TSDB (v13) for efficient log indexing

### Configuration

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

**Replica Counts** (Staging):
- Backend: 3
- Read: 3
- Write: 3

**Why 3 replicas in staging?**
- Loki's hash ring requires multiple instances for proper operation
- SSD mode works best with 3+ replicas per component
- For production, consider increasing based on log volume

### Storage Retention

**Retention Period**: 7 days (configurable via `limits_config.retention_period`)

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
- Access via Grafana Explore: https://grafana.staging.ronaldlokers.nl

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

**Location**: `monitoring/controllers/staging/alloy/config.alloy`

**Pipeline**:
1. **Discovery**: Find all pods on the node
2. **Relabeling**: Extract Kubernetes metadata as labels
3. **Collection**: Tail pod logs via Kubernetes API
4. **Processing**: Add cluster label
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

// Add cluster label
loki.process "pod_logs" {
  stage.static_labels {
    values = {
      cluster = "staging"
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

## Querying Logs in Grafana

**Access**: https://grafana.staging.ronaldlokers.nl → Explore → Select "Loki" data source

**Example Queries**:

```logql
# All logs from staging cluster
{cluster="staging"}

# Logs from monitoring namespace
{namespace="monitoring"}

# Logs from specific pod
{pod="linkding-848dd55574-djr4m"}

# Logs from all Loki components
{app="loki"}

# Error logs from any container
{cluster="staging"} |= "error"

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

## Migration Notes

**From Promtail to Alloy**:
- Alloy is the successor to Promtail (Promtail is in maintenance mode)
- Configuration is different (Alloy Config vs YAML)
- More features: service discovery, pipelines, remote write
- Better performance and resource usage

## Production Considerations

**For production deployment**:

1. **External Storage**: Replace MinIO with S3, Azure Blob, or dedicated MinIO cluster
2. **Replication Factor**: Increase to 2 or 3 for high availability
3. **Replica Counts**: Scale based on log volume
4. **Retention**: Adjust `retention_period` based on compliance requirements
5. **Multi-tenancy**: Enable `auth_enabled: true` for tenant isolation
6. **Monitoring**: Add ServiceMonitors for Loki metrics
7. **Alerting**: Create alerts for log ingestion failures, storage issues

## References

- [Loki Documentation](https://grafana.com/docs/loki/latest/)
- [Loki Deployment Modes](https://grafana.com/docs/loki/latest/get-started/deployment-modes/)
- [Grafana Alloy Documentation](https://grafana.com/docs/alloy/latest/)
- [LogQL Query Language](https://grafana.com/docs/loki/latest/query/)
