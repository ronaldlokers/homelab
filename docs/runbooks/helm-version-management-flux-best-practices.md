# Helm Chart Version Management with Flux Best Practices

## Quick Reference

- **Severity**: Low (architectural/maintainability issue)
- **Estimated Time to Resolve**: 30-60 minutes
- **Symptoms**: Can't test different versions per environment, configuration sprawl
- **Affected Components**: Any Helm-deployed application with staging/production
- **Prerequisites**: Understanding of Flux, Kustomize overlays, Helm
- **Data Loss Risk**: None (configuration refactor)

## Symptoms & Detection

### Configuration Anti-Patterns

**Version lock-in**:
- Can only update chart/image versions globally
- Staging and production must run identical versions
- Can't test new versions safely

**Configuration sprawl**:
- Separate Ingress files per environment
- Duplicate configuration
- Not using Helm chart's built-in features

**Broken ingress after updates**:
- Ingress created for wrong services
- Multiple ingresses when expecting one

### Observable Behavior

- Wanting to test v2.x in staging while production runs v1.x
- Can't find where versions are defined
- Ingress configuration scattered across multiple files
- Helm chart version changes affect both environments simultaneously

## Immediate Actions

**This isn't an urgent incident** - it's a maintainability and best-practice issue.

**However, if you need to test different versions per environment RIGHT NOW:**

You'll need to refactor to follow Flux best practices (see Resolution Steps).

## Diagnosis Steps

### 1. Check where versions are defined

```bash
# Look in base directory
grep -r "version:" apps/base/<app>/

# If found in base, that's an anti-pattern
```

```bash
# Check for chart version
grep -r "chart:" apps/base/<app>/ | grep version

# Check for image tags
grep -r "tag:" apps/base/<app>/
```

**Anti-pattern**: Versions in `apps/base/`
**Best practice**: Versions in `apps/<env>/` overlays

### 2. Check for external Ingress resources

```bash
# Look for separate ingress files
ls apps/staging/<app>/ingress.yaml
ls apps/production/<app>/ingress.yaml

# Check if listed in kustomization
cat apps/staging/<app>/kustomization.yaml | grep ingress.yaml
```

**Anti-pattern**: Separate `ingress.yaml` files
**Best practice**: Ingress in Helm values

### 3. Check Helm chart capabilities

```bash
# Get Helm chart documentation
helm show readme <chart-repo>/<chart-name>

# Or download and view values
helm show values <chart-repo>/<chart-name> > /tmp/values.yaml
less /tmp/values.yaml

# Look for:
# - ingress section
# - server.ingress or similar
```

**Most Helm charts** provide ingress configuration - use it!

### 4. Confirm diagnosis

**This is the right runbook if:**
- ✅ Chart version and/or image tags in base directory
- ✅ Want to test different versions per environment
- ✅ Have separate Ingress YAML files instead of using Helm
- ✅ Recently had ingress issues after version changes
- ✅ Using Flux with Helm

**This is NOT the right runbook if:**
- ❌ Using pure Kubernetes manifests (not Helm)
- ❌ Single environment only
- ❌ Don't need version independence per environment

## Resolution Steps

### Step 1: Review Flux best practices

