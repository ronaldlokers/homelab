# ntfy

[ntfy](https://ntfy.sh/) is a simple HTTP-based pub-sub notification service for sending notifications to phones and desktops via scripts.

**Features**:
- HTTP-based pub/sub for sending notifications
- Web app for receiving notifications
- Mobile apps (iOS and Android)
- Web push notifications
- User authentication
- Topic-based subscriptions
- Attachment support
- REST API

**Deployment**:
- Single replica
- Persistent storage for cache, attachments and web push database
- Version: v2.25
- Production only (not deployed in staging)

**Storage**:
- PVC `ntfy-cache`, 5Gi, mounted at `/var/cache/ntfy` (Longhorn, 3 replicas)
- Message cache database: `/var/cache/ntfy/cache.db`
- Web push database: `/var/cache/ntfy/webpush.db`
- Attachment cache directory: `/var/cache/ntfy/attachments`
- Cache duration: 12 hours

**Access**:
- **Production**: https://ntfy.ronaldlokers.nl

**Authentication**:
- Login enabled for authenticated topics
- User credentials managed via ntfy CLI
- Access control lives in `NTFY_AUTH_*` env vars, not in `server.yml` — an
  `auth-access` block in the config file is silently overridden by the
  `NTFY_AUTH_ACCESS` env var

**Configuration**:
- Base URL: https://ntfy.ronaldlokers.nl
- Upstream relay: https://ntfy.sh (for iOS notifications)
- Web push enabled with VAPID keys
- Web push email: ronaldlokers@me.com
- Web push expiry: 60 days (warning at 55 days)
- Behind proxy mode enabled (for Traefik integration)
- Prometheus metrics enabled at `/metrics` endpoint

**Attachments**:
- `attachment-cache-dir: /var/cache/ntfy/attachments` — ntfy takes full control
  of this directory, so it is a subdirectory of the volume rather than the
  volume root (which also holds `cache.db` and `webpush.db`)
- `attachment-total-size-limit: 3G` — sized against the 5Gi PVC, leaving
  headroom for the two databases
- `attachment-file-size-limit: 50M` — deliberately under the 100MB request body
  cap that Cloudflare's free plan enforces; ntfy is publicly exposed through the
  Cloudflare Tunnel, so a larger value would fail at the edge rather than in ntfy
- `attachment-expiry-duration: 72h`
- Per-visitor limits: 500M total, 1G daily bandwidth. These key off the client
  IP, which only works because `behind-proxy: true` makes ntfy trust
  `X-Forwarded-For` from Traefik — without it every request appears to come from
  the ingress pod and all visitors share a single bucket

**Config is generated, not patched**:
`apps/production/ntfy/kustomization.yaml` builds `ntfy-config` with a
`configMapGenerator` from `apps/production/ntfy/server.yml`. Kustomize appends a
content hash to the ConfigMap name and rewrites the Deployment's volume
reference, so editing `server.yml` produces a new ConfigMap name, which changes
the Deployment spec, which rolls the pod.

This replaced an in-place ConfigMap patch that silently did not work. `data` is a
`map[string]string`, so patching the `server.yml` key replaced the entire string
rather than deep-merging the YAML inside it — `cache-file`, `cache-duration`,
`behind-proxy` and `enable-web-app` from the base ConfigMap never reached
production. Worse, because ntfy reads `server.yml` only at startup and nothing
triggered a rollout, config changes applied cleanly and then sat unused until
some unrelated event restarted the pod.

There is no base ConfigMap any more — its content was always discarded by the
overlay. Each overlay owns its own `server.yml`.

Verify a render with:

```bash
kustomize build apps/production/ntfy | yq 'select(.kind=="ConfigMap")'
```

**Secrets**:
- Web push VAPID keys (public and private)
- Upstream access token for iOS relay
- Stored in SOPS-encrypted `ntfy-secret.yaml`

**Ingress**:
- TLS with wildcard certificate from cert-manager
- HTTPS redirect middleware
- WebSocket support for real-time notifications
- Also reachable publicly via the Cloudflare Tunnel (`apps/production/cloudflared/`)

**Monitoring**:
- Prometheus metrics exposed at `/metrics`
- Liveness probe: `GET /v1/health`
- Readiness probe: `GET /v1/health`

**Use Cases**:
- Server monitoring alerts
- Deployment notifications
- IoT device notifications
- Home automation alerts
- Personal reminders and notifications
