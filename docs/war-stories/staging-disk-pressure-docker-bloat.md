# Staging Cluster Frozen for Weeks by Unbounded Docker Disk Growth

**Date**: July 2, 2026
**Severity**: High
**Duration**: ~6 weeks of silent degradation, ~1 hour to diagnose and resolve
**Impact**: Staging cluster's GitOps pipeline completely frozen since May 20, 2026 - no reconciliation of any kind, ~40 commits of drift accumulated unnoticed

## The Problem

### Initial Symptoms

A routine repo audit ("check if there are things broken, or if things can be improved") turned up a `pgadmin` pod in production stuck in `ContainerCreating` for 4+ days. While investigating that, staging was checked as a comparison point and turned out to be in far worse shape:

```
NAME                      	REVISION          	SUSPENDED	READY  	MESSAGE
apps                      	main@sha1:fa2af35b	False    	Unknown	Reconciliation in progress
flux-system               	main@sha1:6b6477bf	False    	Unknown	Reconciliation in progress
infrastructure-configs    	main@sha1:f69abbc3	False    	False  	dependency 'flux-system/infrastructure-controllers' is not ready
infrastructure-controllers	main@sha1:6b6477bf	False    	Unknown	Reconciliation in progress
monitoring-controllers    	main@sha1:fa2af35b	False    	Unknown	Reconciliation in progress
```

Every Flux Kustomization was stuck mid-reconcile. `kubectl get pods -n flux-system --context=staging` showed why:

```
NAME                                       READY   STATUS      RESTARTS       AGE
notification-controller-59cb8b7957-kcnmw   0/1     Pending     0              4d16h
notification-controller-654c97f68d-567wd   0/1     Pending     0              43d
source-controller-74fc857f77-x66wh         0/1     Pending     0              4d16h
```

`source-controller` - the component that pulls new commits from GitHub - had been `Pending` for 4.5 days. Without a source-controller, nothing downstream could ever reconcile.

### Error Messages

```
Warning  FailedScheduling  9m40s (x1348 over 4d16h)  default-scheduler  0/4 nodes are available: 4 node(s) had untolerated taint {node.kubernetes.io/disk-pressure: }. preemption: 0/4 nodes are available: 4 Preemption is not helpful for scheduling.
```

All four k3d nodes carried the `node.kubernetes.io/disk-pressure` taint:

```
NAME                   DISK   TAINTS
k3d-staging-agent-0    True   node.kubernetes.io/disk-pressure
k3d-staging-agent-1    True   node.kubernetes.io/disk-pressure
k3d-staging-agent-2    True   node.kubernetes.io/disk-pressure
k3d-staging-server-0   True   node.kubernetes.io/disk-pressure
```

`kubectl describe node` on the server node showed the condition had been `True` since **May 20, 2026** - over six weeks before it was noticed:

```
DiskPressure     True    Thu, 02 Jul 2026 14:58:02 +0000   Wed, 20 May 2026 07:15:07 +0000   KubeletHasDiskPressure   kubelet has disk pressure
```

## The Investigation

### Step 1: Confirm It's Actually a Scheduling Problem

The `Pending` state plus `FailedScheduling` events with `disk-pressure` in the taint name pointed straight at the node condition rather than anything application-level. This ruled out RBAC, image pull failures, or resource quota exhaustion as causes - the pods simply had nowhere they were allowed to land.

### Step 2: Locate the Actual Disk Usage

k3d nodes are Docker containers, so their disk footprint lives on the Proxmox VM hosting the k3d Docker daemon, not inside `kubectl`-visible storage. On the VM:

```bash
df -h /
docker system df
```

This split the problem into two layers worth checking separately: the host's own Docker state, and the containerd state living *inside* each k3d node container (since k3d nodes are themselves Docker containers whose own filesystem holds the cluster's images and container logs).

### Step 3: Find Where the Bytes Actually Are

```bash
sudo du -sh /var/lib/docker/volumes/* | sort -rh | head
sudo du -sh /var/lib/docker/containers/* | sort -rh | head
```

Two very different numbers turned up:
- `/var/lib/docker/volumes` - **51G** (k3d node containerd state: images, containerd content)
- `/var/lib/docker/containers` - **26G** (Docker container logs)

The second number was the surprise. **26 gigabytes of container logs**, for a homelab staging cluster.

### Step 4: Understand the Log Growth

Docker's default `json-file` log driver has **no rotation configured out of the box**. Every container's stdout/stderr grows unbounded for the life of the container. The k3d node containers had been running for 212+ days at that point - over 7 months of unrotated logs from every pod that had ever run inside them.

## The Root Cause

Two independent, compounding problems:

1. **No log rotation on the Docker daemon.** `/etc/docker/daemon.json` didn't exist at all, meaning Docker was running with hard-coded defaults - unbounded `json-file` logging. Every container's logs grew forever.

2. **Unbounded image accumulation inside the k3d node containers.** Months of Renovate-driven image bumps (Flux, controllers, application images) left every previous image version cached in each node's containerd state, since kubelet's own image garbage collection wasn't keeping pace.

Neither problem is unique to this incident - they're standing structural gaps that had been quietly accumulating since the cluster was created, until they crossed the kubelet disk-pressure eviction threshold (~85-90% by default) on May 20 and silently froze the entire GitOps pipeline. No alert fired, because the thing that broke (Flux reconciliation itself) is also the thing that would normally be responsible for deploying alerting rule changes - a full-circle failure mode.

## The Solution

### Immediate Recovery

