# CLAUDE.md

Guidance for Claude Code (claude.ai/code) working code this repo.

## Repository Overview

Production-grade GitOps-managed Kubernetes homelab, two environments:
- **Staging**: k3d cluster (1 server + 3 agents) in Proxmox VM at 10.0.40.52
- **Production**: HA K3s cluster (3 control plane nodes) on Raspberry Pi CM5 modules at 10.0.40.100 (MetalLB VIP)

Infra declaratively configured, managed by Flux CD; new commits picked up + applied within ~1 minute.

## User Context

User: experienced dev (15+ years), likes tech, learns fast. Knows dev concepts, Git, Kubernetes basics, system architecture. Don't over-explain basics or hand-hold every detail. Concise guidance, trust their ability to figure things out.

## How Claude Code Should Help

### Documentation Updates
Claude Code can directly help update docs in `docs/` and this CLAUDE.md. Create, edit, improve docs as needed.

### Cluster Operations (Apps, Infrastructure, Debugging)
When asked to add apps, modify infra, or debug cluster issues (e.g., troubleshoot not-running pod):

**DO NOT run commands directly.** Instead:

1. **Teach, don't do**: Guide user at level fit for experienced dev. Direction without excessive hand-holding
2. **Show how to investigate**: Teach user to find needed info themselves (which commands, which logs, which docs)
3. **Documentation first**: For debugging help, first guide to relevant official docs (Kubernetes, Flux, Helm, etc.) explaining commands to use. Only give command direct if docs unavailable or impractical.
4. **Explain commands**: When giving commands, always explain:
   - What command does
   - Why it help their specific issue
   - What output to look for
5. **Explain file structures**: Help user understand where to create/modify files, what info they need
6. **Search for references**: Always search internet for up-to-date docs, examples, best practices for tech involved (Kubernetes, Helm charts, specific apps, etc.)
7. **Verify current versions**: Web search for latest stable versions, config patterns, recommended practices
8. **Be critical about repetition**: Same/similar question repeated → point it out. Remind of previous conversations or docs they should've learned from. Goal: need help less over time for same question types.
9. **Verification after completion**: After user says done (e.g. "I am done implementing the app" or "I have fixed the issue"), allowed to run commands to verify their work. If wrong:
   - Back to teaching mode
   - Ask questions, understand their thinking
   - Find where understanding went wrong
   - Guide them to fix it themselves

Goal: build user's knowledge + self-sufficiency, not do work for them. User should grow more independent each interaction.

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

**Prerequisites**: Both private age keys live in SOPS's default key file `~/.config/sops/age/keys.txt` (backed up in Proton Pass, never in repo). SOPS finds them auto — no `SOPS_AGE_KEY_FILE` export needed. In devcontainer, `~/.config/sops` is persistent named volume; if empty (first use after adding volume), re-provision `keys.txt` from Proton Pass (see `docs/security.md`).

**Edit encrypted secret** (either environment):
```bash
sops apps/staging/linkding/linkding-container-env-secret.yaml
sops apps/production/linkding/linkding-container-env-secret.yaml
```

**View encrypted secret**:
```bash
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

**Create sops-age secret** (required after bootstrap; each cluster gets only its own key):
```bash
grep -A1 "^# production" ~/.config/sops/age/keys.txt | grep AGE-SECRET-KEY | \
  kubectl --context=production create secret generic sops-age \
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
├── controllers/      # Helm releases (cert-manager, cloudnative-pg, kyverno, longhorn, metallb, renovate)
│   ├── base/        # Shared releases
│   ├── staging/     # Staging overlays
│   └── production/  # Production overlays (adds Longhorn; Kyverno not yet promoted here)
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

1. **Infrastructure Controllers** → Core services (cert-manager, CloudNative-PG, Kyverno, Longhorn, MetalLB, Renovate)
2. **Infrastructure Configs** → Configuration (ClusterIssuers, PostgreSQL clusters, Traefik middlewares)
3. **Applications** → User-facing apps (depends on infra ready)
4. **Monitoring** → Observability stack (independent)

Dependencies enforced via Kustomization `dependsOn` fields.

### Kustomize Pattern

All resources follow **base/overlay pattern**:
- `base/` dirs hold shared config
- `staging/` and `production/` dirs hold environment-specific overlays
- Enables environment parity while allowing customization

### Key Technologies

- **GitOps**: Flux CD (version managed by Renovate; new commits applied within ~1 minute)
- **Configuration**: Kustomize (base/overlay pattern) + Helm (via Flux HelmRelease CRDs)
- **Secrets**: SOPS + age encryption (environment-specific keys)
- **Storage**: Longhorn (production) with 3-replica HA, local-path (staging)
- **Load Balancing**: MetalLB (production), k3d built-in (staging)
- **Database**: CloudNative-PG with DocumentDB extension, automated backups to Backblaze B2
- **TLS**: cert-manager with Let's Encrypt DNS-01 challenges via Cloudflare
- **Ingress**: Traefik (K3s built-in)
- **Policy**: Kyverno (base + staging; not yet promoted to production)
- **Monitoring**: kube-prometheus-stack (Prometheus + Grafana)
- **Automation**: Renovate (hourly CronJob for dependency updates)

### Applications

1. **Commafeed** - Self-hosted RSS reader
2. **Homepage** - Application dashboard
3. **Immich** - Photo/video management (external NFS library mount)
4. **Linkding** - Bookmark manager
5. **Nightscout** - CGM remote monitoring (uses FerretDB for MongoDB compatibility)
6. **ntfy** - Notification service, web push + iOS support (production only)
7. **pgAdmin** - PostgreSQL administration
8. **Speedtest Tracker** - Internet speed history with Grafana integration (production only)

