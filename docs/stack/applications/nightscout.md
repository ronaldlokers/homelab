# Nightscout

[Nightscout](https://nightscout.github.io/) (CGM in the Cloud) is an open-source remote monitoring system for continuous glucose monitoring (CGM) data.

**Features**:
- Real-time CGM data display
- Blood glucose trend visualization
- Alerts and notifications for high/low glucose
- Treatment logging (insulin, carbs, exercise)
- Reports and analytics
- Mobile app integration
- Caregiver remote monitoring

**Deployment**:
- Single replica
- PostgreSQL database via CloudNative-PG cluster (through FerretDB)
- Uses FerretDB to provide MongoDB API compatibility
- Display units configured as mmol/L

**Database Architecture**:
- **FerretDB**: Provides MongoDB API compatibility layer (v2.1.0)
- **Backend**: PostgreSQL 17 with DocumentDB extension
- **Database**: `nightscout` database in the PostgreSQL cluster
- **High Availability**: Through PostgreSQL replication (3 instances)

**How FerretDB Works**:
1. Nightscout connects to FerretDB using MongoDB protocol
2. FerretDB uses the DocumentDB PostgreSQL extension for native BSON support
3. MongoDB operations are executed using the extension's functions
4. Data stored in PostgreSQL with same reliability and backup strategy
5. No separate MongoDB cluster needed

**Requirements**:
- PostgreSQL with DocumentDB extension installed
- FerretDB v2.x requires this extension (v1.x worked with plain PostgreSQL)
- The extension provides native MongoDB compatibility at the database level

**Access**:
- **Staging**: https://nightscout.staging.ronaldlokers.nl
- **Production**: https://nightscout.ronaldlokers.nl

**Authentication**:
- API_SECRET stored in SOPS-encrypted secret
- Required for accessing and modifying data
- Environment-specific secrets for staging and production

**Configuration**:
- Display units: mmol/L (configurable)
- MongoDB connection via FerretDB service
- PostgreSQL credentials stored in encrypted secrets
- Ingress with TLS certificate from cert-manager
- HTTPS redirect middleware

**Benefits**:
- Unified database platform (PostgreSQL for all applications)
- Automated backups and point-in-time recovery
- High availability through PostgreSQL replication
- No need to maintain separate MongoDB cluster
