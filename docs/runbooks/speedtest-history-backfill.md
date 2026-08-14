# Backfilling speedtest history into its Prometheus

Run once, by hand. This is not a Flux resource on purpose: a Job that recreates
itself on every cluster rebuild would rewrite blocks that are already there.

The speedtest-only Prometheus
(`monitoring/controllers/production/speedtest-prometheus/`) keeps 400 days, but
it only knows what it has scraped since it started. Every result since
2025-12-06 is in Postgres — 4,603 of them at the time of writing, one an hour —
and this moves that history into the TSDB so a query can reach back past the
day the instance was created.

## What it does

`psql` writes OpenMetrics text straight out of a `SELECT`, `promtool` turns each
metric family into blocks, and the blocks are moved into the PVC while the
instance is scaled to zero.

Five families are backfilled — `download_bits`, `upload_bits`, `download_bytes`,
`upload_bytes`, `ping_ms` — because those are what a sheet reads. The rest of
what the exporter publishes (jitter, per-test latency percentiles, elapsed
times) is not reconstructed: it lives in the `data` JSON per row and nothing
asks for it.

**Backfilled samples carry fewer labels than scraped ones.** A scraped sample
carries the pod name, the pod IP, and the server and ISP of that particular
test; a row from 2026-02 knows the server but not which pod scraped it. So
queries must aggregate — `max(speedtest_tracker_download_bits)`, which is what
the existing Grafana dashboard already does, and what the beat does. Query the
bare metric name and you will see the history and the live series as two lines.

## Before

Confirm the schema still matches. This was written against speedtest-tracker
1.14.7, where `download` and `upload` are bytes per second and `ping` is
milliseconds:

```bash
kubectl --context=production exec -n database postgres-cluster-3 -c postgres -- \
  psql -U postgres -d speedtest -c "\d results"
```

Copy the database credential into `monitoring`, where the Job runs. It is
deleted again at the end:

```bash
kubectl --context=production get secret pg-role-speedtest -n database -o json \
  | jq 'del(.metadata.namespace, .metadata.resourceVersion, .metadata.uid, .metadata.creationTimestamp, .metadata.ownerReferences)' \
  | kubectl --context=production apply -n monitoring -f -
```

Stop the instance, so nothing is writing to the TSDB directory while blocks are
moved into it. Flux would put the replica back within the minute, so suspend it
first:

```bash
flux suspend kustomization monitoring-controllers --context=production
kubectl --context=production patch prometheus speedtest -n monitoring \
  --type merge -p '{"spec":{"replicas":0}}'
kubectl --context=production wait --for=delete pod/prometheus-speedtest-0 -n monitoring --timeout=2m
```

## The Job

`kubectl --context=production apply -f -` the following.

