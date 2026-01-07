# inotify Limits Exhausted

## Quick Reference

- **Severity**: High (prevents pod startup)
- **Estimated Time to Resolve**: 10 minutes
- **Symptoms**: Pods crash with "too many open files" errors
- **Affected Components**: Any pod using file watching (Loki, Alloy, FerretDB, Homepage, etc.)
- **Environment**: k3d staging cluster (Docker-based)
- **Prerequisites**: SSH access to k3d host VM

## Symptoms & Detection

### Error Messages

```
failed to create fsnotify watcher: too many open files
```

### Observable Behavior

- Pods start successfully, then immediately crash
- CrashLoopBackOff state
- Multiple different applications affected simultaneously
- Error occurs during file watching initialization
- More prevalent after cluster reboot or when deploying multiple apps

### Monitoring Indicators

- High pod restart counts across multiple namespaces
- Consistent crash pattern across unrelated applications

## Immediate Actions

**If you need pods running RIGHT NOW:**

1. Restart specific failing pods (temporary relief):
   ```bash
   kubectl delete pod <pod-name> -n <namespace>
   ```

   This might allow a few pods to start if others release watches, but **this is not a solution** - you need to increase limits.

## Diagnosis Steps

### 1. Confirm it's an inotify issue

Check pod logs for the specific error:

```bash
# Check Alloy pods
kubectl logs -n monitoring -l app.kubernetes.io/name=alloy | grep "too many open files"

# Check Loki pods
kubectl logs -n monitoring -l app.kubernetes.io/name=loki | grep "too many open files"

# Check all pods in all namespaces
kubectl get pods -A -o json | jq -r '.items[] | select(.status.phase=="CrashLoopBackOff") | "\(.metadata.namespace)/\(.metadata.name)"' | while read pod; do
  kubectl logs $pod 2>/dev/null | grep -i "too many open files"
done
```

**Expected**: Multiple pods showing "failed to create fsnotify watcher: too many open files"

### 2. Check current inotify limits on host

SSH to the k3d host VM:

```bash
ssh user@10.0.40.52

# Check current limits
cat /proc/sys/fs/inotify/max_user_watches
# Expected: 8192 (too low!)

cat /proc/sys/fs/inotify/max_user_instances
# Expected: 128

# Check current usage
find /proc/*/fd -lname anon_inode:inotify 2>/dev/null | wc -l
# If close to max_user_watches, this is your problem
```

### 3. Confirm diagnosis

**This is the right runbook if:**
- ✅ Running k3d cluster (Docker-based)
- ✅ Multiple pods showing "too many open files" errors
- ✅ Current inotify watch usage near or at limit
- ✅ `/proc/sys/fs/inotify/max_user_watches` is 8192 or low

**This is NOT the right runbook if:**
- ❌ Native Kubernetes cluster (see related issues)
- ❌ Only one specific application failing
- ❌ Error mentions file descriptors, not "fsnotify watcher"

## Resolution Steps

### Step 1: Increase inotify limits on host

SSH to k3d host VM:

```bash
ssh user@10.0.40.52

# Edit sysctl configuration
sudo nano /etc/sysctl.conf

# Add these lines at the end:
fs.inotify.max_user_watches=524288
fs.inotify.max_user_instances=512

# Save and exit (Ctrl+X, Y, Enter)
```

### Step 2: Apply new limits immediately

```bash
# Apply without reboot
sudo sysctl -p

# Verify new limits are active
cat /proc/sys/fs/inotify/max_user_watches
# Should show: 524288

cat /proc/sys/fs/inotify/max_user_instances
# Should show: 512
```

### Step 3: Restart affected pods

Exit SSH and return to your workstation, then restart affected pods:

```bash
# Restart all monitoring pods
kubectl delete pods -n monitoring -l app.kubernetes.io/name=alloy
kubectl delete pods -n monitoring -l app.kubernetes.io/name=loki

# Restart other affected apps
kubectl delete pods -n homepage -l app.kubernetes.io/name=homepage
kubectl delete pods -n nightscout -l app.kubernetes.io/name=ferretdb

# Wait for pods to restart and stabilize
kubectl get pods -A --watch
```

## Verification

### Confirm resolution:

- [ ] All previously crashing pods now showing `Running` status
      ```bash
      kubectl get pods -A | grep -v Running | grep -v Completed
      # Should show no CrashLoopBackOff pods
      ```

- [ ] No "too many open files" errors in logs
      ```bash
      kubectl logs -n monitoring -l app.kubernetes.io/name=alloy | grep -i "too many"
      # Should return no results
      ```

