# A HelmRelease Stuck "Failed" for Seven Months While Its Pods Ran Fine

**Date**: July 2, 2026
**Severity**: Medium (cosmetic/status drift, not a live outage)
**Duration**: ~7 months of incorrect status (since 2025-12-16), ~15 minutes to diagnose and fix
**Impact**: Flux reported `alloy` (log shipping DaemonSet) as permanently broken on staging, even though every `alloy` pod had been running healthily the whole time

## The Problem

### Initial Symptoms

While auditing staging after recovering it from a [disk-pressure outage](staging-disk-pressure-docker-bloat.md), the `alloy` HelmRelease stood out as the one thing still unhealthy:

```
NAMESPACE   NAME    REVISION   READY   MESSAGE
monitoring  alloy   1.5.0      False   Helm install failed for release monitoring/alloy with chart alloy@1.5.0: context deadline exceeded
```

But `kubectl get pods -n monitoring | grep alloy` told a completely different story:

```
alloy-g55pt   2/2   Running   2 (3h17m ago)   5h3m
alloy-j87f9   2/2   Running   2 (3h17m ago)   5h3m
alloy-tl7ng   2/2   Running   2 (3h46m ago)   3h46m
alloy-wfv57   2/2   Running   2 (3h16m ago)   5h3m
```

All four DaemonSet pods, fully ready, actively running. The Helm release said "failed" while the actual Kubernetes resources it manages were completely healthy.

## The Investigation

### Step 1: Confirm This Is Stale Status, Not an Active Problem

`helm status alloy -n monitoring` showed the full picture:

```
STATUS: failed
REVISION: 1
DESCRIPTION: Release "alloy" failed: context deadline exceeded

==> v1/DaemonSet
NAME    DESIRED   CURRENT   READY   UP-TO-DATE   AVAILABLE
alloy   4         4         4       4            4
```

`helm history alloy -n monitoring` showed exactly **one** revision, dated `Tue Dec 16 23:38:35 2025` - the exact window the staging disk-pressure outage began. The working theory: the original `helm install` created all the underlying resources successfully, but then timed out waiting for them to report ready (the nodes were under disk pressure at that moment and pod scheduling was almost certainly slow), so Helm recorded the install as failed even though the resources eventually converged on their own.

### Step 2: Try the Obvious Fix

```bash
flux reconcile helmrelease alloy -n monitoring --context=staging
# ► annotating HelmRelease alloy in monitoring namespace
# ✔ HelmRelease annotated
# ◎ waiting for HelmRelease reconciliation
# ✗ Failed to install after 1 attempt(s)
```

Still failed. And critically: `helm history` afterward *still* showed only the single December revision - meaning this reconcile attempt didn't even try a fresh install. Something was short-circuiting before any real work happened.

### Step 3: Check What the Controller Actually Logged

```bash
kubectl logs -n flux-system -l app=helm-controller --since=15m | grep -i alloy
```

```json
{"level":"info","msg":"release is in a failed state", ...}
{"level":"error","msg":"Reconciler error","error":"terminal error: exceeded maximum retries: cannot remediate failed release"}
```

There it was: **`terminal error`**. Flux's `helm-controller` marks a `HelmRelease` install as terminal once it exhausts its retry budget (the `alloy` HelmRelease didn't set `install.remediation.retries` explicitly, so it used Flux's low default). A terminal error is a deliberate circuit-breaker - once tripped, the controller will **never automatically retry again**, no matter how many times `flux reconcile` is called or how long it waits. Every subsequent reconcile just re-reports the same historical failure without doing any real work, which is exactly consistent with `helm history` never gaining a second entry.

## The Root Cause

