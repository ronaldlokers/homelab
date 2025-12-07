# CloudNative-PG

[CloudNative-PG](https://cloudnative-pg.io/) is a Kubernetes operator for PostgreSQL databases.

**Version**: 0.23.0

**Components**:
- **cnpg-controller-manager**: Main operator managing PostgreSQL clusters
- **Cluster CRD**: Custom resource defining PostgreSQL cluster configuration

**PostgreSQL Cluster Configuration**:
- **Name**: postgres-cluster
- **Namespace**: database
- **Instances**: 3 (high availability)
- **Image**: ghcr.io/ferretdb/postgres-documentdb:17-0.102.0-ferretdb-2.1.0
- **Storage Size**: 10Gi per instance
- **Storage Class**:
  - Staging: local-path
  - Production: longhorn (replicated)

**DocumentDB Extension**:
The cluster uses PostgreSQL 17 with the DocumentDB extension installed, which provides:
- MongoDB API compatibility for FerretDB v2.x
- Native BSON data type support
- Optimized MongoDB-like operations at the database level
- Required for Nightscout (via FerretDB) to function properly

**Automatic Credential and Permission Management**:

A PostSync job (`grant-documentdb-permissions`) runs automatically after the cluster is created or updated to ensure proper configuration:

1. **DocumentDB Extension Setup**: Creates the DocumentDB extension and all required schemas
2. **Role Assignment**: Grants the `documentdb_admin_role` to the `app` user (required for FerretDB operations like SET ROLE)
3. **Schema Permissions**: Grants necessary privileges on all DocumentDB schemas (documentdb_api, documentdb_core, documentdb_data, etc.)
4. **Password Synchronization**: Resets the `app` user password to match the `postgres-cluster-app` secret

**Why Password Synchronization is Important**:
- When restoring from backup, user passwords come from the backup data
- Application secrets may have different passwords than the restored backup
- The job ensures passwords are synchronized after every cluster deployment or restore
- This prevents authentication failures in applications (linkding, FerretDB) after disaster recovery

**Manual Password Reset** (if needed):
```bash
# Get the app password from the secret
APP_PASSWORD=$(kubectl get secret -n database postgres-cluster-app -o jsonpath='{.data.password}' | base64 -d)

# Reset the password in PostgreSQL
kubectl exec -n database postgres-cluster-1 -- psql -U postgres -c "ALTER USER app WITH PASSWORD '${APP_PASSWORD}';"
```

**Features**:
- Automated PostgreSQL cluster provisioning
- Built-in high availability with automatic failover
- Continuous backup and point-in-time recovery
- Rolling updates with zero downtime
- Connection pooling with PgBouncer
- Monitoring integration with Prometheus
- DocumentDB extension for MongoDB compatibility layer

**High Availability**:
- Primary-replica architecture
- Automatic failover on primary failure
- Read replicas for scaling reads
- Synchronous or asynchronous replication

**How it works**:
1. Cluster resource created with desired configuration
2. Operator provisions PostgreSQL pods (1 primary + 2 replicas)
3. Primary handles writes, replicas handle reads
4. Automatic health monitoring and failover
5. Continuous backup to object storage (Backblaze B2)

**Access**:
- **Service**: postgres-cluster-rw (read-write, points to primary)
- **Service**: postgres-cluster-ro (read-only, load-balanced across replicas)
- **Service**: postgres-cluster-r (read-only, includes primary)

**Connection**:
```bash
# Connect to primary (read-write)
kubectl exec -it -n database postgres-cluster-1 -- psql -U postgres

# Get credentials
kubectl get secret -n database postgres-cluster-app -o jsonpath='{.data.password}' | base64 -d
```

**Backup and Recovery**:

CloudNative-PG uses Barman for automated backups to Backblaze B2 object storage.

**Backup Configuration**:
- **Storage**: Backblaze B2 (`s3://homelab-postgres-backups/`)
- **Schedule**:
  - Staging: Daily at 2 AM
  - Production: Daily at 3 AM
- **Retention**:
  - Staging: 14 days
  - Production: 30 days
- **WAL Archiving**: Continuous (enables point-in-time recovery)
- **Compression**: gzip (both WAL and data)

**How Backups Work**:
1. Scheduled backup runs daily via CronJob
2. Full backup of database cluster uploaded to B2
3. WAL (Write-Ahead Log) files continuously archived
4. Old backups automatically deleted per retention policy
5. Backups stored in separate staging/production paths

**Checking Backup Status**:
```bash
# List all backups
kubectl get backup -n database

# Check scheduled backup status
kubectl get scheduledbackup -n database

# View backup details
kubectl describe backup <backup-name> -n database
```

**Manually Triggering Backup**:
```bash
# Create on-demand backup
kubectl cnpg backup postgres-cluster -n database
```

**Restoring from Backup**:

There are two recovery scenarios:

**1. Restore to New Cluster (Safe Method)**:
```bash
# List available backups
kubectl get backup -n database

# Create new cluster from specific backup
cat <<EOF | kubectl apply -f -
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: postgres-cluster-restore
  namespace: database
spec:
  instances: 3
  storage:
    size: 10Gi
    storageClass: longhorn
  bootstrap:
    recovery:
      source: postgres-cluster
      recoveryTarget:
        targetTime: "2025-12-03 10:00:00+00:00"  # Optional: point-in-time
  externalClusters:
    - name: postgres-cluster
      barmanObjectStore:
        destinationPath: "s3://homelab-postgres-backups/production/"
        endpointURL: "https://s3.eu-central-003.backblazeb2.com"
        s3Credentials:
          accessKeyId:
            name: b2-credentials
            key: ACCESS_KEY_ID
          secretAccessKey:
            name: b2-credentials
            key: ACCESS_SECRET_KEY
EOF

# Verify recovery
kubectl get cluster -n database
kubectl logs -n database postgres-cluster-restore-1 -f

# Once verified, update applications to use new cluster or migrate data back
```

**2. In-Place Recovery (Restore Existing Cluster)**:
```bash
# WARNING: This will overwrite existing data
# Annotate cluster for recovery
kubectl annotate cluster postgres-cluster \
  cnpg.io/reconciliationLoop=disabled \
  -n database

# Delete existing cluster pods
kubectl delete pod -n database -l cnpg.io/cluster=postgres-cluster

# Update cluster spec to bootstrap from recovery
kubectl edit cluster postgres-cluster -n database
# Add bootstrap.recovery section (see example above)

# Re-enable reconciliation
kubectl annotate cluster postgres-cluster \
  cnpg.io/reconciliationLoop- \
  -n database
```

**Point-in-Time Recovery (PITR)**:

Restore to any moment in time (within retention period):
```bash
# Restore to specific timestamp
recoveryTarget:
  targetTime: "2025-12-03 14:30:00+00:00"

# Or restore to specific transaction ID
recoveryTarget:
  targetXID: "12345"

# Or restore to named restore point
recoveryTarget:
  targetName: "before-migration"
```

**Recovery Best Practices**:
1. Always restore to a new cluster first to verify data
2. Test backups regularly in staging environment
3. Document recovery procedures for your team
4. Keep B2 credentials secure and backed up separately
5. Monitor backup jobs for failures

**Backup Costs** (Backblaze B2):
- Storage: $0.006/GB/month (~$0.06/month for 10GB)
- Downloads: $0.01/GB (only when restoring)
- Very affordable for homelab use

**Monitoring**:
- Metrics exposed for Prometheus
- PodMonitor for automatic scraping
- Integration with Grafana dashboards
- Backup job status visible in kubectl
