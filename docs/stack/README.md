# Technology Stack

This document provides detailed information about all components running in the homelab clusters.

- [GitOps & Automation](./gitops-and-automation.md)
- Infrastructure
  - [cert-manager](./infrastructure/cert-manager.md)
  - [CloudNative-PG](./infrastructure/cloudnative-pg.md)
  - [Longhorn](./infrastructure/longhorn.md)
  - [MetalLB](./infrastructure/metallb.md)
  - [Traefik](./infrastructure/traefik.md)
- Applications
  - [Commafeed](./applications/commafeed.md)
  - [Homepage](./applications/homepage.md)
  - [Immich](./applications/immich.md)
  - [Linkding](./applications/linkding.md)
  - [Nightscout](./applications/nightscout.md)
  - [pgAdmin](./applications/pgadmin.md)
  - [Speedtest](./applications/speedtest.md)
- Monitoring
  - [kube-prometheus-stack](./monitoring/kube-prometheus-stack.md)
  - [Loki + Alloy](./monitoring/loki-alloy.md)

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
| Nightscout | ✅ | ✅ | CGM remote monitoring |
| Commafeed | ✅ | ✅ | RSS feed reader |
| Immich | ✅ | ✅ | Photo and video management |
| pgAdmin | ❌ | ✅ | PostgreSQL administration |
| Speedtest | ❌ | ✅ | Network speed test |
| kube-prometheus-stack | ✅ | ✅ | Monitoring and observability |
| Loki + Alloy | ✅ | ❌ | Log aggregation and querying |

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