### PostgreSQL Architecture

CloudNative-PG operator manages PostgreSQL clusters:
- **Cluster Configuration**: 3 instances (1 primary + 2 replicas)
- **High Availability**: Automatic failover
- **Extensions**: DocumentDB extension for MongoDB compatibility (used by Nightscout via FerretDB)
- **Backups**: Daily automated backups to Backblaze B2 (7-day retention production, 14-day staging)
- **WAL Archiving**: Continuous for point-in-time recovery (PITR)
- **Disaster Recovery**: Bootstrap recovery mode for auto restoration

Each app has own DB created via DatabaseClaim CRDs.

### Networking

**Staging**:
- DNS points to 10.0.40.52 (Proxmox VM)
- k3d load balancer distributes traffic internally

**Production**:
- DNS points to 10.0.40.100 (MetalLB VIP)
- MetalLB gives Layer 2 load balancing with auto failover
- All services use automatic TLS via cert-manager with Let's Encrypt DNS-01 challenges

**Traffic Flow**:
External request → DNS → Load Balancer IP → Traefik → Service → Pod

### High Availability (Production)

- **Control Plane**: 3-node HA with embedded etcd (survives 1-node failure)
- **Storage**: Longhorn 3-replica (survives 2-node failure)
- **Database**: PostgreSQL cluster with 3 instances
- **Load Balancing**: MetalLB VIP with auto failover
- **RTO**: ~20-30 minutes for complete cluster recovery
- **RPO**: Near-zero (continuous WAL archiving)

## Working in This Repository

### Making Changes

1. **Edit config files** in working directory
2. **Commit and push** to main branch
3. **Flux auto-reconciles** within ~1 minute
4. **Or force immediate sync**: `flux reconcile kustomization flux-system --context=production`

### Adding a New Application

1. Create base manifests in `apps/base/<app-name>/`
2. Create environment overlays in `apps/staging/<app-name>/` and `apps/production/<app-name>/`
3. Add to apps kustomization in `apps/staging/kustomization.yaml` and `apps/production/kustomization.yaml`
4. If needs DB, create DatabaseClaim in `infrastructure/configs/<env>/cloudnative-pg/`
5. Create SOPS-encrypted secrets if needed
6. Commit and push, or force reconciliation

### Adding Secrets

1. Create secret YAML with `stringData` field (plain text)
2. Encrypt with SOPS using environment-specific age public key
3. Place in appropriate dir (`apps/<env>/` or `infrastructure/configs/<env>/`)
4. Reference in app deployment
5. Commit (encrypted secrets safe to commit)

### Modifying Helm Releases

Helm charts managed via Flux HelmRelease CRDs:
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
1. Deploy new PostgreSQL cluster with `bootstrap.recovery` config
2. Operator auto-restores from Backblaze B2 backups
3. PITR to specific point in time if needed
4. See `docs/runbooks/postgresql-cluster-disaster-recovery.md` and `docs/war-stories/postgres-bootstrap-recovery.md` for detailed procedures

**Complete Cluster Failure**:
1. Rebuild cluster infrastructure
2. Bootstrap Flux
3. Create `sops-age` secret
4. Flux reconciles all resources
5. PostgreSQL clusters auto-restore from backups

## Important Notes

### Secrets Security

- **NEVER commit private age keys** — live in `~/.config/sops/age/keys.txt` (outside repo), Proton Pass as backup
- **Verify encryption** before committing secrets (check `data`/`stringData` fields encrypted)
- **Use correct environment key** - staging and production have separate keys
- **Only `data` and `stringData` fields encrypted** - metadata stays readable

### Deployment Timing

- Git polled every minute; Kustomizations reconcile every minute (flux-system one every 10)
- Manual reconciliation instant: `flux reconcile kustomization flux-system`
- Some resources depend on others - check `dependsOn` in Kustomization files; all Kustomizations use `wait: true`, so dependents wait for dependencies to be *healthy*, not just applied
- Infra must be ready before apps deploy

### Resource Constraints

- **Production**: ARM64 Raspberry Pi CM5, 16GB RAM per node
- Resource requests/limits tuned for ARM64 architecture
- Longhorn storage limited to 512GB NVMe per node
- Monitor resource usage via Grafana: https://grafana.ronaldlokers.nl

### Renovate Automation

- Runs hourly as CronJob in cluster
- Creates PRs for dependency updates (Helm charts, container images)
- Review PRs before merging - some updates may break compatibility
- Config in `renovate.json` at repo root

### Common Gotchas

1. **SOPS decryption failures**: Ensure `sops-age` secret exists in `flux-system` namespace with correct key
2. **Kustomization not ready**: Check `dependsOn` - dependent resources won't reconcile until dependencies healthy
3. **Helm release failures**: Check Helm controller logs and HelmRelease status for version/values issues
4. **Certificate issues**: Verify Cloudflare API token has DNS edit permissions
5. **Storage issues (production)**: Check Longhorn dashboard for replica health and disk space
6. **NFS mount failures on Debian Trixie**: See `docs/war-stories/nfs-debian-trixie.md` for systemd mount workaround

## Documentation

See `docs/` dir for full docs:
- **setup.md**: Cluster setup procedures
- **architecture.md**: Detailed infra architecture
- **security.md**: Secrets management guide
- **network-security.md**: Network security policies
- **stack/**: Component-specific docs
- **runbooks/**: Step-by-step operational procedures
- **war-stories/**: Real-world troubleshooting experiences (highly recommended reading)