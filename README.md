# Homelab

GitOps-managed Kubernetes homelab using Flux CD.

## Overview

This repository contains the complete infrastructure and application configuration for a Kubernetes homelab environment. Everything is managed declaratively through Git, with Flux CD automatically reconciling the cluster state.

## Architecture

The repository follows a structured layout separating concerns by layers:

```
.
├── clusters/staging/          # Cluster-specific Flux configuration
├── infrastructure/
│   ├── controllers/           # Infrastructure Helm releases (cert-manager, renovate)
│   └── configs/              # Infrastructure configuration (issuers, middlewares)
├── apps/                     # Application deployments
└── monitoring/               # Observability stack (kube-prometheus-stack)
```

Each component uses Kustomize overlays with `base/` and `staging/` directories for environment-specific configuration.

## Stack

**GitOps & Automation**
- [Flux CD](https://fluxcd.io/) - GitOps continuous delivery
- [Renovate](https://docs.renovatebot.com/) - Automated dependency updates

**Infrastructure**
- [cert-manager](https://cert-manager.io/) - Automated certificate management with Let's Encrypt
- [Traefik](https://traefik.io/) - Ingress controller with HTTPS redirect middleware

**Applications**
- [Linkding](https://github.com/sissbruecker/linkding) - Bookmark manager

**Monitoring**
- [kube-prometheus-stack](https://github.com/prometheus-operator/kube-prometheus) - Prometheus & Grafana

## Security

Secrets are encrypted using [SOPS](https://github.com/getsops/sops) with [age](https://github.com/FiloSottile/age) encryption. Flux automatically decrypts secrets during deployment using the cluster's age key.

Configuration: `clusters/staging/.sops.yaml`

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
- Environment-specific overlays in `staging/` directories
- Kustomization files reference environment-specific resources

The cluster reconciles from `clusters/staging/`, which references the appropriate overlays for each component.
