# PostgreSQL Replica Won't Recover - Stale NetworkPolicy Dataplane Sync

## Quick Reference

- **Severity**: High
- **Estimated Time to Resolve**: 20-40 minutes
- **Symptoms**: A reprovisioned CNPG replica crash-loops with `connection refused` reaching the Kubernetes API server, even though the NetworkPolicy config looks correct
- **Affected Components**: CloudNative-PG database cluster (any namespace using `allow-k8s-api-egress`-style NetworkPolicies)
- **Environment**: Production or Staging
- **Prerequisites**: `kubectl`, `flux` CLI access, ability to create throwaway diagnostic pods

## Symptoms & Detection

### Error Messages

Replica readiness probe:
```
Warning  Unhealthy  4m57s (x1121 over 169m)  kubelet  spec.containers{postgres}: Readiness probe failed: HTTP probe failed with statuscode: 500
```

Replica pod logs, if it's mid-timeline-fork-recovery (a **different**, earlier stage of this same class of incident):
```
FATAL: could not start WAL streaming: ERROR: requested starting point A2/B3000000 on timeline 2 is not in this server's history
DETAIL: This server's history forked from timeline 2 at A2/1703AEC8.
```

**After** deleting and reprovisioning the broken instance, the *new* replacement instance's logs show a different error - this is the one this runbook is actually about:
```
"error":"Get \"https://10.43.0.1:443/apis/postgresql.cnpg.io/v1/namespaces/database/clusters/postgres-cluster\": dial tcp 10.43.0.1:443: connect: connection refused"
```

### Observable Behavior

- `kubectl get cluster <name> -n database` shows `INSTANCES: 3, READY: 2` (or fewer) and `STATUS: Waiting for the instances to become active` or `Creating a new replica`
- The replacement instance restarts repeatedly (`CrashLoopBackOff`), always failing at the exact same "waiting for API server" step, never progressing further
- CNPG's join Jobs for the new instance may also fail with the identical `connection refused` error

### Monitoring Indicators

- `cnpg_cluster_instances_ready` metric below `cnpg_cluster_instances` (if Prometheus/CNPG metrics are scraped)

## Immediate Actions

**If this is a fresh timeline-fork replica (not yet reprovisioned):**

```bash
kubectl delete pod <broken-instance> -n database --context=<ctx>
kubectl delete pvc <broken-instance> -n database --context=<ctx>
```