```yaml
---
# Temporary, both of them: the Job needs to reach Postgres from monitoring,
# which nothing else there does. Deleted with the Job.
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-speedtest-backfill-to-database
  namespace: monitoring
spec:
  podSelector:
    matchLabels:
      job-name: speedtest-backfill
  policyTypes:
    - Egress
  egress:
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: database
      ports:
        - protocol: TCP
          port: 5432
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-speedtest-backfill-ingress
  namespace: database
spec:
  podSelector: {}
  policyTypes:
    - Ingress
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: monitoring
          podSelector:
            matchLabels:
              job-name: speedtest-backfill
      ports:
        - protocol: TCP
          port: 5432
---
apiVersion: batch/v1
kind: Job
metadata:
  name: speedtest-backfill
  namespace: monitoring
spec:
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      # Matches the Prometheus pod, so the blocks are owned by the process that
      # has to read them.
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        runAsGroup: 2000
        fsGroup: 2000
      volumes:
        - name: tsdb
          persistentVolumeClaim:
            claimName: prometheus-speedtest-db-prometheus-speedtest-0
        - name: work
          emptyDir: {}
      initContainers:
        # One file per metric family: OpenMetrics wants a family's samples
        # contiguous, and promtool wants them in time order. Both fall out of
        # a per-family query with ORDER BY created_at.
        - name: export
          image: postgres:17-alpine
          env:
            - name: PGHOST
              value: postgres-cluster-rw.database.svc.cluster.local
            - name: PGDATABASE
              value: speedtest
            - name: PGUSER
              valueFrom:
                secretKeyRef:
                  name: pg-role-speedtest
                  key: username
            - name: PGPASSWORD
              valueFrom:
                secretKeyRef:
                  name: pg-role-speedtest
                  key: password
          command: ["/bin/sh", "-c"]
          args:
            - |
              set -eu
              # name:column:scale — the exporter publishes bits as bytes * 8,
              # and download/upload are bytes per second in this schema.
              for spec in \
                "speedtest_tracker_download_bits:download:8" \
                "speedtest_tracker_upload_bits:upload:8" \
                "speedtest_tracker_download_bytes:download:1" \
                "speedtest_tracker_upload_bytes:upload:1" \
                "speedtest_tracker_ping_ms:ping:1"
              do
                metric=${spec%%:*}; rest=${spec#*:}; column=${rest%%:*}; scale=${rest##*:}
                {
                  echo "# TYPE ${metric} gauge"
                  psql -tAq -v ON_ERROR_STOP=1 -c "
                    SELECT format('${metric}{job=\"speedtest\",namespace=\"speedtest\",service=\"speedtest\",status=\"%s\",scheduled=\"%s\",healthy=\"%s\"} %s %s',
                                  status,
                                  scheduled,
                                  coalesce(healthy, false),
                                  (${column} * ${scale})::numeric,
                                  extract(epoch FROM created_at)::bigint)
                    FROM results
                    WHERE ${column} IS NOT NULL AND created_at IS NOT NULL
                    ORDER BY created_at, id;"
                  echo "# EOF"
                } > /work/${metric}.om
                echo "${metric}: $(($(wc -l < /work/${metric}.om) - 2)) samples"
              done
          volumeMounts:
            - name: work
              mountPath: /work
      containers:
        - name: blocks
          image: quay.io/prometheus/prometheus:v3.13.2
          command: ["/bin/sh", "-c"]
          args:
            - |
              set -eu
              mkdir -p /work/blocks
              for file in /work/*.om; do
                echo "== ${file}"
                promtool tsdb create-blocks-from openmetrics "${file}" /work/blocks
              done
              # Only now, into the live directory. Prometheus is scaled to zero,
              # so nothing is compacting underneath this.
              mv /work/blocks/*/ /prometheus/
              ls -1 /prometheus | tail -20
          volumeMounts:
            - name: work
              mountPath: /work
            - name: tsdb
              mountPath: /prometheus
```

The `blocks` container needs a shell, so it is the plain image rather than the
`-distroless` variant the operator runs.

## After

```bash
kubectl --context=production logs -n monitoring job/speedtest-backfill --all-containers
```

Expect roughly 4,600 samples per family, then a list of new block directories.

Start it again and let Flux own it once more:

```bash
kubectl --context=production patch prometheus speedtest -n monitoring \
  --type merge -p '{"spec":{"replicas":1}}'
flux resume kustomization monitoring-controllers --context=production
```

Clean up what was temporary:

```bash
kubectl --context=production delete job speedtest-backfill -n monitoring
kubectl --context=production delete networkpolicy allow-speedtest-backfill-to-database -n monitoring
kubectl --context=production delete networkpolicy allow-speedtest-backfill-ingress -n database
kubectl --context=production delete secret pg-role-speedtest -n monitoring
```

Then prove it, against the instance rather than the default datasource:

```bash
kubectl --context=production port-forward -n monitoring svc/prometheus-speedtest 9091:9090 &
curl -sG http://127.0.0.1:9091/api/v1/query \
  --data-urlencode 'query=max(speedtest_tracker_download_bits)' \
  --data-urlencode 'time=2026-03-01T12:00:00Z'
```

A value from a date before the instance existed means the history is in.
