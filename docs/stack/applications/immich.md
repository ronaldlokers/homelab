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
- Longhorn storage for ML cache (production only)

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
- **ML Cache**: Longhorn volume for machine learning model cache (production)

**Integration**:
- Homepage widget displays library statistics
- API key authentication for widget integration
