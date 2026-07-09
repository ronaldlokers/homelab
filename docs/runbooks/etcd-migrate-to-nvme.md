# Migrate etcd Data Directory from SD/eMMC to NVMe

> **Status: proposed, not yet executed.** Unlike other runbooks in this directory, this one does not document a resolved incident — it's a reviewed-but-unexecuted procedure written up after root-causing the issue in [`docs/war-stories/etcd-emmc-storage-latency.md`](/docs/war-stories/etcd-emmc-storage-latency.md). Read that first for full context. Treat every step here as needing a fresh sanity check against actual node state before running it, not as a rehearsed, previously-successful sequence.

## Quick Reference

- **Severity**: Medium — not an active outage. A latent cause of unpredictable delays in anything that depends on the API server's watch mechanism (NetworkPolicy propagation, controller reconciliation, etc.)
- **Estimated Time to Resolve**: ~15-30 minutes per node, times 3 nodes, plus stability monitoring between each — budget a half day with margin, not a quick evening task
- **Symptoms**: etcd key reads taking multiple seconds instead of ~100ms (see war story); NetworkPolicy or other watch-based changes taking anywhere from minutes to multiple days to actually apply on a node
- **Affected Components**: All 3 production control-plane nodes (`kube-srv-1`, `kube-srv-2`, `kube-srv-3`) — confirmed identical layout on all three
- **Environment**: Production only (staging is a single k3d node in a VM, different storage model entirely — check before assuming this applies there)
- **Prerequisites**: `kubectl` access with node-debug permissions (`kubectl debug node`, as used throughout the linked war story — no direct SSH required), or SSH access as a fallback

## Diagnosis Steps

### 1. Confirm etcd is actually on the slow device

```bash
kubectl debug node/<node-name> --context=production --image=nicolaka/netshoot --profile=sysadmin -- sleep 120
# wait ~10s for it to schedule, then:
kubectl get pods -n database --context=production | grep node-debugger
kubectl exec -n database <debug-pod> --context=production -- chroot /host readlink -f /var/lib/rancher/k3s/server/db/etcd
kubectl exec -n database <debug-pod> --context=production -- chroot /host df -h / /mnt/longhorn
```

