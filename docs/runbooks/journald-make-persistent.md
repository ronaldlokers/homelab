# Make journald Persistent Across Reboots

**Status:** proposed, not yet executed. Diagnosis done 2026-08-01.
**Access:** `ssh ronald@<node>`, then `sudo`. Unlike the etcd migration this does not stop k3s, so `kubectl debug node` also works.
**Time:** ~5 min per node. Three nodes.
**Risk:** low. Restarting `systemd-journald` does not restart k3s or evict workloads.

## Why

journald on the production nodes runs in **volatile** mode: logs live on tmpfs at `/run/log/journal/<machine-id>/` and are lost on reboot. `/var/log/journal` exists but is empty, which is what makes `Storage=auto` fall back to volatile.

Measured 2026-08-01 on `kube-srv-1`:

```
/var/log/journal    drwxr-sr-x+ 2 root systemd-journal    <- link count 2 = no subdirectories = empty
/run/log/journal    drwxr-s---+ 2 root systemd-journal 7e2b2c694305408d82c5716af66b16ec
journalctl --header -> File path: /run/log/journal/7e2b2c69.../system.journal
Storage=            -> unset (default auto)
```

Alloy ships these logs to Loki (see `monitoring/controllers/production/alloy/config.alloy`), so current logs *are* retained centrally. What is lost is the node's own history after a reboot — which matters exactly when you need it most:

- The etcd NVMe migration produced a ~7 minute latency burst on reboot ([`etcd-migrate-to-nvme.md`](etcd-migrate-to-nvme.md)). Investigating that *after* the fact is impossible today.
- A node that reboots unexpectedly takes the evidence of why with it.
- If Alloy is down or misconfigured during an incident, the local journal is the only copy — and it has been misconfigured twice, see the war story below.

## Pre-flight

```bash
# confirm it is actually volatile on this node -> expect /run/log/journal/...
ssh ronald@<target-ip> "sudo journalctl --header | grep -m1 'File path'"

# confirm the directory is empty -> expect link count 2
ssh ronald@<target-ip> "sudo ls -ld /var/log/journal"

# check free space on / -> journald caps at 10% of the filesystem by default
ssh ronald@<target-ip> "df -h /"
```

Node IPs: `kube-srv-1` 10.0.40.101, `kube-srv-2` 10.0.40.102, `kube-srv-3` 10.0.40.103.

**Note the storage location:** `/var/log` is on the eMMC root filesystem, not the NVMe. This writes journal data to the same slow device the etcd migration moved *away* from. Journal writes are far lighter than etcd's fsync pattern so this is not a repeat of that problem, but cap the size (below) rather than leaving the 10% default.

## Procedure — per node, no need to stagger

Unlike the etcd migration there is no quorum concern, but doing one at a time still gives a clean comparison if something misbehaves.

### 1. Create the directory and cap its size

```bash
sudo mkdir -p /var/log/journal
sudo systemd-tmpfiles --create --prefix /var/log/journal
```

`systemd-tmpfiles` sets the correct ownership and ACLs (`root:systemd-journal`, setgid). Do **not** just `mkdir` and move on — wrong permissions leave journald unable to write and it silently falls back to volatile, which is the failure this runbook exists to fix.

Cap the size so journal growth cannot fill the eMMC root filesystem:

```bash
sudo mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nStorage=persistent\nSystemMaxUse=500M\n' | \
  sudo tee /etc/systemd/journald.conf.d/persistent.conf
```

`Storage=persistent` is stated explicitly rather than relying on `auto`, so the behaviour does not silently depend on whether a directory happens to exist.

### 2. Restart journald

```bash
sudo systemctl restart systemd-journald
```

### 3. Verify

```bash
sudo journalctl --header | grep -m1 'File path'
```

→ must show `/var/log/journal/<machine-id>/...`
→ **if it still shows `/run/log/journal`: the directory permissions are wrong.** Re-run `systemd-tmpfiles --create` and check `ls -ld /var/log/journal` shows `root systemd-journal`.

```bash
sudo journalctl --disk-usage
sudo ls -ld /var/log/journal          # link count should now be 3+, not 2
```

### 4. Confirm Alloy still collects

Alloy needs **no configuration change** — `loki.source.journal` in `config.alloy` deliberately leaves `path` unset so sdjournal resolves `/run` vs `/var` itself, and the DaemonSet already mounts both. Confirm collection did not break:

```bash
# workstation — expect a rising counter, and NOT zero
kubectl -n monitoring port-forward ds/alloy 12345:12345 --context=production &
curl -s http://127.0.0.1:12345/metrics | grep loki_source_journal_target_lines_total
```

Component health is **not** a sufficient check here — it reports "journal tailer is running" even when reading an empty directory. Check the counter.

`AlloyJournalCollectionStalled` also covers this automatically (see `monitoring/controllers/production/alloy/alerts.yaml`), but it needs 30 minutes to fire.

## Verification after all three nodes

- [ ] `journalctl --header` shows `/var/log/journal/...` on all three
- [ ] `loki_source_journal_target_lines_total` still rising on all three
- [ ] `df -h /` on eMMC has not moved meaningfully
- [ ] reboot one node, then confirm `journalctl --boot=-1` returns the previous boot's logs — this is the actual thing being bought, so test it
- [ ] update this file's status header

## Rollback

```bash
sudo rm /etc/systemd/journald.conf.d/persistent.conf
sudo systemctl restart systemd-journald
sudo rm -rf /var/log/journal          # only if reclaiming the space matters
```

journald returns to volatile immediately. No effect on k3s or workloads.

## Related

- [Migrate etcd from eMMC to NVMe](etcd-migrate-to-nvme.md) — the reboot burst this would let you investigate after the fact
- [War story: etcd on eMMC](/docs/war-stories/etcd-emmc-storage-latency.md) — found only by reading `journalctl` on a node, because at the time node logs reached nothing else
- `monitoring/controllers/production/alloy/config.alloy` — why `path` is unset, and how volatile journal was discovered

---
**Last updated:** 2026-08-01 · **Tested:** diagnosis only; procedure unexecuted
