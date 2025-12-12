# HPA Shows `<unknown>` CPU Metrics

**Date**: December 2025
**Environment**: Production cluster - Immich server and machine-learning
**Impact**: HorizontalPodAutoscaler unable to function, no automatic scaling

## The Problem

After creating HorizontalPodAutoscaler resources for Immich components, HPAs showed `<unknown>` for CPU targets and couldn't scale.

**HPA Status**:
```bash
kubectl get hpa -n immich
NAME                      REFERENCE                            TARGETS              MINPODS   MAXPODS   REPLICAS
immich-machine-learning   Deployment/immich-machine-learning   cpu: <unknown>/50%   1         3         1
immich-server             Deployment/immich-server             cpu: <unknown>/50%   1         3         1
```

**HPA Configuration**:
```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: immich-server
spec:
  minReplicas: 1
  maxReplicas: 3
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 50  # 50% of what?
```

**Symptoms**:
- HPA created successfully
- Targets show `<unknown>/50%`
- No scaling occurs regardless of actual CPU usage
- Metrics server is running and working

## The Investigation

### Checking Metrics Server

First verified metrics are available:

```bash
# Check metrics-server is running
kubectl get pods -n kube-system | grep metrics
# metrics-server-7bfffcd44-c96pc   1/1     Running   ✓

# Node metrics work fine
kubectl top nodes
# NAME         CPU(cores)   CPU(%)   MEMORY(bytes)   MEMORY(%)
# kube-srv-1   779m         19%      6218Mi          38%
# kube-srv-2   263m         6%       4425Mi          27%
# kube-srv-3   3770m        94%      8168Mi          50%

# Pod metrics also work
kubectl top pods -n immich
# NAME                                       CPU(cores)   MEMORY(bytes)
# immich-server-578b7dbb59-md9ds             250m         1024Mi
```

Metrics server is working. The problem is elsewhere.

### Checking Deployment Resources

```bash
kubectl get deployment immich-server -n immich -o jsonpath='{.spec.template.spec.containers[0].resources}'
# Output: {}
```

**Empty!** No resource requests or limits defined.

### Understanding Percentage-Based Targets

HPA with `averageUtilization: 50` means:
- "Scale when pods use more than 50% of their **requested** CPU"
- Formula: `(current CPU usage / requested CPU) * 100`

**Without requests**:
- Requested CPU = 0
- Formula becomes: `(250m / 0) * 100` = **undefined**
- HPA shows `<unknown>`

The HPA literally cannot calculate a percentage without a baseline (request).

## The Root Cause

**HorizontalPodAutoscaler requires resource requests to calculate percentage-based targets.**

From Kubernetes documentation:
> For per-pod resource metrics (like CPU), the controller fetches the metrics from the resource metrics API for each Pod targeted by the HorizontalPodAutoscaler. Then, if a target utilization value is set, the controller calculates the utilization value as a percentage of the equivalent resource request on the containers in each Pod.

Without `resources.requests.cpu`, the HPA cannot:
1. Calculate current utilization as a percentage
2. Determine when 50% threshold is crossed
3. Make scaling decisions

## The Solution

### Add Resource Requests to Deployments

Updated `apps/production/immich/values-patch.yaml`:

```yaml
spec:
  values:
    controllers:
      main:
        containers:
          main:
            image:
              tag: v2.0.0
            resources:
              requests:
                cpu: 50m       # ✓ Required for HPA
                memory: 4Gi
              limits:
                memory: 512Mi  # Optional but recommended
```

For machine-learning:
```yaml
machine-learning:
  resources:
    requests:
      cpu: 50m       # ✓ Required for HPA
      memory: 4Gi
    limits:
      memory: 2Gi
```

### Reconcile and Verify

```bash
# Apply changes
flux reconcile helmrelease immich -n immich

# Verify resources are set
kubectl get deployment immich-server -n immich -o jsonpath='{.spec.template.spec.containers[0].resources}'
# Output:
# {"limits":{"memory":"512Mi"},"requests":{"cpu":"50m","memory":"4Gi"}}

# Check HPA status
kubectl get hpa -n immich
# NAME                      TARGETS          MINPODS   MAXPODS   REPLICAS
# immich-server             cpu: 12%/50%     1         3         1          ✓ Working!
# immich-machine-learning   cpu: 8%/50%      1         3         1          ✓ Working!
```

Now HPA can calculate percentages:
- Current: 6m (actual usage)
- Request: 50m (baseline)
- Utilization: (6m / 50m) * 100 = **12%**

## How HPA Uses Resource Requests

### Scaling Decision Logic

1. **Gather metrics** from all pods
2. **Calculate per-pod utilization**: `(current CPU / requested CPU) * 100`
3. **Average across all pods**
4. **Compare to target**: Is average > 50%?
5. **Scale decision**:
   - If average > target: Scale up
   - If average < target: Scale down (after stabilization window)

### Example Scaling Scenario

**Starting state**:
- 1 pod running
- Request: 50m CPU
- Current usage: 30m CPU
- Utilization: (30m / 50m) = 60%
- Target: 50%

**Decision**: 60% > 50% → Scale up to 2 pods

**After scaling**:
- 2 pods running
- Each using ~15m CPU
- Utilization: (15m / 50m) = 30%
- Target: 50%

