# The NetworkPolicies That Were Never Applied

Ninety-nine NetworkPolicies in the staging cluster. `kubectl get networkpolicy`
listed every one of them. Flux reported them applied and healthy. None of them
were doing anything.

They had been written, reviewed, merged and — as far as anything in the system
could tell — deployed, over a period of weeks. The cluster enforced none of it,
and nothing anywhere said so.

## How It Surfaced

Not from a security review. From a question about something else.

Mealie needed to reach authentik to complete an OIDC login. Authentik's public
hostname resolves to the cluster's own ingress address:

```
authentik.staging.ronaldlokers.nl -> 10.0.40.52
```

and Mealie's `allow-internet-egress` deliberately excludes `10.0.0.0/8` as an
SSRF guard. That connection should have been blocked. It worked perfectly.

The interesting question is not "why did it work" but "why did I believe it
would". I had read the policy, seen the exclusion, and expected the login to
fail. When it succeeded I was one step away from concluding the SSRF guard had
a hole in it — a plausible, entirely wrong conclusion that would have sent me
editing the wrong file.

## Establishing the Fact Before Explaining It

The probe that settled it used a positive and a negative control, from the same
pod, in both clusters:

| from mealie to | permitted? | staging | production |
|---|---|---|---|
| `postgres-cluster-rw.database:5432` | yes | connected | connected |
| prometheus pod IP`:9090` | **no** | **connected** | refused |

The positive control connects in both, so the probe works and the pod has a
network. In staging the negative control connects just as freely, including
straight to a pod IP with Services bypassed entirely.

Both clusters' `kubectl get networkpolicy -n mealie` output is identical, so it
was not a missing manifest. Staging simply was not enforcing.

## Two Faults, Not One

k3s runs kube-router's network policy controller in-process, and neither
cluster disables it. So it should have been running. Reading the node logs:

```
k3d-staging-server-0:  "Starting network policy controller"   x3
k3d-staging-agent-0:   (no mention of it, across 36 hours of uptime)
```

**The controller had never started on the agent node.** Restarting that
container started it, and pods on the agent came under enforcement for the
first time.

That left the server node. Dumping its firewall rules showed the hooks present,
the chains built — and every one of them pointing at the wrong pods:

```
node k3d-staging-server-0 (its own pods are 10.42.1.x)
  jump rules for 10.42.0.x (the *other* node's pods):  124
  jump rules for 10.42.1.x (its own pods):               0
```

Each pod's firewall existed on the node where that pod does not run, so its
traffic never passes through it. The policies were not missing, not misapplied,
not too permissive. They were installed somewhere the packets never go.

## The Check That Lied

With the fault understood, the fix was a check that would catch it next time: a
canary pod on every node, covered by a deny-all-egress policy, told to reach
something forbidden. If it gets through, that node is not enforcing.

It reported both staging nodes healthy.

```
k3d-staging-agent-0      enforcing   (BLOCKED refused)
k3d-staging-server-0     enforcing   (BLOCKED refused)
```

The server node was not enforcing. I had measured that minutes earlier.

**Newly created pods were getting their firewall chains correctly; pods that
had been running for hours were not.** A canary is, by construction, always a
new pod. The check was measuring the one case that worked, and it was measuring
it precisely because it created its own subject.

That is the same mistake as checking `pg_stat_activity` before a `REVOKE` and
concluding nothing would break — a measurement taken on the population that
cannot exhibit the problem. This time it had been rebuilt into a tool, where it
would have gone on producing a green result indefinitely.

The fix is to probe from pods that were already there. An ephemeral container
joins an existing pod's network namespace, so it is filtered by exactly the
chains that pod's traffic is:

```
kubectl debug -n gatus <pod> --image=alpine --container=netpol-probe -- ...
```

```
k3d-staging-agent-0      enforcing        (BLOCKED refused, from authentik/authentik-server-…)
k3d-staging-server-0     NOT ENFORCING    (ALLOWED, from commafeed/commafeed-…)
```

## What Enforcement Broke When It Arrived

Within a minute of the agent node starting to enforce, Tandoor's OIDC login
stopped working — the token exchange goes to that same private ingress address
the SSRF guard excludes.

This is not a regression. It is a bug that had been merged and had been sitting
in the cluster the whole time, invisible because nothing was enforcing the rule
it violated. Turning enforcement on did not break Tandoor; it revealed that
Tandoor had never been correct.

Running the finished check against production says the same thing about a
change that has not happened yet:

```
== is production enforcing NetworkPolicy at all? ==
  kube-srv-1               enforcing
  kube-srv-2               enforcing
  kube-srv-3               enforcing

  FAIL mealie   -> 10.0.40.100:443   BLOCKED refused
       server-side half of the OIDC login: discovery and token exchange
```

Production enforces properly, so promoting authentik there would have broken
both logins on arrival — with the error surfacing on the application side,
pointing away from the network policy that caused it.

## Lessons

**A policy that is applied is not a policy that is enforced.** Every signal
this repository has — Flux reconciled, `kubectl get` lists it, `validate.sh`
passes, CI green — reports that the manifest reached the cluster. Not one of
them reports whether the cluster acts on it. That gap held for weeks and
nothing in the pipeline was capable of noticing.

**A staging environment that silently differs from production is worse than no
staging environment.** Not because it fails to catch bugs, but because it
issues a passing verdict on changes it never tested. Every NetworkPolicy merged
in that window carried a staging green light that meant nothing, and would have
taken effect for the first time in production, on live applications.

**A test that builds its own subject tests the construction, not the system.**
The canary passed because creating a pod was exactly the operation that still
worked. Probe what is already running, in the state it is already in.

**Deny-side checks are the whole point.** Every allow-side expectation in the
final check passed against a cluster enforcing nothing — a cluster that permits
everything satisfies every "this should work" assertion. Only "this must not
work" can tell the difference between a correct policy and no policy at all.

## Related

- [`scripts/netpol-check.py`](../../scripts/netpol-check.py) — the check this produced
- [The Revoke That Waited Four Hours to Break Anything](revoke-that-only-broke-things-four-hours-later.md) — same failure of reasoning, different subsystem
