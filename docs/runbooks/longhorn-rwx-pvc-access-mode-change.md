# Longhorn ReadWriteMany PVC Access Mode Change

## Quick Reference

- **Severity**: Medium (blocks horizontal scaling)
- **Estimated Time to Resolve**: 10 minutes
- **Symptoms**: Second replica pod stuck in `ContainerCreating` with "Multi-Attach error"
- **Affected Components**: Applications with HorizontalPodAutoscaler using shared volumes
- **Prerequisites**: Understanding of PVC immutability, backup if volume contains data
- **Data Loss Risk**: ⚠️ HIGH - PVC deletion destroys data (acceptable for caches)

## Symptoms & Detection

### Error Messages

```
Warning  FailedAttachVolume  Multi-Attach error for volume "pvc-xxx"
Volume is already used by pod(s) immich-machine-learning-649544d5df-mcsjm
```

### Observable Behavior

- HPA creates additional replicas
- New pods stuck in `ContainerCreating`
- First pod runs fine
- Additional pods on different nodes cannot attach same PVC
- `kubectl get pods` shows mixed states:
  - 1 pod: `Running` (first)
  - N pods: `ContainerCreating` (additional)

### Monitoring Indicators

- HPA shows desired replicas > current replicas
- Pod events show "Multi-Attach error"
- Volume attachment errors in node events

## Immediate Actions

**If you need horizontal scaling RIGHT NOW:**

There's no quick workaround - you must recreate the PVC with correct access mode.

**Before proceeding, determine**:

1. **Is this a cache or critical data?**
   ```bash
   # Check what's in the volume
   kubectl exec <running-pod> -n <namespace> -- ls -lh /mount/path
   ```

   - **Cache/temporary data**: Proceed (safe to lose)
   - **Critical data**: STOP - back up first (see Data Loss Considerations section)

2. **Can application tolerate brief downtime?**
   - Yes: Proceed
   - No: Plan maintenance window

## Diagnosis Steps

### 1. Confirm it's an access mode issue

Check pod events:

```bash
# Get pod details
kubectl describe pod <stuck-pod> -n <namespace>

# Look for:
# Warning  FailedAttachVolume  Multi-Attach error
```

### 2. Check current PVC access mode

```bash
kubectl get pvc <pvc-name> -n <namespace> -o jsonpath='{.spec.accessModes}'
# Output: ["ReadWriteOnce"]  ← Problem for multi-replica

# Also check which pods are using it
kubectl get pods -n <namespace> -o json | jq -r '.items[] | select(.spec.volumes[]?.persistentVolumeClaim.claimName=="<pvc-name>") | .metadata.name'
```

### 3. Understand why RWX is needed

```bash
# Check if HPA exists
kubectl get hpa -n <namespace>

# Check deployment replica count
kubectl get deployment <deployment-name> -n <namespace> -o jsonpath='{.spec.replicas}'
```

**Why ReadWriteMany?**
- Horizontal scaling requires multiple pods
- Pods may be scheduled on different nodes
- RWO only allows one node to mount at a time
- RWX allows multiple nodes to mount simultaneously

### 4. Confirm diagnosis

**This is the right runbook if:**
- ✅ PVC has `accessModes: [ReadWriteOnce]`
- ✅ Multiple pods need to use the same PVC
- ✅ Pods are on different nodes
- ✅ Error message mentions "Multi-Attach" or "already used by"
- ✅ Using Longhorn (supports RWX)

**This is NOT the right runbook if:**
- ❌ PVC already has ReadWriteMany
- ❌ All pods on same node (RWO works for same-node multi-pod)
- ❌ Storage class doesn't support RWX
- ❌ Different error (not multi-attach)

## Resolution Steps

### Step 1: Update Helm values to use ReadWriteMany

Edit your values patch file:

```yaml
# apps/production/<app>/values-patch.yaml
spec:
  values:
    <component>:
      persistence:
        <volume-name>:
          storageClass: longhorn
          accessMode: ReadWriteMany  # ← Change from ReadWriteOnce
          size: 10Gi
```

**Commit but don't apply yet** - we need to delete PVC first:

```bash
git add apps/production/<app>/values-patch.yaml
git commit -m "fix: change <volume> to ReadWriteMany for horizontal scaling"
# Don't push yet - we need to coordinate timing
```

