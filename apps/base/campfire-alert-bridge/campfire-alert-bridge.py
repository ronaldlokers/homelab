#!/usr/bin/env python3
"""Turn Alertmanager webhook payloads into Campfire messages.

Alertmanager can only POST its own JSON to a static URL. Campfire's bot
endpoint treats the entire request body as the message, so pointing one at the
other directly puts raw JSON in the room. This sits between them.

It also has to emit HTML rather than text. Campfire stores messages as rich
text and ignores newline characters, so a plain-text alert arrives as one
run-on line. Tags are filtered server-side by ContentFilters::SanitizeTags;
everything used here (strong, em, code, pre, ul, li, a) is on its allow list.

What this deliberately does NOT do: replace a firing message when the alert
resolves. Campfire exposes exactly one bot route — POST create — so a resolved
alert can only be a new message. See docs/stack/... and #377-era discussion.

Env:
    CAMPFIRE_URL  full bot endpoint including the room and bot key
    LISTEN_PORT   default 8080
"""

import html
import json
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# One destination per source, each its own room. Gatus is absent on purpose:
# its `custom` alerting provider templates its own body, so it posts to Campfire
# directly and needs nothing here.
DESTINATIONS = {
    "/alerts": os.environ.get("CAMPFIRE_URL", ""),
    "/flux": os.environ.get("CAMPFIRE_FLUX_URL", ""),
}
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "8080"))
TIMEOUT = 10

# Labels worth showing, in the order they read best. Everything else is noise
# in a chat message — alertname and severity are already in the heading, and
# the rest (job, endpoint, prometheus, ...) are for querying, not reading.
LABELS = ("namespace", "cluster", "pod", "instance", "node", "volume", "persistentvolumeclaim")


def log(msg):
    print(msg, flush=True)


def emoji(status, severity):
    if status == "resolved":
        return "✅"
    return "🚨" if severity == "critical" else "⚠️"


def render_alert(alert):
    """One alert as an HTML fragment."""
    labels = alert.get("labels", {})
    annotations = alert.get("annotations", {})
    name = labels.get("alertname", "alert")
    severity = labels.get("severity", "")
    status = alert.get("status", "firing")

    head = f"<strong>{emoji(status, severity)} {html.escape(name)}</strong>"
    if severity:
        head += f" · <em>{html.escape(severity)}</em>"
    parts = [head]

    summary = annotations.get("summary", "").strip()
    if summary:
        parts.append(f"<div>{html.escape(summary)}</div>")

    items = [
        f"<li>{html.escape(k)}: <code>{html.escape(str(labels[k]))}</code></li>"
        for k in LABELS
        if labels.get(k)
    ]
    if items:
        parts.append("<ul>" + "".join(items) + "</ul>")

    # <pre> keeps error strings, paths and lock names readable. Descriptions in
    # this repo are long and often contain a literal command or metric name.
    description = annotations.get("description", "").strip()
    if description:
        parts.append(f"<pre>{html.escape(description)}</pre>")

    url = alert.get("generatorURL", "")
    if url:
        parts.append(f'<a href="{html.escape(url, quote=True)}">source</a>')

    return "".join(parts)


def render(payload):
    """A whole webhook payload as one message.

    One message per payload rather than per alert: Alertmanager already groups,
    and groupInterval is 5m (#414), so honouring the grouping is what keeps the
    room readable instead of reproducing the stream this was meant to avoid.
    """
    alerts = payload.get("alerts", [])
    if not alerts:
        return None
    if len(alerts) == 1:
        return render_alert(alerts[0])

    status = payload.get("status", "firing")
    verb = "resolved" if status == "resolved" else "firing"
    head = f"<strong>{len(alerts)} alerts {verb}</strong>"
    return head + "<hr>" + "<hr>".join(render_alert(a) for a in alerts)


def render_flux(payload):
    """A Flux notification-controller event as an HTML fragment.

    Flux posts its Event JSON to a `generic` Provider and offers no body
    templating, which is why these come through here rather than going straight
    to Campfire the way Gatus does.
    """
    obj = payload.get("involvedObject", {})
    severity = payload.get("severity", "info")
    kind = obj.get("kind", "object")
    name = obj.get("name", "?")
    namespace = obj.get("namespace", "")
    reason = payload.get("reason", "")
    message = payload.get("message", "").strip()

    mark = "🚨" if severity == "error" else "ℹ️"
    head = f"<strong>{mark} {html.escape(kind)}/{html.escape(name)}</strong>"
    if severity:
        head += f" · <em>{html.escape(severity)}</em>"
    parts = [head]

    items = []
    if namespace:
        items.append(f"<li>namespace: <code>{html.escape(namespace)}</code></li>")
    if reason:
        items.append(f"<li>reason: <code>{html.escape(reason)}</code></li>")
    if items:
        parts.append("<ul>" + "".join(items) + "</ul>")

    if message:
        parts.append(f"<pre>{html.escape(message)}</pre>")

    return "".join(parts)


def publish(url, body):
    request = urllib.request.Request(
        url,
        data=body.encode("utf-8"),
        headers={"Content-Type": "text/html; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.status


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Probes. Report unhealthy when a destination is unconfigured rather
        # than accepting events and dropping them.
        missing = [path for path, url in DESTINATIONS.items() if not url]
        ok = self.path == "/healthz" and not missing
        self.send_response(200 if ok else 503)
        self.end_headers()
        self.wfile.write(b"ok" if ok else f"unconfigured: {missing}".encode())

    def do_POST(self):
        destination = DESTINATIONS.get(self.path)
        if not destination:
            log(f"no destination for {self.path}")
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as error:
            log(f"bad payload: {error}")
            self.send_response(400)
            self.end_headers()
            return

        message = render(payload) if self.path == "/alerts" else render_flux(payload)
        if message is None:
            # Nothing to say. 200 so Alertmanager does not retry forever.
            self.send_response(200)
            self.end_headers()
            return

        try:
            status = publish(destination, message)
            count = len(payload.get("alerts", [])) if self.path == "/alerts" else 1
            log(f"{self.path}: published {count} event(s), campfire returned {status}")
            self.send_response(200)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            # 5xx so the sender retries: a chat message nobody sent is
            # indistinguishable from an event that never happened.
            log(f"{self.path}: publish failed: {error}")
            self.send_response(500)
        self.end_headers()

    def log_message(self, *args):
        pass  # access logs are noise; publishes are logged above


def main():
    for path, url in DESTINATIONS.items():
        if not url:
            log(f"no destination configured for {path}; /healthz serves 503 until there is")
    log(f"listening on :{LISTEN_PORT}, routing {', '.join(sorted(DESTINATIONS))}")
    ThreadingHTTPServer(("", LISTEN_PORT), Handler).serve_forever()


if __name__ == "__main__":
    sys.exit(main())
