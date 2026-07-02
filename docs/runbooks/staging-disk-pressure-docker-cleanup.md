# Staging Cluster Frozen by Disk Pressure (k3d/Docker)

## Quick Reference

- **Severity**: High
- **Estimated Time to Resolve**: 15-30 minutes
- **Symptoms**: Flux Kustomizations stuck at "Reconciliation in progress" or "Unknown" for hours/days; `flux-system` pods `Pending`
- **Affected Components**: Entire staging cluster - all Flux reconciliation halts
- **Environment**: Staging (k3d in Proxmox VM) only - production uses bare-metal K3s and isn't susceptible to this specific Docker-layer issue
- **Prerequisites**: SSH access to the Proxmox VM hosting the k3d Docker daemon, `kubectl` access to staging

## Symptoms & Detection

### Error Messages

```
Warning  FailedScheduling  9m40s (x1348 over 4d16h)  default-scheduler  0/4 nodes are available: 4 node(s) had untolerated taint {node.kubernetes.io/disk-pressure: }.
```

### Observable Behavior

- `flux get kustomizations --context=staging` shows Kustomizations stuck at an old revision, `Ready: Unknown` or `False`
- `source-controller`/`notification-controller` pods in `flux-system` stuck `Pending`
- New commits pushed to `main` never reach staging, no matter how long you wait
- Application pods elsewhere in the cluster may be actively `Evicted`

### Monitoring Indicators

- `KubeNodeDiskPressure`-class Prometheus alerts, if wired to Alertmanager (as of this writing, this alert path is **not** confirmed to route anywhere - check Alertmanager routing before trusting it caught this)
- No built-in dashboard currently tracks Docker host disk usage on the Proxmox VM directly - this has to be checked manually on the VM itself

## Immediate Actions

**Confirm this is actually disk pressure before doing anything else:**

```bash
kubectl get nodes --context=staging -o custom-columns='NAME:.metadata.name,DISK:.status.conditions[?(@.type=="DiskPressure")].status,TAINTS:.spec.taints[*].key'
```

If any node shows `DISK: True` or a `node.kubernetes.io/disk-pressure` taint, this is the right runbook.

## Diagnosis Steps

### 1. Check how long the condition has been active

```bash
kubectl describe node <affected-node> --context=staging | grep -A2 DiskPressure
```

A `LastTransitionTime` far in the past (days/weeks) means this has been silently broken for a while, not a fresh transient blip.

### 2. Find where the disk usage actually is

On the Proxmox VM hosting the k3d Docker daemon (not via `kubectl` - k3d nodes are Docker containers, so their disk usage lives at the Docker host level):

```bash
df -h /
docker system df -v
sudo du -sh /var/lib/docker/volumes/* /var/lib/docker/containers/* | sort -rh | head
```

Two categories to check separately:
- `/var/lib/docker/volumes/*` - k3d node containerd state (images, layers)
- `/var/lib/docker/containers/*` - Docker container logs (often the bigger surprise)

### 3. Confirm diagnosis

**This is the right runbook if:**
- ✅ `kubectl get nodes` shows `DiskPressure: True` and/or the taint
- ✅ Flux Kustomizations are stuck, not just slow
- ✅ `flux-system` controller pods are `Pending`

**This is NOT the right runbook if:**
- ❌ Nodes show healthy disk conditions but reconciliation is still stuck (check Flux controller logs for a different error instead)
- ❌ Only one specific app is failing, not the whole reconciliation pipeline

## Resolution Steps

### Step 1: Truncate container logs (fastest space recovery)

**Why**: Docker's default `json-file` log driver has no rotation - logs grow unbounded for the life of the container. Deleting the files doesn't help since Docker holds the file handles open; truncating does.

```bash
sudo truncate -s 0 /var/lib/docker/containers/*/*-json.log
```

**Expected result**: Immediate, often multi-gigabyte space recovery.

### Step 2: Prune unused images inside each k3d node

**Why**: Months of routine image bumps (Renovate, etc.) leave every prior version cached inside each node's containerd state.

```bash
for n in k3d-staging-server-0 k3d-staging-agent-0 k3d-staging-agent-1 k3d-staging-agent-2; do
  docker exec "$n" crictl rmi --prune
done
```

### Step 3: Prune the Docker host's own state

```bash
docker system prune -a
```

### Step 4: Add permanent log rotation

**Why**: Without this, the disk refills at the same unbounded rate - this step is what makes the fix stick rather than needing to be repeated in a few weeks.