### Step 2: Scale down to zero to release PVC

```bash
# Scale deployment to 0
kubectl scale deployment <deployment-name> --replicas=0 -n <namespace>

# Wait for all pods to terminate
kubectl get pods -n <namespace> --watch

# Verify no pods running
kubectl get pods -n <namespace> | grep <deployment-name>
# Should return no results
```

### Step 3: Delete the PVC

⚠️ **WARNING**: This deletes all data in the volume!

```bash
# Delete PVC
kubectl delete pvc <pvc-name> -n <namespace>

# If it hangs, check for finalizers
kubectl get pvc <pvc-name> -n <namespace> -o yaml | grep finalizers
# May need to wait for volume to detach
```

**If PVC won't delete** (stuck in Terminating):

```bash
# Check what's holding it
kubectl describe pvc <pvc-name> -n <namespace>

# Last resort - remove finalizer (dangerous!)
kubectl patch pvc <pvc-name> -n <namespace> -p '{"metadata":{"finalizers":null}}'
```

### Step 4: Push config and reconcile

```bash
# Push Git changes
git push

# Force Flux to reconcile
flux reconcile kustomization apps --context=production

# Or specific HelmRelease
flux reconcile helmrelease <app-name> -n <namespace>
```

Flux will:
1. Apply new Helm values with RWX access mode
2. Helm creates new PVC with ReadWriteMany
3. Deployment scales back up (if autoscaling) or stays at 0

### Step 5: Verify new PVC created with RWX

```bash
# Check PVC access mode
kubectl get pvc <pvc-name> -n <namespace> -o yaml | grep -A2 accessModes
# Should show:
#   accessModes:
#   - ReadWriteMany

# Check new PVC ID
kubectl get pvc <pvc-name> -n <namespace> -o jsonpath='{.metadata.uid}'
# Will be different from old PVC

# Check Longhorn created share-manager
kubectl get pods -n longhorn-system | grep share-manager | grep <pvc-uid>
# Should see running share-manager pod
```

### Step 6: Scale back up and verify

```bash
# Scale deployment back up (if not using HPA)
kubectl scale deployment <deployment-name> --replicas=2 -n <namespace>

# Or trigger HPA
# (HPA will scale automatically based on metrics)

# Watch pods start
kubectl get pods -n <namespace> --watch

# All pods should reach Running state
```

## Verification

### Confirm resolution:

- [ ] PVC shows `ReadWriteMany` access mode
      ```bash
      kubectl get pvc <pvc-name> -n <namespace> -o jsonpath='{.spec.accessModes}'
      # [ReadWriteMany]
      ```

- [ ] Longhorn share-manager pod running
      ```bash
      kubectl get pods -n longhorn-system | grep "share-manager.*<pvc-uid>"
      # Should show Running
      ```

- [ ] Multiple pods can run simultaneously
      ```bash
      kubectl get pods -n <namespace> -l <app-selector>
      # Should show multiple Running pods
      ```

- [ ] No Multi-Attach errors
      ```bash
      kubectl get events -n <namespace> --field-selector type=Warning | grep -i "multi-attach"
      # Should return no results
      ```

- [ ] HPA can scale up/down
      ```bash
      kubectl get hpa -n <namespace>
      # TARGETS should show current/target, REPLICAS should match desired
      ```

- [ ] Application functions correctly
      ```bash
      # Test app-specific functionality
      # For Immich ML example: trigger face detection job
      ```

## Root Cause

### Why PVC Access Modes Are Immutable

**Kubernetes design decision**: PVC spec fields are immutable after creation.

From Kubernetes source:
> `spec` is immutable after creation except `resources.requests` and `volumeAttributesClassName` for bound claims

**Why?**
- Storage provisioner created volume with specific capabilities
- Changing access mode would require volume recreation
- Could lead to data loss or inconsistency
- Explicit deletion forces acknowledgment of consequences

### How Longhorn Implements ReadWriteMany

**RWO volumes** (ReadWriteOnce):
```
Pod 1 (node-1) → Direct mount → Longhorn volume
Pod 2 (node-2) → Blocked (volume attached to node-1)
```

