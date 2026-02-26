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
│       ├── base/
│       │   ├── network-policies/  # NetworkPolicy definitions
│       │   ├── cert-manager/
│       │   ├── cloudnative-pg/
│       │   └── traefik/
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

## Hardware

### Staging Environment

- **Platform**: Ubuntu Server VM running in Proxmox on MS-01 mini PC
- **k3d cluster**: 1 server node + 3 agent nodes (containerized)
- **Storage**: VM disk storage

### Production Environment

- **Platform**: Sipeed NanoCluster with 3× Raspberry Pi CM5 modules
- **Nodes**: 3× Raspberry Pi CM5 (16GB RAM each)
- **Storage**: Each node has a dedicated 512GB NVMe SSD for Longhorn storage
- **Network**: Gigabit Ethernet

**Node Configuration**:
- **kube-srv-1** (10.0.40.101): Control plane + worker
- **kube-srv-2** (10.0.40.102): Control plane + worker
- **kube-srv-3** (10.0.40.103): Control plane + worker

**Operating System**:
- Debian GNU/Linux 13 (Trixie)
- Kernel: 6.12.47+rpt-rpi-2712

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
- **Hardware**: 512GB NVMe SSD on each Raspberry Pi node
- **Replication**: 3 replicas across all three nodes
- **Management UI**: https://longhorn.ronaldlokers.nl

**Features**:
- Automatic data replication for high availability
- Volume snapshots and backups
- Dynamic provisioning
- Survives two-node failure (3 replicas)
- iSCSI-based block storage
- Web UI for management
- High-performance NVMe backend storage

**How it works**:
1. When a PVC is created, Longhorn creates a volume
2. Data is replicated to 3 nodes automatically (stored on NVMe SSDs)
3. If a node fails, replicas on other nodes remain available
4. Applications can continue running during node failures
5. Replicas automatically rebalance using "least-effort" strategy

**Configuration**:
- Default replica count: 3
- Replica anti-affinity: Disabled (allows replicas on same node if needed)
- Auto-balance: least-effort (balances when convenient)
- Data path: `/mnt/longhorn` on each node's NVMe SSD

## Networking

### DNS Configuration

All services are exposed via DNS records pointing to cluster node IPs.

#### Staging (k3d cluster - 10.0.40.52)

DNS records point to the Proxmox VM IP:
- `linkding.staging.ronaldlokers.nl` → 10.0.40.52
- `grafana.staging.ronaldlokers.nl` → 10.0.40.52

The k3d load balancer distributes traffic internally to pods.

#### Production (3-node HA with MetalLB)

DNS records point to a single virtual IP provided by MetalLB:
- `linkding.ronaldlokers.nl` → 10.0.40.100
- `longhorn.ronaldlokers.nl` → 10.0.40.100
- `homepage.ronaldlokers.nl` → 10.0.40.100
- `grafana.ronaldlokers.nl` → 10.0.40.100

MetalLB provides a single stable IP address with automatic failover:
- **Load Balancer VIP**: 10.0.40.100
- Layer 2 mode with ARP announcement
- Automatic failover between nodes
- No DNS round-robin required

### Load Balancing

#### Staging: k3d Built-in Load Balancer

k3d includes a load balancer that:
- Maps ports 80 and 443 from the host to the cluster
- Distributes traffic to Traefik ingress controller
- Handles external connectivity

#### Production: MetalLB

MetalLB provides network load balancing for bare-metal Kubernetes clusters:
- Assigns a single virtual IP to LoadBalancer services
- Layer 2 mode using ARP announcement
- Automatic failover between nodes
- Standard Kubernetes LoadBalancer interface

**Configuration**:
- **IP Pool**: 10.0.40.100/32
- **Mode**: Layer 2 (L2Advertisement)
- **VIP**: 10.0.40.100

When a LoadBalancer service is created (like Traefik), MetalLB:
1. Assigns the VIP (10.0.40.100) to the service
2. Speaker pods announce the VIP via ARP on the network
3. Traffic to 10.0.40.100 is routed to the service pods
4. Automatic failover if the announcing node fails
5. New speaker takes over and re-announces the VIP

### Ingress

Both clusters use **Traefik** as the ingress controller.

**Traffic flow**:
1. External request → DNS resolves to load balancer IP
   - Staging: 10.0.40.52 (k3d VM)
   - Production: 10.0.40.100 (MetalLB VIP)
2. Request hits load balancer on port 80 or 443
3. Load balancer forwards to Traefik pod
4. Traefik routes based on Host header to backend service
5. Service forwards to application pod

**Features**:
- Automatic TLS certificates from cert-manager
- HTTPS redirect middleware (HTTP → HTTPS)
- SNI-based routing
- Integrates with MetalLB (production) or k3d (staging) for load balancing

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

### NetworkPolicies

Both environments implement **zero-trust network security** using Kubernetes NetworkPolicies:

