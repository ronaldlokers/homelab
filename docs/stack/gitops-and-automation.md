# GitOps & Automation

This document covers the tools used for GitOps and automation in the homelab.

## Flux CD

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