1. **Truncate (not delete) the container log files**, since Docker holds the file handles open and deleting the files wouldn't free space until the containers restarted:
```bash
sudo truncate -s 0 /var/lib/docker/containers/*/*-json.log
```
This alone recovered the ~26G immediately.

2. **Prune unused images inside each k3d node container**, since `crictl rmi --prune` only removes images not currently backing a running pod:
```bash
for n in k3d-staging-server-0 k3d-staging-agent-0 k3d-staging-agent-1 k3d-staging-agent-2; do
  docker exec $n crictl rmi --prune
done
```

3. **Prune the host's own Docker state** as a general cleanup:
```bash
docker system prune -a
```

### The Second Wave

Cleanup alone wasn't quite enough. Once the disk-pressure taint cleared and Flux resumed, staging had ~40 commits of accumulated changes to catch up on all at once - pulling a burst of new images and generating a burst of new logs in a very short window. This refilled the disk almost immediately, and `alloy` DaemonSet pods were observed being actively evicted in real time:

```
Warning  Evicted    47s   kubelet   The node had condition: [DiskPressure].
```

This made clear that a one-time cleanup wasn't the actual fix - the *rate* of log growth needed to be bounded, not just the current backlog cleared.

### Long-Term Fix

Added log rotation to the Docker daemon config on the VM:

```json
{
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
```

```bash
sudo tee /etc/docker/daemon.json <<'EOF'
{
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
EOF
sudo systemctl restart docker
```

**Important caveat discovered during this fix**: log rotation settings in `daemon.json` only apply to containers created *after* the config change - the existing long-lived k3d node containers keep their original unbounded-logging behavior until they're recreated. Since k3d nodes are meant to be recreated periodically anyway (staging is explicitly disposable per the cluster's own design), this is an acceptable gap rather than something requiring an immediate node rebuild.

## Verification

```bash
kubectl get nodes --context=staging -o custom-columns='NAME:.metadata.name,DISK:.status.conditions[?(@.type=="DiskPressure")].status,TAINTS:.spec.taints[*].key'
# NAME                   DISK    TAINTS
# k3d-staging-agent-0    False   <none>
# k3d-staging-agent-1    False   <none>
# k3d-staging-agent-2    False   <none>
# k3d-staging-server-0   False   <none>
```

All four nodes clear, no taints. `flux get kustomizations --context=staging` subsequently caught up to the latest revision on its own, without any manual reconcile needed - the taint clearing was the only blocker.

## Prevention

### 1. Log Rotation (Done)
`max-size: 10m`, `max-file: 3` per container caps total log growth at 30MB/container going forward.

### 2. Monitoring Gap (Not Yet Addressed)
This is the most important open item: **kube-prometheus-stack ships `KubeNodeDiskPressure`-class alerts by default**, but nothing paged anyone for six weeks while staging was frozen. The likely gap is Alertmanager routing rather than a missing alert rule - worth auditing separately, and wiring to the existing ntfy integration used for production alerts.

### 3. Recognize the Blast Radius of "Just a Log Rotation Gap"
The lesson generalizes beyond Docker specifically: **anything that silently degrades the system responsible for detecting and fixing silent degradation is a single point of failure for observability itself.** Flux couldn't reconcile a fix for the disk filling up, because the disk filling up is what stopped Flux from reconciling.

## Lessons Learned

1. **Disposable != unmonitored** - staging being explicitly lower-stakes doesn't mean it's fine for it to silently die for six weeks; it's still the environment new work gets validated in first.
2. **Docker's default logging has no rotation** - this is easy to forget on any long-lived Docker host, not just k3d-based Kubernetes nodes.
3. **`truncate`, not `rm`, for open log files** - deleting a file Docker still has open doesn't free the disk space until the process restarts.
4. **Separate "clear the backlog" from "fix the rate"** - the first cleanup pass looked successful until the very next reconciliation burst refilled the disk in minutes, because only the symptom (accumulated bytes) was addressed, not the cause (unbounded growth rate).
5. **A GitOps system that fails can't self-heal its own root cause** - if the failure prevents the reconciler from running, it can't apply the fix that would resolve it. This class of failure needs to be caught by something outside the loop it breaks (host-level monitoring, not cluster-level).
6. **k3d nodes are Docker containers with their own nested disk usage** - `kubectl` and `df` on the host tell two different stories; both layers need checking independently.

## Related Documentation

- [Runbook: Staging Cluster Frozen by Disk Pressure](/docs/runbooks/staging-disk-pressure-docker-cleanup.md) - action-oriented version of this investigation
- [Repository Architecture](/docs/architecture.md) - staging/production cluster topology
- [Deployment Recreate Strategy Server-Side-Apply Conflict](deployment-recreate-strategy-ssa-conflict.md) - a downstream incident discovered while catching staging back up after this outage
- [PostgreSQL Replica Recovery via NetworkPolicy Dataplane Sync](postgres-replica-networkpolicy-dataplane-sync.md) - another downstream incident from the same catch-up window

## Commands Reference

**Check node disk pressure and taints**:
```bash
kubectl get nodes --context=staging -o custom-columns='NAME:.metadata.name,DISK:.status.conditions[?(@.type=="DiskPressure")].status,TAINTS:.spec.taints[*].key'
```

**Find Docker disk usage by category**:
```bash
docker system df -v
sudo du -sh /var/lib/docker/volumes/* /var/lib/docker/containers/* | sort -rh | head
```

**Truncate all container logs (frees space without restarting containers)**:
```bash
sudo truncate -s 0 /var/lib/docker/containers/*/*-json.log
```

**Prune unused images inside a k3d node**:
```bash
docker exec <k3d-node-name> crictl rmi --prune
```