**RWX volumes** (ReadWriteMany):
```
Longhorn volume
    ↓
share-manager pod (runs NFS-Ganesha)
    ↓
NFS export (ClusterIP service on port 2049)
    ↙         ↓           ↘
Pod 1      Pod 2       Pod 3
(node-1)   (node-2)    (node-3)
```

**Requirements for Longhorn RWX**:
1. Share-manager pod runs on one node
2. Exports volume via NFS
3. Other pods mount via NFS client
4. Requires `nfs-common` on all nodes (see related runbook)

**Performance implications**:
- RWO: Direct block access (faster)
- RWX: NFS layer (slight overhead, enables multi-node)

## Data Loss Considerations

### Caches - Safe to Delete

**Examples**:
- Machine learning model caches
- Thumbnail caches
- Compiled assets
- Temporary processing files

**Why safe?**:
- Data regenerated automatically
- No permanent information loss
- Application continues functioning

**For Immich ML cache**:
- Models re-downloaded on first use
- ~5 minutes to rebuild cache
- No photo/video data lost

### Critical Data - BACKUP FIRST

**Examples**:
- User uploads
- Database files
- Application state
- Configuration files

**Before deleting**:

1. **Longhorn snapshot**:
   ```bash
   # Via Longhorn UI or API
   # Create snapshot before deletion
   ```

2. **Backup to external storage**:
   ```bash
   # Exec into pod and copy data
   kubectl exec <pod> -n <namespace> -- tar czf /tmp/backup.tar.gz /data

   kubectl cp <namespace>/<pod>:/tmp/backup.tar.gz ./backup.tar.gz
   ```

3. **After PVC recreation, restore**:
   ```bash
   kubectl cp ./backup.tar.gz <namespace>/<new-pod>:/tmp/backup.tar.gz

   kubectl exec <new-pod> -n <namespace> -- tar xzf /tmp/backup.tar.gz -C /
   ```

## Prevention

### Plan Access Modes Upfront

**Before creating PVC**:

- [ ] Will this app need horizontal scaling?
      - Yes → Use ReadWriteMany from the start
      - No → ReadWriteOnce is fine

- [ ] Is this a cache or critical data?
      - Cache → Can change later (safe to delete)
      - Critical → Get it right first time

- [ ] Does storage class support RWX?
      ```bash
      kubectl get storageclass <class-name> -o yaml | grep -A5 allowedTopologies
      ```

### Document PVC Purpose

```yaml
# In your Helm values or manifest
persistence:
  cache:
    accessMode: ReadWriteMany  # RWX for horizontal ML scaling
    storageClass: longhorn
    size: 10Gi
    # Comment: ML model cache - safe to delete
```

### Test Scaling Early

Don't wait until production to test HPA:

```bash
# In staging, manually scale to test
kubectl scale deployment <app> --replicas=3 -n <namespace>

# Verify all pods start
kubectl get pods -n <namespace>

# If you see Multi-Attach errors, fix access mode before production
```

## Related Issues

- **nfs-common required**: Longhorn RWX needs nfs-common on all nodes
  - See: [NFS Mounts Failing on Debian Trixie](nfs-mounts-failing-debian-trixie.md)

- **HPA requires resource requests**: HPA won't work without CPU requests
  - See: [HPA Shows Unknown CPU Metrics](hpa-resource-requests-unknown.md)

- **Different storage classes**: Not all support RWX
  - Local-path: RWO only
  - Longhorn: Both RWO and RWX
  - NFS: RWX native

## Original War Story

For the complete narrative including the failed patch attempt and Helm error messages, see: [`docs/war-stories/longhorn-rwx-multi-attach.md`](../war-stories/longhorn-rwx-multi-attach.md)

## References

- [Kubernetes PersistentVolumeClaims](https://kubernetes.io/docs/concepts/storage/persistent-volumes/)
- [Access Modes](https://kubernetes.io/docs/concepts/storage/persistent-volumes/#access-modes)
- [Longhorn RWX Volumes](https://longhorn.io/docs/latest/advanced-resources/rwx-workloads/)
- [Longhorn Share Manager](https://longhorn.io/docs/latest/high-availability/rwx-workloads/)

---

**Last Updated**: 2026-01-07
**Tested On**: Production Immich deployment
**Success Rate**: 100% (data loss acceptable for cache volumes)
**Data Loss**: Expected for non-backup volumes
