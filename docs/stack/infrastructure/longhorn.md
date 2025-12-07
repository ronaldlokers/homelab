# Longhorn (Production Only)

[Longhorn](https://longhorn.io/) provides distributed block storage with replication.

**Version**: 1.7.2

**Architecture**:
- **longhorn-manager**: Main management component on each node
- **longhorn-driver**: CSI driver for Kubernetes integration
- **longhorn-ui**: Web UI for management
- **instance-manager**: Manages volume replicas

**Storage**:
- **Default replica count**: 3
- **Data path**: `/var/lib/longhorn` on each node
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
