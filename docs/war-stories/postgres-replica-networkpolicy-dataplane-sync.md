# A PostgreSQL Replica That Wouldn't Come Back - and a Wrong Hypothesis Ruled Out with an A/B Test

**Date**: July 2, 2026
**Severity**: High
**Duration**: ~171 minutes broken before intervention, ~35 minutes to recover and diagnose the real cause
**Impact**: PostgreSQL cluster running at 2/3 instances on staging; the replacement replica then spent ~10 minutes crash-looping before the actual root cause was found

## The Problem

### Initial Symptoms

While auditing staging's recovery from a [disk-pressure outage](staging-disk-pressure-docker-bloat.md), `postgres-cluster` showed a degraded state:

```
NAME               INSTANCES   READY   STATUS                                       PRIMARY
postgres-cluster   3           2       Waiting for the instances to become active   postgres-cluster-3
```

`postgres-cluster-1`'s readiness probe had been failing continuously for 169 minutes straight:

```
Warning  Unhealthy  4m57s (x1121 over 169m)  kubelet  spec.containers{postgres}: Readiness probe failed: HTTP probe failed with statuscode: 500
```

### The First Root Cause: A Real Timeline Fork

`kubectl logs postgres-cluster-1` showed the actual PostgreSQL-level error:

```
"error while waiting": FATAL: could not start WAL streaming: ERROR: requested starting point A2/B3000000 on timeline 2 is not in this server's history
DETAIL: This server's history forked from timeline 2 at A2/1703AEC8.
```

At some point - almost certainly during the disk-pressure incident - `postgres-cluster` had failed over, promoting `postgres-cluster-3` to primary and starting a new WAL timeline. `postgres-cluster-1`'s own WAL history had diverged from that new timeline and could never catch up via ordinary streaming replication; it needed a full re-clone from the current primary, not more retries. Since it was a genuine replica in a 3-instance cluster with two other healthy members, the standard CloudNativePG remediation is to delete the broken instance's pod and PVC and let the operator reprovision it fresh via `pg_basebackup`:

```bash
kubectl delete pod postgres-cluster-1 -n database --context=staging
kubectl delete pvc postgres-cluster-1 -n database --context=staging
```

CNPG immediately started provisioning a replacement - under a **new** instance number, `postgres-cluster-4`, since the operator never reuses an ordinal once it's been assigned, even after the old instance is deleted (this is deliberate: it avoids any chance of a fresh instance's identity being confused with a previous, broken one).

## The Investigation

### The Replacement Wouldn't Come Up Either

`postgres-cluster-4` immediately started crash-looping, hitting `connection refused` trying to reach the Kubernetes API server on every single attempt:

```
"error":"Get \"https://10.43.0.1:443/apis/postgresql.cnpg.io/v1/namespaces/database/clusters/postgres-cluster\": dial tcp 10.43.0.1:443: connect: connection refused"
```

Over the following ~10 minutes: 5 pod restarts, `CrashLoopBackOff`, and two of the three join-job attempts also failed with the identical error (one join attempt did eventually succeed on its third try).

### First Hypothesis: Node-Level Flakiness (Wrong)

Every pod involved in this recovery - the crash-looping main pod, all three join-job attempts - had landed on the same node, `k3d-staging-server-0`, which happens to be the node that also runs the K3s API server process itself. The two already-healthy replicas (`postgres-cluster-2`, `postgres-cluster-3`) were on the two agent nodes instead. That pattern, combined with "connection refused" rather than a timeout (which is the typical signature of a NetworkPolicy `DROP` rule silently discarding packets rather than actively rejecting them), pointed toward a hypothesis: something specific to *new* connections from `server-0` to its own locally-hosted API server - possibly conntrack table state churned up by the burst of pod/job creation happening on that node in a short window.

This hypothesis was reasonable, testable, and **wrong** - but it took a direct question to catch that.

### The Question That Changed the Investigation

After watching several more crash-loop cycles with no progress, the direct question came: *"can it be a network policy error?"*

That was the right pushback. The "connection refused rules out NetworkPolicy" assumption baked into the first hypothesis was based on how Calico/Cilium typically enforce policy (`DROP`, producing a timeout). But this cluster's NetworkPolicy enforcement wasn't from either of those - `kubectl get pods -n kube-system | grep -i router` turned up nothing, meaning this K3s cluster was using its default, embedded NetworkPolicy controller rather than a separately-visible one, and there was no basis to assume it uses `DROP` semantics rather than `REJECT` (which produces exactly "connection refused").

### Settling It With a Controlled Experiment

Rather than keep guessing, a direct A/B test was the fastest way to get a real answer. Two throwaway pods, same node, same target, only one variable changed - which namespace's NetworkPolicies applied:

