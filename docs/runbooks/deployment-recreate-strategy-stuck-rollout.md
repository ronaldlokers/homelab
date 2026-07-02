# Single-Replica Deployment Wedged After Switching to `Recreate` Strategy

## Quick Reference

- **Severity**: High
- **Estimated Time to Resolve**: 15-20 minutes
- **Symptoms**: Flux Kustomization stuck with `spec.strategy.rollingUpdate: Forbidden: may not be specified when strategy type is 'Recreate'`; or a single-replica Deployment with an RWO PVC permanently stuck `ContainerCreating` on a routine image bump
- **Affected Components**: Any single-replica Deployment backed by a `ReadWriteOnce` PVC (in this repo: pgadmin, ntfy, commafeed, speedtest)
- **Environment**: Production and/or Staging
- **Prerequisites**: `kubectl` access, cluster admin rights to patch Deployments

## Symptoms & Detection

### Error Messages

**Symptom A - the original deadlock** (before `Recreate` is set):
No error at all - the old pod just keeps running while a new pod sits in `ContainerCreating` indefinitely. `kubectl describe pod` on the new pod shows no error events, just silence.

**Symptom B - after adding `strategy: {type: Recreate}` to the manifest**:
```
Deployment/<app>/<app> dry-run failed (Invalid): Deployment.apps "<app>" is invalid: spec.strategy.rollingUpdate: Forbidden: may not be specified when strategy `type` is 'Recreate'
```
This blocks the *entire* Flux Kustomization the Deployment belongs to, not just that one app.

### Observable Behavior

- Two pods for the same Deployment, one old (`Running`) and one new (`ContainerCreating`), never resolving
- The new pod's node differs from the node the PVC is currently attached to
- After the `Recreate` fix is pushed: `flux get kustomization apps` shows `Ready: False` citing the `rollingUpdate` validation error above

### Monitoring Indicators

- No specific alert exists for this - the old pod continuing to serve traffic means there's no user-visible outage, which is exactly why it can go unnoticed for days

## Immediate Actions

**If Symptom A (deadlocked rollout, no `Recreate` set yet):**

The old pod is still serving - there's no urgency to fix immediately. Continue to Resolution Steps.

**If Symptom B (Kustomization blocked after adding `Recreate`):**

This blocks *everything* in that Kustomization from applying, including unrelated changes. Prioritize the fix below.

## Diagnosis Steps

### 1. Check the Deployment's live strategy field

```bash
kubectl get deployment <name> -n <namespace> -o jsonpath='{.spec.strategy}'
```

If you see `{"rollingUpdate":{...},"type":"RollingUpdate"}` on a Deployment whose Git manifest says `type: Recreate`, this is stale drift from the API server's original defaulting - not something the current manifest ever declared.

### 2. Confirm the app fits the deadlock pattern

```bash
kubectl get deployment <name> -n <namespace> -o jsonpath='{.spec.replicas}'
kubectl get pvc -n <namespace> -o wide
```

**This is the right runbook if:**
- ✅ `replicas: 1`
- ✅ The Deployment mounts a PVC with `accessModes: [ReadWriteOnce]`
- ✅ Either the pod is stuck `ContainerCreating` on a rollout, or the Kustomization shows the `rollingUpdate: Forbidden` error

