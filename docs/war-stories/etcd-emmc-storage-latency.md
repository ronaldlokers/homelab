# All Three Control-Plane Nodes Running etcd on SD Card Instead of NVMe

**Date**: July 9, 2026
**Severity**: Medium (latent — not an active outage, but a root cause for slow, unpredictable GitOps/NetworkPolicy propagation)
**Duration**: Undetermined (evidence points to since initial cluster provisioning; the specific symptom investigated was ~4 days old)
**Impact**: Delayed propagation of any change that depends on the API server's watch mechanism — observed directly as a NetworkPolicy update taking 10+ minutes (and, historically, the original policy taking ~4 days) to actually apply on a node
**Status**: Root-caused and documented tonight. Remediation **not yet executed** — see the paired runbook, [`etcd-migrate-to-nvme.md`](/docs/runbooks/etcd-migrate-to-nvme.md).

## The Problem

### Initial Symptoms

This started as a routine Alertmanager sweep ("check if anything's broken"). Among the noise, `TargetDown` alerts for `kube-system/kubelet` and `metallb-system/metallb-speaker` had been firing since `2026-06-27`, unresolved. The eventual fix was a real, self-contained bug: `allow-prometheus-to-system-namespaces` used `namespaceSelector` egress rules, which only match traffic to pod IPs — kubelet isn't a pod at all, and metallb-speaker runs `hostNetwork: true` (its "pod IP" *is* the node IP), so neither was ever reachable through those rules. `node-exporter` had hit the identical problem earlier and was fixed with an explicit `ipBlock` + port rule; the fix here (PR #73) extended that same pattern to kubelet (`10250`) and metallb-speaker (`7472`, `7473`).

That fix was applied directly to the live cluster to verify it before merging — and immediately exposed a second, much stranger problem.

### The Live Convergence That Wouldn't Converge

Prometheus's `up` metric for the affected targets should flip from `0` to `1` within a scrape interval or two once the NetworkPolicy took effect. Instead, 30+ minutes after applying the corrected policy, only 3 of 15 targets had converged — and the count stayed frozen at exactly 3 for the entire observation window, split unevenly across nodes:

```
10.0.40.101:10250 /metrics/cadvisor down
10.0.40.101:10250 /metrics down
10.0.40.101:10250 /metrics/probes down
10.0.40.101:7472 monitoring down
10.0.40.101:7473 monitoring down
10.0.40.102:10250 /metrics/cadvisor up
10.0.40.102:10250 /metrics down
10.0.40.102:10250 /metrics/probes down
10.0.40.103:10250 /metrics/cadvisor up
10.0.40.103:7472 monitoring up
```

Node `kube-srv-1` hadn't picked up the change at all. This was the actual anomaly worth chasing — not "the fix is wrong" (it was independently verified correct, see below), but "why won't this specific node apply a NetworkPolicy update it's clearly watching for."

## The Investigation

### Step 1: Verify the Fix Independently of Live Convergence

Before chasing the propagation issue, it mattered to rule out "the fix itself is wrong." A `kubectl debug node` pod on `kube-srv-1` (hostNetwork, sysadmin profile) confirmed both ports respond correctly from the node's own network:

```bash
curl -sk -m 5 -o /dev/null -w "HTTP:%{http_code}\n" https://10.0.40.101:10250/metrics
# HTTP:401  (unauthorized — correct, connection succeeded)
curl -s -m 5 -o /dev/null -w "HTTP:%{http_code}\n" http://10.0.40.101:7472/metrics
# HTTP:200  (correct)
```

