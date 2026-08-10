#!/usr/bin/env python3
"""Answer `@Claude status` in Campfire with what the cluster is actually doing.

A Campfire bot carries a callback URL. When someone mentions the bot, Campfire
POSTs the message there and — this is the part that shapes everything below —
treats a 200 response whose Content-Type is text/html or text/plain as the
bot's reply, posting it into the same room. The answer IS the response body, so
this needs no bot key and holds no credential of its own.

Two consequences of Webhook#deliver are load-bearing:

  * A response that is not a 200 text reply but still carries a Content-Type
    gets turned into an *attachment* via Mime::Type.lookup. A 500 with a
    Content-Type header would upload an error page into the room. So every
    answer here is 200 text/html, failures included, and the only silent path
    sends no Content-Type header at all.
  * ENDPOINT_TIMEOUT is 7 seconds, after which Campfire posts "Failed to
    respond within 7 seconds" itself. Gathering therefore runs against a hard
    budget and reports what it has rather than blocking for the full set.

There is deliberately no LLM here. This is the boring version: it proves the
webhook loop — Campfire reaching the bot, RBAC, the NetworkPolicy hops, the
reply landing in the room — with nothing to bill and nothing to prompt-inject.
Alert text is attacker-influenced; a model can go behind this same loop later,
once the loop itself is known to work.

Env:
    KUBE_API          API base URL, default https://kubernetes.default.svc
    KUBE_TOKEN_FILE   ServiceAccount token; ignored when absent
    KUBE_CA_FILE      ServiceAccount CA; ignored when absent
    LISTEN_PORT       default 8080

Leaving both file paths pointing at nothing turns off auth and TLS, which is
how this runs against `kubectl proxy` when testing off-cluster.
"""

import html
import json
import os
import ssl
import sys
import time
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

SSL_CONTEXT = ssl.create_default_context(cafile=CA_FILE) if os.path.exists(CA_FILE) else None

# Campfire gives up at 7s and posts its own failure notice. Finish well inside
# that: a partial answer beats a timeout message.
DEADLINE = 5.0
PER_REQUEST_TIMEOUT = 1.5

# A room message nobody scrolls is a room message nobody reads. Past this many
# bullets the list stops being a status and starts being a log.
MAX_ITEMS = 15

# 24h schedule plus room for a slow run. Hours rather than a clock time so the
# answer means the same thing whenever it is asked.
BACKUP_MAX_AGE_HOURS = 26


def heading(text):
    """A headline on its own line.

    <strong> is inline, so two in a row render as one unbroken run of bold
    text: "✅ all green" followed by "backups" arrived in the room as
    "all greenbackups". A block wrapper is the only thing that separates them,
    because Campfire ignores newline characters entirely. <div> is on
    ContentFilters::SanitizeTags' allow list; a bare newline is not a tag.
    """
    return f"<div><strong>{text}</strong></div>"


HELP = heading("commands") + (
    "<ul>"
    "<li><code>status</code> — Flux, pods and backup freshness</li>"
    "<li><code>help</code> — this</li>"
    "</ul>"
)

# Everything this reads is a list, read-only, and named in the ClusterRole next
# to this file. Adding a row here means adding a rule there.
FLUX_KINDS = (
    ("/apis/kustomize.toolkit.fluxcd.io/v1/kustomizations", "Kustomization"),
    ("/apis/helm.toolkit.fluxcd.io/v2/helmreleases", "HelmRelease"),
)

# Anything the API can raise that should degrade one section rather than kill
# the whole answer. KeyError and ValueError cover a response shaped other than
# expected, which is as much a failed check as a refused connection.
API_ERRORS = (urllib.error.URLError, TimeoutError, OSError, KeyError, ValueError)


def log(message):
    print(message, flush=True)


