# [Concise Issue Title]

<!--
Example: "Pod Evicted Due to Disk Pressure"
Keep it short and searchable
-->

## Quick Reference

- **Severity**: [Critical/High/Medium/Low]
- **Estimated Time to Resolve**: [X minutes/hours]
- **Symptoms**: [One-line description of what you observe]
- **Affected Components**: [List of services/apps affected]
- **Environment**: [Production/Staging/Both]
- **Prerequisites**: [What you need to troubleshoot - SSH access, kubectl, etc.]

## Symptoms & Detection

<!-- How do you know this is happening? -->

### Error Messages

```
[Paste exact error messages here]
```

### Observable Behavior

- Bullet point of symptoms
- What user experiences
- What logs/metrics show

### Monitoring Indicators

- Alert names that would fire
- Grafana dashboard anomalies
- Prometheus metrics that spike

## Immediate Actions

<!-- Stop the bleeding FIRST, diagnose later -->

**If you need [service/app] running RIGHT NOW:**

1. Quick mitigation step 1
   ```bash
   command to run
   ```

2. Quick mitigation step 2
   ```bash
   another command
   ```

**Note**: These are temporary fixes. Continue to Resolution Steps for permanent fix.

## Diagnosis Steps

<!-- Confirm you have the RIGHT issue before applying fixes -->

### 1. Check [first thing to verify]

```bash
# Command to check
kubectl get something

# Expected output if this is the issue:
[paste what you should see]
```

### 2. Verify [second diagnostic]

```bash
# Another command
command here

# What it means:
[interpretation]
```

### 3. Confirm diagnosis

**This is the right runbook if:**
- ✅ Condition 1 is true
- ✅ Condition 2 matches
- ✅ Condition 3 observed

**This is NOT the right runbook if:**
- ❌ Different symptom X occurs
- ❌ Only affects specific component Y
- ❌ Error mentions Z instead

**Related runbooks to check**:
- [Link to similar runbook 1]
- [Link to similar runbook 2]

## Resolution Steps

<!-- Step-by-step fix -->

### Step 1: [First action]

**Why**: [Brief explanation of what this step does]

```bash
# Command to run
your-command --with-flags

# Expected output:
[what success looks like]
```

**If this fails**: [troubleshooting tip]

### Step 2: [Second action]

```bash
# Next command
another-command
```

**Verify this step**:
```bash
# How to confirm it worked
verification-command
```

### Step 3: [Continue pattern]

[More steps as needed]

## Verification

<!-- Prove it's actually fixed -->

### Confirm resolution:

- [ ] Check 1 passes
      ```bash
      command-to-verify
      # Expected: [result]
      ```

- [ ] Check 2 passes
      ```bash
      another-verification
      # Expected: [result]
      ```

- [ ] Service is accessible
      ```bash
      curl -I https://service.domain.com
      # Expected: HTTP 200
      ```

- [ ] No errors in logs for 5 minutes
      ```bash
      kubectl logs -n namespace deployment/app --since=5m | grep -i error
      # Expected: no output
      ```

- [ ] Fix persists after [reboot/redeploy/restart]
      ```bash
      # How to test persistence
      ```

## Root Cause

<!-- Technical deep-dive: WHY did this happen? -->

### What Caused This

[Explain the underlying technical reason]

### Why It Manifested Now

[Why did this issue appear at this time? What changed?]

### Component Interaction

```
[Optional ASCII diagram or explanation of how components interact]
Component A → Component B → Failure Point
```

### Technical Details

[Any additional context that helps understand the issue]

## Prevention

<!-- How to avoid this in the future -->

### Immediate Prevention

**These steps prevent recurrence:**

- [ ] Action 1
      ```bash
      command-to-prevent
      ```
      **Impact**: [what this changes]

- [ ] Action 2
      ```bash
      another-prevention-step
      ```

### Long-term Prevention

**Consider these improvements:**

- [ ] Add monitoring for [specific metric]
      - **Why**: Catch this before it causes an outage
      - **How**: [brief implementation guidance]

- [ ] Implement [architectural change]
      - **Why**: [rationale]
      - **Effort**: [time estimate]

### Monitoring & Alerting

**Add these alerts to catch early:**

```yaml
# Example Prometheus alert
- alert: [AlertName]
  expr: [metric condition]
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "[Description]"
```

### Documentation Updates

- [ ] Update architecture docs with new understanding
- [ ] Add to setup guide if this affects new deployments
- [ ] Document in change management procedures

## Related Issues

<!-- Links to similar problems -->

- **[Related issue 1]**: [Link] - Similar symptoms but different cause
- **[Related issue 2]**: [Link] - Same component, different failure mode
- **[Prerequisite issue]**: [Link] - Can cause this as a downstream effect

## Original War Story

<!-- Link to the narrative version -->

For the complete investigation narrative and technical deep-dive, see: [`docs/war-stories/[filename].md`](../war-stories/[filename].md)

## References

<!-- External documentation -->

- [Tool/component official docs](https://link)
- [Relevant Kubernetes documentation](https://link)
- [Blog post or article](https://link)
- [GitHub issue](https://link)

---

**Last Updated**: YYYY-MM-DD
**Tested On**: [Environment description]
**Success Rate**: X/Y incidents resolved (XX%)
**Original Incident**: [Date or ticket number if applicable]
