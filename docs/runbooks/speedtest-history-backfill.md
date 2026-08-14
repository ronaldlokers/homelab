# Backfilling speedtest history into its Prometheus

**Done once, on 2026-08-14: 4,594 results from 2025-12-06 onward are in.** This
is the record of how, for the next time a beat wants history that predates the
instance holding it.

Not a Flux resource on purpose: a Job that recreated itself on every cluster
rebuild would rewrite blocks that are already there.

The speedtest-only Prometheus
(`monitoring/controllers/production/speedtest-prometheus/`) keeps 400 days, but
only knows what it has scraped since it started. Every result lives in Postgres
— one an hour since the tracker was deployed — and this moves that history into
the TSDB so a query can reach back past the day the instance was created.

## Three things that decide the shape of this

**The volume is mounted with a subPath.** prometheus-operator mounts the PVC at
`/prometheus` with `subPath: prometheus-db`, so the TSDB is one level below the
volume root. Blocks placed at the root are never seen: the instance starts
clean, replays an empty WAL, and says nothing about it. That cost a full
round trip here — the symptom is a healthy Prometheus that answers "no data"
for every past date.

**Block length is fixed at 2h.** `promtool tsdb create-blocks-from openmetrics`
has no `--max-block-duration`, so eight months is ~2,300 windows however few
samples there are. Import *one merged file* containing all metric families
rather than one file per family, or you get that number five times over for the
same data. Prometheus compacts them into larger blocks over the following days.

**Nothing in `monitoring` may reach Postgres.** The first attempt ran the export
as a Job there with two temporary NetworkPolicies; the connection was refused
anyway, and the diagnosis was not finished. The route below sidesteps the
question: the export runs from a workstation that already reaches Postgres
through `kubectl exec`, and the pod that writes blocks touches no network at
all.

## 1. Export, from a workstation

Confirm the schema first — this was written against speedtest-tracker 1.14.7,
where `download` and `upload` are bytes per second and `ping` is milliseconds:

```bash
kubectl --context=production exec -n database postgres-cluster-4 -c postgres -- \
  psql -U postgres -d speedtest -c "\d results"
```

Then write one OpenMetrics file per family. `psql` formats the sample lines
itself; the labels are a deliberate subset (see *What is lost*, below):

```bash
OUT=$(mktemp -d)
for spec in \
  "speedtest_tracker_download_bits:download:8" \
  "speedtest_tracker_upload_bits:upload:8" \
  "speedtest_tracker_download_bytes:download:1" \
  "speedtest_tracker_upload_bytes:upload:1" \
  "speedtest_tracker_ping_ms:ping:1"
do
  metric=${spec%%:*}; rest=${spec#*:}; column=${rest%%:*}; scale=${rest##*:}
  echo "# TYPE ${metric} gauge" > "$OUT/${metric}.om"
  kubectl --context=production exec -n database postgres-cluster-4 -c postgres -- \
    psql -U postgres -d speedtest -tAq -c "
      SELECT format('${metric}{job=\"speedtest\",namespace=\"speedtest\",service=\"speedtest\",status=\"%s\",scheduled=\"%s\",healthy=\"%s\"} %s %s',
                    status,
                    CASE WHEN scheduled THEN 'true' ELSE 'false' END,
                    CASE WHEN coalesce(healthy,false) THEN 'true' ELSE 'false' END,
                    (${column} * ${scale})::numeric,
                    extract(epoch FROM created_at)::bigint)
      FROM results
      WHERE ${column} IS NOT NULL AND created_at IS NOT NULL
      ORDER BY created_at, id;" >> "$OUT/${metric}.om"
  echo "# EOF" >> "$OUT/${metric}.om"
done
wc -l "$OUT"/*.om
```

`created_at` is UTC in this schema; `DISPLAY_TIMEZONE` only affects the app's
own rendering.

## 2. Stop the instance

Flux would put the replica back within the minute, so suspend it first:

```bash
flux suspend kustomization monitoring-controllers --context=production
kubectl --context=production patch prometheus speedtest -n monitoring \
  --type merge -p '{"spec":{"replicas":0}}'
kubectl --context=production wait --for=delete pod/prometheus-speedtest-0 -n monitoring --timeout=3m
```

