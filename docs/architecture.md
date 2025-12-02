# Architecture

This document describes the architecture and infrastructure of the homelab Kubernetes clusters.

## Repository Structure

The repository follows a structured layout separating concerns by layers:

```
.
├── clusters/
│   ├── staging/              # Staging cluster Flux configuration
│   └── production/           # Production cluster Flux configuration
├── infrastructure/
│   ├── controllers/          # Infrastructure Helm releases
│   │   ├── base/
│   │   ├── staging/
│   │   └── production/
│   └── configs/              # Infrastructure configuration
│       ├── staging/
│       └── production/
├── apps/                     # Application deployments
│   ├── base/
│   ├── staging/
│   └── production/
├── monitoring/               # Observability stack
│   ├── controllers/
│   │   ├── base/
│   │   ├── staging/
│   │   └── production/
│   └── dashboards/
│       └── production/       # Grafana dashboards
└── docs/                     # Documentation
```

### Kustomize Overlay Pattern

All resources follow the same pattern:
- **Base configuration** in `base/` directories - shared configuration
- **Environment-specific overlays** in `staging/` and `production/` directories
- Kustomization files reference environment-specific resources

This allows:
- Sharing common configuration across environments
- Environment-specific customization (replicas, resources, secrets, etc.)
- Easy promotion from staging to production

### Cluster Entry Points

Each cluster reconciles from its respective directory:
- **Staging cluster**: `clusters/staging/`
- **Production cluster**: `clusters/production/`

These directories contain:
- `flux-system/` - Flux installation and configuration
- Kustomizations that reference infrastructure, apps, and monitoring

## Deployment Flow

Flux monitors this repository and automatically applies changes in the following order:

1. **Infrastructure Controllers** (`infrastructure/controllers/`)
   - Core services deployed first (cert-manager, longhorn, renovate)
   - Creates namespaces and installs Helm releases

2. **Infrastructure Configs** (`infrastructure/configs/`)
   - Configuration applied after controllers are ready
   - Certificate issuers, Traefik middlewares, etc.
   - Depends on controllers being healthy

3. **Applications** (`apps/`)
   - Applications deployed after infrastructure is ready
   - Uses infrastructure services (ingress, certificates, storage)

4. **Monitoring** (`monitoring/`)
   - Observability stack deployed independently
   - kube-prometheus-stack with Grafana
   - Dashboards loaded after stack is ready

Dependencies are enforced through Kustomization `dependsOn` fields to ensure correct ordering.

## Storage Architecture

### Staging Environment

- **Storage Class**: `local-path` (default K3s storage)
- **Type**: Node-local storage
- **Provisioner**: Rancher local-path-provisioner
- **Location**: `/var/lib/rancher/k3s/storage` on each node
- **Replication**: None (single-node persistence)
- **Use Case**: Development and testing

**Characteristics**:
- Fast (local disk)
- No replication or HA
- Data persists only on the node where created
- Sufficient for staging workloads

### Production Environment

- **Storage Class**: `longhorn` (default)
- **Type**: Distributed block storage
- **Replication**: 3 replicas across all three nodes
- **Management UI**: https://longhorn.ronaldlokers.nl

**Features**:
- Automatic data replication for high availability
- Volume snapshots and backups
- Dynamic provisioning
- Survives two-node failure (3 replicas)
- iSCSI-based block storage
- Web UI for management

**How it works**:
1. When a PVC is created, Longhorn creates a volume
2. Data is replicated to 3 nodes automatically
3. If a node fails, replicas on other nodes remain available
4. Applications can continue running during node failures
5. Replicas automatically rebalance using "least-effort" strategy

**Configuration**:
- Default replica count: 3
- Replica anti-affinity: Disabled (allows replicas on same node if needed)
- Auto-balance: least-effort (balances when convenient)
- Data path: `/var/lib/longhorn` on each node

## Networking

### DNS Configuration

All services are exposed via DNS records pointing to cluster node IPs.

#### Staging (k3d cluster - 10.0.40.52)

DNS records point to the Proxmox VM IP:
- `linkding.staging.ronaldlokers.nl` → 10.0.40.52
- `grafana.staging.ronaldlokers.nl` → 10.0.40.52

The k3d load balancer distributes traffic internally to pods.

#### Production (3-node HA with K3s ServiceLB)