def kube_get(path, deadline):
    """One API read, bounded by whatever is left of the budget."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError(f"budget spent before {path}")

    headers = {"Accept": "application/json"}
    if os.path.exists(TOKEN_FILE):
        # Re-read per call. The projected token is short-lived and rotated in
        # place, so a value cached at startup stops working within the hour.
        with open(TOKEN_FILE) as handle:
            headers["Authorization"] = "Bearer " + handle.read().strip()

    request = urllib.request.Request(KUBE_API + path, headers=headers)
    timeout = min(PER_REQUEST_TIMEOUT, remaining)
    with urllib.request.urlopen(request, timeout=timeout, context=SSL_CONTEXT) as response:
        return json.load(response)


def ready_condition(obj):
    for condition in (obj.get("status") or {}).get("conditions") or []:
        if condition.get("type") == "Ready":
            return condition
    return None


def check_flux(deadline):
    """Kustomizations and HelmReleases whose Ready condition is not True."""
    problems = []
    for path, kind in FLUX_KINDS:
        for item in kube_get(path, deadline).get("items", []):
            condition = ready_condition(item)
            if condition and condition.get("status") == "True":
                continue
            meta = item["metadata"]
            # No Ready condition at all means it has never reconciled, which is
            # a problem in the same way a failing one is.
            reason = (
                (condition or {}).get("message")
                or (condition or {}).get("reason")
                or "not reconciled yet"
            )
            problems.append(f"{kind} {meta['namespace']}/{meta['name']}: {reason}")
    return problems


def check_pods(deadline):
    """Pods that are neither running-and-ready nor finished.

    Every pod in the cluster, unfiltered: a field selector on status.phase
    would shrink the response but would also hide CrashLoopBackOff, which sits
    in Running with a container flapping and is the state most worth seeing.
    """
    problems = []
    for pod in kube_get("/api/v1/pods", deadline).get("items", []):
        status = pod.get("status") or {}
        phase = status.get("phase")
        if phase == "Succeeded":
            continue  # a finished Job pod, not a fault
        name = f"{pod['metadata']['namespace']}/{pod['metadata']['name']}"
        if phase != "Running":
            problems.append(f"Pod {name}: {phase or 'unknown'}")
            continue
        for container in status.get("containerStatuses") or []:
            if container.get("ready"):
                continue
            waiting = (container.get("state") or {}).get("waiting") or {}
            detail = f" ({waiting['reason']})" if waiting.get("reason") else ""
            problems.append(f"Pod {name}: {container['name']} not ready{detail}")
    return problems


def check_backups(deadline):
    """Newest successful backup per PostgreSQL cluster.

    Reads Cluster.status.lastSuccessfulBackup rather than listing Backup
    objects: one small list instead of every backup ever taken, and the
    operator keeps the field current across the barman plugin migration (#377).
    """
    fresh, problems = [], []
    now = datetime.now(timezone.utc)
    for cluster in kube_get("/apis/postgresql.cnpg.io/v1/clusters", deadline).get("items", []):
        name = f"{cluster['metadata']['namespace']}/{cluster['metadata']['name']}"
        stamp = (cluster.get("status") or {}).get("lastSuccessfulBackup")
        if not stamp:
            problems.append(f"Backup {name}: none recorded")
            continue
        taken = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        age = (now - taken).total_seconds() / 3600
        if age > BACKUP_MAX_AGE_HOURS:
            problems.append(
                f"Backup {name}: {age:.1f}h old, past the {BACKUP_MAX_AGE_HOURS}h window"
            )
        else:
            fresh.append(f"{name}: {age:.1f}h ago")
    return fresh, problems


def bullets(items):
    shown = items[:MAX_ITEMS]
    body = "".join(f"<li>{html.escape(item)}</li>" for item in shown)
    if len(items) > len(shown):
        body += f"<li>… and {len(items) - len(shown)} more</li>"
    return "<ul>" + body + "</ul>"


def render_status():
    deadline = time.monotonic() + DEADLINE
    problems, fresh, skipped = [], [], []

    for label, check in (("flux", check_flux), ("pods", check_pods)):
        try:
            problems += check(deadline)
        except API_ERRORS as error:
            skipped.append(f"{label}: {error}")

    try:
        fresh, backup_problems = check_backups(deadline)
        problems += backup_problems
    except API_ERRORS as error:
        skipped.append(f"backups: {error}")

    if problems:
        plural = "" if len(problems) == 1 else "s"
        parts = [heading(f"⚠️ {len(problems)} problem{plural}"), bullets(problems)]
    elif skipped:
        # Never claim green over a section that was never fetched. A check that
        # could not run is an unknown, and an unknown reported as healthy is
        # the one failure mode that makes this worse than having no bot.
        parts = [heading("❓ nothing failing in what could be read")]
    else:
        parts = [heading("✅ all green")]

    if fresh:
        parts.append(heading("backups") + bullets(fresh))
    if skipped:
        parts.append(heading("not checked") + bullets(skipped))
    return "".join(parts)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Liveness only — deliberately not a cluster read. A readiness probe
        # that hit the API every 10s would be more API traffic than the bot
        # itself generates, and RBAC breakage shows up in the answer anyway,
        # under "not checked".
        ok = self.path == "/healthz"
        self.send_response(200 if ok else 404)
        self.end_headers()
        self.wfile.write(b"ok" if ok else b"")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as error:
            log(f"bad payload: {error}")
            self.silence()
            return

        message = (payload.get("message") or {}).get("body") or {}
        # `plain` arrives with the bot mention already stripped by
        # Webhook#without_recipient_mentions, so "@Claude status" is "status".
        words = (message.get("plain") or "").strip().lower().split()
        verb = words[0] if words else ""
        who = (payload.get("user") or {}).get("name", "?")
        room = (payload.get("room") or {}).get("name", "?")
        log(f"{room}: {who} said {verb!r}")

        if verb in ("", "status"):
            try:
                body = render_status()
            except Exception as error:  # noqa: BLE001
                # Nothing may escape: an unhandled exception here would send a
                # 500 that Campfire turns into an attachment.
                log(f"status failed: {error}")
                body = heading("⚠️ could not read the cluster") + (
                    f"<pre>{html.escape(str(error))}</pre>"
                )
        else:
            body = HELP
        self.reply(body)

    def reply(self, body):
        raw = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def silence(self):
        # 204 and, crucially, no Content-Type: extract_attachment_from only
        # runs when the response has one, so omitting it is the only way to
        # say nothing at all.
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass  # access logs are noise; every question is logged above


def main():
    log(f"listening on :{LISTEN_PORT}, reading {KUBE_API}")
    ThreadingHTTPServer(("", LISTEN_PORT), Handler).serve_forever()


if __name__ == "__main__":
    sys.exit(main())