## 3. Build the blocks in a pod that mounts the volume root

Note `mountPath: /mnt` and no subPath: this pod wants to see both the volume
root and `prometheus-db` inside it.

```bash
kubectl --context=production apply -f - <<'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: speedtest-backfill-loader
  namespace: monitoring
spec:
  restartPolicy: Never
  securityContext:
    runAsNonRoot: true
    runAsUser: 1000
    runAsGroup: 2000
    fsGroup: 2000
  containers:
    - name: promtool
      # The plain image, not -distroless: this needs a shell.
      image: quay.io/prometheus/prometheus:v3.13.2
      command: ["/bin/sh", "-c", "sleep 3600"]
      volumeMounts:
        - name: tsdb
          mountPath: /mnt
  volumes:
    - name: tsdb
      persistentVolumeClaim:
        claimName: prometheus-speedtest-db-prometheus-speedtest-0
EOF
kubectl --context=production wait --for=condition=Ready pod/speedtest-backfill-loader -n monitoring --timeout=3m
```

The container's root filesystem is read-only for the non-root user, so staging
files go on the volume:

```bash
kubectl --context=production exec -n monitoring speedtest-backfill-loader -- mkdir -p /mnt/backfill-in
for f in "$OUT"/*.om; do
  kubectl --context=production cp "$f" monitoring/speedtest-backfill-loader:/mnt/backfill-in/$(basename "$f")
done

kubectl --context=production exec -n monitoring speedtest-backfill-loader -- sh -c '
  set -e
  for f in /mnt/backfill-in/*.om; do grep -v "^# EOF" "$f"; done > /mnt/backfill-in/merged.txt
  echo "# EOF" >> /mnt/backfill-in/merged.txt
  mkdir -p /mnt/backfill-out
  promtool tsdb create-blocks-from openmetrics -q /mnt/backfill-in/merged.txt /mnt/backfill-out
  echo "blocks: $(ls -1 /mnt/backfill-out | wc -l)"'
```

Then into the TSDB — `prometheus-db`, not the root:

```bash
kubectl --context=production exec -n monitoring speedtest-backfill-loader -- sh -c '
  set -e
  n=0
  for d in /mnt/backfill-out/*/; do mv "$d" /mnt/prometheus-db/ && n=$((n+1)); done
  echo "moved $n blocks"
  rm -rf /mnt/backfill-in /mnt/backfill-out'
kubectl --context=production delete pod speedtest-backfill-loader -n monitoring
```

## 4. Start it again

```bash
kubectl --context=production patch prometheus speedtest -n monitoring \
  --type merge -p '{"spec":{"replicas":1}}'
kubectl --context=production wait --for=condition=Ready pod/prometheus-speedtest-0 -n monitoring --timeout=6m
flux resume kustomization monitoring-controllers --context=production
```

It logs one `Found healthy block` per block at startup, which is how you know
they were seen:

```bash
kubectl --context=production logs -n monitoring prometheus-speedtest-0 -c prometheus \
  | grep -c "Found healthy block"
```

## 5. Prove it

**Use a window, not an instant.** Samples are hourly and an instant query only
looks back five minutes, so `max(metric)` at a past date returns nothing even
when the data is there. That looks exactly like a failed backfill:

```bash
kubectl --context=production port-forward -n monitoring svc/prometheus-speedtest 9091:9090 &
curl -sG http://127.0.0.1:9091/api/v1/query \
  --data-urlencode 'query=max(max_over_time(speedtest_tracker_download_bits[24h]))/1e6' \
  --data-urlencode 'time=2026-03-01T12:00:00Z'
```

On 2026-08-14 that returned 940 Mbps for December, February and April, 931 for
June — eight months of history, in 63 MB across 2,306 blocks.

## What is lost

Backfilled samples carry fewer labels than scraped ones: a row from February
knows its status and whether it was scheduled, but not which pod scraped it or
which server answered. **So queries must aggregate** — `max(...)`, which the
Grafana dashboard and the speedtest beat both do. Query the bare metric name and
the history and the live series appear as two lines.

Jitter, per-test latency percentiles and elapsed times are not reconstructed.
They exist per row in the `data` JSON column; nothing asks for them.
