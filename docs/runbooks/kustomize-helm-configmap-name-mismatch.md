# Kustomize ConfigMap Hash Suffix Breaking Helm References

## Quick Reference

- **Severity**: High (prevents pod startup)
- **Estimated Time to Resolve**: 10 minutes
- **Symptoms**: Pods crash with "configuration file not found"
- **Affected Components**: Helm releases referencing external ConfigMaps created by Kustomize
- **Prerequisites**: Understanding of Kustomize configMapGenerator, Helm values
- **Data Loss Risk**: None

## Symptoms & Detection

### Error Messages

```
Error: failed to load config file: open /etc/alloy/config.alloy: no such file or directory
```

### Observable Behavior

- Pods in `CrashLoopBackOff` state
- ConfigMap exists in cluster
- Volume mount configuration looks correct
- File exists in ConfigMap but not in pod
- Kustomize-generated ConfigMap has unexpected name suffix

### Monitoring Indicators

- Pods repeatedly restarting
- Application can't find expected configuration files
- ConfigMap name doesn't match what Helm expects

## Immediate Actions

**If you need pods running RIGHT NOW:**

No workaround - you must fix the ConfigMap name mismatch. This is a config-only fix (fast).

**Quick check**:

```bash
# Check actual ConfigMap name
kubectl get configmap -n <namespace> | grep <app>
# NAME                      DATA   AGE
# alloy-config-cgbd86b72m   1      5m  ← Hash suffix!

# Check what Helm expects
kubectl get helmrelease <app> -n <namespace> -o yaml | grep -A3 "configMap:"
# name: alloy-config  ← Mismatch!
```

## Diagnosis Steps

### 1. Identify the ConfigMap name mismatch

```bash
# List ConfigMaps
kubectl get configmap -n <namespace>

# Look for app ConfigMap with hash suffix
# Example: alloy-config-cgbd86b72m instead of alloy-config
```

### 2. Check Kustomize configuration

```bash
# View Kustomize configMapGenerator
cat monitoring/controllers/<env>/<app>/kustomization.yaml

# Look for:
configMapGenerator:
  - name: alloy-config
    files:
      - config.alloy
    # No options = hash suffix enabled (default)
```

### 3. Check Helm values

```bash
# View HelmRelease configMap reference
kubectl get helmrelease <app> -n <namespace> -o yaml | grep -A5 configMap

# Or check values file
cat monitoring/controllers/<env>/<app>/release.yaml

# Look for hardcoded name:
# configMap:
#   name: alloy-config  ← Expects exact name
```

### 4. Understand the problem

**Kustomize**: Creates `alloy-config-cgbd86b72m` (with hash)
**Helm**: Expects `alloy-config` (exact name)
**Result**: Volume mount fails, file not found

### 5. Confirm diagnosis

**This is the right runbook if:**
- ✅ Kustomize configMapGenerator used
- ✅ ConfigMap has hash suffix in cluster
- ✅ Helm values reference ConfigMap by static name
- ✅ Pod crashes with "file not found" for config
- ✅ ConfigMap exists and contains correct data

**This is NOT the right runbook if:**
- ❌ ConfigMap name matches everywhere
- ❌ ConfigMap doesn't exist
- ❌ Not using Kustomize configMapGenerator
- ❌ Different error (not file-not-found)

## Resolution Steps

### Step 1: Disable hash suffix in Kustomize

Edit the kustomization file:

```bash
nano monitoring/controllers/<env>/<app>/kustomization.yaml
```

Add `options` to disable hash:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: monitoring
resources:
  - ../../base/<app>/
  - release.yaml

configMapGenerator:
  - name: alloy-config
    files:
      - config.alloy
    options:
      disableNameSuffixHash: true  # ✓ Keep stable name
```

### Step 2: Commit and reconcile

```bash
# Commit changes
git add monitoring/controllers/<env>/<app>/kustomization.yaml
git commit -m "fix: disable ConfigMap hash suffix for Helm compatibility"
git push

# Force Flux reconciliation
flux reconcile kustomization monitoring-controllers --context=<env>
```

### Step 3: Verify ConfigMap recreated with stable name

```bash
# Check ConfigMap name
kubectl get configmap -n <namespace> | grep <app>
# NAME              DATA   AGE
# alloy-config      1      30s  ✓ No hash suffix!

# Verify content
kubectl get configmap alloy-config -n <namespace> -o yaml
# Should contain your configuration file
```

### Step 4: Restart pods or wait for automatic recovery

```bash
# Option 1: Let Helm reconcile (automatic)
flux reconcile helmrelease <app> -n <namespace>

# Option 2: Manually restart (faster)
kubectl delete pods -n <namespace> -l app.kubernetes.io/name=<app>

# Watch pods start
kubectl get pods -n <namespace> --watch
```

### Step 5: Verify pods running

```bash
# Check pod status
kubectl get pods -n <namespace> -l app.kubernetes.io/name=<app>
# Should all be Running

