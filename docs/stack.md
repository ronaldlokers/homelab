# Technology Stack

This document provides detailed information about all components running in the homelab clusters.

## GitOps & Automation

### Flux CD

[Flux](https://fluxcd.io/) is the GitOps continuous delivery tool that manages the entire cluster state.

**Version**: v2.7.5

**Components**:
- **source-controller**: Fetches artifacts from Git repositories and Helm repositories
- **kustomize-controller**: Applies Kustomize configurations to the cluster
- **helm-controller**: Manages Helm release lifecycles
- **notification-controller**: Handles events and notifications

**How it works**:
1. Flux monitors this Git repository for changes
2. When changes are detected, controllers reconcile the cluster state
3. Resources are applied in dependency order
4. Helm releases are installed/upgraded automatically
5. SOPS-encrypted secrets are decrypted using the age key

**Configuration**:
- **Sync interval**: Every 10 minutes (configurable per Kustomization)
- **Retry behavior**: Automatic retries with exponential backoff
- **Prune**: Removes resources deleted from Git
- **Health checks**: Waits for resources to become healthy before proceeding

**Reconciliation**:
```bash
# Manually trigger reconciliation
flux reconcile kustomization flux-system --context=production

# Check status
flux get kustomizations --context=production
flux get helmreleases --context=production
```

### Renovate

[Renovate](https://docs.renovatebot.com/) provides automated dependency updates through pull requests.

**Deployment**: CronJob running hourly

**Updates**:
- Helm chart versions in HelmRelease resources
- Container image tags in Kubernetes manifests
- GitHub Actions workflow dependencies
- Docker base images

**Configuration**:
- Runs in `renovate` namespace
- Uses GitHub personal access token for API access
- Configured via `.github/renovate.json` in the repository
- Creates PRs for dependency updates

**How it works**:
1. CronJob triggers every hour
2. Renovate scans the repository for dependencies
3. Checks for newer versions
4. Creates pull requests with updates
5. PRs include changelogs and release notes

**Workflow**:
1. Renovate creates PR
2. Review changes (optionally test in staging)
3. Merge PR
4. Flux automatically applies changes to the cluster

## Infrastructure

### cert-manager

[cert-manager](https://cert-manager.io/) provides automated TLS certificate management.

**Version**: v1.16.3

**Components**:
- **cert-manager-controller**: Main controller for certificate lifecycle
- **cert-manager-webhook**: Validating webhook for cert-manager resources
- **cert-manager-cainjector**: Injects CA bundles into webhooks and API services

**Certificate Issuers**:
- **letsencrypt-production**: Production Let's Encrypt issuer (rate-limited)
- **letsencrypt-staging**: Staging issuer for testing

**DNS-01 Challenge**:
Uses Cloudflare DNS API for DNS-01 challenges:
1. cert-manager receives certificate request
2. Creates TXT record in Cloudflare: `_acme-challenge.domain.com`
3. Let's Encrypt verifies the TXT record
4. Certificate issued and stored in Secret
5. TXT record cleaned up

**Benefits of DNS-01**:
- Works for services not publicly accessible
- Can issue wildcard certificates
- No port 80/443 requirements

**Configuration**:
- Cloudflare API token stored in SOPS-encrypted secret
- Automatic certificate renewal at 30 days before expiry
- Certificates valid for 90 days

### Traefik

[Traefik](https://traefik.io/) is the ingress controller and reverse proxy.

**Deployment**: Installed by K3s by default

**Features**:
- Automatic service discovery
- SNI-based routing
- TLS termination
- HTTP to HTTPS redirect middleware
- Integration with cert-manager for automatic TLS

**How it works**:
1. Ingress resource created with host and path rules
2. Traefik detects the ingress
3. Configures routing rules
4. cert-manager provisions TLS certificate
5. Traefik serves traffic with TLS termination

**Load Balancing**:
- **Staging**: k3d built-in load balancer forwards to Traefik
- **Production**: K3s ServiceLB exposes Traefik on all node IPs

**Access**:
- Traefik runs in `kube-system` namespace
- LoadBalancer service on ports 80 and 443
- Dashboard not exposed (security)

### MetalLB (Production Only)

[MetalLB](https://metallb.universe.tf/) provides network load balancing for bare-metal Kubernetes clusters.

**Version**: 0.14.9

**Architecture**:
- **metallb-controller**: Manages IP address assignment for LoadBalancer services
- **metallb-speaker**: Announces IPs on the local network (runs as DaemonSet)

**Configuration**:
- **IPAddressPool**: Defines the pool of IP addresses MetalLB can assign (10.0.40.100/32)
- **L2Advertisement**: Configures Layer 2 mode for IP announcement

**Load Balancer IP**: 10.0.40.100

**How it works**:
1. LoadBalancer service created (e.g., Traefik)
2. MetalLB controller assigns an IP from the pool
3. Speaker pods announce the IP via ARP on the local network
4. Traffic to the VIP is routed to the appropriate service
5. Automatic failover if a node goes down

**Benefits**:
- Single stable IP address for ingress instead of round-robin DNS
- Automatic failover between nodes
- Standard Kubernetes LoadBalancer interface
- No external load balancer hardware required

**Layer 2 Mode**:
- Uses ARP to announce IP addresses on the local network
- Simple configuration, no BGP required
- IP moves between nodes automatically on failure
- Suitable for homelab environments

### Longhorn (Production Only)

[Longhorn](https://longhorn.io/) provides distributed block storage with replication.

**Version**: 1.7.2

**Architecture**:
- **longhorn-manager**: Main management component on each node
- **longhorn-driver**: CSI driver for Kubernetes integration
- **longhorn-ui**: Web UI for management
- **instance-manager**: Manages volume replicas

**Storage**:
- **Default replica count**: 3
- **Data path**: `/var/lib/longhorn` on each node
- **Replication**: Synchronous replication across 3 nodes
- **Auto-balance**: least-effort strategy

**Features**:
- Dynamic volume provisioning
- Volume snapshots
- Volume backups (S3-compatible storage)
- Volume cloning
- Disaster recovery
- Replica rebuilding
- Storage over-provisioning

**Requirements**:
- `open-iscsi` package on all nodes
- `iscsid` service running
- Block storage (not NFS)

**High Availability**:
- Survives 2 node failures (with 3 replicas)
- Automatic replica rebuilding when node recovers
- Replicas spread across nodes for redundancy

**Web UI**: https://longhorn.ronaldlokers.nl

**Monitoring**:
- Prometheus ServiceMonitor enabled
- Metrics exposed for Grafana dashboards
- Custom Longhorn dashboard in Grafana

**Resource Limits** (Raspberry Pi CM5):
- **longhorn-manager**: 50m CPU / 128Mi memory (request), 500m CPU / 512Mi memory (limit)
- **longhorn-driver**: 50m CPU / 64Mi memory (request), 200m CPU / 256Mi memory (limit)
- **longhorn-ui**: 10m CPU / 64Mi memory (request), 100m CPU / 128Mi memory (limit)

### CloudNative-PG

[CloudNative-PG](https://cloudnative-pg.io/) is a Kubernetes operator for PostgreSQL databases.

**Version**: 0.23.0

**Components**:
- **cnpg-controller-manager**: Main operator managing PostgreSQL clusters
- **Cluster CRD**: Custom resource defining PostgreSQL cluster configuration

**PostgreSQL Cluster Configuration**:
- **Name**: postgres-cluster
- **Namespace**: database
- **Instances**: 3 (high availability)
- **Storage Size**: 10Gi per instance
- **Storage Class**:
  - Staging: local-path
  - Production: longhorn (replicated)

**Features**:
- Automated PostgreSQL cluster provisioning
- Built-in high availability with automatic failover
- Continuous backup and point-in-time recovery
- Rolling updates with zero downtime
- Connection pooling with PgBouncer
- Monitoring integration with Prometheus

**High Availability**:
- Primary-replica architecture
- Automatic failover on primary failure
- Read replicas for scaling reads
- Synchronous or asynchronous replication

**How it works**:
1. Cluster resource created with desired configuration
2. Operator provisions PostgreSQL pods (1 primary + 2 replicas)
3. Primary handles writes, replicas handle reads
4. Automatic health monitoring and failover
5. Continuous backup to object storage (if configured)

**Access**:
- **Service**: postgres-cluster-rw (read-write, points to primary)
- **Service**: postgres-cluster-ro (read-only, load-balanced across replicas)
- **Service**: postgres-cluster-r (read-only, includes primary)

**Connection**:
```bash
# Connect to primary (read-write)
kubectl exec -it -n database postgres-cluster-1 -- psql -U postgres

# Get credentials
kubectl get secret -n database postgres-cluster-app -o jsonpath='{.data.password}' | base64 -d
```

**Monitoring**:
- Metrics exposed for Prometheus
- PodMonitor for automatic scraping
- Integration with Grafana dashboards

## Applications

### Homepage

[Homepage](https://gethomepage.dev/) is a modern, fully static, fast application dashboard with integrations.

**Features**:
- Service dashboard with icons and links
- Kubernetes cluster monitoring widgets
- Resource usage displays (CPU, memory)
- Service widgets with live data
- Automatic service discovery via Ingress annotations
- Customizable layouts and themes
- Over 100 service integrations

**Deployment**:
- Single replica
- RBAC-enabled ServiceAccount for cluster access
- ConfigMap-based configuration
- No persistent storage required (stateless)

**Access**:
- **Staging**: https://homepage.staging.ronaldlokers.nl
- **Production**: https://homepage.ronaldlokers.nl

**Configuration**:
- Kubernetes cluster mode enabled for monitoring
- Pre-configured service widgets for Linkding and Grafana
- Dark theme with clean header style
- Custom bookmarks and search integration

**Kubernetes Integration**:
- ClusterRole for reading namespaces, pods, nodes, and ingresses
- Automatic service discovery from Ingress annotations
- Real-time metrics from Kubernetes metrics API
- Resource usage tracking per service

### Linkding

[Linkding](https://github.com/sissbruecker/linkding) is a bookmark manager with tagging and search.

**Features**:
- Bookmark saving with title and description
- Tag-based organization
- Full-text search
- Browser extensions
- REST API
- Archive snapshots

**Deployment**:
- Single replica
- PostgreSQL database via CloudNative-PG cluster
- Connects to `postgres-cluster-rw.database.svc.cluster.local`
- No persistent volumes required (data stored in PostgreSQL cluster)

**Database**:
- Uses `linkding` database in the PostgreSQL cluster
- Database credentials stored in SOPS-encrypted secret
- High availability through PostgreSQL replication (3 instances)

**Access**:
- **Staging**: https://linkding.staging.ronaldlokers.nl
- **Production**: https://linkding.ronaldlokers.nl

**Authentication**:
- Superuser credentials stored in SOPS-encrypted secret
- Multi-user support

**Configuration**:
- PostgreSQL connection via environment variables
- Environment variables via Secret
- Ingress with TLS certificate from cert-manager
- HTTPS redirect middleware

## Monitoring

### kube-prometheus-stack

[kube-prometheus-stack](https://github.com/prometheus-operator/kube-prometheus) provides complete monitoring and observability.

**Version**: 79.9.0

**Components**:

#### Prometheus
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

#### Grafana
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

#### Alertmanager
Alert routing and management.

**Features**:
- Alert grouping and deduplication
- Notification routing
- Silencing

**Configuration**:
- Can integrate with Slack, email, PagerDuty, etc.
- Currently not configured for external notifications

#### Node Exporter
Node-level metrics.

**Metrics**:
- CPU usage
- Memory usage
- Disk I/O
- Network traffic
- Filesystem usage

Runs as a DaemonSet on all nodes.

#### kube-state-metrics
Kubernetes object metrics.

**Metrics**:
- Deployment status
- Pod status
- Node status
- Resource requests and limits
- ConfigMap and Secret metrics

### Custom Dashboards

#### Longhorn Dashboard (Production)

Location: `monitoring/dashboards/production/longhorn-dashboard.yaml`

**Metrics**:
- Volume health and status
- Replica distribution
- Storage capacity and usage
- I/O performance
- Node storage metrics

Automatically provisioned to Grafana via ConfigMap.

## Summary

| Component | Staging | Production | Purpose |
|-----------|---------|------------|---------|
| Flux CD | ✅ | ✅ | GitOps continuous delivery |
| Renovate | ✅ | ✅ | Automated dependency updates |
| cert-manager | ✅ | ✅ | TLS certificate management |
| Traefik | ✅ | ✅ | Ingress controller |
| MetalLB | ❌ | ✅ | Network load balancer |
| Longhorn | ❌ | ✅ | Distributed storage |
| CloudNative-PG | ✅ | ✅ | PostgreSQL operator |
| Homepage | ✅ | ✅ | Application dashboard |
| Linkding | ✅ | ✅ | Bookmark manager |
| kube-prometheus-stack | ✅ | ✅ | Monitoring and observability |

## Resource Usage

### Staging (k3d)

Running in Docker on VM:
- Low resource usage
- Shared host resources
- Suitable for testing

### Production (Raspberry Pi CM5)

Per node:
- **CPU**: ARM64 processor
- **RAM**: 16GB
- **Storage**: 64GB eMMC

**Cluster Total**:
- **CPU**: 3 nodes
- **RAM**: 48GB
- **Storage**: 192GB (before Longhorn replication)

**Effective Storage** (with Longhorn 3-replica):
- ~64GB usable storage (3x replication)

Resource limits configured for low-power ARM processors:
- Conservative CPU/memory requests
- Allows multiple services per node
- Prevents resource exhaustion