```bash
kubectl run netpol-test --image=nicolaka/netshoot --restart=Never \
  -n database --context=staging \
  --overrides='{"spec":{"nodeSelector":{"kubernetes.io/hostname":"k3d-staging-server-0"}}}' \
  -- sleep 300

kubectl run netpol-test-default --image=nicolaka/netshoot --restart=Never \
  -n default --context=staging \
  --overrides='{"spec":{"nodeSelector":{"kubernetes.io/hostname":"k3d-staging-server-0"}}}' \
  -- sleep 300
```

```bash
kubectl exec netpol-test -n database -- nc -zv -w3 10.43.0.1 443
# nc: connect to 10.43.0.1 port 443 (tcp) failed: Connection refused

kubectl exec netpol-test-default -n default -- nc -zv -w3 10.43.0.1 443
# Connection to 10.43.0.1 443 port [tcp/https] succeeded!
```

Same node, same moment, same destination IP and port - and only the `database`-namespace pod failed. That's conclusive: it **was** a NetworkPolicy problem, and the earlier "connection refused rules it out" reasoning had simply been wrong for this cluster's specific enforcement mechanism.

### Confirming the Policy Config Itself Was Correct

The obvious next question: was the policy actually misconfigured? Pulling the live YAML said no:

```yaml
# allow-k8s-api-egress
spec:
  egress:
    - ports: [{port: 443, protocol: TCP}]
      to: [{ipBlock: {cidr: 10.43.0.1/32}}]
  podSelector: {}
  policyTypes: [Egress]
```

