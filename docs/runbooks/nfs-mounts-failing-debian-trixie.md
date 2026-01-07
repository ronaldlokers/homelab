# NFS Mounts Failing on Debian Trixie

## Quick Reference

- **Severity**: High (blocks pod startup)
- **Estimated Time to Resolve**: 5 minutes (once diagnosed)
- **Symptoms**: Pods stuck in `ContainerCreating` with NFS mount errors
- **Affected Components**: Any pod using NFS (direct mounts or Longhorn RWX volumes)
- **Environment**: Raspberry Pi nodes running Debian Trixie
- **Prerequisites**: SSH access to cluster nodes

## Symptoms & Detection

### Error Messages

```
MountVolume.SetUp failed for volume "immich-library-nfs-production" : mount failed: exit status 32
Output: mount: /var/lib/kubelet/pods/.../...: fsconfig() failed: NFS: mount program didn't pass remote address.
```

### Observable Behavior

- Pods stuck in `ContainerCreating` state
- NFS-backed PersistentVolumes fail to mount
- **Inconsistent behavior across nodes** - same config works on some nodes, fails on others
- Error affects both:
  - Direct NFS mounts (TrueNAS, external storage)
  - Longhorn ReadWriteMany volumes (uses NFS internally via share-manager)

### Monitoring Indicators

- Pods not reaching `Running` state
- Volume mount errors in events:
  ```bash
  kubectl describe pod <pod-name> -n <namespace>
  # Look for: Warning FailedMount ... fsconfig() failed
  ```

## Immediate Actions

**If you need pods running urgently:**

This isn't a quick workaround issue - you need to install `nfs-common` on affected nodes (it's fast though).

**Check which nodes are affected**:

```bash
# See which nodes have failing pods
kubectl get pods -A -o wide | grep ContainerCreating

# SSH to each node and check for nfs-common
for node in kube-srv-1 kube-srv-2 kube-srv-3; do
  echo "=== $node ==="
  ssh $node "dpkg -l | grep nfs-common"
done
```

## Diagnosis Steps

### 1. Confirm it's an NFS mount issue

Check pod events:

```bash
# Get failing pod
kubectl get pods -n <namespace> | grep ContainerCreating

# Check events for mount errors
kubectl describe pod <pod-name> -n <namespace> | grep -A5 "Warning.*FailedMount"
```

**Look for**:
- "fsconfig() failed: NFS: mount program didn't pass remote address"
- "mount failed: exit status 32"

### 2. Verify NFS server is accessible

```bash
# From a working node or your workstation
ping <nfs-server>

# Test NFS port connectivity
nc -zv <nfs-server> 2049

# Or if using TrueNAS/specific server
showmount -e <nfs-server>
```

**If NFS server is unreachable**, this is a different issue (network/firewall).

### 3. Check node-level differences

```bash
# SSH to a WORKING node (if any)
ssh <working-node>
dpkg -l | grep nfs-common
# Should show: ii  nfs-common  1:2.X.X-X  arm64  NFS support files common to client and server

# SSH to FAILING node
ssh <failing-node>
dpkg -l | grep nfs-common
# Should show: nothing or "no packages found"
```

### 4. Confirm diagnosis

**This is the right runbook if:**
- ✅ Running Debian Trixie on nodes
- ✅ Pods show NFS mount errors with "mount program didn't pass remote address"
- ✅ Some nodes work, others don't (or all fail)
- ✅ `nfs-common` package missing on failing nodes

**This is NOT the right runbook if:**
- ❌ NFS server is unreachable (network issue)
- ❌ Authentication/permission errors (credentials issue)
- ❌ Different error message
- ❌ Not using Debian Trixie

## Resolution Steps

### Step 1: Install nfs-common on all nodes

```bash
# SSH to each node and install
# For kube-srv-1
ssh kube-srv-1
sudo apt update
sudo apt install -y nfs-common
exit

# For kube-srv-2
ssh kube-srv-2
sudo apt update
sudo apt install -y nfs-common
exit

# For kube-srv-3
ssh kube-srv-3
sudo apt update
sudo apt install -y nfs-common
exit
```

**OR use a loop** (if you have SSH key auth):

```bash
for node in kube-srv-1 kube-srv-2 kube-srv-3; do
  echo "=== Installing nfs-common on $node ==="
  ssh $node "sudo apt update && sudo apt install -y nfs-common"
done
```

**No reboot required** - mounts work immediately after installation.

### Step 2: Verify installation

```bash
# Check package installed on all nodes
for node in kube-srv-1 kube-srv-2 kube-srv-3; do
  echo "=== $node ==="
  ssh $node "dpkg -l | grep nfs-common"
done

# Should show installed on all nodes:
# ii  nfs-common  1:2.X.X-X  arm64  NFS support files common to client and server
```

### Step 3: Test NFS mount manually

```bash
# SSH to previously-failing node
ssh <failing-node>

# Create test mount point
sudo mkdir -p /mnt/nfs-test

# Try manual NFS mount
sudo mount -t nfs <nfs-server>:/path/to/export /mnt/nfs-test

# Should succeed without errors

# Unmount test
sudo umount /mnt/nfs-test
exit
```

### Step 4: Restart affected pods

