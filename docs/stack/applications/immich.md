# Immich

[Immich](https://immich.app/) is a high-performance, self-hosted photo and video management solution.

**Features**:
- Mobile app for automatic photo backup (iOS and Android)
- Fast photo browsing with timeline view
- AI-powered face detection and recognition
- Object detection and smart search
- Photo albums and sharing
- Live photos support
- RAW image support
- Geolocation and map view
- Multi-user support with granular permissions

**Deployment**:
- Distributed architecture with multiple components:
  - **Server**: Main API and web interface
  - **Machine Learning**: AI features (face/object detection)
  - **Valkey**: Redis-compatible in-memory cache
- PostgreSQL database with pgvector/vchord extension for vector search
- NFS storage for photo library (ReadWriteMany)
- Longhorn storage for ML cache (production: ReadWriteMany for horizontal scaling)
- Helm chart deployment using Flux GitOps

**Version Management**:
- Chart and image versions managed per cluster
- Staging can test newer versions before production
- Base configuration shared, versions in overlay patches

**Access**:
- **Staging**: https://immich.staging.ronaldlokers.nl
- **Production**: https://immich.ronaldlokers.nl

**Database**:
- Dedicated PostgreSQL cluster (`immich-cluster`) with 3 instances for HA
- Uses TensorChord's CloudNativePG image with VectorChord extension
- Required extensions: vchord, vector, cube, earthdistance
- Automated backups to Backblaze B2

**Storage**:
- **Library Storage**: NFS persistent volume (500Gi) on TrueNAS
  - Configured with `maproot=root` to prevent permission issues
  - ReadWriteMany access mode for multi-pod access
- **ML Cache**: Longhorn volume for machine learning model cache
  - Production: ReadWriteMany (10Gi) for horizontal scaling
  - Staging: ReadWriteOnce (local-path)

**Scaling (Production)**:
- **HorizontalPodAutoscaler** configured for server and machine-learning components
- Min replicas: 1, Max replicas: 3
- CPU target: 50% average utilization
- Stabilization window: 300 seconds (5 minutes) for scale up/down
- Resource requests required for HPA metrics:
  - Server: 50m CPU, 4Gi memory
  - Machine Learning: 50m CPU, 4Gi memory

**Ingress**:
- Managed via Helm chart values (not separate Ingress resource)
- Traefik ingress class
- Automatic TLS certificates via cert-manager
- HTTPS redirect middleware
- Unlimited request body size for photo uploads

**Integration**:
- Homepage widget displays library statistics
- API key authentication for widget integration
