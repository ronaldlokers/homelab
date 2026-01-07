# Incident Runbooks

Quick-reference guides for diagnosing and resolving production issues in the homelab Kubernetes cluster.

## What is a Runbook?

A **runbook** is an action-oriented guide for handling specific incidents. Unlike war stories (which tell a narrative), runbooks are structured for **someone actively handling an incident** who needs to:

1. Quickly confirm they have the right issue
2. Take immediate action to mitigate impact
3. Diagnose the root cause
4. Apply the fix
5. Verify the resolution
6. Prevent recurrence

## When to Use Runbooks vs War Stories

**Use Runbooks when:**
- 🚨 An incident is happening NOW
- ⏱️ You need fast, step-by-step resolution
- ✅ You want a checklist to follow
- 🔍 You need to confirm diagnosis quickly

**Use War Stories when:**
- 📚 Learning about past incidents
- 🧠 Understanding the investigation process
- 💡 Researching similar issues
- 📖 Deep-diving into technical details

**Both link to each other** - runbooks reference war stories for context, war stories link to runbooks for action.

## Runbook Index

### Infrastructure Issues

- [**inotify Limits Exhausted**](inotify-limits-exhausted.md)
  - **Symptoms**: Pods crash with "too many open files"
  - **Severity**: High
  - **Time to Fix**: 10 minutes

- [**NFS Mounts Failing on Debian Trixie**](nfs-mounts-failing-debian-trixie.md)
  - **Symptoms**: Pods stuck in ContainerCreating with NFS mount errors
  - **Severity**: High
  - **Time to Fix**: 5 minutes

- [**PostgreSQL Cluster Disaster Recovery**](postgresql-cluster-disaster-recovery.md)
  - **Symptoms**: Database cluster deleted or corrupted
  - **Severity**: Critical
  - **Time to Fix**: 15-30 minutes

- [**Longhorn ReadWriteMany PVC Access Mode Change**](longhorn-rwx-pvc-access-mode-change.md)
  - **Symptoms**: Multi-Attach error, pods can't scale horizontally
  - **Severity**: Medium
  - **Time to Fix**: 10 minutes
  - **Warning**: PVC deletion required (data loss risk)

- [**Longhorn S3 Backups Failing with MinIO**](longhorn-s3-backup-connectivity-minio.md)
  - **Symptoms**: Backup jobs fail with S3 connectivity errors
  - **Severity**: High
  - **Time to Fix**: 5 minutes

### Monitoring & Logging Issues

- [**Loki Ring Errors: Too Many Unhealthy Instances**](loki-ring-unhealthy-instances.md)
  - **Symptoms**: Loki refusing to ingest logs, ring errors
  - **Severity**: High
  - **Time to Fix**: 15 minutes

### Application Scaling Issues

- [**HPA Shows Unknown CPU Metrics**](hpa-resource-requests-unknown.md)
  - **Symptoms**: HorizontalPodAutoscaler shows `<unknown>/X%`
  - **Severity**: Medium
  - **Time to Fix**: 10 minutes

### Configuration Issues

- [**Kustomize ConfigMap Hash Suffix Breaking Helm**](kustomize-helm-configmap-name-mismatch.md)
  - **Symptoms**: Pods crash with "configuration file not found"
  - **Severity**: High
  - **Time to Fix**: 10 minutes

- [**Helm Version Management with Flux Best Practices**](helm-version-management-flux-best-practices.md)
  - **Symptoms**: Can't test versions independently per environment
  - **Severity**: Low (maintainability)
  - **Time to Fix**: 30-60 minutes

## Runbook Structure

Each runbook follows this format:

```markdown
# [Issue Title]

## Quick Reference
- Severity, estimated time, symptoms, prerequisites

## Symptoms & Detection
How do you know this is happening?

## Immediate Actions
Stop the bleeding first

## Diagnosis Steps
Confirm you have the right issue

## Resolution Steps
Step-by-step fix with commands

## Verification
Checklist to confirm it's fixed

## Root Cause
Technical deep-dive

## Prevention
How to avoid this in the future

## Related Issues
Links to similar problems

## Original War Story
Link to full narrative
```

## Using Runbooks During Incidents

### Step 1: Identify the Issue

**Start here** when something goes wrong:

1. Observe symptoms (error messages, failing pods, etc.)
2. Scan the runbook index for matching symptoms
3. Open the most likely runbook

### Step 2: Confirm Diagnosis

**Use the "Diagnosis Steps" section** to verify you have the right runbook:

- Each runbook has a "This is the right runbook if..." checklist
- If diagnosis doesn't match, check "Related Issues" for alternatives
- If no runbook matches, document the new issue for future runbook creation

