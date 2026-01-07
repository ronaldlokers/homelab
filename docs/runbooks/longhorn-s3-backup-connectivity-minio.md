# Longhorn S3 Backups Failing with MinIO

## Quick Reference

- **Severity**: High (backup system non-functional)
- **Estimated Time to Resolve**: 5 minutes
- **Symptoms**: Longhorn backups fail with S3 connectivity errors
- **Affected Components**: Longhorn backup target, volume backups
- **Environment**: Using MinIO or other non-AWS S3-compatible storage
- **Prerequisites**: Access to Longhorn S3 secret configuration

## Symptoms & Detection

### Error Messages

S3 connectivity failures in Longhorn manager logs or backup job failures.

### Observable Behavior

- Longhorn UI shows backup target as disconnected or error state
- Backup jobs fail immediately
- MinIO is accessible and working for other applications
- S3 credentials are correct
- Bucket exists with proper permissions
- Endpoint URL is reachable from cluster

### Monitoring Indicators

- Backup jobs stuck or failing
- Longhorn backup target shows "Error" status
- No recent successful backups

## Immediate Actions

**If you need backups RIGHT NOW:**

No workaround - you must fix the S3 configuration. This is a one-line change.

**Quick check**:

```bash
# Check if VIRTUAL_HOSTED_STYLE is set
kubectl get secret longhorn-s3-secret -n longhorn-system -o yaml | grep VIRTUAL_HOSTED_STYLE
# If missing, that's likely your problem
```

## Diagnosis Steps

### 1. Verify MinIO is accessible

```bash
# From within cluster (or your workstation if accessible)
curl -I https://minio.example.com

# Should return HTTP 200 or similar
# If unreachable, this is a different issue (network/DNS)
```

### 2. Verify S3 credentials are correct

```bash
# Check secret exists
kubectl get secret longhorn-s3-secret -n longhorn-system

# Verify keys present
kubectl get secret longhorn-s3-secret -n longhorn-system -o jsonpath='{.data}' | jq 'keys'
# Should show: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_ENDPOINTS
```

### 3. Test S3 connectivity with proper credentials

```bash
# Using AWS CLI with MinIO endpoint
AWS_ACCESS_KEY_ID=<key> \
AWS_SECRET_ACCESS_KEY=<secret> \
aws s3 ls --endpoint-url=https://minio.example.com

# Should list buckets if credentials work
```

**If credentials don't work**, fix credentials first (different issue).

### 4. Check for VIRTUAL_HOSTED_STYLE setting

```bash
# View secret contents
kubectl get secret longhorn-s3-secret -n longhorn-system -o yaml

# Look for VIRTUAL_HOSTED_STYLE
# If missing, that's the problem!
```

### 5. Confirm diagnosis

**This is the right runbook if:**
- ✅ Using MinIO (or other S3-compatible, non-AWS storage)
- ✅ MinIO is accessible
- ✅ Credentials are correct
- ✅ Bucket exists
- ✅ `VIRTUAL_HOSTED_STYLE` not set in secret OR set to `"true"`
- ✅ Longhorn can't connect to S3 despite above

**This is NOT the right runbook if:**
- ❌ Using AWS S3 (this setting not needed)
- ❌ MinIO is unreachable (network issue)
- ❌ Credentials are wrong (auth issue)
- ❌ Bucket doesn't exist (configuration issue)

## Resolution Steps

### Step 1: Add VIRTUAL_HOSTED_STYLE to S3 secret

**If using SOPS-encrypted secret**:

```bash
# Export SOPS key
export SOPS_AGE_KEY_FILE=/path/to/production-age.key

# Edit encrypted secret
sops infrastructure/controllers/production/longhorn/s3-secret.yaml
```

**Add the setting**:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: longhorn-s3-secret
  namespace: longhorn-system
type: Opaque
stringData:
  AWS_ACCESS_KEY_ID: <access-key>
  AWS_SECRET_ACCESS_KEY: <secret-key>
  AWS_ENDPOINTS: https://minio.example.com
  AWS_CERT: ""
  VIRTUAL_HOSTED_STYLE: "false"  # ← Add this line
```

**Save and encrypt** (SOPS does this automatically).

### Step 2: Commit and apply

```bash
# Commit changes
git add infrastructure/controllers/production/longhorn/s3-secret.yaml
git commit -m "fix: set VIRTUAL_HOSTED_STYLE to false for MinIO compatibility"
git push

# Reconcile Flux
flux reconcile kustomization infrastructure-controllers --context=production
```

**Or apply directly** (if not using GitOps):

```bash
kubectl apply -f infrastructure/controllers/production/longhorn/s3-secret.yaml
```

### Step 3: Restart Longhorn manager pods

The secret change may not be picked up immediately:

```bash
# Restart Longhorn manager
kubectl rollout restart deployment/longhorn-manager -n longhorn-system

# Or delete pods
kubectl delete pods -n longhorn-system -l app=longhorn-manager

# Wait for pods to restart
kubectl get pods -n longhorn-system -l app=longhorn-manager --watch
```

### Step 4: Test backup target connectivity

**Via Longhorn UI**:
1. Open Longhorn UI
2. Navigate to Settings → Backup Target
3. Click "Test Connection"
4. Should show "Success"

**Via CLI** (trigger manual backup):

```bash
# Create a test backup
kubectl create -f - <<EOF
apiVersion: longhorn.io/v1beta2
kind: Backup
metadata:
  name: test-backup-$(date +%s)
  namespace: longhorn-system
spec:
  snapshotName: <snapshot-name>
  volumeName: <volume-name>
EOF

# Watch backup progress
kubectl get backup -n longhorn-system --watch

