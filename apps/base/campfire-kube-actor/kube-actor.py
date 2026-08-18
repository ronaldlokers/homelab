#!/usr/bin/env python3
"""Perform the two cluster actions the status bot is allowed to ask for.

This exists as its own process for one reason: **the bot that reads pod logs
must never hold a write credential**. `why` puts a model in front of
attacker-influenced text — a log line is written by whatever is running in the
pod — and a prompt injection plus a bug in the same process as write RBAC means
one bad log line and one mistake share a blast radius with restarting
workloads.

So the reading bot keeps its list/get ClusterRole and holds nothing writable.
This holds `patch` on exactly two kinds, reads no logs, calls no model, and
takes no free-form input: a verb from a fixed set and a target from a fixed
list. Everything it can possibly do is enumerated in the two tuples below.

The status bot forwards here after checking the asker is TRIAGE_USER_ID, and
this checks the allowlist again rather than trusting that. Two cheap checks in
different processes beat one careful one.

Both actions are annotation patches, which is why there is no kubectl and no
Flux CLI in the image:

    reconcile  reconcile.fluxcd.io/requestedAt on a Kustomization
    restart    kubectl.kubernetes.io/restartedAt on a Deployment's pod template

Both are idempotent and both are things routinely done by hand after reading a
status, which is the whole argument for automating them and none of the
argument for automating anything else.

Env:
    KUBE_API         API base URL, default https://kubernetes.default.svc
    KUBE_TOKEN_FILE  ServiceAccount token
    KUBE_CA_FILE     ServiceAccount CA
    LISTEN_PORT      default 8080
"""

import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KUBE_API = os.environ.get("KUBE_API", "https://kubernetes.default.svc")
TOKEN_FILE = os.environ.get(
    "KUBE_TOKEN_FILE", "/var/run/secrets/kubernetes.io/serviceaccount/token"
)
CA_FILE = os.environ.get(
    "KUBE_CA_FILE", "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
)
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "8080"))
TIMEOUT = 10

SSL_CONTEXT = ssl.create_default_context(cafile=CA_FILE) if os.path.exists(CA_FILE) else None

# Every Flux Kustomization, all of them in flux-system. Reconciling any of these
# is what you would do by hand after a merge, and the worst case is Flux
# applying what git already says.
RECONCILE_ALLOW = (
    "apps",
    "flux-system",
    "infrastructure-configs",
    "infrastructure-controllers",
    "monitoring-controllers",
    "monitoring-dashboards",
    "monitoring-servicemonitors",
)

# Deployments worth restarting from a phone, as namespace/name.
#
# campfire/campfire and campfire/campfire-status-bot are deliberately absent.
# Restarting Campfire kills the room the answer is posted to, and restarting the
# status bot kills the process waiting to report the result — both would leave
# an action that happened with nothing to show for it.
RESTART_ALLOW = (
    "campfire/campfire-alert-bridge",
    "cloudflared/cloudflared",
    "commafeed/commafeed",
    "fizzy/fizzy",
    "gatus/gatus",
    "homepage/homepage",
    "immich/immich-machine-learning",
    "immich/immich-server",
    "immich/immich-valkey",
    "linkding/linkding",
    "nightscout/ferretdb",
    "nightscout/nightscout",
    "ntfy/ntfy",
    "pgadmin/pgadmin4",
    "speedtest/speedtest",
    "tandoor/tandoor",
)


def log(message):
    print(message, flush=True)


def token():
    if not os.path.exists(TOKEN_FILE):
        return ""
    with open(TOKEN_FILE, encoding="utf-8") as handle:
        return handle.read().strip()


def patch(path, body):
    """One merge patch. The only kind of write this process can perform."""
    request = urllib.request.Request(
        KUBE_API + path,
        data=json.dumps(body).encode("utf-8"),
        method="PATCH",
        headers={
            "Content-Type": "application/merge-patch+json",
            "Authorization": f"Bearer {token()}",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT, context=SSL_CONTEXT) as response:
        return response.status


def now():
    # Flux compares this against the last value it saw, so it only has to
    # differ. RFC3339 because that is what `flux reconcile` writes.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def do_reconcile(target):
    if target not in RECONCILE_ALLOW:
        return False, f"{target} is not a Kustomization this may reconcile"
    status = patch(
        f"/apis/kustomize.toolkit.fluxcd.io/v1/namespaces/flux-system/kustomizations/{target}",
        {"metadata": {"annotations": {"reconcile.fluxcd.io/requestedAt": now()}}},
    )
    return True, f"asked Flux to reconcile {target} (HTTP {status})"


def do_restart(target):
    if target not in RESTART_ALLOW:
        return False, f"{target} is not a Deployment this may restart"
    namespace, name = target.split("/", 1)
    status = patch(
        f"/apis/apps/v1/namespaces/{namespace}/deployments/{name}",
        {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {"kubectl.kubernetes.io/restartedAt": now()}
                    }
                }
            }
        },
    )
    return True, f"rolled {target} (HTTP {status})"


VERBS = {"reconcile": do_reconcile, "restart": do_restart}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        ok = self.path == "/healthz"
        self.send_response(200 if ok else 404)
        self.end_headers()
        self.wfile.write(b"ok" if ok else b"")

    def do_POST(self):
        if self.path != "/act":
            self.respond(404, {"ok": False, "detail": "no such path"})
            return

        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as error:
            self.respond(400, {"ok": False, "detail": f"bad payload: {error}"})
            return

        verb = str(payload.get("verb", ""))
        target = str(payload.get("target", ""))
        # Who asked is logged, never trusted. The status bot has already
        # checked it against TRIAGE_USER_ID; this is for the audit trail.
        who = str(payload.get("who", "?"))

        action = VERBS.get(verb)
        if not action:
            log(f"refused: unknown verb {verb!r} from {who}")
            self.respond(400, {"ok": False, "detail": f"unknown verb {verb}"})
            return

        try:
            ok, detail = action(target)
        except urllib.error.HTTPError as error:
            ok, detail = False, f"kubernetes said {error.code}: {error.reason}"
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            ok, detail = False, f"could not reach the API: {error}"

        log(f"{'did' if ok else 'refused'}: {verb} {target} for {who} — {detail}")
        self.respond(200 if ok else 403, {"ok": ok, "detail": detail})

    def respond(self, code, body):
        raw = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass  # every decision is already logged above


def main():
    log(
        f"listening on :{LISTEN_PORT}; "
        f"{len(RECONCILE_ALLOW)} reconcilable, {len(RESTART_ALLOW)} restartable"
    )
    ThreadingHTTPServer(("", LISTEN_PORT), Handler).serve_forever()


if __name__ == "__main__":
    sys.exit(main())
