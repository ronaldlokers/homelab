# NFS Mounts Failing on Debian Trixie

**Date**: December 2025
**Environment**: Production cluster - Raspberry Pi CM5 nodes running Debian Trixie
**Impact**: Multiple applications unable to start, Immich photo library inaccessible

## The Problem

After setting up Immich in production, pods were stuck in `ContainerCreating` state. Some pods worked fine on certain nodes, but failed on others with the same configuration.

**Error Message**:
```
MountVolume.SetUp failed for volume "immich-library-nfs-production" : mount failed: exit status 32
Mounting command: mount
Mounting arguments: -t nfs -o hard,intr,nfsvers=4.1 truenas.ronaldlokers.nl:/mnt/tank/media/immich/production /var/lib/kubelet/pods/.../volumes/kubernetes.io~nfs/immich-library-nfs-production
Output: mount: /var/lib/kubelet/pods/.../immich-library-nfs-production: fsconfig() failed: NFS: mount program didn't pass remote address.
```

**Symptoms**:
- Pods stuck in `ContainerCreating`
- Same error for both direct NFS mounts (TrueNAS) and Longhorn RWX volumes
- Error: `fsconfig() failed: NFS: mount program didn't pass remote address`
- Inconsistent behavior across nodes - some worked, some didn't

## The Investigation

### Initial Theories (All Wrong)

1. **TrueNAS Connection Limits**: We thought maybe too many connections were hitting the NFS share
   - Checked TrueNAS settings - no connection limits configured
   - Checked concurrent mount attempts - normal levels

2. **Network/Firewall Issues**: Maybe network problems between nodes and TrueNAS
   - Ping tests: all successful
   - NFS port connectivity: working fine
   - Same NFS share worked from some nodes, failed on others

3. **Kubernetes Configuration**: Perhaps something wrong with the PV/PVC setup
   - Configuration was identical to working nodes
   - Same YAML worked in staging cluster

### The Breakthrough

The key clue was **inconsistent node behavior**. Two pods on `kube-srv-2` and `kube-srv-3` mounted successfully, but pods on `kube-srv-1` failed.

This suggested a **node-level difference**, not a configuration issue.

### Web Search Discovery

Searching for the specific error message with kernel version led to:
- [Raspberry Pi Forums - Debian Trixie NFS issue](https://forums.raspberrypi.com/viewtopic.php?t=393085)
- [Arch Linux Forums - Similar NFS error](https://bbs.archlinux.org/viewtopic.php?id=294628)

Both showed the same error on fresh minimal Linux installs.

**The answer**: Debian Trixie intentionally excludes `nfs-common` from minimal/lite installations to reduce boot overhead from udev rules.

## The Root Cause

**Missing `nfs-common` package on nodes.**

The `nfs-common` package provides:
- `mount.nfs` - The NFS mount helper program
- `lockd`, `statd` - NFS locking services
- Other essential NFS client utilities

Without it, the kernel's NFS client can't properly communicate mount parameters, resulting in the cryptic "mount program didn't pass remote address" error.

**Why some nodes worked**: Likely had the package installed from previous testing or different installation methods.

## The Solution

Install `nfs-common` on all nodes:

```bash
# On each node
sudo apt update
sudo apt install -y nfs-common
```

**No reboot required** - mounts work immediately after installation.

Verify:
```bash
# Check package
dpkg -l | grep nfs-common

# Test mount
sudo mount -t nfs truenas.ronaldlokers.nl:/mnt/tank/test /mnt/test
```

## Impact on Our Infrastructure

This affected TWO different types of NFS usage:

1. **Direct NFS Mounts**:
   - Immich photo library (500GB NFS volume from TrueNAS)
   - Any future NFS-backed PersistentVolumes

2. **Longhorn ReadWriteMany Volumes**:
   - Longhorn uses NFS internally via `share-manager` pods
   - Immich machine-learning cache (ReadWriteMany for horizontal scaling)
   - Any PVC with `accessMode: ReadWriteMany`

## Prevention

1. **Documentation**: Added `nfs-common` to node setup prerequisites
2. **Setup Scripts**: Include in any node provisioning automation
3. **Monitoring**: Consider alerting on pods stuck in ContainerCreating
4. **Testing**: Verify NFS mounts work during node setup

## Lessons Learned

1. **Error messages can be misleading**: "mount program didn't pass remote address" doesn't mention missing packages
2. **Minimal distros are REALLY minimal**: Don't assume standard packages are installed
3. **Node-level inconsistencies are a huge clue**: Different behavior on different nodes points to environmental differences
4. **Search with specifics**: Including "Debian Trixie" and kernel version in searches led to the answer
5. **Documentation matters**: Debian Trixie's decision to exclude nfs-common was intentional and documented, but not obvious

## Related Issues

- Kernel 6.12 compatibility with NFS
- Debian Trixie design philosophy (minimal by default)
- Container runtime assumptions about host system packages
- The gap between Kubernetes abstractions and host dependencies

## Timeline

- **Issue discovered**: Immich pods stuck on kube-srv-1
- **Initial investigation**: 20+ minutes checking TrueNAS, network, configs
- **Breakthrough**: Noticed working nodes vs failing nodes pattern
- **Solution found**: Web search with kernel version and error message
- **Time to resolution**: ~30 minutes of active debugging
- **Actual fix**: 30 seconds (apt install)

## References

- [Raspberry Pi Forums - NFS on Trixie](https://forums.raspberrypi.com/viewtopic.php?t=393085)
- [Arch Linux Forums - NFS Utils Missing](https://bbs.archlinux.org/viewtopic.php?id=294628)
- [Debian Package - nfs-common](https://packages.debian.org/trixie/nfs-common)
