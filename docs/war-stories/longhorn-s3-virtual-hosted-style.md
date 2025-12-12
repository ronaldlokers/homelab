# Longhorn S3 Backups Failing with MinIO

**Commit**: `d4104aa` - fix: set VIRTUAL_HOSTED_STYLE to false

## The Problem

After configuring Longhorn to backup volumes to a self-hosted MinIO S3 instance, backups were failing. The Longhorn backup target showed connection errors when trying to reach the MinIO endpoint.

**Symptoms**:
- Longhorn backup jobs failing
- S3 connectivity errors in Longhorn manager logs
- MinIO was accessible and working for other applications

## The Investigation

Initial troubleshooting focused on:
1. Verifying MinIO was running and accessible
2. Checking S3 credentials (access key, secret key) were correct
3. Confirming bucket existed and permissions were set
4. Testing endpoint URL was reachable from the cluster

All of these checked out fine. The credentials were valid, MinIO was responding, and the bucket existed. Yet Longhorn still couldn't connect.

The key clue came from understanding how different S3 implementations handle URL formatting.

## The Root Cause

MinIO and AWS S3 support two different URL styles for accessing buckets:

1. **Virtual-hosted-style**: `https://bucket-name.s3.amazonaws.com/object-key`
   - Default for AWS S3
   - Bucket name is part of the hostname

2. **Path-style**: `https://s3.amazonaws.com/bucket-name/object-key`
   - Default for MinIO
   - Bucket name is part of the path

Longhorn's S3 client defaults to virtual-hosted-style URLs (the AWS standard). When connecting to MinIO without explicit configuration, Longhorn was trying to use virtual-hosted-style URLs, which MinIO wasn't configured to handle.

The missing configuration was the `VIRTUAL_HOSTED_STYLE` environment variable in the S3 secret.

## The Solution

Add the `VIRTUAL_HOSTED_STYLE: false` setting to the Longhorn S3 backup secret:

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
  VIRTUAL_HOSTED_STYLE: "false"  # This was missing!
```

After adding this setting and updating the secret, Longhorn successfully connected to MinIO and backups started working.

## Prevention

**When configuring Longhorn S3 backups with MinIO**:

1. Always set `VIRTUAL_HOSTED_STYLE: "false"` in the S3 secret
2. Use path-style URLs in your endpoint configuration
3. Test backup connectivity before relying on it for production

**Documentation to check**:
- Longhorn's S3 backup documentation mentions this setting but it's easy to miss
- MinIO documentation explains the difference between URL styles

**General lesson**: When integrating S3-compatible storage that isn't AWS, always check if URL style configuration is needed. Different S3-compatible implementations (MinIO, Ceph, Backblaze B2) may have different defaults.

## Related Resources

- Longhorn S3 backup configuration: `infrastructure/controllers/production/longhorn/s3-secret.yaml`
- MinIO deployment: Check infrastructure configuration
- AWS S3 URL style deprecation: AWS is deprecating path-style URLs, but MinIO still uses them as default
