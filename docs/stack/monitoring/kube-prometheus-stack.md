# kube-prometheus-stack

[kube-prometheus-stack](https://github.com/prometheus-operator/kube-prometheus) provides complete monitoring and observability.

**Version**: 79.9.0

**Components**:

## Prometheus
Metrics collection and storage.

**Features**:
- ServiceMonitor CRDs for automatic service discovery
- Metric retention and storage
- Alerting rules
- Query language (PromQL)

**ServiceMonitors**:
- Kubernetes system metrics (kubelet, api-server, etc.)
- Longhorn metrics (production)
- Application metrics (if exposed)

**Storage**:
- Persistent volume for metric storage
- Configurable retention period

## Grafana
Dashboards and visualization.

**Features**:
- Pre-configured dashboards for Kubernetes
- Custom Longhorn dashboard (production)
- Data source auto-configuration
- Dashboard provisioning

**Access**:
- **Staging**: https://grafana.staging.ronaldlokers.nl
- **Production**: https://grafana.ronaldlokers.nl

**Authentication**:
- Admin credentials auto-generated
- Stored in `kube-prometheus-stack-grafana` secret

**Dashboards**:
- Kubernetes cluster metrics
- Node metrics
- Pod metrics
- Persistent volume metrics
- Longhorn dashboard (production only)

## Alertmanager
Alert routing and management.

**Features**:
- Alert grouping and deduplication
- Notification routing
- Silencing

**Configuration**:
- Can integrate with Slack, email, PagerDuty, etc.
- Currently not configured for external notifications

## Node Exporter
Node-level metrics.

**Metrics**:
- CPU usage
- Memory usage
- Disk I/O
- Network traffic
- Filesystem usage

Runs as a DaemonSet on all nodes.

## kube-state-metrics
Kubernetes object metrics.

**Metrics**:
- Deployment status
- Pod status
- Node status
- Resource requests and limits
- ConfigMap and Secret metrics

## Custom Dashboards

### Longhorn Dashboard (Production)

Location: `monitoring/dashboards/production/longhorn-dashboard.yaml`

**Metrics**:
- Volume health and status
- Replica distribution
- Storage capacity and usage
- I/O performance
- Node storage metrics

Automatically provisioned to Grafana via ConfigMap.
