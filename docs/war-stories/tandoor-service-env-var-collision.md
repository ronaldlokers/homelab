# The App That Started Perfectly and Served Nothing

Deploying Tandoor Recipes took three failed attempts across five hours. Every
log line said the application had started successfully. Nothing was listening on
port 80.

The cause was a Kubernetes feature working exactly as documented, colliding with
an application's configuration convention. Neither side was wrong.

## The Problem

### Initial Symptoms

The pod never went `Ready`. The startup probe failed with connection refused, the
container was killed at the probe budget, and the whole sequence began again:

```
Warning  Unhealthy  Startup probe failed: Get "http://10.42.0.245:80/":
                    dial tcp 10.42.0.245:80: connect: connection refused
                    (x3006 over 5h18m)
```

Meanwhile the container log was entirely healthy. Migrations applied, static
files collected, workers booted:

```
Running migrations:
  Applying cookbook.0242_space_household_setup_completed... OK
814 static files copied to '/opt/recipes/staticfiles', 2232 post-processed.
Starting gunicorn
[INFO] Listening at: unix:/tmp/tandoor.sock (7)
[INFO] Booting worker with pid: 24
[INFO] Booting worker with pid: 25
[INFO] Booting worker with pid: 26
```

CPU dropped to 1m afterwards — fully started, and idle.

### The First Wrong Turn

`Listening at: unix:/tmp/tandoor.sock` looked like the answer. gunicorn was
bound to a Unix socket, not a TCP port, and `netstat` inside the container
confirmed nothing on port 80:

```
$ netstat -ltn
Active Internet connections (only servers)
Proto Recv-Q Send-Q Local Address    Foreign Address    State
                       (nothing)
```

Upstream's `docker-compose.yml` pairs the application with a **separate nginx
container**. The obvious conclusion: this image does not serve HTTP, and needs an
nginx sidecar.

That conclusion was wrong, and the sidecar made things worse:

```
nginx: [emerg] bind() to 0.0.0.0:80 failed (98: Address in use)
```

Containers in a pod share a network namespace. Something was already on port 80.

## The Investigation

### Comparing Two Environments Instead of Guessing

Five hours of iterating on a live deployment had produced three wrong fixes. The
change of approach that actually worked: run the image in a scratch namespace as
a bare pod — no Deployment, no probes, no Flux, nothing that could kill it or
restart it — and simply look at what it does.

The lab pod worked immediately:

```
$ ps aux
PID 1  /sbin/tini -- /opt/recipes/boot.sh
PID 7  gunicorn --bind unix:/tmp/tandoor.sock
PID 11 nginx: master process nginx -g pid /tmp/nginx.pid;

$ curl http://127.0.0.1/
HTTP 200  "Cookbook Setup"
```

The image bundles nginx. It had all along.

Which raised the real question: **why does the same image behave differently in
two namespaces?**

### Why `netstat` Lied

Reading the whole entrypoint rather than its tail explained the first wrong turn:

```sh
# start nginx early to display error pages with writable location as non-root
echo "Starting nginx"
nginx -g 'pid /tmp/nginx.pid;'

echo "Checking configuration..."
# ... then: wait for database, migrate, collectstatic, exec gunicorn
```

nginx starts *first*, but the log line scrolls past above 240 migrations. The
earlier `netstat` had been run mid-boot, when nginx had already died and gunicorn
had not yet bound its socket. A snapshot of a transient state, read as a
permanent property of the image.

### The Actual Difference

The first line of the container log — buried above the migrations, never scrolled
back to — was the whole answer:

```
nginx: [emerg] invalid host in "tcp://10.43.209.23:80" of the "listen"
directive in /etc/nginx/http.d/Recipes.conf:9
```

`10.43.209.23` is the ClusterIP of the app's own Service.

## The Root Cause

Kubernetes injects an environment variable for every Service in the pod's
namespace, named after the Service, following Docker's legacy link convention:

```
TANDOOR_PORT=tcp://10.43.209.23:80
TANDOOR_SERVICE_HOST=10.43.209.23
TANDOOR_PORT_80_TCP=tcp://10.43.209.23:80
```

Tandoor's entrypoint uses `TANDOOR_PORT` for something entirely different — the
port nginx should listen on:

```sh
export TANDOOR_PORT="${TANDOOR_PORT:-80}"
envsubst '$MEDIA_ROOT $STATIC_ROOT $TANDOOR_PORT' \
  < /opt/recipes/http.d/Recipes.conf.template > /opt/recipes/http.d/Recipes.conf
```

The `:-80` default never fires, because the variable **is** set — to a URL. nginx
receives `listen tcp://10.43.209.23:80;`, refuses to start, and gunicorn spends
the rest of its life serving a socket nobody reads.

The collision requires the Service name to match the variable the application
expects. Name the Service `tandoor`, get `TANDOOR_PORT`. Had it been called
`recipes`, this would never have happened.

**This is also why the scratch namespace worked.** The lab pod had no Service
named `tandoor`, so nothing was injected, so the default applied. The difference
between the two environments *was* the bug — which is exactly why comparing them
found in minutes what five hours of log-reading had not.

## The Solution

```yaml
spec:
  template:
    spec:
      enableServiceLinks: false
      containers:
        - name: tandoor
          env:
            - name: TANDOOR_PORT
              value: "80"
```

`enableServiceLinks: false` removes the injection at source. The explicit
`TANDOOR_PORT` keeps the listen directive correct even if that is ever
re-enabled. Nothing in this repo discovers services by environment variable;
everything uses DNS.

## Lessons

**Kubernetes injects a variable per Service, named after the Service.** Any
application reading a variable named `<SERVICENAME>_PORT`, `<SERVICENAME>_HOST`
or similar will silently receive a URL where it expected a value. Setting
`enableServiceLinks: false` is close to free and removes the entire class of
problem; there is little reason to leave it on in a cluster that resolves
services by DNS.

**"Started successfully" is not "serving".** Every log line was healthy. Both
processes existed. The only signal that mattered — a listening socket on the
expected port — was absent, and no application log would ever have said so.

**Read the first line of the log, not the last.** The error was line one. Above
it: nothing. Below it: 240 migrations, then a clean gunicorn startup that looked
like success. Container logs are read from the bottom by habit, and startup
errors live at the top.

**A snapshot of a booting process is not a property of the image.** `netstat`
showing no listeners was true for the moment it ran and false a minute later.
That single observation produced a sidecar, a regression, and a revert.

**When two environments differ, the difference is the bug.** After hours of
inspecting the failing deployment in isolation, the answer arrived in minutes by
running the same image somewhere else and asking what was different. The
scratch-namespace pod cost nothing to create and was not subject to probes,
restarts, or GitOps reconciliation fighting the investigation.

## Related

- [Switching to `Recreate` Strategy Couldn't Fix Itself Through Git Alone](deployment-recreate-strategy-ssa-conflict.md)
- [NetworkPolicy Connectivity Debugging](networkpolicy-connectivity-debugging.md)