Flux's retry-exhaustion protection is working as designed - it exists specifically to stop a genuinely broken release from retrying forever and burning resources. The problem was that the underlying *cause* of the original failure (nodes under disk pressure, causing a timeout) had long since resolved itself, but Flux had no way to know that on its own. The circuit breaker doesn't distinguish "this will never succeed" from "this failed once due to a transient condition that's since cleared" - both look identical from its perspective (an install that didn't reach `deployed` status within budget).

## The Solution

Flux's CLI has a purpose-built flag for exactly this situation:

```bash
flux reconcile helmrelease alloy -n monitoring --context=staging --reset
# ► annotating HelmRelease alloy in monitoring namespace
# ✔ HelmRelease annotated
# ◎ waiting for HelmRelease reconciliation
# ✔ applied revision 1.5.0
```

`--reset` clears the failure counter that trips the terminal-error circuit breaker, which lets the *next* reconcile actually attempt real work again instead of short-circuiting. Since the target Kubernetes resources already existed and matched the desired state, the resulting "install" converged almost instantly.

## Verification

```bash
flux get helmrelease alloy -n monitoring --context=staging
# NAME   REVISION   READY   MESSAGE
# alloy  1.5.0      True    Helm upgrade succeeded for release monitoring/alloy.v2 with chart alloy@1.5.0
```

`helm history` now shows a second, successful revision (`v2`), and the HelmRelease's status finally matches what the running pods had shown the whole time.

## Prevention

### 1. Set an Explicit Retry Budget
`install.remediation.retries` isn't set on the `alloy` HelmRelease (or several others in this repo). An explicit, more generous retry count - or explicit `retries: -1` for infinite retries on genuinely idempotent, low-risk releases - would have let this self-heal automatically once the disk-pressure condition cleared, instead of needing a manual `--reset` months later.

### 2. Alert on Terminal HelmRelease State Specifically
A `HelmRelease` reporting `Ready: False` for an extended period is a distinguishable signal Prometheus/Alertmanager could catch (`kube_helmrelease_status` or similar exported by Flux's own metrics) - this would have surfaced the problem in days, not months.

### 3. Don't Assume `flux reconcile` Alone Is Sufficient for a Stuck Release
When a `HelmRelease` won't clear after a plain reconcile, check the `helm-controller` logs before assuming the underlying chart or values are broken - `terminal error` is a distinct, specific condition with its own specific fix (`--reset`), different from a genuine chart/values problem that would need actual manifest changes.

## Lessons Learned

1. **A `Ready: False` status can be completely disconnected from actual resource health** - always cross-check the HelmRelease/Kustomization status against `kubectl get pods` for the resources it's supposed to manage before assuming a controller-level status accurately reflects reality.
2. **`helm history` staying at one revision after a reconcile attempt is a diagnostic signal on its own** - it means the reconcile didn't actually try anything, which narrows the search space immediately.
3. **Flux's retry-exhaustion protection is a circuit breaker, not a bug** - understanding *why* it exists (preventing infinite retry loops against genuinely broken releases) explains why a plain reconcile can't clear it, and why a distinct override mechanism (`--reset`) has to exist.
4. **Root-cause timing correlation is a real diagnostic tool** - the `LAST DEPLOYED` timestamp landing in the exact window of a known, separate incident (the disk-pressure outage) was the clue that connected an otherwise-unexplained "context deadline exceeded" to something concrete, rather than a mystery chart bug.
5. **Read the actual controller logs, not just the summarized status message** - the HelmRelease's own `MESSAGE` field (`context deadline exceeded`) was accurate but insufficient; the real explanation (`terminal error: exceeded maximum retries`) only showed up in `helm-controller`'s own logs.

## Related Documentation

- [Runbook: Flux HelmRelease Stuck in Terminal-Error State](/docs/runbooks/flux-helmrelease-terminal-failure.md) - action-oriented version of this investigation
- [Staging Disk Pressure from Unbounded Docker Growth](staging-disk-pressure-docker-bloat.md) - the incident that most likely caused the original install timeout
- [Flux HelmRelease documentation](https://fluxcd.io/flux/components/helm/helmreleases/) - official remediation and retry semantics

## Commands Reference

**Check HelmRelease status vs actual resource health**:
```bash
flux get helmrelease <name> -n <namespace> --context=<ctx>
kubectl get pods -n <namespace> -l <app-label>
```

**Check Helm's own release history and status**:
```bash
helm status <release> -n <namespace> --kube-context=<ctx>
helm history <release> -n <namespace> --kube-context=<ctx>
```

**Check helm-controller's own logs for the real error**:
```bash
kubectl logs -n flux-system -l app=helm-controller --since=15m | grep -i <release-name>
```

**Clear a terminal-error state and force a genuine fresh attempt**:
```bash
flux reconcile helmrelease <name> -n <namespace> --context=<ctx> --reset
```