### Step 3: Follow Resolution Steps

**Execute the fix**:

- Copy/paste commands (they're tested)
- Check command output matches expected results
- If a step fails, note where and check troubleshooting

### Step 4: Verify Resolution

**Use the verification checklist** to ensure:

- The immediate issue is resolved
- No side effects were introduced
- The fix is persistent (survives reboots, deployments, etc.)

### Step 5: Follow Up

**After the incident**:

- Document any deviations from the runbook
- Update the runbook if steps were unclear
- Implement prevention measures
- Schedule a review if this is a recurring issue

## Creating New Runbooks

### From a New Incident

When you encounter a new issue:

1. **During the incident**: Take notes
   - Error messages (exact text)
   - Commands you ran
   - What worked, what didn't

2. **After resolution**: Create the runbook
   - Use the template below
   - Focus on actionable steps
   - Include actual commands with output

3. **Test the runbook**: Verify it in staging
   - Can someone else follow it?
   - Are steps clear and complete?

4. **Link to war story**: Create detailed investigation narrative

### Converting War Stories to Runbooks

We have 9 detailed war stories that can be converted. Use this process:

1. Read the war story completely
2. Copy the runbook template (see `TEMPLATE.md`)
3. Extract key information:
   - Symptoms → "Symptoms & Detection"
   - First actions taken → "Immediate Actions"
   - Investigation steps → "Diagnosis Steps"
   - The solution → "Resolution Steps"
   - Verification → "Verification"
   - Root cause explanation → "Root Cause"
   - Prevention → "Prevention"
4. Make commands copy-pasteable
5. Add checklists for verification
6. Link back to original war story

## Runbook Quality Guidelines

**Good runbooks have:**

- ✅ **Clear symptoms**: Anyone can recognize the issue
- ✅ **Copy-pasteable commands**: No need to edit
- ✅ **Expected outputs**: Show what success looks like
- ✅ **Time estimates**: Set expectations
- ✅ **Verification steps**: Prove it's fixed
- ✅ **Prevention guidance**: Don't let it happen again

**Avoid in runbooks:**

- ❌ Long explanations (save for war stories)
- ❌ Historical context (link to war story instead)
- ❌ Multiple possible solutions without guidance
- ❌ Commands that need customization without examples

## Quick Reference: Severity Levels

Use these consistently across runbooks:

| Severity | Description | Example | Response Time |
|----------|-------------|---------|---------------|
| **Critical** | Data loss risk, complete service down | Database cluster deleted | Immediate |
| **High** | Service degraded, pods failing | inotify limits, OOM kills | < 30 min |
| **Medium** | Feature broken, workaround available | Ingress misconfiguration | < 2 hours |
| **Low** | Cosmetic issue, no user impact | Dashboard missing data | Next maintenance window |

## Maintenance

### Keeping Runbooks Current

Runbooks become stale. Update them when:

- Commands change (new Kubernetes version, different tools)
- Infrastructure changes (different cluster, new components)
- A real incident reveals missing steps
- Prevention measures are implemented

### Runbook Review Schedule

- **After each use**: Note any issues
- **Quarterly**: Test critical runbooks in staging
- **After major changes**: Review affected runbooks

### Retiring Runbooks

Archive a runbook when:

- The component is no longer used
- Prevention makes the issue impossible
- The issue hasn't occurred in 2+ years

Move to `docs/runbooks/archive/` with retirement note.

## Contributing

### Adding a New Runbook

1. Copy `TEMPLATE.md` to new file: `docs/runbooks/your-issue-name.md`
2. Fill in all sections
3. Test in staging if possible
4. Add to index in this README
5. Create PR with:
   - The runbook
   - Updated README index
   - Link to related war story (if exists)

### Improving Existing Runbooks

Found a problem with a runbook? Please:

1. Note what was unclear or wrong
2. Submit a PR with improvements
3. Add "Last Updated" timestamp
4. Increment success rate if you used it successfully

## War Stories Reference

For detailed investigations and learning, see: [`docs/war-stories/`](../war-stories/)

War stories complement runbooks by providing:
- Full investigation narratives
- Wrong turns and dead ends
- Technical deep dives
- Context for design decisions

## Useful External Runbooks

Other teams' runbooks for inspiration:

- [Kubernetes Failure Stories](https://k8s.af/)
- [Google SRE Book - Example Runbook](https://sre.google/sre-book/service-level-objectives/)
- [PagerDuty Incident Response](https://response.pagerduty.com/)

---

**Runbook Count**: 9 complete
**Last Updated**: 2026-01-07
**Maintained By**: homelab operations (that's you!)