- **Default-deny**: All traffic blocked unless explicitly allowed
- **Namespace isolation**: Prevents lateral movement between services
- **Egress control**: Applications can only reach approved external endpoints
- **Least privilege**: Each service has minimal required network access

**Implemented policies**:
- Default deny (all namespaces)
- DNS resolution (CoreDNS access)
- Ingress from Traefik
- Database connectivity (app → PostgreSQL)
- Monitoring (Prometheus scraping)
- Internet egress (controlled per namespace)
- Homepage dashboard access (internal service APIs)
- Database operator (Kubernetes API access)
- Immich microservices (internal communication)
- Loki logging (S3 storage and ring membership)

See [Network Security](network-security.md) for detailed policy documentation.

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
- MetalLB provides single VIP with automatic failover
- Traefik runs as a Deployment (can scale)
- Applications can run multiple replicas
- PostgreSQL cluster with 3 instances (1 primary + 2 replicas)

**Failure Scenarios**:
- 1 node down: Cluster fully operational
- 2 nodes down: Control plane and storage still work, applications may be impacted
- 3 nodes down: Cluster offline

## Backup and Disaster Recovery

### PostgreSQL Backups

**Backup Strategy**:
- **Storage**: Backblaze B2 object storage
- **Method**: Barman via CloudNative-PG operator
- **Type**: Physical backups + WAL archiving
- **Frequency**: Daily automated backups
- **Retention**: 14 days (staging), 30 days (production)

**Architecture**:
```
PostgreSQL Cluster
    ├─ Primary Instance (read-write)
    ├─ Replica 1 (read-only)
    └─ Replica 2 (read-only)
         │
         ├─ WAL Files → Continuous archiving to B2
         └─ Base Backup → Daily full backup to B2
                              ↓
                    Backblaze B2 Bucket
                    └─ homelab-postgres-backups/
                        ├─ staging/
                        │   ├─ base/
                        │   └─ wals/
                        └─ production/
                            ├─ base/
                            └─ wals/
```

**What Gets Backed Up**:
- Full database cluster (all databases, users, schemas)
- Write-Ahead Logs (WAL) for point-in-time recovery
- PostgreSQL configuration
- Compressed with gzip to reduce storage costs

**Recovery Capabilities**:
- **Full Restore**: Restore entire cluster to last backup
- **Point-in-Time Recovery (PITR)**: Restore to any moment within retention period
- **Selective Restore**: Create new cluster from backup without affecting existing one
- **Cross-cluster Restore**: Restore production backup to staging for testing

**Disaster Recovery Scenarios**:

1. **Single Database Corruption**:
   - Restore to new cluster from latest backup
   - Verify data integrity
   - Switch applications to new cluster

2. **Complete Cluster Failure**:
   - Deploy new PostgreSQL cluster
   - Bootstrap from B2 backup
   - Automatic recovery with PITR
   - Applications reconnect automatically

3. **Accidental Data Deletion**:
   - Use PITR to restore to moment before deletion
   - Restore to new cluster first to verify
   - Export/import specific data if needed

4. **Regional Disaster**:
   - Backblaze B2 data replicated across data centers
   - Deploy new cluster in different region/environment
   - Restore from B2 backups
   - Update DNS/application configuration

**Backup Monitoring**:
- Kubernetes CronJob for scheduled backups
- CloudNative-PG operator manages backup lifecycle
- Backup status visible via `kubectl get backup`
- Failed backups visible in pod logs

**Recovery Time Objective (RTO)**:
- New cluster deployment: ~5 minutes
- Backup restore (10GB): ~10-15 minutes
- Total recovery time: ~20-30 minutes

**Recovery Point Objective (RPO)**:
- Maximum data loss: Minutes (WAL archiving is continuous)
- Practical data loss: Near-zero (WAL archived every few minutes)

**Testing Strategy**:
- Monthly backup restore tests in staging
- Quarterly disaster recovery drills
- Automated backup verification
- Document all recovery procedures

### Application Data Backups

**Linkding**:
- All data stored in PostgreSQL cluster
- Backed up via PostgreSQL backup strategy
- No additional backup needed

**Storage Volumes**:
- Longhorn provides volume snapshots (production)
- Used for non-database persistent data
- Local-path storage in staging (not backed up)

## Monitoring and Observability

Both clusters include the kube-prometheus-stack:
- **Prometheus**: Metrics collection and storage
- **Grafana**: Dashboards and visualization
- **Alertmanager**: Alert routing and management
- **Node Exporter**: Node-level metrics
- **kube-state-metrics**: Kubernetes object metrics

Staging also includes log aggregation:
- **Loki**: Log storage and indexing (Simple Scalable Deployment mode)
- **Grafana Alloy**: Log collection agent (DaemonSet on all nodes)

See [Loki + Alloy documentation](../stack/monitoring/loki-alloy.md) for details.

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