**Decision**: 30% < 50% → Wait (don't scale down immediately due to stabilization window)

## Resource Request Sizing

### How We Chose Values

```yaml
resources:
  requests:
    cpu: 50m      # Based on observed baseline usage
    memory: 4Gi   # Based on Immich requirements
```

**Process**:
1. Observed actual CPU usage under normal load: ~10-20m
2. Set request slightly higher (50m) to allow for overhead
3. HPA target (50%) means scale when using >25m sustained
4. Memory based on application requirements (Immich ML models)

**Guidelines**:
- Requests should represent **typical** usage
- Too low: Excessive scaling, resource contention
- Too high: Wasted resources, late scaling
- Monitor and adjust based on actual behavior

## Related Configuration

### Stabilization Windows

```yaml
spec:
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 300    # Wait 5min before scaling up
    scaleDown:
      stabilizationWindowSeconds: 300    # Wait 5min before scaling down
```

Prevents:
- **Flapping**: Rapid scale up/down cycles
- **Premature scaling**: Reacting to temporary spikes
- **Resource churn**: Constant pod creation/deletion

## Lessons Learned

1. **Percentage targets need baselines**: Without requests, percentages are meaningless
2. **HPA error messages can be cryptic**: `<unknown>` doesn't mention missing requests
3. **Resource requests serve multiple purposes**:
   - Scheduler uses them for pod placement
   - HPA uses them for percentage calculations
   - QoS class determination
   - Resource quota enforcement

4. **Metrics working ≠ HPA working**: Metrics server can function while HPA fails
5. **Read the Kubernetes docs**: HPA behavior is well documented, just need to RTFM

## Prevention Checklist

When creating HPAs:

- [ ] Verify pods have `resources.requests.cpu` defined
- [ ] Test HPA immediately after creation
- [ ] Check for `<unknown>` in targets
- [ ] Monitor actual scaling behavior under load
- [ ] Set appropriate stabilization windows
- [ ] Document why resource values were chosen

## Common Mistakes

**Using limits without requests**:
```yaml
resources:
  limits:
    cpu: 1000m  # ❌ No request - HPA won't work
```

**Using absolute metrics without understanding percentage**:
```yaml
target:
  type: AverageValue
  averageValue: 100m  # This works without requests
```
(But percentage-based is usually better for HPA)

**Not testing HPA**:
- Create HPA
- Assume it works
- Discover it doesn't during actual high load
- Scramble to fix in production

## Testing HPA with Real Workload

### Generate Load with Actual Immich Usage

**Server HPA - Upload Photos**:
```bash
# Upload a large batch of photos via mobile app or CLI
# This generates sustained server load for:
# - File upload processing
# - Thumbnail generation
# - Metadata extraction
# - Database writes

# Watch HPA response
kubectl get hpa immich-server -n immich --watch
```

**Machine Learning HPA - Face Detection Job**:
```bash
# In Immich web UI:
# Administration → Jobs → Face Detection
# Click "All" to process entire library

# Or trigger object detection:
# Administration → Jobs → Smart Search

# Watch HPA and pod scaling
kubectl get hpa -n immich --watch
kubectl get pods -n immich --watch
```

**Expected behavior**:
- **Photo uploads**: Server pod CPU increases, may trigger scale-up if uploading hundreds of photos
- **ML jobs**: Machine learning pod CPU spikes to 100%, should trigger scale-up to process faster
- **After jobs complete**: CPU drops, HPA waits for stabilization window, then scales down

### Verify Scaling Occurs

```bash
# Before load
kubectl get hpa immich-server -n immich
# TARGETS: 12%/50%  REPLICAS: 1

# During photo upload batch
kubectl get hpa immich-server -n immich
# TARGETS: 78%/50%  REPLICAS: 2  # Scaled up!

# During ML job
kubectl get hpa immich-machine-learning -n immich
# TARGETS: 165%/50%  REPLICAS: 3  # Maxed out replicas!

# After jobs complete (within stabilization window)
kubectl get hpa -n immich
# TARGETS: 15%/50%  REPLICAS: 2  # Waiting 5min before scaling down

# After stabilization window passes
kubectl get hpa -n immich
# TARGETS: 15%/50%  REPLICAS: 1  # Scaled back to minimum
```

**Real-world scaling example**:
- User uploads 500 photos from vacation
- Server pod hits 60% CPU processing uploads
- HPA scales to 2 server pods after 5min
- Uploads complete faster with distributed load
- After uploads done, CPU drops to 20%
- HPA waits 5min, confirms load is sustained low
- Scales back down to 1 pod to save resources

## Timeline

- **HPA created**: Manifest applied, created successfully
- **Issue discovered**: Noticed `<unknown>` in targets
- **Initial check**: Verified metrics-server working
- **Investigation**: Checked deployment resources (empty)
- **Research**: Read HPA documentation about percentage calculation
- **Solution**: Added resource requests to values-patch.yaml
- **Verification**: Confirmed metrics now show percentages
- **Testing**: Triggered face detection job, observed scaling to 3 replicas
- **Time to fix**: ~20 minutes (after understanding the issue)

## References

- [Kubernetes HPA Documentation](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/)
- [HPA Algorithm Details](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/#algorithm-details)
- [Resource Requests and Limits](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/)
- [HPA Walkthrough](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale-walkthrough/)