CNPG will provision a replacement automatically under a **new instance number** (it does not reuse the deleted ordinal - this is expected, not a bug; see [Root Cause](#root-cause)). Continue to Diagnosis Steps if the replacement then also fails to come up.

## Diagnosis Steps

### 1. Confirm the new instance is failing at the API-server-reachability step specifically

```bash
kubectl logs <new-instance-pod> -n database --context=<ctx> --tail=10
```

Look for `dial tcp <cluster-ip>:443: connect: connection refused` repeated on every retry, followed by the container exiting.

### 2. Rule out node-level explanations first (they're usually NOT the cause, but check quickly)

```bash
kubectl get pods -n database --context=<ctx> -o wide
kubectl get nodes --context=<ctx>
```

If the failing pod and the already-healthy replicas are on different nodes, node-specific flakiness is a *plausible-looking* but often wrong hypothesis - don't stop here, go to Step 3.

### 3. Run the definitive A/B test

**This is the fastest way to get a conclusive answer** - don't spend time re-reading logs repeatedly if this test hasn't been run yet.

```bash
# Pod subject to the suspect namespace's NetworkPolicies, same node as the failing instance
kubectl run netpol-test --image=nicolaka/netshoot --restart=Never \
  -n database --context=<ctx> \
  --overrides='{"spec":{"nodeSelector":{"kubernetes.io/hostname":"<node-name>"}}}' \
  -- sleep 300

# Control pod, same node, no restrictive NetworkPolicy (default namespace)
kubectl run netpol-test-control --image=nicolaka/netshoot --restart=Never \
  -n default --context=<ctx> \
  --overrides='{"spec":{"nodeSelector":{"kubernetes.io/hostname":"<node-name>"}}}' \
  -- sleep 300
```

Wait ~10 seconds for both to reach `Running`, then test the same connection from each:

```bash
kubectl exec netpol-test -n database --context=<ctx> -- nc -zv -w3 <cluster-ip> 443
kubectl exec netpol-test-control -n default --context=<ctx> -- nc -zv -w3 <cluster-ip> 443
```

**Confirm diagnosis:**

**This is the right runbook if:**
- ✅ The `database`-namespace test **fails** (`Connection refused`)
- ✅ The `default`-namespace control test **succeeds**
- ✅ The relevant NetworkPolicy (e.g. `allow-k8s-api-egress`) already explicitly permits this exact traffic when you read its YAML

**This is NOT the right runbook if:**
- ❌ Both tests fail the same way → look at node/cluster-wide networking instead (kube-proxy, CNI health)
- ❌ Both tests succeed → the problem isn't NetworkPolicy-related; look at other reachability factors (DNS, actual API server health)

Clean up the diagnostic pods regardless of outcome:
```bash
kubectl delete pod netpol-test -n database --context=<ctx>
kubectl delete pod netpol-test-control -n default --context=<ctx>
```

### 4. Confirm the policy config itself is actually correct (rule out a real misconfiguration)

```bash
kubectl get networkpolicy -n database --context=<ctx> -o yaml
```

Check the relevant policy explicitly allows the destination IP/port (e.g. the cluster's Kubernetes API service ClusterIP on 443). If the YAML is textbook-correct and the A/B test still failed, this confirms **stale dataplane sync**, not a config mistake.

## Resolution Steps

### Step 1: Force a fresh NetworkPolicy dataplane sync

**Why**: deleting and letting Flux recreate the policy objects forces a completely fresh sync to the underlying enforcement layer (iptables/nftables), rather than relying on an incremental update that may have desynced at some point.

```bash
kubectl delete networkpolicy <policy-name> [<policy-name>...] -n database --context=<ctx>
flux reconcile kustomization infrastructure-configs --context=<ctx>
```

For the specific case that triggered this runbook, the affected policies were `allow-k8s-api-egress` and `default-deny-egress`.

### Step 2: Let CNPG's own retry loop recover

No further manual intervention is needed - once the network path is genuinely open, the crash-looping instance's own retry loop (and/or the next kubelet-triggered restart) picks the recovery back up automatically.

```bash
kubectl get pods -n database --context=<ctx> -w
```

## Verification

- [ ] Cluster shows all instances ready
      ```bash
      kubectl get cluster <name> -n database --context=<ctx>
      # Expected: INSTANCES == READY, STATUS = "Cluster in healthy state"
      ```

- [ ] The new instance pod reached `1/1 Running` with no further restarts
      ```bash
      kubectl get pods -n database --context=<ctx>
      ```

- [ ] No more `connection refused` errors in its logs
      ```bash
      kubectl logs <new-instance-pod> -n database --context=<ctx> --tail=20
      ```

## Root Cause

### What Caused This

The NetworkPolicy objects correctly declared the allowed traffic, but the actual enforcement dataplane (whatever programs iptables/nftables rules from those objects - varies by CNI/cluster) had drifted out of sync with the declared policy state. Forcing a delete-and-recreate resolved it, confirming this was stale sync rather than a genuine policy-engine bug in how multiple `Egress`-type policies merge for one pod.

### Why It Manifested Now

Most likely triggered by a burst of reconciliation churn (e.g., catching up on a large backlog of commits after an unrelated outage), during which the dataplane sync fell behind the declared policy state and never caught back up on its own.

### Component Interaction

```
NetworkPolicy objects (correct, in Git and in the API) ──X──  actual enforced iptables/nftables rules (stale)
                                                                        │
                                                                        ▼
                                            New pod's egress to the K8s API ClusterIP is refused
                                                                        │
                                                                        ▼
                                CNPG instance-manager can't fetch the Cluster CRD → exits fatally → CrashLoopBackOff
```

### Technical Details

**Do not assume "connection refused" rules out NetworkPolicy as a cause.** That assumption holds for CNI plugins enforcing policy via silent `DROP` (e.g. Calico, Cilium default config), producing a *timeout* rather than an active rejection. Some policy engines (including K3s's embedded/default enforcement, observed here) use `REJECT`-style behavior that produces exactly `connection refused`. When in doubt, run the A/B test in Diagnosis Step 3 rather than reasoning from the TCP-level symptom alone.

## Prevention

### Immediate Prevention

- [x] Forced resync resolved the immediate incident

### Long-term Prevention

- [ ] Investigate whether this specific cluster's NetworkPolicy enforcement has a known dataplane-sync-lag issue under reconciliation bursts, and if so, whether a periodic health check/resync job is warranted
      - **Why**: prevents this from silently recurring after the next large catch-up reconciliation
      - **Effort**: research-only, no immediate action identified

### Documentation Updates

- [x] Fixed two runbooks/docs that hardcoded a specific CNPG instance ordinal (`postgres-cluster-1`) as if it were permanent - CNPG never reuses ordinals after a replica is recreated, so any script assuming a fixed pod name will break the next time this exact recovery happens. See `docs/stack/infrastructure/cloudnative-pg.md` and `docs/runbooks/postgresql-cluster-disaster-recovery.md`, both now look up the current primary via the `role=primary` label instead.

## Related Issues

- **[Staging Disk Pressure Cleanup](staging-disk-pressure-docker-cleanup.md)** - the likely original trigger for the failover that caused the timeline fork requiring replica reprovisioning in the first place
- **[NetworkPolicy Connectivity Troubleshooting](networkpolicy-connectivity-troubleshooting.md)** - general NetworkPolicy debugging runbook for other connectivity issues

## Original War Story

For the complete investigation narrative, including the wrong node-flakiness hypothesis and how it got corrected, see: [`docs/war-stories/postgres-replica-networkpolicy-dataplane-sync.md`](../war-stories/postgres-replica-networkpolicy-dataplane-sync.md)

## References

- [CloudNativePG replica recovery documentation](https://cloudnative-pg.io/documentation/current/failure_modes/)
- [Kubernetes NetworkPolicy documentation](https://kubernetes.io/docs/concepts/services-networking/network-policies/)

---

**Last Updated**: 2026-07-02
**Tested On**: Staging
**Success Rate**: 1/1 incidents resolved (100%)
**Original Incident**: 2026-07-02
