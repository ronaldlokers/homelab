# Migrate etcd Data Directory from SD/eMMC to NVMe

> **Status: corrected, not yet executed.** The procedure was rewritten on 2026-08-01 after
> the original version was validated against live node state and found to contain two
> errors that would each have caused it to fail — see [Corrections](#corrections-to-the-original-procedure).
> Diagnosis and pre-flight check 1 have been completed and their results are recorded below.
> The resolution steps themselves have still never been run. Treat them as reviewed, not rehearsed.

## Quick Reference

- **Severity**: High — was Medium when first written. An etcd apply p50 of ~2s and a 27s maximum caused a live API-server outage on 2026-08-01 (`ServiceUnavailable`, `[-]etcd failed`) during a Longhorn upgrade, with leader re-elections across cnpg, all four Flux controllers and Longhorn's snapshotter.
- **Estimated Time to Resolve**: ~15-30 minutes per node, times 3 nodes, plus stability monitoring between each — budget a half day with margin.
- **Symptoms**: etcd applies taking seconds instead of milliseconds; watch-based changes (NetworkPolicy propagation, controller reconciliation) delayed from minutes to days; under load, outright API `ServiceUnavailable`.
- **Affected Components**: All 3 production control-plane nodes — confirmed identical layout on all three.
- **Environment**: Production only. Staging is k3d in a VM, different storage model entirely.
- **Prerequisites**: **SSH access to each node as root.** `kubectl debug` is *not* sufficient — see [Corrections](#corrections-to-the-original-procedure).
- **Blocking prerequisite**: `storageReserved` must be non-zero on every Longhorn node disk. Done in #134 (10Gi each).

## Corrections to the original procedure

The version written on 2026-07-09 contained two errors. Both are fixed below; they are recorded here because each would have failed in a way that looked like success.

**1. The target path did not exist on the NVMe.** The original said to copy etcd to `/mnt/nvme-etcd`, deliberately outside `/mnt/longhorn`, to keep it separate from Longhorn's data. That separation is not achievable on this hardware:

```
nvme0n1      476.9G
`-nvme0n1p1  476.9G  /mnt/longhorn     <- the only partition on the device
mmcblk0       58.2G
`-mmcblk0p2   57.7G  /                 <- etcd is here
```

`/mnt/nvme-etcd` would have been a plain directory on the eMMC root filesystem. The procedure would have copied etcd from eMMC to eMMC, bind-mounted it, restarted k3s, and the node would have returned `Ready` with latency completely unchanged. This runbook now uses `/mnt/longhorn/etcd`.

Sharing the filesystem with Longhorn is safe: Longhorn manages only `replicas/` and `longhorn-disk.cfg` within its disk path and ignores everything else — `lost+found` has sat there untouched since 2025-12-06. The cost is I/O contention between etcd and replica writes, accepted deliberately because contended NVMe is far better than uncontended eMMC.

**2. `kubectl exec` cannot be used for the resolution steps.** The original drove every step through a debug pod on the target node. Step 1 stops k3s, which stops that node's kubelet — and `kubectl exec` reaches a pod *through* its node's kubelet. From Step 2 onward you would have had no way to run anything on the node you had just taken offline.

Separately, `kubectl exec` against this cluster has been observed returning **empty output with exit code 0** while etcd is under load — it silently produced wrong answers twice during validation. Do not trust it for verification on a degraded control plane. Use SSH.

## Diagnosis Steps

**Completed 2026-08-01. Recorded here as the baseline; re-run only if node state may have changed.**

All three nodes confirmed on eMMC, none previously remediated (`readlink -f` returns the path unchanged):

```
node          etcd device       size  used  avail        nvme free
kube-srv-1    /dev/mmcblk0p2     57G   41G   15G (75%)   144G
kube-srv-2    /dev/mmcblk0p2     57G   38G   17G (70%)   257G
kube-srv-3    /dev/mmcblk0p2     57G   38G   17G (70%)   205G
```

etcd apply latency, `journalctl -u k3s`, 60-minute window:

```
node          warnings   p50      p90      p99       max      >1s    >5s
kube-srv-1      1,833   1,929ms  6,606ms  18,038ms  25,099ms  1,136  318
kube-srv-2      1,624   1,222ms  6,057ms  10,086ms  18,853ms    872  246
kube-srv-3      2,088   2,299ms  8,123ms  20,187ms  27,170ms  1,373  508
```

From the API server's own metrics, 1.19% of all etcd requests exceeded 1s and 0.26% exceeded 4s. Healthy etcd on NVMe is effectively 0% over 1s.

etcd data directory size: **424M** — the copy itself takes seconds.

To re-run diagnosis on a node:

```bash
ssh root@<node>
readlink -f /var/lib/rancher/k3s/server/db/etcd    # expect: same path, no symlink
df -h /var/lib/rancher/k3s/server/db/etcd          # expect: /dev/mmcblk0p2 before migration
journalctl -u k3s --since '60 min ago' --no-pager | grep -c "apply request took too long"
```

## Pre-Flight Safety Checks

**Do these before touching any node. Do not proceed if any check is unclear or fails.**

### 1. etcd snapshot backups — verified 2026-08-01

All three nodes have current 12-hourly snapshots (5 each, most recent `Aug 1 12:00`, ~50-57MB). Re-confirm on the target node immediately before starting:

```bash
ssh root@<node> ls -la /var/lib/rancher/k3s/server/db/snapshots/
```

If empty or missing, **stop** — take one manually with `k3s etcd-snapshot save` and confirm it succeeds first.

### 2. etcd cluster health baseline — must be re-run fresh each time

```bash
kubectl get --raw /healthz/etcd --context=production   # expect: ok
kubectl get nodes --context=production                 # expect: all 3 Ready
```

This is point-in-time. Re-run it before **each** node, not once at the start. Do not begin storage surgery on a cluster that is not already known-good.

### 3. Longhorn reserved space — verified 2026-08-01

etcd will share the Longhorn filesystem, so Longhorn must be prevented from filling it:

```bash
kubectl -n longhorn-system get nodes.longhorn.io -o custom-columns=\
'NODE:.metadata.name,RESERVED:.spec.disks.ssd-disk.storageReserved' --context=production
```

Expect `10737418240` (10Gi) on every node. Set in #134. If this is `0`, **stop** — a full Longhorn disk would stop etcd writing and take down the control plane.

### 4. Identify the etcd leader — migrate it last

k3s does not ship `etcdctl`, but exposes etcd metrics on `127.0.0.1:2381`:

```bash
ssh root@<node> curl -s http://127.0.0.1:2381/metrics | grep '^etcd_server_is_leader '
# 1 = leader, 0 = follower
```

As of 2026-08-01: **kube-srv-1 is the leader**; kube-srv-2 and kube-srv-3 are followers.

**Recommended order: kube-srv-2 → kube-srv-3 → kube-srv-1.** Followers first so an error on the most uncertain run does not also force a leader election. kube-srv-2 first specifically — it has the most free NVMe and the lowest latency of the three. Re-check the leader before the final node; it may have moved.

## Resolution Steps

**One node at a time. Verify fully, then move to the next.** This 3-node etcd tolerates exactly one member down — taking a second offline mid-procedure risks quorum loss and a cluster you cannot recover without a snapshot restore.

Everything below runs over **SSH on the target node**, as root.

### Step 1: Stop k3s

**Why**: etcd's data directory cannot be safely copied while the process holds it open.

```bash
systemctl stop k3s
```

**Verify from your workstation**: `kubectl get nodes --context=production` shows the target `NotReady`, the other two `Ready`.

### Step 2: Copy the etcd data directory to the NVMe

**Why**: `rsync -a` preserves ownership, permissions and timestamps, which etcd's on-disk format depends on. `rsync` is present on these nodes.

```bash
mkdir -p /mnt/longhorn/etcd
rsync -a /var/lib/rancher/k3s/server/db/etcd/ /mnt/longhorn/etcd/
```

**Verify** — sizes must match:

```bash
du -sh /var/lib/rancher/k3s/server/db/etcd /mnt/longhorn/etcd
```

### Step 3: Move the original aside — do not delete

**Why**: this is the rollback path. Keep it until the node has been stable for a full monitoring window.

```bash
mv /var/lib/rancher/k3s/server/db/etcd /var/lib/rancher/k3s/server/db/etcd.bak-$(date +%Y%m%d)
```

### Step 4: Bind-mount the NVMe copy at the original path

**Why**: k3s needs no configuration change — it sees the same path, now backed by NVMe. Use a bind mount, not a symlink; Longhorn and k3s both handle bind mounts correctly and symlinks are not reliably resolved.

```bash
mkdir -p /var/lib/rancher/k3s/server/db/etcd
mount --bind /mnt/longhorn/etcd /var/lib/rancher/k3s/server/db/etcd
echo "/mnt/longhorn/etcd /var/lib/rancher/k3s/server/db/etcd none bind 0 0" >> /etc/fstab
```

**Verify the mount is real and on the right device** — this is the check that catches a no-op migration:

```bash
findmnt /var/lib/rancher/k3s/server/db/etcd
# SOURCE must show /dev/nvme0n1p1, NOT /dev/mmcblk0p2

df -h /var/lib/rancher/k3s/server/db/etcd | tail -1
# Filesystem column must be /dev/nvme0n1p1
```

If either still shows `mmcblk0p2`, **stop and roll back** — the migration has not moved anything.

### Step 5: Restart k3s

```bash
systemctl start k3s
```

**Verify from your workstation**, after ~30-60s:

```bash
kubectl get nodes --context=production          # target back to Ready
kubectl get --raw /healthz/etcd --context=production   # ok
```

**If this fails**: roll back immediately (below) rather than debugging in place on a control-plane node with etcd down.

### Step 6: Monitor before touching the next node

Wait **at least 15 minutes**, and confirm all of:

- Node stays `Ready`
- `journalctl -u k3s --since '15 min ago' | grep -c "apply request took too long"` has dropped sharply — pre-migration this node was logging 1,600-2,100 per hour
- All 19 Longhorn volumes still `attached` / `healthy`:
  ```bash
  kubectl -n longhorn-system get volumes.longhorn.io --context=production
  ```
- `findmnt` still shows the bind mount on `/dev/nvme0n1p1`

Only then move to the next node and repeat Steps 1-6.

## Verification

After all 3 nodes are migrated:

- [ ] All 3 nodes `Ready` — `kubectl get nodes --context=production`
- [ ] etcd healthy — `kubectl get --raw /healthz/etcd --context=production`
- [ ] **Every node's etcd is genuinely on NVMe** — on each: `findmnt /var/lib/rancher/k3s/server/db/etcd` shows `/dev/nvme0n1p1`
- [ ] Apply latency collapsed — `journalctl -u k3s --since '60 min ago' | grep -c "apply request took too long"` should be near zero, against a pre-migration baseline of 1,624-2,088 per node per hour
- [ ] All 19 Longhorn volumes `attached` / `healthy`
- [ ] Fix survives reboot — reboot one node deliberately and confirm the `/etc/fstab` bind mount re-establishes and `findmnt` still shows NVMe
- [ ] Only after a stable week: remove the `etcd.bak-*` directories to reclaim eMMC space

## Rollback

If a node fails to come back healthy after Step 5, over SSH on that node:

```bash
systemctl stop k3s
umount /var/lib/rancher/k3s/server/db/etcd
rmdir /var/lib/rancher/k3s/server/db/etcd
mv /var/lib/rancher/k3s/server/db/etcd.bak-<date> /var/lib/rancher/k3s/server/db/etcd
sed -i '\|/mnt/longhorn/etcd|d' /etc/fstab
systemctl start k3s
```

Verify the node returns to `Ready` on its original eMMC data before deciding anything else. Do not retry the migration on that node until the failure is understood.

If the node will not return at all, restore from the snapshot taken in pre-flight check 1 — see the k3s cluster-reset and snapshot-restore documentation. This is why check 1 is mandatory.

## Root Cause

See [`docs/war-stories/etcd-emmc-storage-latency.md`](/docs/war-stories/etcd-emmc-storage-latency.md) for the full investigation. Summary: k3s's default etcd data directory was never relocated off the node's SD/eMMC root filesystem during initial provisioning (`docs/setup.md` never sets a `--data-dir` flag); the NVMe on each node was provisioned and reserved exclusively for Longhorn.

## Prevention

### Long-term Prevention

- [ ] Add monitoring for etcd request latency
      - **Why**: this was invisible to existing alerting — etcd never went "down", it just got slower, until an unrelated investigation surfaced it. It then caused a real outage before anything alerted.
      - **How**: k3s's embedded etcd exposes Prometheus metrics on `127.0.0.1:2381` (`etcd_disk_wal_fsync_duration_seconds`, `etcd_request_duration_seconds`). Confirm these are scraped and alert on p99 fsync above ~100-500ms.
- [ ] Document the intentional storage layout in `docs/architecture.md`, including which paths live on which device — the gap in documentation is part of why this went unnoticed.
- [ ] Reconsider a dedicated etcd partition. Sharing the Longhorn filesystem is a deliberate compromise forced by there being one NVMe partition. If etcd latency shows contention with Longhorn replica I/O, shrinking `nvme0n1p1` for a dedicated `nvme0n1p2` is the clean fix — but it requires draining Longhorn replicas from each node first.

### Documentation Updates

- [ ] Once executed, change the status note at the top to a normal resolved-runbook header and add real execution notes and gotchas.
- [ ] Update `docs/architecture.md`'s Storage Architecture section with the corrected etcd location.

## Related Issues

- **[War Story: etcd on SD Card](/docs/war-stories/etcd-emmc-storage-latency.md)** - the investigation that found this
- **[NetworkPolicy Connectivity Troubleshooting](networkpolicy-connectivity-troubleshooting.md)** - general methodology

## References

- [etcd Hardware recommendations](https://etcd.io/docs/latest/op-guide/hardware/) - explicit guidance against slow/consumer-grade storage for etcd
- [k3s data directory documentation](https://docs.k3s.io/cli/server) - `--data-dir` flag and defaults
- [Longhorn: use bind mounts, not symlinks, for alternative disk paths](https://longhorn.io/docs/1.12.0/nodes-and-volumes/nodes/multidisk/)

---

**Last Updated**: 2026-08-01
**Tested On**: Diagnosis and pre-flight validated against live production nodes 2026-08-01. Resolution steps not yet executed.
**Success Rate**: N/A — corrected but unexecuted procedure
