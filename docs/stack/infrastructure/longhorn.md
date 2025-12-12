# Longhorn (Production Only)

[Longhorn](https://longhorn.io/) provides distributed block storage with replication.

**Version**: 1.7.2

**Architecture**:
- **longhorn-manager**: Main management component on each node
- **longhorn-driver**: CSI driver for Kubernetes integration
- **longhorn-ui**: Web UI for management
- **instance-manager**: Manages volume replicas

**Storage**:
- **Hardware**: 512GB NVMe SSD on each Raspberry Pi CM5 node
- **Default replica count**: 3
- **Data path**: `/mnt/longhorn` on each node's NVMe SSD
- **Replication**: Synchronous replication across 3 nodes
- **Auto-balance**: least-effort strategy

**Features**:
- Dynamic volume provisioning
- Volume snapshots
- Volume backups (S3-compatible storage)
- Volume cloning
- Disaster recovery
- Replica rebuilding
- Storage over-provisioning

**Requirements**:
- `open-iscsi` package on all nodes
- `iscsid` service running
- Block storage (not NFS)

**High Availability**:
- Survives 2 node failures (with 3 replicas)
- Automatic replica rebuilding when node recovers
- Replicas spread across nodes for redundancy

**Web UI**: https://longhorn.ronaldlokers.nl

**Monitoring**:
- Prometheus ServiceMonitor enabled
- Metrics exposed for Grafana dashboards
- Custom Longhorn dashboard in Grafana

**Resource Limits** (Raspberry Pi CM5):
- **longhorn-manager**: 50m CPU / 128Mi memory (request), 500m CPU / 512Mi memory (limit)
- **longhorn-driver**: 50m CPU / 64Mi memory (request), 200m CPU / 256Mi memory (limit)
- **longhorn-ui**: 10m CPU / 64Mi memory (request), 100m CPU / 128Mi memory (limit)

## S3 Backup Configuration

**Backup Target**: MinIO on TrueNAS Scale
- **Endpoint**: `http://10.0.40.10:9000`
- **Bucket**: `longhorn-backups`
- **Region**: `us-east-1`
- **Credentials**: Stored in encrypted secret `longhorn-s3-secret` (SOPS)

**Configuration**:
```yaml
backupTarget: "s3://longhorn-backups@us-east-1/"
backupTargetCredentialSecret: "longhorn-s3-secret"
```

**Secret Fields**:
- `AWS_ACCESS_KEY_ID`: MinIO access key
- `AWS_SECRET_ACCESS_KEY`: MinIO secret key
- `AWS_ENDPOINTS`: `http://10.0.40.10:9000`
- `AWS_REGION`: `us-east-1`
- `VIRTUAL_HOSTED_STYLE`: `false` (required for MinIO)
- `AWS_CERT`: Empty (using HTTP)

## Recurring Backup Jobs

Automated backup schedules configured via RecurringJob CRDs:

**Daily Backups**:
- Schedule: 2 AM daily
- Retention: 7 days
- Concurrency: 2 volumes

**Weekly Backups**:
- Schedule: 3 AM on Sundays
- Retention: 4 weeks
- Concurrency: 1 volume

**Monthly Backups**:
- Schedule: 4 AM on 1st of month
- Retention: 12 months
- Concurrency: 1 volume

**Daily Snapshots** (local):
- Schedule: 1 AM daily
- Retention: 3 days
- Concurrency: 2 volumes

**Applying Jobs to Volumes**:

Via Longhorn UI:
1. Go to Volume → Select volume
2. Click Recurring Job tab
3. Select desired jobs
4. Save

Via Volume Labels:
```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  labels:
    recurring-job.longhorn.io/backup-daily: enabled
    recurring-job.longhorn.io/backup-weekly: enabled
spec:
  # ... rest of spec
```

## Accessing MinIO Backups

**From Arch Linux laptop**:

Install MinIO Client:
```bash
sudo pacman -S minio-client
```

Configure alias:
```bash
mc alias set truenas http://10.0.40.10:9000 <access-key> <secret-key>
```

List backups:
```bash
mc ls truenas/longhorn-backups/
```

Download backup:
```bash
mc cp truenas/longhorn-backups/backup-file.tar.gz ~/Downloads/
```

**Web Console**: http://10.0.40.10:9002

## Validation

Check recurring jobs:
```bash
kubectl get recurringjobs -n longhorn-system
```

Check backup target status:
```bash
kubectl get backuptargets -n longhorn-system default -o yaml
```

View backups in UI:
- Navigate to https://longhorn.ronaldlokers.nl
- Go to Backup section
- Verify connection to S3 bucket
