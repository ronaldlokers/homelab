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
- Loki data source for log querying (see [Loki + Alloy](./loki-alloy.md))

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

Alert routing, grouping, deduplication and silencing. Configured in
`monitoring/controllers/production/kube-prometheus-stack/alertmanager-config.yaml`
as a single `AlertmanagerConfig`.

Everything lands in one of three receivers:

| Receiver | What goes there |
|---|---|
| `campfire` | Every real alert, via `campfire-alert-bridge` into the `#Alertmanager` room |
| `blackhole` | `InfoInhibitor` — machinery, not news |
| `deadman` | `Watchdog`, out to healthchecks.io |

`alertmanagerConfigMatcherStrategy: type: None` is what makes delivery outside
the `monitoring` namespace work at all, and it leaves the top route without
matchers — so it catches everything and the exceptions have to be **child
routes**, which are evaluated first and do not continue.

### The deadman

`Watchdog` fires forever by design. On its own that is worth nothing: a
heartbeat only means something when something notices it **stopping**.

It is routed off-cluster, to a healthchecks.io check, and no longer into the
room. Off-cluster is the whole point — Gatus, a PrometheusRule and a check in
the status bot were all considered, and each has to deliver its warning through
the path it is testing, so the single failure it exists to catch is the one it
cannot report. Gatus in particular runs *in* the production cluster.

- `repeatInterval: 15m` on that route overrides the parent's `12h`. It is the
  detection window: half a day of a dead cluster before anything says so is not
  a deadman worth having.
- `sendResolved: false`. A resolved `Watchdog` means Alertmanager stopped seeing
  the alert that always fires — the exact failure being watched. Pinging on it
  would reset the timer and hide it.
- The ping URL **is** the credential; anyone holding it can ping the check and
  mask an outage. It lives in the `alertmanager-deadman` SOPS secret, referenced
  via `urlSecret`.

Alertmanager reaches the internet through `allow-internet-egress`, which already
permits 443 to public addresses from every pod in the namespace.

**A missing `alertmanager-deadman` secret is not harmless.** prometheus-operator
will not generate a config it cannot resolve, so the whole `AlertmanagerConfig`
is skipped and alerts stop reaching Campfire. Create the secret before the route
that references it.

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
