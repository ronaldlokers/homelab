# Commafeed

[Commafeed](https://www.commafeed.com/) is a self-hosted RSS reader.

**Features**:
- RSS/Atom feed aggregation
- Tagging and organization
- Full-text search
- Sharing and favoriting

**Deployment**:
- Single replica
- PostgreSQL database via CloudNative-PG cluster
- Connects to `postgres-cluster-rw.database.svc.cluster.local`
- No persistent volumes required (data stored in PostgreSQL cluster)

**Database**:
- Uses `commafeed` database in the PostgreSQL cluster
- Database credentials stored in SOPS-encrypted secret
- High availability through PostgreSQL replication (3 instances)

**Access**:
- **Staging**: https://commafeed.staging.ronaldlokers.nl
- **Production**: https://commafeed.ronaldlokers.nl
