# Longhorn ReadWriteMany and Multi-Attach Errors

**Date**: December 2025
**Environment**: Production cluster - Immich machine-learning component
**Impact**: Unable to scale ML component horizontally, deployment stuck

## The Problem

After adding HorizontalPodAutoscaler to Immich for production, the machine-learning component couldn't scale beyond 1 replica.

**Error Messages**:
```
Warning  FailedAttachVolume  Multi-Attach error for volume "pvc-xxx"
Volume is already used by pod(s) immich-machine-learning-649544d5df-mcsjm
```

**Symptoms**:
- New ML pods stuck in `ContainerCreating`
- Old pod running fine on kube-srv-3
- New pod on kube-srv-1 couldn't attach the same PVC
- HPA created new pod but it couldn't start

## The Investigation

### Understanding the Issue

Checked the PVC access mode:
```bash
kubectl get pvc immich-machine-learning -n immich -o yaml
```

Output showed:
```yaml
spec:
  accessModes:
  - ReadWriteOnce  # <-- Problem!
  storageClassName: longhorn
```

**ReadWriteOnce (RWO)**: Volume can only be mounted by a single node at a time.
**What we needed**: ReadWriteMany (RWX) for horizontal pod autoscaling.

### Why This Matters for Horizontal Scaling

When HPA scales from 1 to 2 replicas:
1. Kubernetes tries to schedule second pod (possibly on different node)
2. Pod tries to mount the same PVC
3. Longhorn blocks it because volume is RWO and already mounted
4. Pod stuck in ContainerCreating

### Initial Attempt: Change Access Mode in Values

Updated `apps/production/immich/values-patch.yaml`:
```yaml
machine-learning:
  persistence:
    cache:
      storageClass: longhorn
      accessMode: ReadWriteMany  # Changed from ReadWriteOnce
```

Reconciled with Flux, but Helm failed:
```
cannot patch "immich-machine-learning" with kind PersistentVolumeClaim:
PersistentVolumeClaim "immich-machine-learning" is invalid:
spec: Forbidden: spec is immutable after creation except resources.requests
and volumeAttributesClassName for bound claims
```

**Key learning**: You **cannot** change PVC access modes after creation. The spec is immutable.

## The Root Cause

**PVC access modes are immutable in Kubernetes.**

Once a PVC is created with ReadWriteOnce, you cannot patch it to ReadWriteMany. The only solution is to delete and recreate the PVC.

## The Solution

### Step 1: Scale Down to Release PVC

```bash
# Scale machine-learning deployment to 0
kubectl scale deployment immich-machine-learning --replicas=0 -n immich

# Wait for pods to terminate
kubectl get pods -n immich | grep machine-learning
```

### Step 2: Delete the PVC

```bash
# Delete the old PVC
kubectl delete pvc immich-machine-learning -n immich

# Verify deletion
kubectl get pvc -n immich
```

### Step 3: Reconcile to Recreate

```bash
# Suspend and resume HelmRelease to trigger fresh deployment
flux suspend helmrelease immich -n immich
flux resume helmrelease immich -n immich
```

Longhorn automatically creates new PVC with ReadWriteMany access mode.

### Step 4: Verify New PVC

```bash
kubectl get pvc immich-machine-learning -n immich -o yaml
```

Output:
```yaml
spec:
  accessModes:
  - ReadWriteMany  # ✓ Correct!
  storageClassName: longhorn
  resources:
    requests:
      storage: 10Gi
```

New PVC ID: `pvc-50bd0585-c4d6-401e-a0cc-49ca4eb8ba78`

## How Longhorn Implements ReadWriteMany

When a PVC uses `accessMode: ReadWriteMany`, Longhorn:

1. **Creates a share-manager pod**:
   ```bash
   kubectl get pods -n longhorn-system | grep share-manager
   # share-manager-pvc-50bd0585-c4d6-401e-a0cc-49ca4eb8ba78
   ```

2. **Exports volume via NFS**:
   - Runs NFS-Ganesha server in share-manager pod
   - Creates ClusterIP service on port 2049
   - Exports volume path: `/pvc-50bd0585-c4d6-401e-a0cc-49ca4eb8ba78`

3. **Pods mount via NFS**:
   - Multiple pods can mount the same NFS export
   - Share-manager handles concurrent access
   - Requires `nfs-common` on nodes (see nfs-debian-trixie.md)

Check the NFS export:
```bash
kubectl get svc -n longhorn-system | grep pvc-50bd0585
# pvc-50bd0585-c4d6-401e-a0cc-49ca4eb8ba78   ClusterIP   10.43.217.136   <none>   2049/TCP
```

## Data Loss Considerations

**Important**: Deleting the PVC **deletes the volume and all data**.

For Immich ML cache:
- ✅ Safe to delete: It's just a cache for ML models
- Models are re-downloaded automatically on first use
- No permanent data loss

For other volumes:
- ⚠️ **Back up first** if volume contains important data
- Use Longhorn backup/snapshot before deletion
- Restore to new volume after recreation

## Prevention

1. **Plan access modes upfront**:
   - Single replica app? Use RWO (cheaper, faster)
   - Horizontal scaling needed? Use RWX from the start

2. **Consider the tradeoffs**:
   - **RWO**: Better performance, lower overhead, single node only
   - **RWX**: Enables scaling, NFS overhead, requires share-manager

3. **Cache vs. Persistent Data**:
   - Caches can use RWO initially, change later if needed
   - Critical data should match final scaling requirements

4. **Test scaling early**:
   - Don't wait until production to test HPA
   - Verify PVC access modes support your scaling strategy

## Lessons Learned

1. **PVC specs are immutable**: Access modes can't be changed after creation
2. **ReadWriteMany has overhead**: Longhorn uses NFS, which requires nfs-common and share-manager pods
3. **Not all storage classes support RWX**: Check storage class capabilities before choosing access mode
4. **Scaling strategies need storage planning**: HPA decisions affect storage architecture
5. **Caches are safe to recreate**: Don't be afraid to delete and recreate cache volumes

## Related Challenges

- Required nfs-common on nodes (see [nfs-debian-trixie.md](nfs-debian-trixie.md))
- HPA needed resource requests to work (see [hpa-resource-requests.md](hpa-resource-requests.md))
- Helm upgrade failures when trying to patch immutable fields

## Timeline

- **Issue discovered**: HPA created second pod, stuck in ContainerCreating
- **Initial attempt**: Tried patching PVC (failed - immutable)
- **Research**: Learned about PVC immutability and Longhorn RWX implementation
- **Solution implemented**: Scale down → delete PVC → reconcile
- **Time to resolution**: ~15 minutes (plus nfs-common debugging)
- **Data loss**: None (cache volume, regenerated automatically)

## References

- [Kubernetes PVC Spec](https://kubernetes.io/docs/concepts/storage/persistent-volumes/)
- [Longhorn RWX Volumes](https://longhorn.io/docs/latest/advanced-resources/rwx-workloads/)
- [Access Modes](https://kubernetes.io/docs/concepts/storage/persistent-volumes/#access-modes)
