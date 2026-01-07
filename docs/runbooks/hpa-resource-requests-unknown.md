# HPA Shows Unknown CPU Metrics

## Quick Reference

- **Severity**: Medium (HPA non-functional)
- **Estimated Time to Resolve**: 10 minutes
- **Symptoms**: HorizontalPodAutoscaler shows `<unknown>/X%` for CPU targets
- **Affected Components**: Any deployment with HPA using percentage-based CPU targets
- **Prerequisites**: Understanding of Kubernetes resource requests
- **Data Loss Risk**: None

## Symptoms & Detection

### Error Messages

No explicit error - just unhelpful status:

```bash
kubectl get hpa -n <namespace>
NAME            REFERENCE                  TARGETS              MINPODS   MAXPODS   REPLICAS
app-hpa         Deployment/app             cpu: <unknown>/50%   1         3         1
```

### Observable Behavior

- HPA created successfully
- Targets show `<unknown>/X%` where X is the target percentage
- No scaling occurs regardless of actual CPU usage
- Metrics server is running and functional
- `kubectl top pods` works fine

### Monitoring Indicators

- HPA not scaling despite high/low CPU usage
- HPA age increasing but replicas never change
- Describe HPA shows no scaling events

## Immediate Actions

**If you need auto-scaling RIGHT NOW:**

No quick workaround - you must add resource requests. However, this is fast once you know the fix.

**Quick diagnosis**:

```bash
# Check if pods have resource requests
kubectl get deployment <deployment-name> -n <namespace> -o jsonpath='{.spec.template.spec.containers[0].resources}'

# If output is {} or empty, that's your problem
```

## Diagnosis Steps

### 1. Verify HPA configuration

```bash
# Check HPA details
kubectl get hpa <hpa-name> -n <namespace> -o yaml

# Look for:
spec:
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 50  # Percentage-based target
```

**Percentage-based targets require requests**.

### 2. Check metrics server is working

```bash
# Verify metrics-server running
kubectl get pods -n kube-system | grep metrics-server

# Test node metrics
kubectl top nodes
# Should show CPU/memory usage

# Test pod metrics
kubectl top pods -n <namespace>
# Should show CPU/memory for pods
```

**If metrics don't work**, this is a different issue (metrics-server problem).

### 3. Check deployment resource requests

```bash
# Get deployment resources
kubectl get deployment <deployment-name> -n <namespace> -o jsonpath='{.spec.template.spec.containers[*].resources}'

# Empty {} means no resources defined
# Should see something like:
# {"requests":{"cpu":"50m","memory":"256Mi"},"limits":{"memory":"512Mi"}}
```

### 4. Understand the math

**HPA percentage calculation**:
```
current_utilization = (current_cpu / requested_cpu) * 100
```

**Without requests**:
```
current_utilization = (250m / 0) * 100 = undefined
```

**Result**: `<unknown>`

### 5. Confirm diagnosis

**This is the right runbook if:**
- ✅ HPA shows `<unknown>` for CPU target
- ✅ Using percentage-based metric (`averageUtilization`)
- ✅ Pod has no `resources.requests.cpu` defined
- ✅ Metrics server is functional

**This is NOT the right runbook if:**
- ❌ Metrics server not working
- ❌ Using absolute value targets (`averageValue`)
- ❌ Different error message
- ❌ Resource requests already defined

## Resolution Steps

### Step 1: Determine appropriate resource requests

**Check actual usage first**:

```bash
# View current CPU usage
kubectl top pods -n <namespace>
# NAME                   CPU(cores)   MEMORY(bytes)
# app-xxxxx              25m          512Mi

# Or view over time in Grafana
# Query: rate(container_cpu_usage_seconds_total{pod=~"app-.*"}[5m])
```

**Sizing guidelines**:
- Set `requests` to typical baseline usage
- Set slightly above observed minimum (not peak)
- For example: If app uses 10-30m normally, set request to 50m
- Leaves room for overhead and scaling decisions

### Step 2: Update deployment configuration

**For Helm deployments**, edit values patch:

```yaml
# apps/<env>/<app>/values-patch.yaml
spec:
  values:
    controllers:
      main:
        containers:
          main:
            resources:
              requests:
                cpu: 50m        # ✓ Required for HPA
                memory: 256Mi   # Good practice
              limits:
                memory: 512Mi   # Prevent OOM kills
```

**For direct manifests**, edit deployment:

```yaml
# deployment.yaml
spec:
  template:
    spec:
      containers:
        - name: app
          resources:
            requests:
              cpu: 50m
              memory: 256Mi
            limits:
              memory: 512Mi
```

**CPU limits**: Generally avoid CPU limits unless necessary. They cause throttling.

### Step 3: Apply changes

**For GitOps (Flux)**:

```bash
# Commit changes
git add apps/<env>/<app>/values-patch.yaml
git commit -m "fix: add resource requests for HPA to function"
git push

# Reconcile
flux reconcile helmrelease <app> -n <namespace>
```

**For direct kubectl**:

```bash
kubectl apply -f deployment.yaml
```

