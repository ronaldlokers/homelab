# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

This is a production-grade GitOps-managed Kubernetes homelab running two environments:
- **Staging**: k3d cluster (1 server + 3 agents) in a Proxmox VM at 10.0.40.52
- **Production**: HA K3s cluster (3 control plane nodes) on Raspberry Pi CM5 modules at 10.0.40.100 (MetalLB VIP)

The entire infrastructure is declaratively configured and managed by Flux CD, with automatic reconciliation every 10 minutes.

## User Context

The user is an experienced developer (15+ years) who likes tech and can easily learn new things. They understand development concepts, Git, Kubernetes basics, and system architecture. Don't over-explain basic concepts or hold their hand through every detail. Provide concise guidance and trust their ability to figure things out.

## How Claude Code Should Help

### Documentation Updates
Claude Code can directly help with updating documentation files in the `docs/` directory and this CLAUDE.md file. Feel free to create, edit, and improve documentation as needed.

### Cluster Operations (Apps, Infrastructure, Debugging)
When asked to add applications, modify infrastructure, or debug cluster issues (e.g., troubleshoot a not-running pod):

**DO NOT run commands directly.** Instead:

1. **Teach, don't do**: Guide the user at an appropriate level for an experienced developer. Provide direction without excessive hand-holding
2. **Show how to investigate**: Teach the user how to find the necessary information themselves (e.g., which commands to run, which logs to check, which documentation to read)
3. **Documentation first**: When the user asks for debugging help, first try to guide them to the relevant official documentation (Kubernetes, Flux, Helm, etc.) that explains what commands to use. Only provide the command directly if documentation isn't available or practical.
4. **Explain commands**: When providing commands, always explain:
   - What the command does
   - Why you think it will help with their specific issue
   - What output to look for
5. **Explain file structures**: Help the user understand where to create/modify files and what information they need to gather
6. **Search for references**: Always search the internet for up-to-date documentation, examples, and best practices for the technologies involved (Kubernetes, Helm charts, specific applications, etc.)
7. **Verify current versions**: Use web searches to find the latest stable versions, configuration patterns, and recommended practices
8. **Be critical about repetition**: If the user asks the same or very similar questions repeatedly, point this out. Remind them of previous conversations or documentation they should have learned from. The goal is for them to need help less and less over time for the same types of questions.
9. **Verification after completion**: After the user indicates they're done (e.g., "I am done implementing the app" or "I have fixed the issue"), you ARE allowed to run commands to verify their work. If something is wrong:
   - Go back to teaching mode
   - Ask questions to understand their thinking process
   - Figure out where their understanding went wrong
   - Guide them to fix the issue themselves

The goal is to build the user's knowledge and self-sufficiency, not to do the work for them. The user should become increasingly independent with each interaction.

## Development Commands

### Flux Operations

**Manual reconciliation** (force immediate sync):
```bash
# Reconcile everything
flux reconcile kustomization flux-system --context=production

# Reconcile specific kustomization
flux reconcile kustomization infrastructure-controllers --context=production

# Reconcile Helm release
flux reconcile helmrelease cert-manager -n cert-manager --context=production

# Reconcile git source
flux reconcile source git flux-system --context=production
```

**Check status**:
```bash
# Get all Flux kustomizations
flux get kustomizations --context=production

# Get all Helm releases
flux get helmreleases --all-namespaces --context=production

# Check for drift or issues
flux get all --context=production
```

**View logs**:
```bash
# Kustomize controller logs (for reconciliation issues)
kubectl logs -n flux-system -l app=kustomize-controller --context=production

# Helm controller logs (for Helm release issues)
kubectl logs -n flux-system -l app=helm-controller --context=production
```

### Kubernetes Operations

**Context switching**:
```bash
kubectl config use-context staging
kubectl config use-context production
```

