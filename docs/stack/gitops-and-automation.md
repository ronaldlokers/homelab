# GitOps & Automation

This document covers the tools used for GitOps and automation in the homelab.

## Flux CD

[Flux](https://fluxcd.io/) is the GitOps continuous delivery tool that manages the entire cluster state.

**Version**: managed by Renovate (see `clusters/*/flux-system/`)

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
- **Sync interval**: Git is polled every minute; Kustomizations reconcile every minute (the flux-system one every 10)
- **Retry behavior**: Automatic retries with exponential backoff
- **Prune**: Removes resources deleted from Git
- **Health checks**: All Kustomizations set `wait: true` — dependents (`dependsOn`) wait for dependencies to be *healthy*, not just applied

**Alerting**: `Provider`/`Alert` resources in `infrastructure/configs/*/flux-alerts/` send error-severity events (failed reconciliations, unhealthy rollouts) from notification-controller to ntfy — production via the in-cluster service, staging via the public URL. Titles are templated with ntfy's inline templating (`?tpl=yes`).

**Reconciliation**:
```bash
# Manually trigger reconciliation
flux reconcile kustomization flux-system --context=production

# Check status
flux get kustomizations --context=production
flux get helmreleases --context=production
```

## Renovate

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
- Configured via `renovate.json` at the repository root
- The `flux` manager handles HelmRelease chart versions; the `kubernetes` and `kustomize` managers handle image tags (all versions are pinned per environment in the overlays, with base pins as fallbacks)
- Creates PRs for dependency updates

**How it works**:
1. CronJob triggers every hour
2. Renovate scans the repository for dependencies
3. Checks for newer versions
4. Creates pull requests with updates
5. PRs include changelogs and release notes

**Workflow**:
1. Renovate creates PR
2. CI validates and renders the change (see below)
3. Review changes (optionally test in staging)
4. Merge PR
5. Flux automatically applies changes to the cluster

## CI Validation

Two GitHub Actions workflows gate every PR:

- **`validate.yaml`** runs `scripts/validate.sh`: `kustomize build` on every overlay piped through [kubeconform](https://github.com/yannh/kubeconform) with Kubernetes, Flux, and CRD-catalog schemas (SOPS-encrypted Secrets are skipped). Also runnable locally before pushing.
- **`flux-diff.yaml`** renders the full Flux tree (Kustomizations *and* HelmReleases, with real charts) for both the PR and base branch using [flate](https://github.com/home-operations/flate), runs `flate test all` on the PR tree, and posts the rendered diff as a sticky PR comment per cluster. This is what catches breaking chart upgrades — e.g. values-schema changes — before merge. It deliberately renders full-tree rather than using flate's changed-only mode, which misses edits under `apps/base/**` (flate ≤0.4.x); a base-branch render failure is non-fatal so a broken main never blocks the PR that fixes it.
