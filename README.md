# Homelab

GitOps-managed Kubernetes homelab using Flux CD.

[![Flux](https://img.shields.io/badge/Flux-CD-5468ff?logo=flux&logoColor=white)](https://fluxcd.io/)
[![k3s](https://img.shields.io/badge/k3s-FFC61C?logo=k3s&logoColor=black)](https://k3s.io/)
[![Kubernetes](https://img.shields.io/badge/Kubernetes-326CE5?logo=kubernetes&logoColor=white)](https://kubernetes.io/)
[![Longhorn](https://img.shields.io/badge/Longhorn-Storage-1E8CBE?logo=rancher&logoColor=white)](https://longhorn.io/)
[![Renovate](https://img.shields.io/badge/Renovate-enabled-blue?logo=renovatebot&logoColor=white)](https://docs.renovatebot.com/)
[![Grafana](https://img.shields.io/badge/Grafana-F46800?logo=grafana&logoColor=white)](https://grafana.com/)
[![Raspberry Pi](https://img.shields.io/badge/Raspberry%20Pi-CM5-A22846?logo=raspberrypi&logoColor=white)](https://www.raspberrypi.com/)

## Overview

This repository contains the complete infrastructure and application configuration for a Kubernetes homelab environment. Everything is managed declaratively through Git, with Flux CD automatically reconciling the cluster state.

## Cluster Infrastructure

The homelab runs two K3s clusters with Flux CD:

### Staging
- **Single-node cluster**: kube-srv-1.local (10.0.40.101)
- **Storage**: local-path (node-local storage)

### Production
- **Multi-node cluster**: 2 nodes (1 server + 1 agent)
  - **kube-srv-2.local** (10.0.40.102) - Control plane + worker
  - **kube-srv-3.local** (10.0.40.103) - Agent (worker only)
- **Storage**: Longhorn distributed storage with 2-replica redundancy
- **Load Balancing**: K3s ServiceLB (Klipper) distributes traffic across both nodes
- **High Availability**: Applications can survive single node failure
- **Capacity**: Ready for 3rd node expansion
- **Version**: K3s v1.33.6, Flux v2.7.5

### Hardware

The clusters run in a Sipeed NanoCluster, with each node running on:

- **Compute Module**: Raspberry Pi CM5
- **RAM**: 16GB
- **Storage**: 64GB eMMC (SSD upgrade planned)

<div align="center">
  <img src="docs/images/sipeed-nanocluster.png" alt="Sipeed NanoCluster" width="400"/>
  <p><em>Sipeed NanoCluster</em></p>
</div>

<div align="center">
  <img src="docs/images/raspberry-pi-cm5.png" alt="Raspberry Pi CM5" width="400"/>
  <p><em>Raspberry Pi Compute Module 5</em></p>
</div>

## Architecture

The repository follows a structured layout separating concerns by layers:

```
.
├── clusters/
│   ├── staging/              # Staging cluster Flux configuration
│   └── production/           # Production cluster Flux configuration
├── infrastructure/
│   ├── controllers/          # Infrastructure Helm releases (cert-manager, renovate)
│   └── configs/              # Infrastructure configuration (issuers, middlewares)
├── apps/                     # Application deployments
└── monitoring/               # Observability stack (kube-prometheus-stack)
```

Each component uses Kustomize overlays with `base/`, `staging/`, and `production/` directories for environment-specific configuration.

## Storage

### Staging
- **Storage Class**: `local-path` (default K3s storage)
- **Type**: Node-local storage
- **Backup**: Not replicated

### Production
- **Storage Class**: `longhorn` (default)
- **Type**: Distributed block storage
- **Replication**: 2 replicas across both nodes
- **Features**:
  - Automatic data replication for high availability
  - Volume snapshots and backups
  - Dynamic provisioning
  - Survives single node failure
- **Management**: Longhorn UI at https://longhorn.ronaldlokers.nl

## Stack

**GitOps & Automation**
- [Flux CD](https://fluxcd.io/) - GitOps continuous delivery
  - Automatic reconciliation from Git repository
  - Manages Helm releases and Kustomizations
- [Renovate](https://docs.renovatebot.com/) - Automated dependency updates
  - Runs hourly via CronJob
  - Automatic pull requests for Helm chart and container image updates

**Infrastructure**
- [cert-manager](https://cert-manager.io/) - Automated TLS certificate management
  - Let's Encrypt certificates using DNS-01 challenge (Cloudflare)
  - Automatic renewal before expiration
- [Traefik](https://traefik.io/) - Ingress controller and load balancer
  - Automatic HTTPS redirect middleware
  - Integrated with K3s ServiceLB for multi-node distribution
- [Longhorn](https://longhorn.io/) - Distributed block storage with replication (production only)
  - 2-replica redundancy across nodes
  - Automatic data synchronization

**Applications**
- [Linkding](https://github.com/sissbruecker/linkding) - Bookmark manager
  - Staging: https://linkding-staging.ronaldlokers.nl
  - Production: https://linkding.ronaldlokers.nl
- [Longhorn UI](https://longhorn.io/) - Storage management dashboard (production only)
  - Production: https://longhorn.ronaldlokers.nl

**Monitoring**
- [kube-prometheus-stack](https://github.com/prometheus-operator/kube-prometheus) - Prometheus & Grafana
  - Staging: https://grafana-staging.ronaldlokers.nl
  - Production: https://grafana.ronaldlokers.nl

## Networking

### DNS Configuration

All services are exposed via DNS records pointing to cluster node IPs:

**Staging** (kube-srv-1.local - 10.0.40.101):
- linkding-staging.ronaldlokers.nl → 10.0.40.101
- grafana-staging.ronaldlokers.nl → 10.0.40.101

**Production** (multi-node with K3s ServiceLB):
- linkding.ronaldlokers.nl → 10.0.40.102 or 10.0.40.103
- longhorn.ronaldlokers.nl → 10.0.40.102 or 10.0.40.103
- grafana.ronaldlokers.nl → 10.0.40.102 or 10.0.40.103

**Note**: Production DNS can point to either node IP. K3s ServiceLB automatically distributes traffic. For better redundancy, configure DNS round-robin with both IPs.

### TLS Certificates

All ingresses use automatic TLS certificates from Let's Encrypt:
- **Challenge Type**: DNS-01 (Cloudflare API)
- **Issuer**: letsencrypt-production (staging uses letsencrypt-staging)
- **Renewal**: Automatic via cert-manager
- **Validity**: 90 days, renewed at 30 days remaining

## Security

Secrets are encrypted using [SOPS](https://github.com/getsops/sops) with [age](https://github.com/FiloSottile/age) encryption. Flux automatically decrypts secrets during deployment using the cluster's age key.

Each environment has its own SOPS configuration:
- `clusters/staging/.sops.yaml`
- `clusters/production/.sops.yaml`

### Required Secrets

These secrets must be manually created in the cluster before deployment:

#### flux-system/flux-system
Export the required variables:
```bash
export GITHUB_USER=ronaldlokers`
export GITHUB_TOKEN=<personal-access-token>
```

Bootstrap Flux (choose the appropriate environment):
```bash
# For staging
flux bootstrap github \
  --context=staging \
  --owner=$GITHUB_USER \
  --repository=homelab \
  --branch=main \
  --path=./clusters/staging \
  --personal

# For production
flux bootstrap github \
  --context=production \
  --owner=$GITHUB_USER \
  --repository=homelab \
  --branch=main \
  --path=./clusters/production \
  --personal
```

#### flux-system/sops-age
Age private key for decrypting SOPS-encrypted secrets:

# Download the age.key file
This file is stored in Proton Pass

# Create secret in cluster
```bash
cat age.key | kubectl create secret generic sops-age \
  --namespace=flux-system \
  --from-file=age.agekey=/dev/stdin
```

### Managed Secrets

These secrets are encrypted with SOPS and stored in this repository:

- `cert-manager/cloudflare-api-token` - Cloudflare DNS API token for DNS-01 challenges
- `linkding/linkding-container-env` - Linkding application environment variables
- `linkding/tunnel-credentials` - Cloudflare Tunnel credentials
- `renovate/renovate-container-env` - Renovate GitHub token

### Auto-Generated Secrets

These secrets are automatically created by controllers:

- `*-tls` - TLS certificates issued by cert-manager
- `letsencrypt-*` - Let's Encrypt ACME account keys
- `kube-prometheus-stack-grafana` - Grafana admin credentials
- Prometheus and Alertmanager configuration secrets

## Deployment Flow

Flux monitors this repository and automatically applies changes:

1. **Infrastructure Controllers** - Core services deployed first (cert-manager, renovate)
2. **Infrastructure Configs** - Configuration applied after controllers are ready (certificate issuers, middlewares)
3. **Applications** - Apps deployed after infrastructure is ready
4. **Monitoring** - Observability stack deployed independently

Dependencies are enforced through Kustomization `dependsOn` fields to ensure correct ordering.

## Repository Structure

All resources follow the same pattern:
- Base configuration in `base/` directories
- Environment-specific overlays in `staging/` and `production/` directories
- Kustomization files reference environment-specific resources

Each cluster reconciles from its respective directory:
- Staging cluster: `clusters/staging/`
- Production cluster: `clusters/production/`