**Common checks**:
```bash
# View all pods across namespaces
kubectl get pods --all-namespaces --context=production

# Check specific namespace
kubectl get all -n database --context=production

# View logs
kubectl logs -n <namespace> <pod-name> --context=production

# Describe resource for details
kubectl describe pod -n <namespace> <pod-name> --context=production
```

### SOPS Secret Management

**Prerequisites**: Private age keys are stored in Proton Pass (not in this repository). Export the key file path before working with secrets.

**Edit encrypted secret**:
```bash
# Staging
export SOPS_AGE_KEY_FILE=/workspaces/homelab/staging-age.key
sops apps/staging/linkding/linkding-container-env-secret.yaml

# Production
export SOPS_AGE_KEY_FILE=/workspaces/homelab/production-age.key
sops apps/production/linkding/linkding-container-env-secret.yaml
```

**View encrypted secret**:
```bash
export SOPS_AGE_KEY_FILE=/workspaces/homelab/production-age.key
sops --decrypt path/to/secret.yaml
```

**Encrypt new secret**:
```bash
# Create secret YAML with stringData field
# Then encrypt:
export AGE_PUBLIC=age1hh6cdyljk2ks5mkmxqx6g65c7a8rgndy5p2s2d7w2gvqx4h53ggqtwr7rh  # production
sops --age=$AGE_PUBLIC --encrypt --encrypted-regex '^(data|stringData)$' --in-place secret.yaml
```

**Age public keys**:
- Staging: `age1uq9nturwsx36q045qtrm85lkg8qmzpgk9srduqesxs2ahjurw53sp9rhm6`
- Production: `age1hh6cdyljk2ks5mkmxqx6g65c7a8rgndy5p2s2d7w2gvqx4h53ggqtwr7rh`

### Bootstrap Commands

**Bootstrap Flux** (only needed for new clusters):
```bash
export GITHUB_USER=ronaldlokers
export GITHUB_TOKEN=<token>

# Staging
flux bootstrap github \
  --context=staging \
  --owner=$GITHUB_USER \
  --repository=homelab \
  --branch=main \
  --path=./clusters/staging \
  --personal

# Production
flux bootstrap github \
  --context=production \
  --owner=$GITHUB_USER \
  --repository=homelab \
  --branch=main \
  --path=./clusters/production \
  --personal
```

**Create sops-age secret** (required after bootstrap):
```bash
cat production-age.key | kubectl --context=production create secret generic sops-age \
  --namespace=flux-system \
  --from-file=age.agekey=/dev/stdin
```

## Architecture

### Repository Structure

```
clusters/              # Flux entry points
├── staging/          # Staging cluster config
│   ├── flux-system/  # Flux installation
│   ├── .sops.yaml    # SOPS config
│   ├── apps.yaml     # Apps Kustomization
│   ├── infrastructure.yaml
│   └── monitoring.yaml
└── production/       # Production cluster config

infrastructure/
├── controllers/      # Helm releases (cert-manager, cloudnative-pg, longhorn, metallb, renovate)
│   ├── base/        # Shared releases
│   ├── staging/     # Staging overlays
│   └── production/  # Production overlays (adds Longhorn)
└── configs/         # Infrastructure configs (ClusterIssuers, PostgreSQL clusters, databases)
    ├── base/
    ├── staging/
    └── production/

apps/                # Application deployments
├── base/           # Base manifests
├── staging/        # Staging overlays
└── production/     # Production overlays

monitoring/
├── controllers/    # kube-prometheus-stack Helm releases
├── dashboards/     # Custom Grafana dashboards
└── servicemonitors/  # Prometheus ServiceMonitors
```

### Deployment Order (Flux Dependencies)

1. **Infrastructure Controllers** → Core services (cert-manager, CloudNative-PG, Longhorn, MetalLB, Renovate)
2. **Infrastructure Configs** → Configuration (ClusterIssuers, PostgreSQL clusters, Traefik middlewares)
3. **Applications** → User-facing apps (depends on infrastructure being ready)
4. **Monitoring** → Observability stack (independent)