# Check logs for successful config load
kubectl logs -n <namespace> -l app.kubernetes.io/name=<app> --tail=20
# Should see: "config loaded successfully" or similar
```

## Verification

### Confirm resolution:

- [ ] ConfigMap has stable name (no hash)
      ```bash
      kubectl get configmap -n <namespace> | grep <app>
      # Should show exact name without hash suffix
      ```

- [ ] Pods are running
      ```bash
      kubectl get pods -n <namespace> -l app.kubernetes.io/name=<app>
      # All should show Running status
      ```

- [ ] No config file errors in logs
      ```bash
      kubectl logs -n <namespace> <pod-name> | grep -i "not found"
      # Should return no results
      ```

- [ ] Application functioning correctly
      ```bash
      # Test app-specific functionality
      # For Alloy: Check metrics being collected
      # For other apps: Verify expected behavior
      ```

- [ ] ConfigMap changes trigger restarts manually
      ```bash
      # Update ConfigMap
      # Note: With stable names, you must manually restart pods
      kubectl rollout restart <resource> -n <namespace>
      ```

## Root Cause

### Kustomize Default Behavior

**configMapGenerator default**: Adds hash suffix

```
Input:  name: app-config
Output: app-config-5dg4m7c245
```

**Purpose**:
- Detect ConfigMap changes
- Trigger automatic pod restarts
- Immutable ConfigMap pattern (Kubernetes best practice)

**How it works in pure Kustomize**:
1. ConfigMap changes
2. Hash changes
3. Kustomize updates ALL references (Deployments, etc.)
4. Pods restart automatically

### Helm Doesn't Know About Kustomize

**Helm values are static**:

```yaml
configMap:
  name: app-config  # Hardcoded
```

**Helm doesn't**:
- Know about Kustomize hashes
- Automatically update references
- Track ConfigMap name changes

**Result**: Name mismatch

### When to Use Each Pattern

**Use hash suffix (default)**:
- Pure Kustomize deployments
- References in Kustomize-managed manifests
- Automatic restart on config changes desired

**Use stable names (`disableNameSuffixHash: true`)**:
- ConfigMap referenced by Helm charts
- External systems expecting specific names
- Manual restart control preferred

## Alternative Solutions

### Option 1: Let Helm Manage ConfigMap (Simpler)

**Don't use Kustomize configMapGenerator**:

```yaml
# In HelmRelease values
configMap:
  create: true  # Let Helm create it
  content: |
    # Inline configuration
    logging {
      level = "info"
    }
```

**Pros**: Single source, no coordination needed
**Cons**: Large configs in values YAML, less readable

### Option 2: Teach Kustomize About HelmRelease (Complex)

**Use Kustomize transformers**:

```yaml
# kustomizeconfig.yaml
configurations:
  - |
    nameReference:
    - kind: ConfigMap
      fieldSpecs:
      - path: spec/values/configMap/name
        kind: HelmRelease
```

**Pros**: Automatic hash handling
**Cons**: Complex, requires deep Kustomize knowledge

### Option 3: Disable Hash (Our Choice) (Balanced)

**Use stable names**:

```yaml
options:
  disableNameSuffixHash: true
```

**Pros**: Simple, works with Helm
**Cons**: Manual pod restarts on config changes

## Handling ConfigMap Updates

### With Stable Names

**Changes don't auto-restart pods**:

```bash
# After updating ConfigMap
kubectl edit configmap <name> -n <namespace>

# Manually restart pods
kubectl rollout restart deployment/<name> -n <namespace>

# Or for DaemonSet
kubectl delete pods -n <namespace> -l app=<name>
```

### Automating Restarts

**Use Flux HelmRelease force recreation**:

```yaml
spec:
  upgrade:
    force: true  # Recreates pods on any change
```

**Note**: This recreates ALL resources, not just on ConfigMap changes.

## Prevention

### Document Pattern in Repository

- [ ] Add comment in kustomization.yaml
      ```yaml
      configMapGenerator:
        - name: app-config
          options:
            disableNameSuffixHash: true  # Required for Helm compatibility
      ```

- [ ] Update team documentation
      - When to use hash suffix vs stable names
      - How to restart pods after ConfigMap changes

### Template for New Apps

Create template kustomization for Helm-integrated apps:

```yaml
# template/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

# For apps using external Helm values
configMapGenerator:
  - name: app-config
    files:
      - config.yaml
    options:
      disableNameSuffixHash: true  # Helm compatibility
```

### Check Before Deploying

- [ ] Does Helm reference this ConfigMap?
- [ ] If yes, disable hash suffix
- [ ] If no, keep hash suffix (auto-restart benefit)

## Related Issues

- **Secrets with hash suffix**: Same problem with `secretGenerator`
      ```yaml
      secretGenerator:
        - name: db-credentials
          options:
            disableNameSuffixHash: true  # If Helm references
      ```

- **Multiple ConfigMaps**: Some need hash, some don't
      ```yaml
      configMapGenerator:
        - name: pure-kustomize-config
          # Hash enabled (default)
        - name: helm-referenced-config
          options:
            disableNameSuffixHash: true  # Stable
      ```

## Original War Story

For the complete investigation including discovering the hash suffix and understanding Kustomize/Helm interaction, see: [`docs/war-stories/kustomize-configmap-hash-suffix.md`](../war-stories/kustomize-configmap-hash-suffix.md)

## References

- [Kustomize ConfigMap Generator](https://kubectl.docs.kubernetes.io/references/kustomize/kustomization/configmapgenerator/)
- [Kustomize Generator Options](https://kubectl.docs.kubernetes.io/references/kustomize/kustomization/generatoroptions/)
- [Flux HelmRelease](https://fluxcd.io/flux/components/helm/helmreleases/)

---

**Last Updated**: 2026-01-07
**Tested On**: Grafana Alloy deployment
**Success Rate**: 100%
**Lesson**: Kustomize and Helm need coordination