```bash
# Pods in ContainerCreating should automatically mount once nfs-common is installed
# If not, delete them to force recreation:

kubectl delete pod <pod-name> -n <namespace>

# Or restart entire deployment/statefulset:
kubectl rollout restart deployment/<deployment-name> -n <namespace>

# Watch pods start
kubectl get pods -n <namespace> -w
```

## Verification

### Confirm resolution:

- [ ] `nfs-common` installed on all nodes
      ```bash
      for node in kube-srv-1 kube-srv-2 kube-srv-3; do
        ssh $node "dpkg -l | grep nfs-common | grep ^ii"
      done
      # Should return results for all nodes
      ```

- [ ] No pods stuck in `ContainerCreating`
      ```bash
      kubectl get pods -A | grep ContainerCreating
      # Should return no results
      ```

- [ ] NFS-backed pods running successfully
      ```bash
      kubectl get pods -n <namespace> -l <selector-for-nfs-pods>
      # All should show Running
      ```

- [ ] No mount errors in events
      ```bash
      kubectl get events -A --field-selector type=Warning | grep -i mount
      # Should show no NFS-related mount errors
      ```

- [ ] Longhorn RWX volumes working (if applicable)
      ```bash
      # Check share-manager pods
      kubectl get pods -n longhorn-system | grep share-manager
      # All should be Running

      # Check pods using RWX volumes
      kubectl get pods -A -o json | jq -r '.items[] | select(.spec.volumes[]?.persistentVolumeClaim.claimName) | "\(.metadata.namespace)/\(.metadata.name)"'
      # Verify RWX-using pods are Running
      ```

## Root Cause

### Why This Happens

**Debian Trixie intentionally excludes `nfs-common` from minimal/lite installations** to reduce boot overhead from udev rules.

The `nfs-common` package provides:
- `mount.nfs` - NFS mount helper program
- `lockd`, `statd` - NFS locking services
- Essential NFS client utilities

Without it:
- Kernel NFS client can't properly communicate mount parameters
- Results in cryptic "mount program didn't pass remote address" error

### Why Some Nodes Work

Inconsistent behavior happens when:
- Some nodes had the package from previous installations
- Different installation methods (some installed it, others didn't)
- Manual package installations on some nodes for testing

### Two Types of NFS Usage Affected

1. **Direct NFS PersistentVolumes**:
   - Mounting external NFS shares (TrueNAS, NAS devices)
   - Any PV with `nfs:` volume source

2. **Longhorn ReadWriteMany Volumes**:
   - Longhorn implements RWX using NFS share-manager pods
   - Internally creates NFS exports for multi-node access
   - Requires `nfs-common` on all nodes

## Prevention

### For New Node Setup

- [ ] Add to node provisioning checklist
      ```bash
      sudo apt install -y nfs-common
      ```

- [ ] Include in infrastructure-as-code node setup scripts

- [ ] Document in cluster setup guide (`docs/setup.md`)

- [ ] Test NFS mounts during node commissioning
      ```bash
      # Verification test
      mount -t nfs <test-server>:/test /mnt/test
      umount /mnt/test
      ```

### For Cluster Maintenance

- [ ] When adding new nodes, verify `nfs-common` installed

- [ ] Include in node readiness checklist

- [ ] Consider automated node validation script:
      ```bash
      #!/bin/bash
      # node-verify.sh
      echo "Checking required packages..."
      dpkg -l | grep -q nfs-common || {
        echo "ERROR: nfs-common not installed"
        exit 1
      }
      echo "Node ready for NFS workloads"
      ```

### Monitoring

Create alert for pods stuck in ContainerCreating with mount errors:

```yaml
# Prometheus alert example
- alert: PodsFailingNFSMount
  expr: |
    kube_pod_container_status_waiting_reason{reason="ContainerCreating"} > 0
  for: 5m
  annotations:
    summary: "Pod stuck mounting volume - check for NFS issues"
```

## Related Issues

- **Kernel version compatibility**: Debian Trixie uses kernel 6.12+ which may have NFS changes
- **Longhorn RWX dependency**: Longhorn's share-manager needs nfs-common on all nodes
- **Minimal distros**: Many minimal distributions exclude NFS utilities by default
- **Container runtime assumptions**: Kubernetes assumes host has NFS support

## Original War Story

For the full investigation narrative including wrong turns and debugging process, see: [`docs/war-stories/nfs-debian-trixie.md`](../war-stories/nfs-debian-trixie.md)

## References

- [Debian Packages - nfs-common](https://packages.debian.org/trixie/nfs-common)
- [Raspberry Pi Forums - NFS on Trixie](https://forums.raspberrypi.com/viewtopic.php?t=393085)
- [Arch Linux Forums - Similar Issue](https://bbs.archlinux.org/viewtopic.php?id=294628)
- [Kubernetes NFS Volumes](https://kubernetes.io/docs/concepts/storage/volumes/#nfs)
- [Longhorn RWX Volumes](https://longhorn.io/docs/latest/advanced-resources/rwx-workloads/)

---

**Last Updated**: 2026-01-07
**Tested On**: Raspberry Pi CM5 nodes with Debian Trixie
**Success Rate**: 100% (resolved immediately after installing nfs-common)