Dependencies are enforced via Kustomization `dependsOn` fields.

### Kustomize Pattern

All resources follow **base/overlay pattern**:
- `base/` directories contain shared configuration
- `staging/` and `production/` directories contain environment-specific overlays
- This enables environment parity while allowing customization

### Key Technologies

- **GitOps**: Flux CD v2.7.5 (automatic reconciliation every 10 minutes)
- **Configuration**: Kustomize (base/overlay pattern) + Helm (via Flux HelmRelease CRDs)
- **Secrets**: SOPS + age encryption (environment-specific keys)
- **Storage**: Longhorn (production) with 3-replica HA, local-path (staging)
- **Load Balancing**: MetalLB (production), k3d built-in (staging)
- **Database**: CloudNative-PG with DocumentDB extension, automated backups to Backblaze B2
- **TLS**: cert-manager with Let's Encrypt DNS-01 challenges via Cloudflare
- **Ingress**: Traefik (K3s built-in)
- **Monitoring**: kube-prometheus-stack (Prometheus + Grafana)
- **Automation**: Renovate (hourly CronJob for dependency updates)

### Applications

1. **Commafeed** - Self-hosted RSS reader
2. **Homepage** - Application dashboard
3. **Immich** - Photo/video management (with external NFS library mount)
4. **Linkding** - Bookmark manager
5. **Nightscout** - CGM remote monitoring (uses FerretDB for MongoDB compatibility)
6. **ntfy** - Notification service with web push and iOS support (production only)
7. **pgAdmin** - PostgreSQL administration (production only)
8. **Speedtest Tracker** - Internet speed history with Grafana integration (production only)

### PostgreSQL Architecture

CloudNative-PG operator manages PostgreSQL clusters:
- **Cluster Configuration**: 3 instances (1 primary + 2 replicas)
- **High Availability**: Automatic failover
- **Extensions**: DocumentDB extension for MongoDB compatibility (used by Nightscout via FerretDB)
- **Backups**: Daily automated backups to Backblaze B2 with 30-day retention (production)
- **WAL Archiving**: Continuous for point-in-time recovery (PITR)
- **Disaster Recovery**: Bootstrap recovery mode for automatic restoration

Each application has its own database created via DatabaseClaim CRDs.

### Networking

**Staging**:
- DNS points to 10.0.40.52 (Proxmox VM)
- k3d load balancer distributes traffic internally

**Production**:
- DNS points to 10.0.40.100 (MetalLB VIP)
- MetalLB provides Layer 2 load balancing with automatic failover
- All services use automatic TLS via cert-manager with Let's Encrypt DNS-01 challenges

**Traffic Flow**:
External request → DNS → Load Balancer IP → Traefik → Service → Pod

### High Availability (Production)

- **Control Plane**: 3-node HA with embedded etcd (survives 1-node failure)
- **Storage**: Longhorn 3-replica (survives 2-node failure)
- **Database**: PostgreSQL cluster with 3 instances
- **Load Balancing**: MetalLB VIP with automatic failover
- **RTO**: ~20-30 minutes for complete cluster recovery
- **RPO**: Near-zero (continuous WAL archiving)

## Working in This Repository

### Making Changes

1. **Edit configuration files** in your working directory
2. **Commit and push** to the main branch
3. **Flux automatically reconciles** within 10 minutes
4. **Or force immediate sync**: `flux reconcile kustomization flux-system --context=production`

### Adding a New Application

1. Create base manifests in `apps/base/<app-name>/`
2. Create environment overlays in `apps/staging/<app-name>/` and `apps/production/<app-name>/`
3. Add to the apps kustomization in `apps/staging/kustomization.yaml` and `apps/production/kustomization.yaml`
4. If needs database, create DatabaseClaim in `infrastructure/configs/<env>/cloudnative-pg/`
5. Create SOPS-encrypted secrets if needed
6. Commit and push, or force reconciliation

