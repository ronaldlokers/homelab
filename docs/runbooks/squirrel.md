# Bringing Squirrel up

Squirrel is an external memory bot: you send it a direct message in Campfire,
it stores the raw text, and answers with a 🐿️. The code lives in
[ronaldlokers/squirrel](https://github.com/ronaldlokers/squirrel); this
repository owns its manifests.

Two of the steps below cannot be done from Git, and one of them is a setting no
code can verify. That is why this page exists.

## What it needs that nothing else here needs

Squirrel is the first **stateful** workload in the `campfire` namespace. Every
other bot is a stateless report-filer that can be rescheduled freely. Squirrel
writes every incoming message to a spool volume and fsyncs it — file, then the
directory — *before* the room is told it landed. A background loop drains the
spool into Postgres afterwards.

That ordering is the whole design, and it exists because **Campfire does not
retry a failed webhook delivery**. A message dropped at the door is gone. So:

- The Deployment is `replicas: 1` with `strategy: Recreate`. The volume is
  ReadWriteOnce, and two processes draining one directory would race.
- `/healthz` checks that the spool is writable and deliberately does **not**
  check Postgres. A readiness probe that failed during a database outage would
  pull the pod from its Service, Campfire's delivery would fail, and the
  messages sent in that window would be lost — converting a survivable outage
  into permanent data loss.
- The pod runs as uid 65532 with `fsGroup: 65532`. Longhorn hands over a
  root-owned filesystem; without the fsGroup the spool is unwritable,
  `/healthz` answers 503 forever, and the pod never goes ready. It never
  receives a webhook and nothing in the room says so.

## Step 1 — create the bot in Campfire

In Campfire, as an administrator, create a bot user named `squirrel` and set
its webhook URL to:

```
http://squirrel.campfire.svc.cluster.local:8080/transports/campfire
```

Do **not** give the bot key to this cluster. Squirrel holds no Campfire
credential: every reply travels back inside the webhook's own HTTP response, so
there is nothing here to leak or rotate. A bot key arrives only when the
scheduler does, because that is the first thing that needs to *start* a
conversation rather than answer one.

## Step 2 — open a direct message and find the two ids

Start a direct message with `@squirrel`, then read the ids out of Campfire's
database. It lives in SQLite at `/rails/storage/db/production.sqlite3`, and the
image ships no `sqlite3` binary — so copy it out and query it locally. Take the
`-wal` and `-shm` files too, or a room created in the last few minutes will be
missing:

```bash
POD=$(kubectl --context=production get pod -n campfire -l app=campfire \
        -o jsonpath='{.items[0].metadata.name}')
for f in production.sqlite3 production.sqlite3-shm production.sqlite3-wal; do
  kubectl --context=production cp -c campfire \
    "campfire/$POD:/rails/storage/db/$f" "/tmp/$f"
done

sqlite3 -header /tmp/production.sqlite3 "
  select r.id as conversation_id, r.type, u.id as sender_id, u.name
  from rooms r
  join memberships m on m.room_id = r.id
  join users u on u.id = m.user_id
  where r.type = 'Rooms::Direct'
    and r.id in (select room_id from memberships m2
                 join users u2 on u2.id = m2.user_id
                 where u2.role = 'bot' and trim(lower(u2.name)) = 'squirrel')
  order by r.id, u.id;"

shred -u /tmp/production.sqlite3*
```

**Match on `rooms.type`, not on the room's name.** There is a `Rooms::Closed`
room also called "Squirrel" with the same two members; it is indistinguishable
from the direct one by name, and choosing it is the mistake this page exists to
prevent.

Also note the bot's name is `Squirrel ` — capital S, trailing space — which is
why the query trims and lowercases. An exact match on `"squirrel"` finds
nothing and looks like "no direct room exists yet".

Put the non-bot user's id in `CAMPFIRE_SENDER_ID` and the room's id in
`CAMPFIRE_CONVERSATION_ID` in
`apps/production/squirrel/deployment-postgres-patch.yaml`, and commit. Flux
applies within about a minute.

### The obligation no code can check

`CAMPFIRE_CONVERSATION_ID` **must name a direct room.**

The webhook payload carries no room-type field. Campfire models direct rooms as
a distinct subclass with a `direct?` predicate, but never serialises it, so
Squirrel cannot verify what kind of room a message came from and does not
pretend to. Point this at a group room and it will capture from it happily.

What actually holds the line is this setting, plus `CAMPFIRE_SENDER_ID`, plus
the fact that outside direct rooms Campfire only fires the webhook on an
explicit `@squirrel` mention. Anything from another conversation is answered
with total silence and logged at warn with its conversation id.

## Step 3 — revoke PUBLIC's CONNECT

**This does not happen by itself, and it was missed the first time.**

CloudNativePG's `Database` CR creates the database and sets its owner. It does
not revoke `PUBLIC`'s implicit `CONNECT`, and there is no declarative field for
it — the same reason the `bots` role's SELECT grants are applied by hand. A new
database therefore arrives on PostgreSQL's defaults, with every role in the
cluster able to connect to it. That is the state #219 removed everywhere else.

```bash
P=$(kubectl --context=production get pod -n database \
      -l cnpg.io/cluster=postgres-cluster,role=primary \
      -o jsonpath='{.items[0].metadata.name}')

kubectl --context=production exec -n database "$P" -c postgres -- \
  psql -U postgres -c "REVOKE CONNECT ON DATABASE squirrel FROM PUBLIC;"
```

No matching `GRANT` is needed: the owner holds `CONNECT` implicitly, which is
also why revoking from `PUBLIC` does not lock Squirrel out of its own database.

Verify against a database that already has it — the ACLs should be identical in
shape, `=T/<owner>` meaning PUBLIC keeps TEMP and loses CONNECT:

```bash
kubectl --context=production exec -n database "$P" -c postgres -- psql -U postgres -t -c \
  "select datname, array_to_string(datacl,' | ') from pg_database
   where datname in ('squirrel','linkding');"
```

`PostgreSQLDatabaseNotIsolated` fires within a few minutes if this is skipped,
which is how it was caught. Read
[revoke-that-only-broke-things-four-hours-later.md](../war-stories/revoke-that-only-broke-things-four-hours-later.md)
before revoking on any database other than an app's own — on `postgres` this
same command once removed `streaming_replica`'s only grant and broke replica
rejoin for four hours with every signal green.

## Step 4 — confirm it is working

```bash
# Ready, and the spool is writable
kubectl --context=production get pod -n campfire -l app=squirrel

# The boot sequence: listening comes before the database, on purpose
kubectl --context=production logs -n campfire -l app=squirrel | head -20
```

Expect `http.listening`, then `transport.started`, then `db.ready`. If the
database is unreachable the first two still appear and captures still land —
that is the design working, not a failure.

Then send it a message. You should get a 🐿️ back within a second, and:

```bash
kubectl --context=production exec -n database postgres-cluster-1 -- \
  psql -U postgres -d squirrel -c 'select id, raw_text, received_at from items order by id desc limit 5;'
```

## When something is wrong

**No 🐿️, and the room says "Failed to respond within 7 seconds."** The webhook
never arrived or never returned. Check the NetworkPolicy pair — the hop needs
saying twice, ingress on Squirrel and egress on Campfire, because the namespace
default-denies both directions. `allow-campfire-to-squirrel` and
`allow-campfire-egress-to-squirrel`.

**The room gets "⚠️ couldn't save that — please resend."** The spool write
failed. Almost always the volume: check `fsGroup`, check the PVC is bound, check
it is not full. This is the one message that means a thought was genuinely lost,
so it is worth chasing.

**A file appears in the room instead of a message.** A non-200 response carrying
a `Content-Type` is uploaded by Campfire as an attachment. Squirrel answers 200
for everything including failures precisely to avoid this, so a file means
something upstream of the handler answered — usually the wrong URL configured on
the bot, hitting a route that does not exist.

**Everything looks healthy but nothing reaches Postgres.** The drain defers
rather than errors, so captures keep landing on disk and the room keeps getting
its squirrel. Check `allow-squirrel-egress` and `allow-squirrel-ingress` (the
latter lives in the `database` namespace), and look for `db.unavailable` in the
logs. The spool directory is your backlog; nothing is lost, but it will not
drain itself if the policy is missing.

**The pod will not start after a node change.** ReadWriteOnce plus `Recreate`
means the old pod must fully terminate before the new one attaches. If it is
stuck on multi-attach, see
[deployment-recreate-strategy-stuck-rollout.md](deployment-recreate-strategy-stuck-rollout.md).

## What is in the spool

In steady state, nothing — files exist only between arriving and draining,
normally under a second. Two things persist:

- The directory grows during a Postgres outage and drains when it returns.
- `quarantine/` holds captures that could not be inserted for a reason retrying
  will not fix. Those are never deleted and are irreplaceable, which is why the
  PVC carries the daily Longhorn backup labels rather than being treated as
  scratch.

A file in `quarantine/` is worth reading. It is a capture the system could not
store and could not honestly discard.
