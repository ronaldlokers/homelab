# Migrate etcd from eMMC to NVMe

**Status:** corrected 2026-08-01, not yet executed. Diagnosis + pre-flight 1/3/4 already done.
**Access:** SSH as root on each node. `kubectl exec` does NOT work — Step 1 kills the kubelet it relies on.
**Time:** ~30 min per node + 15 min monitoring. Three nodes.
**Rule:** one node at a time. Two nodes down = quorum loss.

## Facts (measured 2026-08-01)

| node | ip | etcd on | nvme free | apply p50 | apply max | warnings/hr |
|---|---|---|---|---|---|---|
| kube-srv-1 | 10.0.40.101 | /dev/mmcblk0p2 | 144G | 1,929ms | 25,099ms | 1,833 |
| kube-srv-2 | 10.0.40.102 | /dev/mmcblk0p2 | 257G | 1,222ms | 18,853ms | 1,624 |
| kube-srv-3 | 10.0.40.103 | /dev/mmcblk0p2 | 205G | 2,299ms | 27,170ms | 2,088 |

etcd data size: 424M. NVMe has one partition, mounted at `/mnt/longhorn`.

## Order — one node at a time, in this sequence

```bash
ssh root@10.0.40.102     # kube-srv-2  — start here (follower, most free space)
ssh root@10.0.40.103     # kube-srv-3  — second     (follower)
ssh root@10.0.40.101     # kube-srv-1  — last       (etcd leader on 2026-08-01)
```

Re-check the leader before the final node — it may have moved.

## Pre-flight

Run before **each** node. Stop if any check fails.

```bash
# 1. cluster healthy  -> expect "ok" and 3x Ready
kubectl get --raw /healthz/etcd --context=production
kubectl get nodes --context=production

# 2. snapshots exist on target  -> expect ~5 files
ssh root@<target-ip> ls /var/lib/rancher/k3s/server/db/snapshots/

# 3. longhorn reserved  -> expect 10737418240 on all nodes
kubectl -n longhorn-system get nodes.longhorn.io --context=production \
  -o custom-columns='NODE:.metadata.name,RESERVED:.spec.disks.ssd-disk.storageReserved'

# 4. confirm target is not the leader  -> expect 0
ssh root@<target-ip> "curl -s http://127.0.0.1:2381/metrics | grep '^etcd_server_is_leader '"

# ...or check all three at once:
for ip in 10.0.40.101 10.0.40.102 10.0.40.103; do
  echo -n "$ip leader="
  ssh root@$ip "curl -s http://127.0.0.1:2381/metrics | grep '^etcd_server_is_leader ' | awk '{print \$2}'"
done
```

## Procedure — repeat per node

All commands run on the node via `ssh root@<target-ip>` unless marked *(workstation)*.
Substitute the IP from the Order block above.

### 1. Stop k3s

```bash
systemctl stop k3s
```

*(workstation)* `kubectl get nodes --context=production` → target `NotReady`, other two `Ready`.

### 2. Copy etcd to NVMe

```bash
mkdir -p /mnt/longhorn/etcd
rsync -a /var/lib/rancher/k3s/server/db/etcd/ /mnt/longhorn/etcd/
du -sh /var/lib/rancher/k3s/server/db/etcd /mnt/longhorn/etcd
```

→ both sizes must match.

### 3. Move original aside

```bash
mv /var/lib/rancher/k3s/server/db/etcd /var/lib/rancher/k3s/server/db/etcd.bak-$(date +%Y%m%d)
```

Do not delete. This is the rollback.

### 4. Bind-mount

```bash
mkdir -p /var/lib/rancher/k3s/server/db/etcd
mount --bind /mnt/longhorn/etcd /var/lib/rancher/k3s/server/db/etcd
echo "/mnt/longhorn/etcd /var/lib/rancher/k3s/server/db/etcd none bind 0 0" >> /etc/fstab
findmnt /var/lib/rancher/k3s/server/db/etcd
```

→ SOURCE must be `/dev/nvme0n1p1`.
→ **If it shows `/dev/mmcblk0p2`: STOP, roll back.** Nothing moved.

### 5. Start k3s

```bash
systemctl start k3s
```

*(workstation)*, after 30-60s:

```bash
kubectl get nodes --context=production                  # target Ready
kubectl get --raw /healthz/etcd --context=production    # ok
```

→ If either fails: roll back now. Do not debug in place.

### 6. Monitor 15 min, then verify all four

```bash
# on node
journalctl -u k3s --since '15 min ago' --no-pager | grep -c "apply request took too long"
findmnt /var/lib/rancher/k3s/server/db/etcd
```
```bash
# workstation
kubectl get nodes --context=production
kubectl -n longhorn-system get volumes.longhorn.io --context=production | grep -c healthy
```

- [ ] node stays `Ready`
- [ ] warning count near zero (was 1,624-2,088/hr)
- [ ] `findmnt` still `/dev/nvme0n1p1`
- [ ] 19 volumes healthy

Only then start the next node.

## Rollback

```bash
systemctl stop k3s
umount /var/lib/rancher/k3s/server/db/etcd
rmdir /var/lib/rancher/k3s/server/db/etcd
mv /var/lib/rancher/k3s/server/db/etcd.bak-<date> /var/lib/rancher/k3s/server/db/etcd
sed -i '\|/mnt/longhorn/etcd|d' /etc/fstab
systemctl start k3s
```

Node must return `Ready` on eMMC data before anything else. Do not retry until the failure is understood.
If the node will not return at all: restore from the pre-flight snapshot (k3s cluster-reset + snapshot restore).

## After all three nodes

- [ ] 3x `Ready`, `/healthz/etcd` = ok
- [ ] `findmnt` = `/dev/nvme0n1p1` on all three
- [ ] warning count near zero on all three
- [ ] 19 volumes healthy
- [ ] reboot one node → bind mount re-establishes from fstab
- [ ] after one stable week: `rm -rf /var/lib/rancher/k3s/server/db/etcd.bak-*`
- [ ] update this file's status header; update `docs/architecture.md` storage section

## Notes

**Why `/mnt/longhorn/etcd`, not `/mnt/nvme-etcd`:** the NVMe has one partition, already mounted at `/mnt/longhorn`. `/mnt/nvme-etcd` would be a directory on eMMC — the original runbook's path would have copied eMMC→eMMC and reported success. Longhorn only manages `replicas/` and `longhorn-disk.cfg`; `lost+found` has been untouched there since 2025-12-06. Trade-off: etcd shares the device with replica I/O. Accepted — contended NVMe beats uncontended eMMC.

**Why SSH, not `kubectl exec`:** Step 1 stops k3s → stops kubelet → `kubectl exec` to that node dies. Also observed returning empty output with exit 0 under etcd load.

**Follow-up:** if etcd latency shows Longhorn contention, shrink `nvme0n1p1` for a dedicated `nvme0n1p2` (requires draining replicas per node first). Add alerting on `etcd_disk_wal_fsync_duration_seconds` p99 > 100ms from `127.0.0.1:2381`.

## Links

- [War story: etcd on eMMC](/docs/war-stories/etcd-emmc-storage-latency.md)
- [etcd hardware guidance](https://etcd.io/docs/latest/op-guide/hardware/)
- [Longhorn: bind mounts, not symlinks](https://longhorn.io/docs/1.12.0/nodes-and-volumes/nodes/multidisk/)

---
**Last updated:** 2026-08-01 · **Tested:** diagnosis + pre-flight only; resolution steps unexecuted