Textbook-correct - explicitly permits exactly the traffic that was being refused. Kubernetes NetworkPolicies are additive (any matching policy's `Egress` rules apply, `default-deny-egress` just establishes the fallback), so on paper this traffic should have been allowed regardless of the coexisting deny-all policy. That left two live possibilities: a genuine bug in how this cluster's policy engine merges multiple `Egress`-type policies for one pod, or stale dataplane state (the actual iptables/nftables rules not matching what the policy objects currently declared).

## The Root Cause

Forcing the two relevant `NetworkPolicy` objects to be deleted and recreated - triggering a completely fresh sync from Flux rather than an incremental one - resolved the issue immediately:

```bash
kubectl delete networkpolicy allow-k8s-api-egress default-deny-egress -n database --context=staging
flux reconcile kustomization infrastructure-configs --context=staging
```

That confirmed it was **stale dataplane sync**, not a genuine bug in how the policy engine merges rules. The policy objects and the actual enforced rules had drifted out of sync at some point - plausibly during the same disk-pressure period that caused the original failover, or during the burst of reconciliation churn while staging caught up on ~40 backlogged commits at once - and nothing had forced a resync since.

## The Solution

1. Diagnose via the A/B test above to conclusively rule *in* NetworkPolicy rather than node flakiness.
2. Delete and let Flux recreate the two relevant policy objects, forcing a fresh dataplane sync.
3. Clean up the diagnostic pods:
```bash
kubectl delete pod netpol-test -n database --context=staging
kubectl delete pod netpol-test-default -n default --context=staging
```
4. CNPG's own retry loop picked the recovery back up automatically once the network path was actually open - no further manual intervention was needed for `postgres-cluster-4` itself.

## Verification

```bash
kubectl get pods -n database --context=staging
# postgres-cluster-2   1/1   Running   10 (3h47m ago)   4h22m
# postgres-cluster-3   1/1   Running   2 (3h43m ago)    5h38m
# postgres-cluster-4   1/1   Running   0                54s

kubectl get cluster postgres-cluster -n database --context=staging
# NAME               INSTANCES   READY   STATUS                     PRIMARY
# postgres-cluster   3           3       Cluster in healthy state   postgres-cluster-3
```

3/3 instances ready, cluster healthy. `postgres-cluster-1` never came back under that name - CNPG's non-reuse of instance ordinals means the cluster's permanent membership is now `-2`, `-3`, and `-4`. This is expected, cosmetic-only behavior, not a sign anything is missing (see [Prevention](#prevention) below).

## Prevention

### 1. Never Hardcode a Specific Instance Ordinal
This incident directly exposed several docs (`docs/stack/infrastructure/cloudnative-pg.md`, `docs/runbooks/postgresql-cluster-disaster-recovery.md`) that assumed `postgres-cluster-1` would always exist and be the primary. Both were fixed to discover the current primary dynamically instead:
```bash
PRIMARY_POD=$(kubectl get pods -n database -l cnpg.io/cluster=postgres-cluster,role=primary -o jsonpath='{.items[0].metadata.name}')
kubectl exec -it -n database "$PRIMARY_POD" -- psql -U postgres
```
Applications and operational scripts should reference the stable `postgres-cluster-rw`/`-ro`/`-r` **services**, which route to whichever pod is actually primary - never a specific pod name.

### 2. Don't Trust "Connection Refused Rules Out NetworkPolicy" as a Universal Rule
That reasoning holds for CNI plugins that enforce policy via silent `DROP` (Calico, Cilium in default config), but not necessarily for every implementation. Before ruling out NetworkPolicy based on the TCP-level symptom alone, check what's actually enforcing policy in the specific cluster - or better, just run the A/B test directly; it's cheap and conclusive either way.

### 3. Prefer Controlled Experiments Over Escalating Guesswork
The node-flakiness hypothesis was plausible and even had some circumstantial supporting evidence (all failing pods on one node). Rather than keep re-diagnosing from the same angle repeatedly, a single well-designed A/B test (same node, same target, one variable changed) settled the question definitively in under a minute of actual test time.

## Lessons Learned

1. **A wrong-but-plausible hypothesis can survive several rounds of "let's just wait and see" before someone asks the right question** - the node-flakiness theory wasn't unreasonable, but nobody had actually tested it against the alternative until directly challenged.
2. **"Connection refused" vs. timeout is CNI-implementation-specific, not universal** - don't carry an assumption about DROP vs. REJECT semantics from one cluster/CNI to another without checking.
3. **A/B tests with throwaway resources are cheap and decisive** - two `netshoot` pods and 60 seconds resolved in certainty what several minutes of log-reading and re-diagnosis hadn't.
4. **Correct-looking policy YAML doesn't guarantee correct enforcement** - the config was genuinely fine; the *sync* between declared state and the actual dataplane had drifted. Always be willing to test "does this actually work right now," not just "does this look right."
5. **CNPG doesn't reuse instance ordinals, by design** - a "missing" `postgres-cluster-1` after a replica recreation isn't data loss; it's the operator deliberately avoiding identity confusion between a replaced-because-broken instance and its replacement.
6. **Fix the docs the incident exposed, not just the cluster** - the ordinal-hardcoding bug in two runbooks would have caused the *next* person's recovery attempt to fail on a `kubectl exec` that silently referenced a pod that no longer existed.

## Related Documentation

- [Runbook: PostgreSQL Replica Won't Recover - Stale NetworkPolicy Dataplane Sync](/docs/runbooks/postgres-replica-networkpolicy-dataplane-sync.md) - action-oriented version of this investigation
- [Staging Disk Pressure from Unbounded Docker Growth](staging-disk-pressure-docker-bloat.md) - the likely original trigger for the failover that caused the timeline fork
- [NetworkPolicy Connectivity Debugging](networkpolicy-connectivity-debugging.md) - prior war story on a different class of NetworkPolicy issue
- [NetworkPolicy Connectivity Troubleshooting runbook](/docs/runbooks/networkpolicy-connectivity-troubleshooting.md)
- [PostgreSQL Cluster Disaster Recovery runbook](/docs/runbooks/postgresql-cluster-disaster-recovery.md)

## Commands Reference

**Check a CNPG cluster's instance health**:
```bash
kubectl get cluster <cluster-name> -n database --context=<ctx>
```

**Force-reprovision a broken replica**:
```bash
kubectl delete pod <instance-name> -n database --context=<ctx>
kubectl delete pvc <instance-name> -n database --context=<ctx>
```

**Find the current primary pod without hardcoding an ordinal**:
```bash
kubectl get pods -n database -l cnpg.io/cluster=<cluster-name>,role=primary -o jsonpath='{.items[0].metadata.name}'
```

**A/B test whether a NetworkPolicy is actually blocking traffic**:
```bash
kubectl run netpol-test --image=nicolaka/netshoot --restart=Never -n <suspect-namespace> \
  --overrides='{"spec":{"nodeSelector":{"kubernetes.io/hostname":"<same-node>"}}}' -- sleep 300
kubectl run netpol-test-control --image=nicolaka/netshoot --restart=Never -n default \
  --overrides='{"spec":{"nodeSelector":{"kubernetes.io/hostname":"<same-node>"}}}' -- sleep 300
kubectl exec netpol-test -n <suspect-namespace> -- nc -zv -w3 <target-ip> <target-port>
kubectl exec netpol-test-control -n default -- nc -zv -w3 <target-ip> <target-port>
# Clean up afterward:
kubectl delete pod netpol-test -n <suspect-namespace>
kubectl delete pod netpol-test-control -n default
```

**Force a fresh NetworkPolicy dataplane sync when config looks correct but isn't being enforced**:
```bash
kubectl delete networkpolicy <policy-name> -n <namespace>
flux reconcile kustomization <owning-kustomization>
```
