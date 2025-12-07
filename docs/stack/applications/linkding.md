# Linkding

[Linkding](https://github.com/sissbruecker/linkding) is a bookmark manager with tagging and search.

**Features**:
- Bookmark saving with title and description
- Tag-based organization
- Full-text search
- Browser extensions
- REST API
- Archive snapshots

**Deployment**:
- Single replica
- PostgreSQL database via CloudNative-PG cluster
- Connects to `postgres-cluster-rw.database.svc.cluster.local`
- No persistent volumes required (data stored in PostgreSQL cluster)

**Database**:
- Uses `linkding` database in the PostgreSQL cluster
- Database credentials stored in SOPS-encrypted secret
- High availability through PostgreSQL replication (3 instances)

**Access**:
- **Staging**: https://linkding.staging.ronaldlokers.nl
- **Production**: https://linkding.ronaldlokers.nl

**Authentication**:
- Superuser credentials stored in SOPS-encrypted secret
- Multi-user support

**Configuration**:
- PostgreSQL connection via environment variables
- Environment variables via Secret
- Ingress with TLS certificate from cert-manager
- HTTPS redirect middleware
