# Switching to `Recreate` Strategy Couldn't Fix Itself Through Git Alone

**Date**: July 2, 2026
**Severity**: High
**Duration**: 4+ days undetected (pgadmin stuck), ~30 minutes to diagnose and fix once found
**Impact**: `pgadmin` pod wedged in `ContainerCreating` for 4+ days in production; fixing it later blocked the entire `apps` Flux Kustomization on both staging and production simultaneously

## The Problem

### Initial Symptoms

A routine repo audit found `pgadmin4-776c55874f-zsgbj` stuck in `ContainerCreating` for 4 days 16 hours in production, while an older `pgadmin4` pod kept running normally alongside it:

```
NAMESPACE   NAME                        READY   STATUS              RESTARTS   AGE
pgadmin     pgadmin4-776c55874f-zsgbj   0/1     ContainerCreating   0          4d16h
pgadmin     pgadmin4-c89b64b85-gbmnh    1/1     Running             0          5d18h
```

`kubectl describe pod` showed no scheduling problem and no error events at all - just silence, forever.

### The First Root Cause

`kubectl get deployment pgadmin4 -o jsonpath='strategy={.spec.strategy.type}'` showed `RollingUpdate` (the implicit Kubernetes default, since the manifest never set `strategy` at all). Cross-referencing the PVC:

```
NAME       STATUS   VOLUME                                     ACCESS MODES
pgadmin4   Bound    pvc-f01e7f3d-89a4-42b7-989e-25d16f00a647   RWO
```

A `dpage/pgadmin4:9.16` Renovate bump had triggered a routine rolling update. Kubernetes tried to schedule the new pod (which landed on `kube-srv-3`) before terminating the old one (running on `kube-srv-2`). Since the PVC is `ReadWriteOnce`, it can only be attached to one node at a time - the new pod could never mount the volume while the old pod held it, and had no way to time out and retry differently. **A single-replica Deployment with an exclusive-attach PVC can never complete a `RollingUpdate`** - it's a structural deadlock, not a transient failure.

### The Fix (Or So It Seemed)

The same shape of bug existed in three other apps with identical single-replica + RWO-PVC patterns: `ntfy`, `commafeed`, `speedtest`. All four got `strategy: {type: Recreate}` added to their Deployment manifests, committed, and pushed.

## The Investigation

### The Fix Broke Something Else

After pushing, `flux get kustomizations --context=production` showed the `apps` Kustomization stuck:

```
NAME	READY	MESSAGE
apps	False	Deployment/commafeed/commafeed dry-run failed (Invalid): Deployment.apps "commafeed" is invalid: spec.strategy.rollingUpdate: Forbidden: may not be specified when strategy `type` is 'Recreate'
```

Since all four apps live in a single Flux Kustomization, this one failing resource blocked *everything* in `apps` from applying - a much bigger blast radius than the original bug.

### Understanding Why

```bash
kubectl get deployment commafeed -n commafeed -o jsonpath='{.spec.strategy}'
# {"rollingUpdate":{"maxSurge":"25%","maxUnavailable":"25%"},"type":"RollingUpdate"}
```

The live object still carried a fully-populated `rollingUpdate` sub-object, even though the Git manifest had never explicitly set it. This was the Kubernetes API server's own defaulting behavior from the object's original creation: when `strategy.type` isn't specified, it defaults to `RollingUpdate` *and* the API server fills in `maxSurge`/`maxUnavailable` defaults as a real, persisted field on the object. Submitting `type: Recreate` without also clearing that sibling field produces an object the Deployment API considers invalid on its face - `rollingUpdate` can't coexist with `type: Recreate`, full stop.

### First Attempted Fix: Explicit `null`

Kubernetes' server-side-apply (SSA) documentation states that submitting a field with an explicit `null` is the correct way to tell SSA to remove a field you own. So the manifest became:

```yaml
strategy:
  type: Recreate
  rollingUpdate: null
```

This should, per the docs, take ownership of `rollingUpdate` and clear it. **It didn't work.** Verified two ways:

1. Inspecting the literal HTTP request body kubectl sends (`kubectl apply --server-side --dry-run=server -v=8`) confirmed the `null` genuinely reached the API server:
```json
"strategy":{"rollingUpdate":null,"type":"Recreate"}
```
2. Re-running the exact same dry-run with `--field-manager=kustomize-controller` (matching Flux's real identity) against the live cluster still failed identically:
```
The Deployment "commafeed" is invalid: spec.strategy.rollingUpdate: Forbidden: may not be specified when strategy `type` is 'Recreate'
```

The `null` was reaching the server and still not clearing the field. This ruled out "kubectl isn't sending it" as an explanation - something about how this specific cluster's Deployment API handles the merge was the real blocker. Rather than keep guessing at SSA internals, the `rollingUpdate: null` addition was reverted from the manifest (it wasn't helping and would have been confusing dead weight for a future reader) and the fix moved to the live objects directly.

## The Root Cause

Kubernetes' Deployment API validates the fully-merged object *after* SSA has combined the new patch with existing field ownership - and in this cluster's version, a declarative `null` from a new field manager wasn't sufficient to strip a field that had been implicitly populated by the API server's own defaulting logic at creation time, attributed to a different (or ambiguous) field manager. This is exactly the kind of drift that **Git alone cannot fix**: the desired state in the repository was correct the whole time, but the live object's history included a field no commit had ever explicitly set, and no amount of re-applying the correct YAML would remove it through the normal GitOps apply path.

## The Solution

### First Attempt: JSON Patch `remove` (Also Didn't Work)