### Step 4: Wait for pods to restart

```bash
# Watch pods restart with new resource config
kubectl get pods -n <namespace> --watch

# All pods should restart and return to Running
```

### Step 5: Verify HPA can now calculate metrics

```bash
# Check HPA status
kubectl get hpa -n <namespace>
# NAME            TARGETS          MINPODS   MAXPODS   REPLICAS
# app-hpa         cpu: 12%/50%     1         3         1
#                     ↑ No longer <unknown>!

# View detailed status
kubectl describe hpa <hpa-name> -n <namespace>
# Should show:
#   current: 12% (6m / 50m)  ← Now calculating correctly
```

## Verification

### Confirm resolution:

- [ ] Resources defined in deployment
      ```bash
      kubectl get deployment <name> -n <namespace> -o jsonpath='{.spec.template.spec.containers[0].resources.requests}'
      # Should show {"cpu":"50m","memory":"256Mi"}
      ```

- [ ] HPA shows percentage instead of unknown
      ```bash
      kubectl get hpa -n <namespace>
      # TARGETS column should show "XX%/YY%" not "<unknown>/YY%"
      ```

- [ ] HPA can make scaling decisions
      ```bash
      kubectl describe hpa <name> -n <namespace> | grep -A5 "Conditions:"
      # Should show: ScalingActive  True
      #              AbleToScale     True
      ```

- [ ] Test scaling behavior (optional but recommended)
      ```bash
      # Generate load (method depends on app)
      # For web app: send many requests
      # For ML app: trigger batch job

      # Watch HPA respond
      kubectl get hpa -n <namespace> --watch
      # Should see REPLICAS increase when TARGETS exceeds threshold
      ```

## Root Cause

### Why HPA Needs Resource Requests

**From Kubernetes documentation**:

> For per-pod resource metrics (like CPU), the controller fetches the metrics from the resource metrics API for each Pod targeted by the HorizontalPodAutoscaler. Then, if a target utilization value is set, the controller calculates the utilization value as a percentage of the equivalent resource request on the containers in each Pod.

**Translation**: HPA needs requests to calculate what "50%" means.

### The Math

**With requests defined**:
```
Current CPU: 25m (from metrics server)
Requested CPU: 50m (from pod spec)
Utilization: (25m / 50m) * 100 = 50%
Target: 50%
Decision: At target, don't scale
```

**Without requests**:
```
Current CPU: 25m
Requested CPU: 0 (undefined)
Utilization: (25m / 0) = ERROR: division by zero
Display: <unknown>
Decision: Cannot scale (no baseline)
```

### Multiple Purposes of Resource Requests

Resource requests aren't just for HPA:

1. **Scheduler**: Places pods on nodes with sufficient resources
2. **HPA**: Calculates percentage-based utilization
3. **QoS Class**: Determines pod quality-of-service tier
4. **Resource Quotas**: Enforces namespace limits
5. **Metrics**: Basis for resource efficiency calculations

## Prevention

### Always Define Requests

**For any pod that might scale**:

- [ ] Set `resources.requests.cpu` from the start
- [ ] Base on observed usage, not guesses
- [ ] Document why values were chosen
- [ ] Review and adjust based on actual metrics

### Request Sizing Strategy

**Too low (100m request, app uses 200m)**:
- Scheduler may pack too many pods per node
- HPA triggers unnecessarily (shows 200% utilization)
- Node resource exhaustion

**Too high (1000m request, app uses 50m)**:
- Wasted node capacity
- Fewer pods fit per node
- Higher costs
- HPA never triggers (shows 5% utilization)

**Just right (100m request, app uses 50-150m)**:
- Room for variance
- Scheduler packs efficiently
- HPA triggers at appropriate times

### Testing HPA

**Don't wait for production load to test**:

```bash
# 1. Deploy with HPA
# 2. Verify HPA shows percentages (not <unknown>)
# 3. Generate test load
# 4. Watch HPA scale up
# 5. Remove load
# 6. Watch HPA scale down (after stabilization window)
```

**For Immich example**:
```bash
# Trigger face detection on large library
# Watch HPA scale ML pods from 1→3
# Verify processing speeds up
# After completion, HPA scales back down
```

## Related Issues

- **HPA scaling too aggressively**: Adjust `averageUtilization` threshold
- **HPA not scaling down**: Check `behavior.scaleDown.stabilizationWindowSeconds`
- **Pods getting OOMKilled**: Set memory limits appropriately
- **CPU throttling**: Avoid CPU limits if possible

## Original War Story

For the complete investigation including the confusion between metrics-server functionality and HPA requirements, see: [`docs/war-stories/hpa-resource-requests.md`](../war-stories/hpa-resource-requests.md)

## References

- [Kubernetes HPA Documentation](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/)
- [HPA Algorithm Details](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/#algorithm-details)
- [Resource Requests and Limits](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/)
- [HPA Walkthrough](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale-walkthrough/)

---

**Last Updated**: 2026-01-07
**Tested On**: Production Immich deployment
**Success Rate**: 100%
**Time Saved**: Minutes vs hours of confusion
