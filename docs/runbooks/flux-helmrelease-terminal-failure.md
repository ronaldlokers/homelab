# Flux HelmRelease Stuck in Terminal-Error State

## Quick Reference

- **Severity**: Medium (usually cosmetic status drift, but blocks all future automated reconciliation of that release)
- **Estimated Time to Resolve**: 5 minutes
- **Symptoms**: `flux get helmrelease` shows `Ready: False` indefinitely; `flux reconcile helmrelease` reports "Failed to install after 1 attempt(s)" every time, with no change in behavior
- **Affected Components**: Any single HelmRelease that hit an install/upgrade failure and exhausted its retry budget
- **Environment**: Production or Staging
- **Prerequisites**: `kubectl` and `flux` CLI access

## Symptoms & Detection

### Error Messages

HelmRelease status:
```
Helm install failed for release <namespace>/<release> with chart <chart>@<version>: context deadline exceeded
```

`helm-controller` logs (the actual smoking gun - not visible in the HelmRelease status message):
```json
{"msg":"release is in a failed state", ...}
{"level":"error","msg":"Reconciler error","error":"terminal error: exceeded maximum retries: cannot remediate failed release"}
```

### Observable Behavior

- `flux reconcile helmrelease <name>` appears to run, but reports failure immediately and `helm history <release>` never gains a new revision - meaning no real install attempt is actually happening
- The underlying Kubernetes resources the chart manages (Deployments, DaemonSets, etc.) may be **completely healthy** despite the `Ready: False` status - this is a status/reality mismatch, not necessarily a live outage

### Monitoring Indicators

- No specific alert currently wired for this condition - it was found via manual audit, not paging

## Immediate Actions

**Check whether the underlying workload is actually broken before treating this as urgent:**

```bash
kubectl get pods -n <namespace> -l <app-label>
```

If the pods are healthy and running, this is a status-accuracy problem, not an active outage - proceed calmly through diagnosis rather than treating it as time-critical.

## Diagnosis Steps

### 1. Check the HelmRelease status

```bash
flux get helmrelease <name> -n <namespace> --context=<ctx>
```

### 2. Cross-check actual resource health

```bash
kubectl get pods -n <namespace> -l <app-label>
```

### 3. Check Helm's own release history

```bash
helm status <release> -n <namespace> --kube-context=<ctx>
helm history <release> -n <namespace> --kube-context=<ctx>
```

A single revision stuck at `STATUS: failed`, with a `LAST DEPLOYED` timestamp that doesn't match any recent activity, is the signature of this issue.

### 4. Try a plain reconcile first, then check the controller logs

```bash
flux reconcile helmrelease <name> -n <namespace> --context=<ctx>
kubectl logs -n flux-system -l app=helm-controller --since=15m --context=<ctx> | grep -i <release-name>
```

**This is the right runbook if:**
- ✅ `helm-controller` logs show `terminal error: exceeded maximum retries: cannot remediate failed release`
- ✅ `helm history` shows only one revision despite the reconcile attempt
- ✅ A plain `flux reconcile` doesn't change anything

**This is NOT the right runbook if:**
- ❌ The error message differs (a genuine chart/values problem needs manifest changes, not a reset)
- ❌ `helm history` shows the reconcile is genuinely attempting new revisions and failing on real errors (check those errors instead)

## Resolution Steps

### Step 1: Reset the failure counter and force a fresh attempt

**Why**: Flux's `terminal error` state is a deliberate circuit breaker that stops retrying automatically once the retry budget is exhausted - it will never self-heal even after the underlying cause resolves, because it doesn't re-evaluate whether the original failure condition still applies. `--reset` clears that counter so the next reconcile does real work instead of short-circuiting.

```bash
flux reconcile helmrelease <name> -n <namespace> --context=<ctx> --reset
```

**Expected output**:
```
► annotating HelmRelease <name> in <namespace> namespace
✔ HelmRelease annotated
◎ waiting for HelmRelease reconciliation
✔ applied revision <version>
```

**If this fails**: the underlying chart/values may have a genuine problem unrelated to the terminal-error state - check the new failure message, which will now reflect a real attempt rather than the old cached one.

## Verification

- [ ] HelmRelease reports `Ready: True`
      ```bash
      flux get helmrelease <name> -n <namespace> --context=<ctx>
      # Expected: READY=True, MESSAGE mentions "succeeded"
      ```

- [ ] `helm history` shows a new, successful revision
      ```bash
      helm history <release> -n <namespace> --kube-context=<ctx>
      # Expected: a second (or later) revision with STATUS=deployed
      ```

- [ ] Underlying pods still healthy (should be unchanged if they already were)
      ```bash
      kubectl get pods -n <namespace> -l <app-label>
      ```

## Root Cause

### What Caused This

Flux's `helm-controller` marks an install as terminal once it exhausts the configured retry budget (`install.remediation.retries`, which defaults low if unset). This is intentional - it prevents a genuinely broken release from retrying forever and burning resources.

### Why It Manifested Now

The original install attempt likely failed due to a transient, unrelated condition (e.g. cluster under disk pressure at the time, causing pod scheduling delays that exceeded Helm's operation timeout). Once that condition cleared, the underlying resources may have converged to healthy on their own - but Flux had no mechanism to notice that and re-evaluate, since it was already in the terminal state.

### Component Interaction

```
Chart install attempted → times out waiting for resources to report ready
        │
        ▼
Retry budget exhausted → helm-controller marks release "terminal"
        │
        ▼
Every subsequent `flux reconcile` short-circuits, re-reporting the SAME cached failure
        │  (no new helm history revision, no real work attempted)
        ▼
Underlying resources may converge to healthy independently, but status never updates
        │
        ▼
`flux reconcile --reset` clears the terminal flag → next reconcile does real work → status catches up to reality
```

## Prevention

### Immediate Prevention

- [ ] Consider setting an explicit, more generous `install.remediation.retries` on low-risk/idempotent releases
      ```yaml
      spec:
        install:
          remediation:
            retries: 3   # or -1 for infinite retries on safe-to-retry releases
      ```
      **Impact**: allows genuine self-healing after a transient failure clears, without needing manual `--reset`

### Long-term Prevention

- [ ] Add alerting on `HelmRelease` resources stuck `Ready: False` for an extended period
      - **Why**: this specific incident went unnoticed for roughly seven months; the status was simply never checked
      - **How**: Flux exports HelmRelease status as Prometheus metrics - alert on sustained `Ready != True`

### Documentation Updates

- [x] Documented in war story: [`docs/war-stories/flux-helmrelease-terminal-failure.md`](../war-stories/flux-helmrelease-terminal-failure.md)

## Related Issues

- **[Staging Disk Pressure Cleanup](staging-disk-pressure-docker-cleanup.md)** - the most likely original trigger for the install timeout that led to this terminal state

## Original War Story

For the complete investigation narrative, see: [`docs/war-stories/flux-helmrelease-terminal-failure.md`](../war-stories/flux-helmrelease-terminal-failure.md)

## References

- [Flux HelmRelease remediation documentation](https://fluxcd.io/flux/components/helm/helmreleases/#remediation)
- [flux reconcile helmrelease CLI reference](https://fluxcd.io/flux/cmd/flux_reconcile_helmrelease/)

---

**Last Updated**: 2026-07-02
**Tested On**: Staging
**Success Rate**: 1/1 incidents resolved (100%)
**Original Incident**: 2026-07-02 (original failure dated 2025-12-16)
