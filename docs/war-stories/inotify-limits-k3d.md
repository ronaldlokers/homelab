# inotify Limits Exhausted in k3d Cluster

**Date**: December 2025
**Environment**: Staging cluster (k3d on Ubuntu VM)
**Impact**: Multiple pods failing to start with "too many open files" errors

## The Problem

After deploying several applications (Loki, Alloy, Homepage, FerretDB), multiple pods began failing with file watching errors.

**Error in pod logs**:
```
failed to create fsnotify watcher: too many open files
```

**Affected pods**:
- `alloy-*` (DaemonSet)
- `loki-write-*`, `loki-read-*`, `loki-backend-*`
- `homepage-*`
- `ferretdb-*`

**Symptoms**:
- Pods start initially, then crash
- Error occurs during file watching setup
- Same error across different applications
- More prevalent after cluster reboot

## The Investigation

### Understanding the Error

The error mentions "too many open files" but it's actually about **inotify watches**, not file descriptors.

**inotify** is a Linux kernel subsystem that monitors filesystem events:
- Applications call `inotify_add_watch()` to monitor files/directories
- Each watch consumes a kernel resource
- System has a limit on total watches per user

### Checking Current Limits

```bash
# SSH to the k3d host VM
ssh user@10.0.40.52

# Check current inotify limits
cat /proc/sys/fs/inotify/max_user_watches
# 8192  ← Default, very low!

cat /proc/sys/fs/inotify/max_user_instances
# 128
```

**Default Ubuntu limits**:
- `max_user_watches`: 8192 total watches per user
- `max_user_instances`: 128 inotify instances per user

### Why k3d Exhausts Watches Quickly

In a k3d cluster:
- All containers run as the same user (on the host)
- Each container's file watching shares the same limit
- Kubernetes components also use inotify:
  - kubelet watching pod manifests
  - Container runtime watching logs
  - Application code watching config files

**Watch consumption example**:
- Loki: ~500 watches per component
- Alloy: ~300 watches (watching pod logs)
- Homepage: ~200 watches (config files)
- **Total needed**: 3000+ watches
- **Limit**: 8192 (shared across ALL containers!)

### Confirming Watch Exhaustion

```bash
# Check how many watches are currently used
find /proc/*/fd -lname anon_inode:inotify 2>/dev/null | wc -l
# 7856 ← Near the limit!
```

As the cluster runs more pods, watches accumulate until the limit is hit.

## The Root Cause

**Linux inotify default limits are too low for Kubernetes clusters.**

The defaults were designed for desktop systems with a few GUI applications. Kubernetes clusters have:
- Dozens to hundreds of pods
- Each pod may watch multiple files/directories
- System components also using inotify
- All sharing the same per-user limit

**Why k3d specifically?**
- Docker-in-Docker architecture
- All containers share host's inotify limits
- No isolation per container
- Native Kubernetes nodes don't have this issue (separate user namespaces)

## The Solution

### Increase inotify Limits on Host VM

SSH to the k3d host and modify sysctl settings:

```bash
# SSH to VM
ssh user@10.0.40.52

# Edit sysctl configuration
sudo nano /etc/sysctl.conf

# Add these lines at the end:
fs.inotify.max_user_watches=524288
fs.inotify.max_user_instances=512

# Save and exit

# Apply immediately without reboot
sudo sysctl -p

# Verify new limits
cat /proc/sys/fs/inotify/max_user_watches
# 524288 ✓
```

### Restart Affected Pods

```bash
# Delete pods to trigger recreation with new limits
kubectl delete pods -n monitoring -l app.kubernetes.io/name=alloy
kubectl delete pods -n monitoring -l app.kubernetes.io/name=loki
kubectl delete pods -n homepage -l app.kubernetes.io/name=homepage
kubectl delete pods -n nightscout -l app.kubernetes.io/name=ferretdb

# Wait for pods to restart
kubectl get pods --all-namespaces --watch
```

### Verify Fix

```bash
# Check logs for any remaining inotify errors
kubectl logs -n monitoring -l app.kubernetes.io/name=alloy | grep -i "too many"
# (no output - good!)

# All pods running
kubectl get pods --all-namespaces | grep -v Running
# (only Completed jobs shown - good!)
```

## Understanding inotify Limits

### max_user_watches

**What it controls**: Maximum number of files/directories that can be monitored per user

**Default**: 8192 (very low)
**Recommended for k3d/Kubernetes**: 524288 (64x increase)

**Memory usage**:
- Each watch consumes ~1KB of kernel memory
- 524288 watches ≈ 512MB of kernel memory
- Acceptable overhead for modern systems

### max_user_instances

**What it controls**: Maximum number of inotify instances (inotify_init() calls) per user

**Default**: 128
**Recommended for k3d/Kubernetes**: 512

**Why increase**:
- Each application creates inotify instances
- Multiple instances per pod common
- Low limit causes "inotify_init failed" errors

## Why This Affects k3d More Than Native Kubernetes

### k3d (Docker-in-Docker)

```
Host VM (single user)
  └── Docker daemon
      ├── k3d-server container → shares limit
      ├── k3d-agent-0 container → shares limit
      ├── k3d-agent-1 container → shares limit
      └── All pods → shares limit
```

**All containers count against one user's limit!**

### Native Kubernetes