```bash
kubectl patch deployment commafeed -n commafeed --type=json \
  -p='[{"op": "remove", "path": "/spec/strategy/rollingUpdate"}]'
# deployment.apps/commafeed patched (no change)
```

`patched (no change)` was the tell. At the moment this ran, the live object's `type` was *still* `RollingUpdate` (Git's fix had never successfully applied). Removing just the `rollingUpdate` sub-field left `type: RollingUpdate` behind for a moment - and the API server's defaulting logic runs on every write, saw `type: RollingUpdate` with no `rollingUpdate` value, and immediately re-populated the exact same defaults within that same request. The "fix" was invisible because it was undone by the same write that applied it.

### The Actual Fix: Atomic Replace

The two fields needed to change together, with no intermediate state where `type` was still `RollingUpdate`:

```bash
for d in commafeed:commafeed ntfy:ntfy pgadmin4:pgadmin speedtest:speedtest; do
  name=${d%%:*}; ns=${d##*:}
  kubectl patch deployment "$name" -n "$ns" --context=production \
    --type=json -p='[{"op": "replace", "path": "/spec/strategy", "value": {"type": "Recreate"}}]'
done
```

Replacing the *whole* `strategy` object in a single JSON Patch operation, rather than surgically removing one sub-key, left no window for the defaulting logic to reassert the old value. This was run against both the production and staging clusters (staging carries `commafeed` and `pgadmin`, not `ntfy`/`speedtest`, since those two are production-only apps).

Once the live drift was cleared this way, the already-correct Git manifest (`strategy: {type: Recreate}`, nothing else) applied cleanly on the very next Flux reconcile - no further changes to the repository were needed.

## Verification

```bash
kubectl get deployment commafeed -n commafeed -o jsonpath='{.spec.strategy}'
# {"type":"Recreate"}   <- no rollingUpdate key at all

flux get kustomization apps --context=production
# NAME  READY  MESSAGE
# apps  True   Applied revision: main@sha1:...
```

The stuck `pgadmin` pod was deleted (not left hanging) and a fresh pod started cleanly under the new `Recreate` strategy, since the deadlock condition (two pods wanting the same RWO volume at once) could no longer occur.

## Prevention

### 1. Set `strategy` Explicitly From the Start
Any future single-replica Deployment with an exclusive-attach volume should get `strategy: {type: Recreate}` in its *first* commit, never relying on the implicit `RollingUpdate` default. This avoids the defaulting-drift problem entirely, since the field is never auto-populated with something Git doesn't know about.

### 2. Remember Git-Declared State ≠ Live State
GitOps tools reconcile toward the declared state, but they can't retroactively erase history the live object accumulated before that declaration existed. When a change to a *type* field (not just a value) fails validation unexpectedly, check whether the live object is carrying leftover sibling fields from before the change - `kubectl get <resource> -o jsonpath='{.spec.<field>}'` against the *live* cluster, not just the rendered Git manifest, is the fastest way to confirm.

### 3. `null` in SSA Is Not Always a Guaranteed Field-Clear
Don't assume the documented SSA behavior for clearing a field with `null` works uniformly across all field types and cluster versions - verify empirically with `--dry-run=server` before relying on it, especially for fields the API server itself may have defaulted rather than a prior applier explicitly set.

## Lessons Learned

1. **A single-replica Deployment with an exclusive-attach PVC can never complete a `RollingUpdate`** - this is a structural incompatibility, not a flaky rollout. Recognize the pattern (`replicas: 1` + `accessModes: [ReadWriteOnce]`) and set `Recreate` proactively.
2. **The old pod staying up masks the failure** - because the previous pod kept serving traffic, this deadlock was silently invisible for over four days. A stuck rollout with no user-visible symptom is easy to miss without alerting on `Deployment` rollout status specifically.
3. **`kubectl apply --dry-run=server` with a matching `--field-manager` is the way to test SSA behavior safely** - it exactly simulates what the real controller would do without persisting anything, and it caught that the `null` fix genuinely didn't work before it was ever pushed as "the fix."
4. **`patched (no change)` is a real signal, not a no-op to ignore** - it means the write happened but produced an object identical to before, which is exactly what happens when a defaulting mechanism silently reverts a partial change within the same request.
5. **When two fields must change together, change them in one atomic operation** - a `replace` on the parent object avoids any window where an inconsistent intermediate state can be reasserted by something else (defaulting, another controller, a webhook).
6. **Git alone can't fix live drift the API server itself introduced** - some fixes require a one-time imperative correction to the live cluster before the declarative source of truth can take over cleanly again.

## Related Documentation

- [Runbook: Single-Replica Deployment Wedged After Switching to Recreate Strategy](/docs/runbooks/deployment-recreate-strategy-stuck-rollout.md) - action-oriented version of this investigation
- [Staging Disk Pressure from Unbounded Docker Growth](staging-disk-pressure-docker-bloat.md) - the incident that surfaced this bug, since staging's frozen Flux reconciliation meant this fix hadn't reached staging either until both were investigated together
- [Repository Architecture](/docs/architecture.md)

## Commands Reference

**Check a Deployment's live strategy (not just what's in Git)**:
```bash
kubectl get deployment <name> -n <namespace> -o jsonpath='{.spec.strategy}'
```

**Safely test an SSA change before it's live, matching Flux's identity**:
```bash
kubectl apply --server-side --dry-run=server --field-manager=kustomize-controller \
  -f <(kustomize build <path>) 2>&1
```

**Atomically replace a whole sub-object via JSON Patch (avoids partial-state defaulting issues)**:
```bash
kubectl patch deployment <name> -n <namespace> --type=json \
  -p='[{"op": "replace", "path": "/spec/strategy", "value": {"type": "Recreate"}}]'
```