DNS records can point to any node IP (or all three for round-robin):
- `linkding.ronaldlokers.nl` → 10.0.40.101, 10.0.40.102, or 10.0.40.103
- `longhorn.ronaldlokers.nl` → 10.0.40.101, 10.0.40.102, or 10.0.40.103
- `grafana.ronaldlokers.nl` → 10.0.40.101, 10.0.40.102, or 10.0.40.103

K3s ServiceLB (Klipper) automatically distributes traffic across all nodes.

**Recommendation**: Configure DNS with all three IPs for best redundancy:
- Cloudflare can do round-robin load balancing
- If one node is down, DNS will route to the others

### Load Balancing

#### Staging: k3d Built-in Load Balancer

k3d includes a load balancer that:
- Maps ports 80 and 443 from the host to the cluster
- Distributes traffic to Traefik ingress controller
- Handles external connectivity

#### Production: K3s ServiceLB (Klipper)

K3s includes a built-in load balancer called Klipper/ServiceLB:
- Assigns external IPs to LoadBalancer services
- Uses all node IPs as external IPs
- Routes traffic to pods across all nodes
- No external load balancer required

When a LoadBalancer service is created (like Traefik), ServiceLB:
1. Assigns all node IPs as external IPs
2. Opens ports on all nodes using iptables
3. Forwards traffic to service pods
4. Handles pod distribution and health checks

### Ingress

Both clusters use **Traefik** as the ingress controller.

**Traffic flow**:
1. External request → DNS resolves to node IP(s)
2. Request hits node on port 80 or 443
3. ServiceLB/k3d forwards to Traefik pod
4. Traefik routes based on Host header to backend service
5. Service forwards to application pod

**Features**:
- Automatic TLS certificates from cert-manager
- HTTPS redirect middleware (HTTP → HTTPS)
- SNI-based routing
- Integrates with K3s ServiceLB for multi-node distribution

### TLS Certificates

All ingresses use automatic TLS certificates from Let's Encrypt.

**Configuration**:
- **Issuer**: `letsencrypt-production` (production uses production issuer)
- **Challenge Type**: DNS-01 (Cloudflare API)
- **Renewal**: Automatic via cert-manager
- **Validity**: 90 days, renewed at 30 days remaining

**How it works**:
1. Ingress created with `tls` section
2. cert-manager detects the ingress
3. Creates a Certificate resource
4. Performs DNS-01 challenge via Cloudflare API
5. Obtains certificate from Let's Encrypt
6. Stores certificate in a Secret
7. Traefik uses the secret for TLS termination

**DNS-01 Challenge**:
- cert-manager creates a TXT record in Cloudflare
- Let's Encrypt verifies the TXT record
- Challenge passes, certificate issued
- TXT record removed

This works even for services not publicly accessible, as only DNS needs to be verified.

## High Availability

### Staging

Not designed for HA:
- Single k3d server node (control plane)
- 3 agent nodes (workers)
- No redundancy - for testing only

### Production

Full high-availability setup:

**Control Plane HA**:
- 3 control plane nodes with embedded etcd
- Etcd quorum: 2 of 3 nodes required
- Survives 1 control plane node failure
- API server available on all nodes

**Storage HA**:
- Longhorn with 3-replica redundancy
- Data stored on 3 different nodes
- Survives 2 storage node failures
- Automatic replica rebuilding

**Application HA**:
- Pods can be scheduled on any node
- K3s ServiceLB distributes ingress traffic
- Traefik runs as a Deployment (can scale)
- Applications can run multiple replicas

**Failure Scenarios**:
- 1 node down: Cluster fully operational
- 2 nodes down: Control plane and storage still work, applications may be impacted
- 3 nodes down: Cluster offline

## Monitoring and Observability

Both clusters include the kube-prometheus-stack:
- **Prometheus**: Metrics collection and storage
- **Grafana**: Dashboards and visualization
- **Alertmanager**: Alert routing and management
- **Node Exporter**: Node-level metrics
- **kube-state-metrics**: Kubernetes object metrics

**ServiceMonitors**:
- Longhorn metrics (production only)
- Kubernetes system metrics
- Application metrics

**Dashboards**:
- Production includes custom Longhorn dashboard
- Default kube-prometheus-stack dashboards
- Custom dashboards stored in `monitoring/dashboards/`

**Access**:
- Staging: https://grafana.staging.ronaldlokers.nl
- Production: https://grafana.ronaldlokers.nl

Grafana admin credentials are auto-generated and stored in the `kube-prometheus-stack-grafana` secret.