- [ ] Inotify limits are persistent across reboots
      ```bash
      # SSH to host and reboot
      ssh user@10.0.40.52 'sudo reboot'

      # After reboot, verify limits still set
      ssh user@10.0.40.52 'cat /proc/sys/fs/inotify/max_user_watches'
      # Should still show: 524288
      ```

- [ ] Current watch usage well below new limit
      ```bash
      ssh user@10.0.40.52 'find /proc/*/fd -lname anon_inode:inotify 2>/dev/null | wc -l'
      # Should be << 524288
      ```

## Root Cause

### Why This Happens

**Linux inotify default limits are designed for desktop systems**, not Kubernetes clusters.

- **Default `max_user_watches`**: 8192 (enough for a few GUI apps)
- **Kubernetes needs**: Thousands to tens of thousands

**Why k3d is affected more than native Kubernetes:**

```
k3d (Docker-in-Docker):
  Host VM (single user)
    └── All containers share one user's limit
        ├── k3d-server
        ├── k3d-agent-0
        ├── k3d-agent-1
        └── All pods → ALL COUNT AGAINST THE SAME 8192 LIMIT!

Native Kubernetes:
  Host
    └── Pods in separate user namespaces
        ├── pod-1 (isolated)
        ├── pod-2 (isolated)
        └── Each has separate limits
```

### What Consumes inotify Watches

Each of these applications watches files/directories:

- **Loki**: ~500 watches per component (read, write, backend)
- **Alloy**: ~300 watches (monitoring pod logs, configs)
- **Homepage**: ~200 watches (config files, icons)
- **Kubernetes system components**: kubelet, container runtime

**Total needed**: 3,000-5,000+ watches
**Default limit**: 8,192 **shared across all containers**

### Memory Impact

- Each watch: ~1KB of kernel memory
- 524,288 watches: ~512MB
- Acceptable overhead on modern systems (16GB+ RAM)

## Prevention

### For New k3d Installations

- [ ] Set inotify limits **before** deploying applications
      ```bash
      sudo tee -a /etc/sysctl.conf <<EOF
      fs.inotify.max_user_watches=524288
      fs.inotify.max_user_instances=512
      EOF
      sudo sysctl -p
      ```

- [ ] Add to infrastructure-as-code VM provisioning scripts

- [ ] Document in cluster setup guide (`docs/setup.md`)

- [ ] Include in cluster bootstrap checklist

### Monitoring & Alerting

Monitor inotify usage to catch this before it becomes critical:

```bash
# Current usage check
ssh k3d-host 'find /proc/*/fd -lname anon_inode:inotify 2>/dev/null | wc -l'

# Create alert if usage > 80% of limit
current=$(ssh k3d-host 'find /proc/*/fd -lname anon_inode:inotify 2>/dev/null | wc -l')
limit=$(ssh k3d-host 'cat /proc/sys/fs/inotify/max_user_watches')
percentage=$((current * 100 / limit))

if [ $percentage -gt 80 ]; then
  echo "WARNING: inotify usage at ${percentage}%"
fi
```

Consider adding to Prometheus metrics if possible.

### Recommended Limits by Cluster Size

```bash
# Small k3d cluster (1-10 apps)
fs.inotify.max_user_watches=131072   # 128K
fs.inotify.max_user_instances=256

# Medium k3d cluster (10-30 apps) - OUR SETTING
fs.inotify.max_user_watches=524288   # 512K
fs.inotify.max_user_instances=512

# Large k3d cluster (30+ apps)
fs.inotify.max_user_watches=1048576  # 1M
fs.inotify.max_user_instances=1024
```

## Related Issues

- **File descriptor limits (ulimit)**: Different issue. Check `ulimit -n` and `/etc/security/limits.conf`
- **Native Kubernetes inotify issues**: Rare due to namespace isolation, but similar fix
- **Container runtime issues**: If containerd/docker itself is affected, may need different limits

## Original War Story

For full investigation narrative and technical deep-dive, see: [`docs/war-stories/inotify-limits-k3d.md`](../war-stories/inotify-limits-k3d.md)

## References

- [inotify man page](https://man7.org/linux/man-pages/man7/inotify.7.html)
- [Linux sysctl documentation](https://www.kernel.org/doc/Documentation/sysctl/fs.txt)
- [Kubernetes Production Environment](https://kubernetes.io/docs/setup/production-environment/container-runtimes/)
- [Docker inotify issues on GitHub](https://github.com/docker/for-linux/issues/611)

---

**Last Updated**: 2026-01-07
**Tested On**: k3d staging cluster (Ubuntu 24.04 VM)
**Success Rate**: 100% (1/1 incidents)