```bash
sudo tee /etc/docker/daemon.json <<'EOF'
{
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
EOF
sudo systemctl restart docker
```

**If this fails**: Check the JSON is valid (`journalctl -u docker -n 20` will show a syntax error clearly if the daemon fails to start). This restart briefly disrupts all containers on the VM - expect staging to be unreachable for a minute or two while it recovers (containers have `restart: unless-stopped`, so they come back automatically).

**Caveat**: log rotation only applies to containers created *after* this config change - the existing long-lived k3d node containers keep unbounded logging until recreated. Acceptable since staging nodes are meant to be disposable/recreatable.

## Verification

- [ ] All nodes clear of the disk-pressure taint
      ```bash
      kubectl get nodes --context=staging -o custom-columns='NAME:.metadata.name,DISK:.status.conditions[?(@.type=="DiskPressure")].status,TAINTS:.spec.taints[*].key'
      # Expected: all rows show DISK=False, TAINTS=<none>
      ```

- [ ] `flux-system` pods scheduled and running
      ```bash
      kubectl get pods -n flux-system --context=staging
      # Expected: no Pending pods
      ```

- [ ] Flux catches up to the latest commit on its own (no manual reconcile needed once the taint clears)
      ```bash
      flux get kustomizations --context=staging
      # Expected: all Ready=True at the current main revision
      ```

- [ ] **Watch for a second wave** - if staging had a large reconciliation backlog, catching up can pull a burst of new images/logs and refill the disk again within minutes. Re-check node disk pressure a few minutes after the first cleanup, not just immediately after.

## Root Cause

### What Caused This

Two independent, compounding gaps: (1) no Docker log rotation configured on the VM, so every container's stdout/stderr grew forever; (2) k3d node containerd state accumulating every historical image version from routine dependency bumps, since kubelet's own image GC wasn't keeping pace inside the nested container environment.

### Why It Manifested Now

Both gaps are slow-burn - they cross the kubelet disk-pressure eviction threshold (~85-90% by default) only after weeks/months of accumulation, with no alert catching the approach.

### Component Interaction

```
Docker json-file logging (no rotation) ─┐
                                          ├─→ VM disk fills → kubelet reports DiskPressure
k3d node containerd image bloat ────────┘         │
                                                     ▼
                                   node.kubernetes.io/disk-pressure taint applied
                                                     │
                                                     ▼
                          flux-system pods can't schedule → reconciliation halts entirely
```

## Prevention

### Immediate Prevention

- [x] Docker log rotation configured (`max-size: 10m`, `max-file: 3`)
      **Impact**: caps total log growth per container at 30MB going forward

### Long-term Prevention

- [ ] Confirm `KubeNodeDiskPressure`-class Prometheus alerts actually route through Alertmanager to a real notification channel (ntfy)
      - **Why**: this incident went undetected for six weeks; alerting is the actual gap, not just the disk usage itself
      - **Effort**: ~30 minutes to audit Alertmanager routing config

- [ ] Consider periodic k3d node recreation for staging
      - **Why**: since log rotation only applies to newly-created containers, staging's long-lived node containers won't benefit from the fix until recreated
      - **Effort**: low - staging is explicitly disposable by design

### Documentation Updates

- [x] Document in war story: [`docs/war-stories/staging-disk-pressure-docker-bloat.md`](../war-stories/staging-disk-pressure-docker-bloat.md)

## Related Issues

- **[Deployment Recreate Strategy Stuck Rollout](deployment-recreate-strategy-stuck-rollout.md)** - discovered while catching staging up after this outage; same root incident, different symptom
- **[PostgreSQL Replica Recovery](postgres-replica-networkpolicy-dataplane-sync.md)** - the WAL timeline fork this outage most likely triggered via an uncontrolled failover

## Original War Story

For the complete investigation narrative, see: [`docs/war-stories/staging-disk-pressure-docker-bloat.md`](../war-stories/staging-disk-pressure-docker-bloat.md)

## References

- [Docker logging drivers documentation](https://docs.docker.com/config/containers/logging/configure/)
- [Kubernetes node-pressure eviction](https://kubernetes.io/docs/concepts/scheduling-eviction/node-pressure-eviction/)

---

**Last Updated**: 2026-07-02
**Tested On**: Staging (k3d in Proxmox VM)
**Success Rate**: 1/1 incidents resolved (100%)
**Original Incident**: 2026-07-02 (condition dated back to 2026-05-20)