**This is NOT the right runbook if:**
- ❌ Multiple replicas (the RWO single-attach deadlock can't occur)
- ❌ The volume is `ReadWriteMany`

## Resolution Steps

### Step 1: Set `strategy: {type: Recreate}` in the Deployment manifest (Git)

```yaml
spec:
  replicas: 1
  strategy:
    type: Recreate
```

**Why**: forces Kubernetes to terminate the old pod (releasing the RWO volume) before scheduling the new one, instead of trying to run both at once.

Commit and push. **This alone is not sufficient if the Deployment was ever created with the implicit `RollingUpdate` default** - continue to Step 2.

### Step 2: Check whether the Kustomization is now blocked

```bash
flux get kustomization apps --context=<ctx>
```

If you see the `spec.strategy.rollingUpdate: Forbidden` error, the live object still carries a `rollingUpdate` sub-object the API server auto-populated at original creation. This has to be cleared imperatively - Git alone cannot fix it (see [Root Cause](#root-cause)).

**Do NOT** just add `rollingUpdate: null` to the manifest and expect it to work - verified in practice that explicit `null` via server-side-apply does not reliably clear this field on this cluster. Skip straight to the imperative fix below.

### Step 3: Atomically replace the live `strategy` field

**Why atomic replace, not a `remove` on just the `rollingUpdate` sub-key**: if `type` is still `RollingUpdate` at the moment of the patch, removing only `rollingUpdate` leaves a one-write window where the API server's defaulting logic immediately re-populates the exact same value - the patch reports `(no change)` and nothing actually happens.

```bash
kubectl patch deployment <name> -n <namespace> --context=<ctx> \
  --type=json -p='[{"op": "replace", "path": "/spec/strategy", "value": {"type": "Recreate"}}]'
```

Repeat for every affected app in the same Kustomization before the next reconcile, e.g.:

```bash
for d in commafeed:commafeed ntfy:ntfy pgadmin4:pgadmin speedtest:speedtest; do
  name=${d%%:*}; ns=${d##*:}
  kubectl patch deployment "$name" -n "$ns" --context=<ctx> \
    --type=json -p='[{"op": "replace", "path": "/spec/strategy", "value": {"type": "Recreate"}}]'
done
```

**Verify this step**:
```bash
kubectl get deployment <name> -n <namespace> -o jsonpath='{.spec.strategy}'
# Expected: {"type":"Recreate"}   <- no rollingUpdate key present at all
```

### Step 4: Let Flux reconcile

```bash
flux reconcile kustomization apps --context=<ctx>
```

The already-correct Git manifest now applies cleanly since there's no conflicting live field left to merge against.

## Verification

- [ ] Live strategy shows no `rollingUpdate` key
      ```bash
      kubectl get deployment <name> -n <namespace> -o jsonpath='{.spec.strategy}'
      # Expected: {"type":"Recreate"}
      ```

- [ ] Kustomization is `Ready: True`
      ```bash
      flux get kustomization apps --context=<ctx>
      ```

- [ ] The previously-stuck pod was actually deleted and replaced, not left hanging
      ```bash
      kubectl get pods -n <namespace> -w
      # Expected: old pod terminates, exactly one new pod reaches Running
      ```

- [ ] Fix persists across a future image bump (next Renovate PR merge should roll cleanly)

## Root Cause

### What Caused This

A Deployment created without an explicit `strategy` field gets Kubernetes' implicit `RollingUpdate` default - and critically, the API server **persists** the default `maxSurge`/`maxUnavailable` values onto the live object at creation time, even though no one's YAML ever declared them. Submitting `type: Recreate` later doesn't automatically strip that sibling field; the Deployment API rejects the combination outright.

### Why It Manifested Now

The deadlock itself (Symptom A) surfaces on the *first* rolling update attempt after the app is created - typically the first routine image bump. The Kustomization-blocking version (Symptom B) only appears once someone tries to fix Symptom A by adding `Recreate` to the manifest, exposing the pre-existing field-ownership drift.

### Component Interaction

```
Deployment created, no strategy field set
        │
        ▼
API server defaults: type=RollingUpdate, rollingUpdate={maxSurge:25%, maxUnavailable:25%}
        │  (persisted on the live object, not declared anywhere in Git)
        ▼
First image bump triggers rolling update
        │
        ▼
New pod scheduled on different node than the RWO PVC's current attachment
        │
        ▼
New pod stuck ContainerCreating forever (Symptom A) ── old pod keeps serving, masking the issue
        │
   (someone adds strategy: {type: Recreate} to fix it)
        │
        ▼
SSA merge produces type=Recreate + still-present rollingUpdate → API validation rejects the whole object (Symptom B)
```

### Technical Details

Explicit `rollingUpdate: null` in the Git manifest was tested as a potential SSA-native fix and **did not work** on this cluster, even when verified to genuinely reach the API server via raw request inspection (`kubectl apply --server-side --dry-run=server -v=8`). The reliable fix is the imperative JSON Patch `replace` shown in Step 3.

## Prevention

### Immediate Prevention

- [x] All four currently-affected apps (`pgadmin`, `ntfy`, `commafeed`, `speedtest`) fixed
      **Impact**: eliminates the deadlock on their next routine image bump

### Long-term Prevention

- [ ] Set `strategy: {type: Recreate}` explicitly in the **first commit** of any future single-replica Deployment backed by an RWO volume, never relying on the implicit default
      - **Why**: avoids the defaulting-drift problem from ever occurring, since the field is never auto-populated with something Git doesn't declare
      - **How**: add to the app's `deployment.yaml` at creation time, not retroactively

### Monitoring & Alerting

No specific alert currently exists for a stuck rollout where the old pod keeps serving (masking user-visible impact). Consider alerting on `kube_deployment_status_replicas_updated < kube_deployment_spec_replicas` sustained for an extended period.

### Documentation Updates

- [x] Documented in war story: [`docs/war-stories/deployment-recreate-strategy-ssa-conflict.md`](../war-stories/deployment-recreate-strategy-ssa-conflict.md)

## Related Issues

- **[Staging Disk Pressure Cleanup](staging-disk-pressure-docker-cleanup.md)** - the outage that froze staging's Flux reconciliation, delaying this fix from reaching staging until both were investigated together

## Original War Story

For the complete investigation narrative including the failed `null` attempt and the exact HTTP request-body verification, see: [`docs/war-stories/deployment-recreate-strategy-ssa-conflict.md`](../war-stories/deployment-recreate-strategy-ssa-conflict.md)

## References

- [Kubernetes Deployment strategy documentation](https://kubernetes.io/docs/concepts/workloads/controllers/deployment/#strategy)
- [Server-Side Apply: clearing fields with null](https://kubernetes.io/docs/reference/using-api/server-side-apply/#clearing-managedfields)

---

**Last Updated**: 2026-07-02
**Tested On**: Production and Staging
**Success Rate**: 4/4 apps resolved (100%)
**Original Incident**: 2026-07-02