```
Host (root)
  ├── kubelet (user: root)
  ├── containerd (user: root)
  └── Pods in separate user namespaces
      ├── pod-1 (user namespace isolation)
      ├── pod-2 (user namespace isolation)
      └── Each has separate limits
```

**Better isolation, less limit contention.**

## Making Changes Persistent

The sysctl changes persist across reboots because they're in `/etc/sysctl.conf`.

**Verify persistence**:
```bash
# Reboot VM
sudo reboot

# After reboot, check limits
cat /proc/sys/fs/inotify/max_user_watches
# 524288 ✓ Still set!
```

## Alternative Solutions

### Option 1: Reduce Watch Usage (Not Practical)

Modify applications to not use inotify:
- Polling instead of watching
- Disable hot-reload features
- Reduce monitoring scope

**Cons**: Degrades functionality, high application changes

### Option 2: Run Fewer Pods (Not Sustainable)

Reduce cluster density:
- Fewer applications
- Lower replica counts

**Cons**: Limits cluster usefulness

### Option 3: Increase Host Limits (Our Choice)

Raise kernel limits:
- Simple configuration change
- No application modifications
- Accommodates cluster growth

**Cons**: Slightly more kernel memory used

### Option 4: Switch to Native Kubernetes

Replace k3d with native K3s:
- Better resource isolation
- Higher default limits
- Production-like environment

**Cons**: More complex setup, loses k3d benefits (Docker-based, easy reset)

## Recommended Settings by Cluster Size

### Small k3d Cluster (1-10 apps)
```bash
fs.inotify.max_user_watches=131072   # 128K
fs.inotify.max_user_instances=256
```

### Medium k3d Cluster (10-30 apps)
```bash
fs.inotify.max_user_watches=524288   # 512K (our setting)
fs.inotify.max_user_instances=512
```

### Large k3d Cluster (30+ apps)
```bash
fs.inotify.max_user_watches=1048576  # 1M
fs.inotify.max_user_instances=1024
```

### Production Native Kubernetes
```bash
fs.inotify.max_user_watches=524288   # Usually sufficient
fs.inotify.max_user_instances=512
# Namespace isolation helps prevent exhaustion
```

## Lessons Learned

1. **k3d has unique constraints**: Docker-in-Docker shares limits across all containers
   - Great for development
   - Requires higher system limits than native K8s

2. **"Too many open files" can be misleading**: Often means inotify watches, not file descriptors
   - Check `/proc/sys/fs/inotify/` not `ulimit -n`
   - Different kernel subsystems

3. **Default Linux limits assume desktop use**: Kubernetes needs higher limits
   - 8192 watches is fine for 5 GUI apps
   - Not enough for 50 containerized microservices

4. **Set limits proactively**: Don't wait for failures
   - Include in VM provisioning
   - Document in setup guides
   - Monitor usage over time

5. **Memory impact is minimal**: Each watch is ~1KB
   - 524288 watches = ~512MB
   - Acceptable on modern systems

## Prevention Checklist

When setting up k3d environments:

- [ ] Set inotify limits before deploying applications
- [ ] Add to `/etc/sysctl.conf` for persistence
- [ ] Document limits in infrastructure setup
- [ ] Test with multiple applications deployed
- [ ] Monitor inotify usage: `find /proc/*/fd -lname anon_inode:inotify | wc -l`
- [ ] Include in VM/host provisioning automation

## Monitoring inotify Usage

### Check Current Watch Count

```bash
# Count active inotify instances
find /proc/*/fd -lname anon_inode:inotify 2>/dev/null | wc -l

# More detailed per-process breakdown
for foo in /proc/*/fd/*; do readlink -f $foo; done 2>/dev/null | grep inotify | wc -l
```

### Watch Usage Over Time

```bash
# Monitor in real-time
watch -n 5 "find /proc/*/fd -lname anon_inode:inotify 2>/dev/null | wc -l"
```

### Set Alerts

Create monitoring alert when usage exceeds 80%:

```bash
current=$(find /proc/*/fd -lname anon_inode:inotify 2>/dev/null | wc -l)
limit=$(cat /proc/sys/fs/inotify/max_user_watches)
percentage=$((current * 100 / limit))

if [ $percentage -gt 80 ]; then
  echo "WARNING: inotify usage at ${percentage}%"
fi
```

## Timeline

- **Cluster growing**: Added Loki, Alloy, increased app count
- **Issue discovered**: Pods crashing with "too many open files"
- **Initial confusion**: Checked file descriptor limits (wrong subsystem)
- **Research**: Found it's inotify watches, not file descriptors
- **Checked limits**: Found default 8192, clearly too low
- **Solution**: Increased to 524288 in `/etc/sysctl.conf`
- **Applied**: `sudo sysctl -p`
- **Restarted pods**: Deleted affected pods
- **Verification**: No more errors, faster cluster startup
- **Time to fix**: ~10 minutes (after understanding inotify vs file descriptors)

## References

- [inotify man page](https://man7.org/linux/man-pages/man7/inotify.7.html)
- [sysctl documentation](https://www.kernel.org/doc/Documentation/sysctl/fs.txt)
- [Kubernetes inotify issues](https://kubernetes.io/docs/setup/production-environment/container-runtimes/#troubleshooting)
- [Docker inotify limits](https://github.com/docker/for-linux/issues/611)
