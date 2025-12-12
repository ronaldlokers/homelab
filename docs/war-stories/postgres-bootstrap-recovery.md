# PostgreSQL Cluster Disaster Recovery Bootstrap Mode

**Commit**: `c762b4b` - fix: ensure PostgreSQL cluster always restores from backup

## The Problem

The PostgreSQL cluster was configured with automated backups to Backblaze B2, but there was a critical gap in the disaster recovery plan: if the cluster was accidentally deleted and Flux recreated it, all data would be lost because the cluster would bootstrap as a fresh installation instead of restoring from backup.

**Risk**:
- Accidental `kubectl delete` of the cluster
- Namespace deletion during troubleshooting
- Cluster corruption requiring recreation
- Any scenario where the cluster needs to be rebuilt

In all these cases, the cluster would come back empty, even though recent backups existed in B2.

## The Investigation

CloudNativePG (CNPG) supports two primary bootstrap modes:

1. **initdb**: Creates a fresh PostgreSQL cluster from scratch
   - Used for initial deployment
   - Runs initialization SQL scripts via `postInitSQL`
   - No data recovery attempted

2. **recovery**: Restores cluster from an existing backup
   - Connects to external backup storage (S3, B2, etc.)
   - Finds the most recent backup
   - Restores data and replays WAL logs
   - Creates a fully recovered cluster

The original configuration used `initdb` because that's what you need for the *first* deployment. However, after the first successful backup, the bootstrap mode should have been changed to `recovery` to protect against accidental deletion.

## The Root Cause

Using `initdb` as the bootstrap mode is correct for initial setup but dangerous for production once backups exist:

```yaml
# DANGEROUS in production after backups exist
bootstrap:
  initdb:
    postInitSQL:
      - 'CREATE EXTENSION IF NOT EXISTS documentdb CASCADE;'
```

This configuration meant:
- First deployment: Works perfectly, creates fresh cluster
- Subsequent deployments after deletion: Creates fresh cluster, **ignoring all backups**

The cluster had automated backups running successfully to B2, but the disaster recovery process wasn't automated - it would require manual intervention to restore from backup.

## The Solution

Change the bootstrap mode to `recovery` and reference the backup location:

```yaml
# Safe for production - always restores from backup
bootstrap:
  recovery:
    source: clusterBackup

# Reference to backup location in B2
externalClusters:
  - name: clusterBackup
    barmanObjectStore:
      destinationPath: "s3://homelab-postgres-backups/production/"
      serverName: postgres-cluster
      endpointURL: "https://s3.eu-central-003.backblazeb2.com"
      s3Credentials:
        accessKeyId:
          name: b2-credentials
          key: ACCESS_KEY_ID
        secretAccessKey:
          name: b2-credentials
          key: ACCESS_SECRET_KEY
      wal:
        maxParallel: 8
```

**Side effect**: The `postInitSQL` hook no longer runs because we're not using `initdb`. The DocumentDB extension creation had to be moved to the PostSync job that manages permissions:

```bash
# Now in grant-documentdb-permissions-job.yaml
psql <<'EOF'
  CREATE EXTENSION IF NOT EXISTS documentdb CASCADE;
  GRANT documentdb_admin_role TO app;
  # ... rest of permissions
EOF
```

## The Behavior Change

**Before (initdb mode)**:
1. Cluster deleted accidentally
2. Flux recreates cluster resources
3. CNPG bootstraps fresh cluster
4. All data lost ❌

**After (recovery mode)**:
1. Cluster deleted accidentally
2. Flux recreates cluster resources
3. CNPG connects to B2 backup storage
4. Finds most recent backup
5. Restores all data and WAL logs
6. Cluster comes back with all data intact ✅

**On first deployment** (when no backup exists):
- Recovery mode gracefully falls back to creating a new cluster
- Once first backup completes, future recreations will restore from it

## Prevention

**For production PostgreSQL clusters**:

1. **Start with initdb for initial deployment**:
   ```yaml
   bootstrap:
     initdb:
       postInitSQL:
         - 'CREATE EXTENSION IF NOT EXISTS documentdb CASCADE;'
   ```

2. **After first successful backup, switch to recovery mode**:
   ```yaml
   bootstrap:
     recovery:
       source: clusterBackup
   externalClusters:
     - name: clusterBackup
       barmanObjectStore:
         destinationPath: "s3://your-backup-bucket/cluster/"
         # ... credentials and config
   ```

3. **Move initialization SQL to PostSync jobs**:
   - Extensions, roles, and permissions should be idempotent
   - Use PostSync hooks or jobs that run after cluster is ready
   - This ensures they run both on initdb AND recovery

4. **Test the disaster recovery process**:
   ```bash
   # Delete the cluster (scary but important to test!)
   kubectl delete cluster -n database postgres-cluster

   # Wait for Flux to recreate it
   # Verify data is restored from backup
   ```

**Warning**: You cannot easily switch between bootstrap modes on an existing cluster. The bootstrap configuration is only used during cluster creation. To change modes:
1. Ensure backups are current
2. Delete the cluster
3. Update the manifest with new bootstrap mode
4. Let Flux recreate the cluster (it will use the new mode)

## Lessons Learned

1. **Bootstrap mode has disaster recovery implications** - It's not just about initial setup
2. **Production clusters should use recovery mode** - After initial deployment and first backup
3. **Test your disaster recovery** - Don't wait for an accident to find out if restore works
4. **Initialization logic needs to be idempotent** - It may run on both fresh installs and restores
5. **Move away from postInitSQL in production** - Use PostSync jobs for better control and idempotency

## Related Resources

- CloudNativePG bootstrap documentation: https://cloudnative-pg.io/documentation/current/bootstrap/
- Cluster configuration: `infrastructure/configs/production/cloudnative-pg/postgres-cluster.yaml`
- Permission job: `infrastructure/configs/production/cloudnative-pg/grant-documentdb-permissions-job.yaml`
- Backup configuration: Check CNPG scheduledBackup resources