### Adding Secrets

1. Create secret YAML with `stringData` field (plain text)
2. Encrypt with SOPS using environment-specific age public key
3. Place in appropriate directory (`apps/<env>/` or `infrastructure/configs/<env>/`)
4. Reference in application deployment
5. Commit (encrypted secrets are safe to commit)

### Modifying Helm Releases

Helm charts are managed via Flux HelmRelease CRDs:
- **Chart versions**: Pinned in environment-specific overlays
- **Values**: Overridden via patches in overlays
- **Location**: `infrastructure/controllers/` or `monitoring/controllers/`

Example: To update cert-manager version, edit `infrastructure/controllers/production/cert-manager/helmrelease.yaml`

### Troubleshooting Flux Issues

**Check Kustomization status**:
```bash
kubectl get kustomization -n flux-system --context=production
```

**View reconciliation errors**:
```bash
kubectl describe kustomization <name> -n flux-system --context=production
```

**Check Helm release status**:
```bash
kubectl get helmrelease -n <namespace> --context=production
```

**Force reconciliation**:
```bash
flux reconcile kustomization <name> --context=production
```

**Suspend/resume reconciliation** (useful for testing):
```bash
flux suspend kustomization <name> --context=production
flux resume kustomization <name> --context=production
```

### Disaster Recovery

**PostgreSQL Recovery**:
1. Deploy new PostgreSQL cluster with `bootstrap.recovery` configuration
2. Operator automatically restores from Backblaze B2 backups
3. PITR to specific point in time if needed
4. See `docs/war-stories/postgresql-disaster-recovery.md` for detailed procedures

**Complete Cluster Failure**:
1. Rebuild cluster infrastructure
2. Bootstrap Flux
3. Create `sops-age` secret
4. Flux reconciles all resources
5. PostgreSQL clusters auto-restore from backups

## Important Notes

### Secrets Security

- **NEVER commit private age keys** - they are stored in Proton Pass
- **Verify encryption** before committing secrets (check that `data`/`stringData` fields are encrypted)
- **Use correct environment key** - staging and production have separate keys
- **Only `data` and `stringData` fields are encrypted** - metadata remains readable

### Deployment Timing

- Flux reconciles every 10 minutes automatically
- Manual reconciliation is instant: `flux reconcile kustomization flux-system`
- Some resources depend on others - check `dependsOn` in Kustomization files
- Infrastructure must be ready before applications deploy

### Resource Constraints

- **Production**: ARM64 Raspberry Pi CM5 with 16GB RAM per node
- Resource requests/limits are tuned for ARM64 architecture
- Longhorn storage limited to 512GB NVMe per node
- Monitor resource usage via Grafana: https://grafana.ronaldlokers.nl

### Renovate Automation

- Runs hourly as a CronJob in the cluster
- Creates PRs for dependency updates (Helm charts, container images)
- Review PRs before merging - some updates may break compatibility
- Configuration in `renovate.json` at repository root

### Common Gotchas

1. **SOPS decryption failures**: Ensure `sops-age` secret exists in `flux-system` namespace with correct key
2. **Kustomization not ready**: Check `dependsOn` - dependent resources won't reconcile until dependencies are healthy
3. **Helm release failures**: Check Helm controller logs and HelmRelease status for version/values issues
4. **Certificate issues**: Verify Cloudflare API token has DNS edit permissions
5. **Storage issues (production)**: Check Longhorn dashboard for replica health and disk space
6. **NFS mount failures on Debian Trixie**: See `docs/war-stories/nfs-on-debian-trixie.md` for systemd mount workaround

## Documentation

See `docs/` directory for comprehensive documentation:
- **setup.md**: Cluster setup procedures
- **architecture.md**: Detailed infrastructure architecture
- **security.md**: Secrets management guide
- **stack/**: Component-specific documentation
- **war-stories/**: Real-world troubleshooting experiences (highly recommended reading)