# Should reach "Completed" state
```

## Verification

### Confirm resolution:

- [ ] Secret contains VIRTUAL_HOSTED_STYLE
      ```bash
      kubectl get secret longhorn-s3-secret -n longhorn-system -o yaml | grep VIRTUAL_HOSTED_STYLE
      # Should show: VIRTUAL_HOSTED_STYLE: ZmFsc2U= (base64 for "false")
      ```

- [ ] Longhorn backup target shows connected
      ```bash
      # Via Longhorn UI: Settings → Backup Target
      # Status should be "Available"
      ```

- [ ] Test backup succeeds
      ```bash
      # Create test backup via UI or CLI
      kubectl get backup -n longhorn-system
      # Should show recent backup with "Completed" state
      ```

- [ ] Scheduled backups working
      ```bash
      # Wait for next scheduled backup
      # Check Longhorn UI for successful backups
      ```

- [ ] Can restore from backup
      ```bash
      # Test restore in non-production environment
      # Verify data integrity
      ```

## Root Cause

### S3 URL Styles

**Two S3 URL formats exist**:

1. **Virtual-hosted-style** (AWS default):
   ```
   https://bucket-name.s3.amazonaws.com/object-key
   ```
   - Bucket name in hostname
   - AWS S3 standard

2. **Path-style** (MinIO default):
   ```
   https://s3.amazonaws.com/bucket-name/object-key
   ```
   - Bucket name in path
   - MinIO, Ceph, older S3 implementations

### Why Longhorn Defaults to Virtual-Hosted

**Longhorn assumes AWS S3 by default**:
- S3 client defaults to virtual-hosted-style
- Works with AWS without configuration
- Breaks with MinIO/Ceph/etc.

### The Fix

**Setting `VIRTUAL_HOSTED_STYLE: "false"`**:
- Tells Longhorn's S3 client to use path-style URLs
- Compatible with MinIO and other S3-compatible storage
- No other changes needed

### AWS S3 Path-Style Deprecation

**Important note**: AWS is deprecating path-style URLs for S3.

- **For AWS S3**: Keep `VIRTUAL_HOSTED_STYLE` unset or `"true"`
- **For MinIO/Ceph/etc.**: Set to `"false"`

**Don't mix**: If migrating from MinIO to AWS, remember to change this setting!

## Prevention

### Document S3 Implementation

- [ ] Note which S3 implementation is used
      ```yaml
      # In secret or docs
      # S3 Implementation: MinIO
      # Requires: VIRTUAL_HOSTED_STYLE=false
      ```

### Template for Non-AWS S3

Create template secret for MinIO deployments:

```yaml
# longhorn-s3-secret-template.yaml
apiVersion: v1
kind: Secret
metadata:
  name: longhorn-s3-secret
  namespace: longhorn-system
type: Opaque
stringData:
  AWS_ACCESS_KEY_ID: <access-key>
  AWS_SECRET_ACCESS_KEY: <secret-key>
  AWS_ENDPOINTS: <minio-endpoint>
  AWS_CERT: ""
  VIRTUAL_HOSTED_STYLE: "false"  # Required for MinIO!
```

### Test Backups After Setup

Don't wait for disaster to discover backups don't work:

```bash
# After configuring S3 backup target
# 1. Test connection in Longhorn UI
# 2. Create manual backup
# 3. Verify backup appears in MinIO
# 4. Test restore in staging
```

### Monitoring

Create alerts for backup failures:

```yaml
# Prometheus alert example
- alert: LonghornBackupFailing
  expr: |
    longhorn_backup_state{state="error"} > 0
  for: 15m
  annotations:
    summary: "Longhorn backups failing - check S3 connectivity"
```

## Related Issues

- **Certificate issues**: If MinIO uses self-signed cert
      ```yaml
      AWS_CERT: |
        -----BEGIN CERTIFICATE-----
        <cert-content>
        -----END CERTIFICATE-----
      ```

- **Network policies**: Ensure Longhorn pods can reach MinIO
- **Bucket permissions**: Verify bucket policy allows Longhorn access
- **Endpoint URL**: Must be full URL with protocol (`https://...`)

## Migration Notes

### From MinIO to AWS S3

If migrating backup storage:

1. **Change S3 secret**:
   ```yaml
   AWS_ENDPOINTS: https://s3.<region>.amazonaws.com
   VIRTUAL_HOSTED_STYLE: "true"  # Or remove this line
   ```

2. **Update backup target** in Longhorn settings

3. **Migrate existing backups** (optional):
   ```bash
   # Use rclone or AWS CLI to copy
   aws s3 sync s3://old-minio-bucket s3://new-aws-bucket
   ```

### From AWS S3 to MinIO

Reverse process:

1. **Update secret**: Add `VIRTUAL_HOSTED_STYLE: "false"`
2. **Change endpoint**: Point to MinIO
3. **Migrate backups** if preserving history

## Original War Story

For the investigation process and understanding of the URL style difference, see: [`docs/war-stories/longhorn-s3-virtual-hosted-style.md`](../war-stories/longhorn-s3-virtual-hosted-style.md)

## References

- [Longhorn S3 Backup Documentation](https://longhorn.io/docs/latest/snapshots-and-backups/backup-and-restore/set-backup-target/)
- [AWS S3 Path-Style Deprecation](https://aws.amazon.com/blogs/aws/amazon-s3-path-deprecation-plan-the-rest-of-the-story/)
- [MinIO Client Configuration](https://min.io/docs/minio/linux/reference/minio-mc.html)

---

**Last Updated**: 2026-01-07
**Tested On**: Longhorn with MinIO backup target
**Success Rate**: 100%
**Commit**: `d4104aa` - Initial fix
