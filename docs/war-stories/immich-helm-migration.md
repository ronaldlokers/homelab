# Immich: Helm Chart Configuration and Following Best Practices

**Date**: December 2025
**Environment**: Both staging and production clusters
**Impact**: Configuration sprawl, version management confusion, broken ingress

## The Problem

Initial Immich deployment had several issues:

1. **Version management chaos**: Chart version and image tag defined only in base, no way to test versions independently per cluster
2. **Separate Ingress resources**: Created standalone Ingress YAML files instead of using Helm chart's built-in ingress management
3. **Broken ingress after Helm update**: After fixing version management, ingress broke - wrong services were exposed

## The Investigation

### Issue 1: Version Management

**Symptom**: Could only update Immich version in `apps/base/immich/release.yaml`, affecting both staging and production simultaneously.

Checked the Flux documentation for best practices:
- [Official Flux multi-env example](https://github.com/fluxcd/flux2-kustomize-helm-example)
- Shows versions should be in **overlay patches**, not base

**Why this matters**:
- Can't test new Immich versions in staging before production
- Staging and production must always run the same version
- Violates the whole point of having a staging environment

### Issue 2: Separate Ingress Resources

**Initial setup**:
```yaml
# apps/staging/immich/ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: immich
spec:
  # ... ingress configuration
```

**Problems**:
- Duplication between staging and production
- Ingress configuration separate from Helm values
- Harder to maintain consistency
- Helm chart already provides ingress management

Checked Immich Helm chart values.yaml:
```yaml
server:
  ingress:
    main:
      enabled: false  # We had this disabled!
```

**Discovery**: The Helm chart has ingress configuration built-in, we just weren't using it.

### Issue 3: Broken Ingress After Migration

After moving ingress to Helm values, reconciling created ingress for **wrong services**:

```bash
kubectl get ingress -n immich
NAME                      HOSTS
immich-machine-learning   immich.ronaldlokers.nl   # ❌ Wrong!
immich-valkey             immich.ronaldlokers.nl   # ❌ Wrong!
```

Expected only `immich-server` ingress.

**Investigation**:
- Checked where we put the ingress config in values
- Initially added it at root level under `ingress:`
- Helm chart applied it to ALL components (server, ML, valkey)

Looked at Helm chart structure:
```yaml
server:        # Server component
  ingress:     # Ingress for server
    main:
      enabled: true

machine-learning:  # ML component
  ingress:         # Would create ingress for ML
    main:
      enabled: false
```

**Root cause**: Placed ingress configuration at wrong level in values hierarchy.

## The Root Cause

Three separate configuration anti-patterns:

1. **Versions in base instead of overlays**: Violated Flux best practices
2. **External resources instead of Helm values**: Didn't use Helm chart's features
3. **Incorrect values hierarchy**: Misunderstood Helm chart structure

## The Solution

### Fix 1: Move Versions to Overlays

**Before** (`apps/base/immich/release.yaml`):
```yaml
spec:
  chart:
    spec:
      chart: immich
      version: "0.10.3"  # ❌ Fixed version in base
  values:
    image:
      tag: v1.122.3      # ❌ Fixed tag in base
```

**After** - Base has NO versions:
```yaml
spec:
  chart:
    spec:
      chart: immich
      # No version - provided by overlays
  values:
    # No image tag - provided by overlays
```

**Staging overlay** (`apps/staging/immich/values-patch.yaml`):
```yaml
spec:
  chart:
    spec:
      version: "0.10.3"
  values:
    controllers:
      main:
        containers:
          main:
            image:
              tag: v2.3.1  # Can test newer version
```

**Production overlay** (`apps/production/immich/values-patch.yaml`):
```yaml
spec:
  chart:
    spec:
      version: "0.10.3"
  values:
    controllers:
      main:
        containers:
          main:
            image:
              tag: v2.0.0  # Stable version
```

### Fix 2: Use Helm Chart Ingress

**Before**: Separate files
- `apps/staging/immich/ingress.yaml`
- `apps/production/immich/ingress.yaml`
- Listed in `kustomization.yaml` resources

**After**: In values-patch.yaml
```yaml
spec:
  values:
    server:  # ✓ Correct: under server component
      ingress:
        main:
          enabled: true
          className: traefik
          annotations:
            cert-manager.io/cluster-issuer: letsencrypt-production
            traefik.ingress.kubernetes.io/router.middlewares: kube-system-https-redirect@kubernetescrd
            nginx.ingress.kubernetes.io/proxy-body-size: "0"
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

**Cleanup**:
```bash
# Remove from kustomization.yaml
# - ingress.yaml

# Delete files
rm apps/staging/immich/ingress.yaml
rm apps/production/immich/ingress.yaml
```

### Fix 3: Verify and Reconcile

```bash
# Reconcile Flux
flux reconcile kustomization apps --with-source
flux reconcile helmrelease immich -n immich

# Verify correct ingress
kubectl get ingress -n immich
# NAME            HOSTS
# immich-server   immich.ronaldlokers.nl  # ✓ Correct!
```

## Benefits of Following Best Practices

### Version Management in Overlays

**Enables**:
- Test v2.3.1 in staging while production runs stable v2.0.0
- Gradual rollout: staging → verify → production
- Quick rollback: just change version in overlay
- Clear version history in Git

**Example workflow**:
1. Update staging to new version
2. Test thoroughly
3. Update production to same version
4. Rollback staging to even newer version for next test

### Helm-Managed Ingress

**Advantages**:
- Single source of truth (values.yaml)
- Automatic cleanup when Helm release deleted
- Consistent with other Helm chart configurations
- Easier to template and maintain
- Version controlled with the release

**Reduced files**:
- Before: 5 files per environment (base + staging + production ingresses)
- After: 2 files per environment (base + overlay values)

## Lessons Learned

1. **Read the documentation first**: Flux example repo showed the correct pattern
2. **Use Helm chart features**: If the chart provides it, use it instead of external resources
3. **Understand value hierarchy**: Helm chart structure matters - know where to place config
4. **Follow established patterns**: There's usually a good reason for best practices
5. **Test version management early**: Don't wait until you need it to set it up
6. **One source of truth**: Configuration should live in one place, not scattered

## Following Flux Best Practices

According to [Flux multi-env example](https://github.com/fluxcd/flux2-kustomize-helm-example):

**Base should contain**:
- Resource structure (what to deploy)
- Common configuration (shared across environments)
- NO specific versions or environment details

**Overlays should contain**:
- Chart versions (enables per-env version management)
- Image tags (if overriding chart defaults)
- Environment-specific values (replicas, resources, domains)
- Secrets (with appropriate encryption)

**Why this matters**:
- Enables safe testing in lower environments
- Reduces configuration duplication
- Makes differences between environments explicit
- Supports gradual rollouts and staged deployments

## Timeline

- **Initial deployment**: Versions in base, separate ingress files
- **Realization**: Needed to test Immich v2.3.1 in staging only
- **Research**: Found Flux best practices and Helm chart documentation
- **Migration**: Moved versions to overlays, ingress to Helm values
- **Issue**: Ingress created for wrong services
- **Fix**: Corrected values hierarchy (under `server:`)
- **Validation**: Confirmed correct ingress, tested version differences
- **Time**: ~1 hour total (including research and documentation)

## Prevention Checklist

When deploying new Helm-based applications:

- [ ] Check if Helm chart provides ingress management
- [ ] Put chart versions in overlay patches, not base
- [ ] Put image tags in overlays if you need per-env control
- [ ] Review Helm chart values.yaml to understand structure
- [ ] Test configuration changes in staging first
- [ ] Verify resources created match expectations
- [ ] Document any deviations from standard patterns

## References

- [Flux Multi-Env Example](https://github.com/fluxcd/flux2-kustomize-helm-example)
- [Immich Helm Chart Values](https://github.com/immich-app/immich-charts/blob/main/charts/immich/values.yaml)
- [Flux Repository Structure Guide](https://fluxcd.io/flux/guides/repository-structure/)
- [Helm Chart Best Practices](https://helm.sh/docs/chart_best_practices/)