A same-shape test from a plain pod-network pod (matching Prometheus's actual position) confirmed the reverse: cross-node, pod-network-sourced traffic to those same ports got `connection refused`. This isolated the problem precisely to "NetworkPolicy enforcement for this specific rule," not the rule's content.

### Step 2: Rule Out a Host Firewall

`nft list ruleset` inside the debug pod (needs `--profile=sysadmin` for `nft`'s netlink access) showed `policy accept` on every base `INPUT`/`FORWARD` chain — no default-deny firewall exists on these nodes at all. Kube-router's rules are additive `KUBE-NWPLCY-*`/`KUBE-POD-FW-*` chains layered on top, not a blanket policy.

### Step 3: Find the Actual Enforcement Rule

Kube-router's NetworkPolicy chains don't inline port numbers directly next to CIDR references — ports and IPs are stored inside `ipset` entries, referenced by hashed set names (`KUBE-DST-*`, `KUBE-SRC-*`), which made grepping for `10250`/`7472`/`7473` misleading at first (a match on `9100` earlier turned out to be an unrelated kube-proxy Service NAT rule). The actual chain was found by searching for the policy's own name in its `DROP by policy` log prefix:

```
chain KUBE-NWPLCY-GDHJ5C4OH3K7UJZC {
    ...
    ip protocol tcp ... tcp dport 9100 ... xt target "MARK"
    ip protocol tcp ... tcp dport 9100 ... return
    ip protocol tcp ... tcp dport 9100 ... xt target "MARK"
    ip protocol tcp ... tcp dport 9100 ... return
    ip protocol tcp ... tcp dport 9100 ... xt target "MARK"
    ip protocol tcp ... tcp dport 9100 ... return
    ... log prefix "DROP by policy monitoring/allow-prometheus-to-system-namespaces" group 100
}
```

Ten-plus minutes after the corrected policy (with `10250`/`7472`/`7473` added) had been applied to the API server, this chain on `kube-srv-1` **still only had the old `9100`-only rules**. Kube-router's controller genuinely hadn't processed the update — not a slow rollout, a stalled one.

### Step 4: Check What Kube-Router Is Actually Waiting On

`journalctl -u k3s` on `kube-srv-1` (via `chroot /host` inside the debug pod) turned up the real signal, buried among unrelated webhook-proxy noise:

```
{"level":"warn",...,"msg":"apply request took too long","took":"3.354978747s","expected-duration":"100ms",
 "prefix":"read-only range ","request":"key:\"/registry/networkpolicies/commafeed/allow-homepage-ingress\" limit:1 "}
```

A single etcd key read taking **3.35 seconds** against a 100ms expectation — a 30x overrun. And the log for NetworkPolicy-related etcd reads went completely silent for a 15-minute stretch (`02:15` to `02:31` local time) in the middle of the investigation window. Kube-router's controller depends on watches against the API server, which depends on etcd; if etcd itself is starved, every controller built on that watch mechanism inherits the same lag, kube-router included. This wasn't a kube-router bug at all.

### Step 5: Find Where etcd Actually Lives

```bash
kubectl exec -n database node-debugger-kube-srv-1-xxxxx --context=production -- \
  chroot /host readlink -f /var/lib/rancher/k3s/server/db/etcd
# /var/lib/rancher/k3s/server/db/etcd   (no symlink — lives directly on root fs)

kubectl exec ... -- chroot /host df -h
# /dev/mmcblk0p2   57G   42G   13G  77% /              <- etcd lives here
# /dev/nvme0n1p1  469G  310G  135G  70% /mnt/longhorn   <- dedicated to Longhorn only
```

`/dev/mmcblk0` is the SD/eMMC boot device. etcd's own documentation explicitly warns against this class of storage: it's fsync-latency sensitive, and consumer SD/eMMC media has notoriously poor random-write/fsync performance compared to NVMe.

A follow-up check on `kube-srv-2` and `kube-srv-3` (read-only, same method) confirmed **identical layout on all three nodes**:

```
kube-srv-2:  /dev/mmcblk0p2  78% /   |  /dev/nvme0n1p1  45% /mnt/longhorn
kube-srv-3:  /dev/mmcblk0p2  76% /   |  /dev/nvme0n1p1  57% /mnt/longhorn
```

This isn't a one-node fluke — it's how the whole HA control plane was provisioned. `docs/setup.md` confirms the original `k3s server`/`k3s agent` join commands never set a `--data-dir` flag, so this is simply k3s's out-of-the-box default, never revisited since initial setup.

## Root Cause

etcd's data directory was left at k3s's default location, which resolves to the node's SD/eMMC root filesystem, on all three production control-plane nodes. The dedicated, much faster NVMe on each node was provisioned and reserved exclusively for Longhorn, leaving etcd — the component most sensitive to write/fsync latency in the entire stack — on the slowest available storage.

### Why It Explains the Original 4-Day Delay

The `allow-prometheus-to-system-namespaces` NetworkPolicy was originally created `2026-06-23`, but the `TargetDown` alerts it should have prevented didn't start firing until `2026-06-27` — a four-day gap between "policy exists" and "policy actually enforced everywhere it needed to be." At the time this looked like an unrelated curiosity. In hindsight, it's the same symptom at a much larger scale: a controller watching etcd for changes, on a cluster where etcd reads can spike to 30x their expected latency, can take an unpredictable, sometimes very long time to actually notice and apply a change.

## Lessons Learned

1. **"Which controller is slow" and "why is the whole watch pipeline slow" are different questions** — chasing kube-router specifically looked productive right up until the etcd latency numbers reframed the entire investigation.
2. **NetworkPolicy enforcement debugging needs the actual per-policy chain, not a blind port grep** — `ipset`-backed rules don't inline the values you're looking for; a policy's own `DROP by policy <name>` log prefix is the reliable way to find its chain.
3. **`kubectl debug node` (with `--profile=sysadmin`) is sufficient for this entire class of node-level investigation** — no SSH access was needed to inspect listening sockets, firewall rules, ipsets, or `journalctl`, all via `chroot /host`.
4. **A slow-but-not-failing component is much harder to notice than a failed one** — etcd here never went down, never triggered a health check failure; it just got quietly, dramatically slower, degrading everything downstream without ever tripping an alert of its own.
5. **"Dedicated NVMe for Longhorn" isn't the same as "fast storage for anything that needs it"** — the NVMe was correctly provisioned for the workload that was top-of-mind (Longhorn) without considering that etcd, an equally (arguably more) latency-critical component, was left on the default.

## Related Documentation

- [Runbook: Migrate etcd Data Directory to NVMe](/docs/runbooks/etcd-migrate-to-nvme.md) - proposed remediation procedure (not yet executed)
- [Repository Architecture](/docs/architecture.md) - production hardware/storage layout
- [NetworkPolicy Connectivity Debugging](networkpolicy-connectivity-debugging.md) - general NetworkPolicy troubleshooting methodology

## Commands Reference

**Find a node's etcd data directory and check if it's on a symlink/bind mount**:
```bash
kubectl debug node/<node-name> --context=production --image=nicolaka/netshoot --profile=sysadmin -- sleep 300
kubectl exec -n <default-namespace-for-context> <debug-pod> --context=production -- \
  chroot /host readlink -f /var/lib/rancher/k3s/server/db/etcd
```

**Check node disk layout**:
```bash
kubectl exec -n <namespace> <debug-pod> --context=production -- chroot /host df -h
```

**Find a NetworkPolicy's actual kube-router enforcement chain**:
```bash
kubectl exec -n <namespace> <debug-pod> --context=production -- nft list ruleset > /tmp/nft.txt
grep -n "DROP by policy <namespace>/<policy-name>" /tmp/nft.txt
```

**Check etcd read latency from k3s's own logs**:
```bash
kubectl exec -n <namespace> <debug-pod> --context=production -- \
  chroot /host journalctl -u k3s --since '30 min ago' --no-pager | grep "apply request took too long"
```

**Clean up debug pods when done** (they don't self-delete when run non-interactively):
```bash
kubectl get pods -n <namespace> --context=production | grep node-debugger
kubectl delete pod <debug-pod-name> -n <namespace> --context=production
```