Read the official example:
- [Flux Multi-Env Example](https://github.com/fluxcd/flux2-kustomize-helm-example)

**Key principles**:
- Base = structure (WHAT to deploy)
- Overlays = specifics (HOW to deploy per environment)
- Versions belong in overlays

### Step 2: Move versions from base to overlays

**Before** (`apps/base/<app>/release.yaml`):

```yaml
spec:
  chart:
    spec:
      chart: immich
      version: "0.10.3"  # ❌ Remove this
  values:
    controllers:
      main:
        containers:
          main:
            image:
              tag: v1.122.3  # ❌ Remove this
```

**After** (base):

```yaml
spec:
  chart:
    spec:
      chart: immich
      # No version - provided by overlays
  values:
    controllers:
      main:
        containers:
          main:
            # No image tag - provided by overlays
```

**Staging overlay** (`apps/staging/<app>/values-patch.yaml`):

```yaml
spec:
  chart:
    spec:
      version: "0.10.3"  # ✓ Test new versions here
  values:
    controllers:
      main:
        containers:
          main:
            image:
              tag: v2.3.1  # ✓ Can test bleeding edge
```

**Production overlay** (`apps/production/<app>/values-patch.yaml`):

```yaml
spec:
  chart:
    spec:
      version: "0.10.3"  # ✓ Stable version
  values:
    controllers:
      main:
        containers:
          main:
            image:
              tag: v2.0.0  # ✓ Proven stable
```

### Step 3: Move Ingress to Helm values

**Check Helm chart structure**:

```bash
helm show values <chart-repo>/<chart-name> | less
# Look for ingress configuration options
```

**For Immich example**:

```yaml
# apps/<env>/<app>/values-patch.yaml
spec:
  values:
    server:  # ✓ Under the component that needs ingress
      ingress:
        main:
          enabled: true
          className: traefik
          annotations:
            cert-manager.io/cluster-issuer: letsencrypt-production
            traefik.ingress.kubernetes.io/router.middlewares: kube-system-https-redirect@kubernetescrd
          hosts:
            - host: immich.ronaldlokers.nl
              paths:
                - path: /
                  pathType: Prefix
                  service:
                    identifier: main
                    port: 2283
          tls:
            - secretName: immich-tls
              hosts:
                - immich.ronaldlokers.nl
```

### Step 4: Remove external Ingress files

```bash
# Remove from kustomization
nano apps/<env>/<app>/kustomization.yaml
# Delete: - ingress.yaml

# Delete files
rm apps/staging/<app>/ingress.yaml
rm apps/production/<app>/ingress.yaml
```

### Step 5: Commit and reconcile

```bash
# Add all changes
git add apps/

# Commit
git commit -m "refactor: follow Flux best practices for version management

- Move chart versions to environment overlays
- Move image tags to overlays
- Migrate ingress to Helm values
- Remove external ingress files

This enables:
- Testing new versions in staging before production
- Environment-specific version control
- Cleaner configuration management"

git push

# Reconcile staging first
flux reconcile kustomization apps --context=staging

# Verify staging works
kubectl get helmrelease -n <namespace> --context=staging
kubectl get pods -n <namespace> --context=staging
kubectl get ingress -n <namespace> --context=staging

# Then reconcile production
flux reconcile kustomization apps --context=production
```

### Step 6: Verify improved workflow

**Test version independence**:

```bash
# Update staging to new version
nano apps/staging/<app>/values-patch.yaml
# Change: version: "0.11.0"

git commit -am "test: upgrade <app> to 0.11.0 in staging"
git push
flux reconcile helmrelease <app> -n <namespace> --context=staging

# Production remains on old version
kubectl get helmrelease <app> -n <namespace> --context=production
# Still shows old version
```

## Verification

### Confirm refactor successful:

- [ ] No versions in base directory
      ```bash
      grep -r "version:" apps/base/<app>/ | grep -v "apiVersion"
      # Should return no chart version results
      ```

- [ ] Versions present in overlays
      ```bash
      grep "version:" apps/staging/<app>/values-patch.yaml
      grep "version:" apps/production/<app>/values-patch.yaml
      # Both should show chart versions
      ```

- [ ] No external ingress files
      ```bash
      ls apps/staging/<app>/ingress.yaml 2>/dev/null
      # Should show: No such file or directory
      ```

- [ ] Ingress in Helm values
      ```bash
      grep -A10 "ingress:" apps/<env>/<app>/values-patch.yaml
      # Should show ingress configuration
      ```

- [ ] Correct ingress created
      ```bash
      kubectl get ingress -n <namespace>
      # Should show only expected ingress(es), not ML/valkey/etc.
      ```

- [ ] Can test version differences
      ```bash
      # Change staging version
      # Verify production unaffected
      ```

## Benefits Achieved

### Version Management in Overlays

**Enables**:
- ✅ Test v2.x in staging, keep v1.x in production
- ✅ Gradual rollout: staging → verify → production
- ✅ Quick rollback: just change overlay version
- ✅ Clear version history in Git (per environment)
- ✅ Independent upgrade cycles

**Example workflow**:
```bash
# Week 1: Test new version in staging
apps/staging/<app>/values-patch.yaml: version: "2.0.0"

# Week 2: Verify in staging, promote to production
apps/production/<app>/values-patch.yaml: version: "2.0.0"

# Week 3: Already testing next version in staging
apps/staging/<app>/values-patch.yaml: version: "2.1.0"
```

### Helm-Managed Ingress

**Advantages**:
- ✅ Single source of truth (values.yaml)
- ✅ Automatic cleanup when release deleted
- ✅ Consistent with Helm patterns
- ✅ Easier templating (Helm handles it)
- ✅ Version controlled with release

**File reduction**:
- Before: 7 files (base + 2 envs × 3 files each)
- After: 3 files (base + 2 overlay patches)

## Prevention

### New Application Checklist

When adding new Helm-deployed apps:

- [ ] Check if chart provides ingress management
- [ ] Put chart version in overlay patches, not base
- [ ] Put image tags in overlays if need per-env control
- [ ] Review chart values.yaml to understand structure
- [ ] Don't create external Ingress unless necessary
- [ ] Test configuration changes in staging first

### Understand Chart Structure

**Before using a Helm chart**:

```bash
# Download chart documentation
helm show readme <chart-repo>/<chart-name>

# Review all available values
helm show values <chart-repo>/<chart-name> | less

# Understand component hierarchy
# - Where does ingress config go?
# - What's configurable per-component?
# - What defaults can be overridden?
```

### Repository Structure

**Follow this pattern**:

```
apps/
├── base/
│   └── <app>/
│       ├── kustomization.yaml
│       ├── namespace.yaml
│       └── release.yaml         # NO versions, NO env specifics
├── staging/
│   └── <app>/
│       ├── kustomization.yaml
│       └── values-patch.yaml    # ✓ Versions, env-specific config
└── production/
    └── <app>/
        ├── kustomization.yaml
        └── values-patch.yaml    # ✓ Versions, env-specific config
```

## Common Mistakes

### Putting Env-Specific Config in Base

**Wrong**:
```yaml
# apps/base/<app>/release.yaml
spec:
  values:
    ingress:
      hosts:
        - host: app.example.com  # ❌ Production domain in base!
```

**Right**:
```yaml
# apps/base/<app>/release.yaml
# No ingress config

# apps/production/<app>/values-patch.yaml
spec:
  values:
    ingress:
      hosts:
        - host: app.example.com  # ✓ In production overlay
```

### Creating External Resources for Helm-Managed Features

**Wrong**:
```yaml
# apps/staging/<app>/ingress.yaml  ❌ Separate file
apiVersion: networking.k8s.io/v1
kind: Ingress
```

**Right**:
```yaml
# apps/staging/<app>/values-patch.yaml
spec:
  values:
    server:
      ingress:  # ✓ In Helm values
        enabled: true
```

### Not Understanding Values Hierarchy

**Wrong** (ingress at root level):
```yaml
spec:
  values:
    ingress:  # ❌ Applied to all components!
      enabled: true
```

**Right** (ingress under specific component):
```yaml
spec:
  values:
    server:  # ✓ Only server component
      ingress:
        enabled: true
```

## Related Issues

- **Helm upgrade failures**: Trying to patch immutable fields
- **Configuration drift**: Staging and production diverge unintentionally
- **Difficult rollbacks**: Can't easily revert to previous versions

## Original War Story

For the complete investigation including the ingress hierarchy confusion and discovering Flux best practices, see: [`docs/war-stories/immich-helm-migration.md`](../war-stories/immich-helm-migration.md)

## References

- [Flux Multi-Env Example](https://github.com/fluxcd/flux2-kustomize-helm-example)
- [Flux Repository Structure Guide](https://fluxcd.io/flux/guides/repository-structure/)
- [Helm Chart Best Practices](https://helm.sh/docs/chart_best_practices/)
- [Kustomize Best Practices](https://kubectl.docs.kubernetes.io/guides/config_management/components/)

---

**Last Updated**: 2026-01-07
**Tested On**: Immich Helm deployment (but applies to all Helm apps)
**Success Rate**: 100%
**Impact**: Improved maintainability, safer version testing