**This is the right runbook if:**
- ✅ `readlink -f` resolves to the plain path with no symlink (i.e. it's really on whatever backs `/`)
- ✅ `df -h /` shows `/dev/mmcblk0p2` (or similar SD/eMMC device name), not the NVMe
- ✅ A live etcd read-latency check (see the war story's "Commands Reference") shows multi-second reads, not ~100ms

**This is NOT the right runbook if:**
- ❌ `/var/lib/rancher/k3s/server/db/etcd` is already a symlink or bind-mounted elsewhere — someone may have already partially remediated this
- ❌ Latency looks normal — the current symptom may be something else entirely (check CPU/memory pressure, network, or the NetworkPolicy troubleshooting runbook instead)

Clean up the debug pod after diagnosis: `kubectl delete pod <debug-pod> -n database --context=production`

## Pre-Flight Safety Checks

**Do these before touching any node. Do not proceed if either check is unclear or fails.**

### 1. Confirm etcd snapshot backups exist and are recent

This repo's docs don't mention etcd snapshot configuration anywhere — verify fresh, don't assume it's set up:

```bash
# k3s takes local snapshots by default every 12h unless disabled; check each node:
kubectl exec -n database <debug-pod> --context=production -- \
  chroot /host ls -la /var/lib/rancher/k3s/server/db/snapshots/
```

If this directory is empty or missing, **stop** — take a manual snapshot first (`k3s etcd-snapshot save`, run on the node via the debug pod's chroot) and confirm it succeeds before proceeding with anything below.

### 2. Confirm current etcd cluster health as a baseline

```bash
kubectl get --raw /healthz/etcd --context=production
kubectl get nodes --context=production   # all 3 should be Ready, no surprises
```

Only proceed if this baseline is clean. If any node is already unhealthy, fix that first — do not start storage surgery on a cluster that isn't already in a known-good state.

## Resolution Steps

**Repeat this entire sequence for ONE node, verify full stability, then move to the next. Never have more than one control-plane node offline at a time** — this 3-node etcd cluster only tolerates a single node down; taking a second one offline mid-procedure risks quorum loss.

Recommended order: start with whichever node is *not* currently the etcd leader (check via `kubectl get --raw /healthz/etcd` or etcd's own leader endpoint), so a mistake on the first, most uncertain run doesn't also trigger a leader election.

### Step 1: Stop k3s on the target node

**Why**: etcd's data directory can't be safely copied while the process holds it open.

```bash
kubectl exec -n database <debug-pod-on-target-node> --context=production -- chroot /host systemctl stop k3s
```

**Verify**: `kubectl get nodes --context=production` shows the target node `NotReady` (expected — it's down), the other two still `Ready`.

### Step 2: Copy the etcd data directory to NVMe

**Why**: `rsync -a` preserves permissions/ownership exactly, which etcd's on-disk format depends on.

```bash
kubectl exec -n database <debug-pod> --context=production -- chroot /host sh -c '
  mkdir -p /mnt/nvme-etcd
  rsync -a /var/lib/rancher/k3s/server/db/etcd/ /mnt/nvme-etcd/etcd/
'
```

Note the deliberate path: `/mnt/nvme-etcd`, not under `/mnt/longhorn` — keep this clearly separate from Longhorn's own data so there's no ambiguity about what owns what on the NVMe.

**Verify**: `du -sh` on both the original and the copy match.

### Step 3: Move the original aside (don't delete)

**Why**: keep a rollback path until the new location is proven stable.

```bash
kubectl exec -n database <debug-pod> --context=production -- chroot /host \
  mv /var/lib/rancher/k3s/server/db/etcd /var/lib/rancher/k3s/server/db/etcd.bak-$(date +%Y%m%d)
```

### Step 4: Bind-mount the NVMe copy at the original path

**Why**: this requires zero k3s configuration changes — k3s still sees the exact same path, now backed by NVMe.

```bash
kubectl exec -n database <debug-pod> --context=production -- chroot /host sh -c '
  mkdir -p /var/lib/rancher/k3s/server/db/etcd
  mount --bind /mnt/nvme-etcd/etcd /var/lib/rancher/k3s/server/db/etcd
  echo "/mnt/nvme-etcd/etcd /var/lib/rancher/k3s/server/db/etcd none bind 0 0" >> /etc/fstab
'
```

**Verify**: `mount | grep etcd` shows the bind mount active; `cat /etc/fstab` shows the new entry.

### Step 5: Restart k3s and verify

```bash
kubectl exec -n database <debug-pod> --context=production -- chroot /host systemctl start k3s
```

**Verify this step**:
```bash
# Wait ~30-60s, then:
kubectl get nodes --context=production
# target node should return to Ready

kubectl get --raw /healthz/etcd --context=production
# should be healthy again, all 3 members present
```

**If this fails**: roll back immediately (see Rollback section below) rather than debugging in place on a control-plane node with etcd down.

### Step 6: Monitor before touching the next node

Wait at least ~15 minutes, watching for:
- Node stays `Ready`
- No repeated etcd errors in `journalctl -u k3s`
- A test etcd read-latency check (see war story) now shows ~sub-100ms reads instead of multi-second ones

Only once this node is confirmed stable, move to the next node and repeat Steps 1-6.

## Verification

### Confirm resolution (after all 3 nodes migrated):

- [ ] All 3 nodes `Ready`
      ```bash
      kubectl get nodes --context=production
      ```
- [ ] etcd cluster healthy
      ```bash
      kubectl get --raw /healthz/etcd --context=production
      ```
- [ ] etcd read latency back to normal on all 3 nodes (no more `"apply request took too long"` warnings in `journalctl -u k3s`)
- [ ] NetworkPolicy propagation is fast again — make a trivial, reversible test change to any NetworkPolicy and confirm it's reflected in the corresponding node's `KUBE-NWPLCY-*` chain within a minute or two, not 10+ minutes (see war story Step 3 for how to find the right chain)
- [ ] Fix persists after a node reboot (the `/etc/fstab` bind-mount entry should re-establish it automatically) — worth testing on at least one node deliberately

## Rollback

If a node fails to come back healthy after Step 5:

```bash
kubectl exec -n database <debug-pod> --context=production -- chroot /host sh -c '
  systemctl stop k3s
  umount /var/lib/rancher/k3s/server/db/etcd
  rmdir /var/lib/rancher/k3s/server/db/etcd
  mv /var/lib/rancher/k3s/server/db/etcd.bak-<date> /var/lib/rancher/k3s/server/db/etcd
  sed -i "\|/mnt/nvme-etcd/etcd|d" /etc/fstab
  systemctl start k3s
'
```

Verify the node returns to `Ready` on the original SD-card data before deciding next steps. Do not attempt the NVMe migration again on that node until the failure is understood.

## Root Cause

See [`docs/war-stories/etcd-emmc-storage-latency.md`](/docs/war-stories/etcd-emmc-storage-latency.md) for the full investigation. Summary: k3s's default etcd data directory was never relocated off the node's SD/eMMC root filesystem during initial provisioning (`docs/setup.md` never sets a `--data-dir` flag); the NVMe on each node was provisioned and reserved exclusively for Longhorn.

## Prevention

### Long-term Prevention

- [ ] Add monitoring for etcd request latency
      - **Why**: this issue was invisible to existing alerting — etcd never went "down," just got dramatically slower, until a specific, unrelated investigation happened to surface it via `journalctl`
      - **How**: k3s's embedded etcd exposes Prometheus metrics (`etcd_disk_wal_fsync_duration_seconds`, `etcd_request_duration_seconds`) — confirm these are scraped and add an alert on p99 latency exceeding a reasonable threshold (etcd upstream recommends alerting above ~100-500ms for fsync duration)
- [ ] Document the intentional partition/storage layout in `docs/architecture.md` once resolved, including which paths are meant to live on which device — this gap in documentation is part of why the original layout went unnoticed

### Documentation Updates

- [ ] Once executed successfully, update this runbook's status note at the top from "proposed, not yet executed" to a normal resolved-runbook header, and add actual execution notes/gotchas encountered
- [ ] Update `docs/architecture.md`'s Storage Architecture section to document the corrected etcd storage location

## Related Issues

- **[War Story: etcd on SD Card](/docs/war-stories/etcd-emmc-storage-latency.md)** - the investigation that found this, including how the original NetworkPolicy fix (PR #73) surfaced it
- **[NetworkPolicy Connectivity Troubleshooting](networkpolicy-connectivity-troubleshooting.md)** - general methodology, relevant if this same investigation path needs repeating for a different symptom

## References

- [etcd Hardware recommendations](https://etcd.io/docs/latest/op-guide/hardware/) - explicit guidance against slow/consumer-grade storage for etcd
- [k3s data directory documentation](https://docs.k3s.io/cli/server) - `--data-dir` flag and defaults

---

**Last Updated**: 2026-07-09
**Tested On**: Not yet executed against real hardware
**Success Rate**: N/A — proposed procedure
**Original Incident**: Discovered 2026-07-09 during NetworkPolicy investigation (PR #73)
