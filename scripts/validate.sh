#!/usr/bin/env bash
# Validates all Kubernetes manifests and kustomize overlays with kubeconform.
# Based on https://github.com/fluxcd/flux2-kustomize-helm-example/blob/main/scripts/validate.sh
#
# Prerequisites: kustomize, kubeconform, git, curl, python3
#
# Schemas are bulk-fetched once into /tmp and reused, so repeat runs are offline:
#   /tmp/flux-crd-schemas   Flux CRDs        (release tarball)
#   /tmp/k8s-json-schemas   core Kubernetes  (sparse git clone)
#   /tmp/crds-catalog       third-party CRDs (sparse git clone, groups derived)
#
# This script fails on any resource it could not actually validate — see the
# comment above the coverage gate for why that matters.

set -euo pipefail

# Kinds that are legitimately never schema-validated. Anything else appearing as
# "skipped" is a coverage regression and fails the run.
#
#   Secret                    excluded via -skip=Secret, because SOPS-encrypted
#                             Secrets carry a top-level `sops:` field that fails
#                             -strict by design. Covered instead by the Secret
#                             encryption check at the end, which is a stronger
#                             guarantee than a schema would give.
#   CustomResourceDefinition  no strict schema exists in either catalog (the
#                             embedded openAPIV3Schema is free-form). All 22 in
#                             this repo come from Flux's generated
#                             clusters/*/flux-system/gotk-components.yaml, which
#                             `flux bootstrap` vendors — they are not authored
#                             here, so schema-checking them buys nothing.
ALLOWED_SKIP_KINDS=("Secret" "CustomResourceDefinition")

SCHEMA_FLUX=/tmp/flux-crd-schemas
SCHEMA_K8S=/tmp/k8s-json-schemas
SCHEMA_CRDS=/tmp/crds-catalog
BUILD_DIR=/tmp/validate-build
REPORT=/tmp/validate-report.json

kubeconform_flags=(
  "-skip=Secret"
  "-strict"
  # Lets the run reach the coverage gate, which reports every unvalidated
  # resource at once instead of dying on the first. It does not weaken the
  # check: skipped kinds outside ALLOWED_SKIP_KINDS fail the run below.
  "-ignore-missing-schemas"
  # Required for JSON output to include passing resources; without it
  # kubeconform omits them and the skip ratio cannot be computed.
  "-verbose"
  "-output" "json"
  # Schema locations are strictly local: no per-resource network location and no
  # "default" fallback. Fetching one file per resource from
  # raw.githubusercontent.com rate-limits (429) on shared CI egress IPs, and
  # when it did, third-party CRDs silently became "skipped" while the script
  # still printed "Validation passed" and exited 0. A missing schema must fail
  # loudly, not degrade quietly.
  "-schema-location" "$SCHEMA_FLUX"
  "-schema-location" "$SCHEMA_K8S"
  "-schema-location" "$SCHEMA_CRDS/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json"
)

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

if [[ ! -d $SCHEMA_FLUX/master-standalone-strict ]]; then
  echo "INFO - Downloading Flux OpenAPI schemas"
  mkdir -p "$SCHEMA_FLUX/master-standalone-strict"
  curl -sL https://github.com/fluxcd/flux2/releases/latest/download/crd-schemas.tar.gz |
    tar zxf - -C "$SCHEMA_FLUX/master-standalone-strict"
fi

if [[ ! -d $SCHEMA_K8S/master-standalone-strict ]]; then
  echo "INFO - Downloading core Kubernetes OpenAPI schemas"
  git clone --depth 1 --filter=blob:none --sparse -q \
    https://github.com/yannh/kubernetes-json-schema.git "$SCHEMA_K8S"
  git -C "$SCHEMA_K8S" sparse-checkout set master-standalone-strict
fi

# ---------------------------------------------------------------------------
# Build every overlay once, one file per kustomization directory.
#
# Building to files rather than piping each directory straight into kubeconform
# means kubeconform runs once over everything, and its JSON report carries the
# originating filename — so a failure still names the overlay it came from.
# ---------------------------------------------------------------------------

echo "INFO - Building kustomize overlays"
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

while IFS= read -r -d $'\0' file; do
  dir="$(dirname "$file")"
  out="$BUILD_DIR/$(echo "${dir#./}" | tr '/' '_').yaml"
  if ! kustomize build "$dir" >"$out" 2>/tmp/kustomize-err; then
    echo "ERROR - kustomize build failed for $dir"
    cat /tmp/kustomize-err
    exit 1
  fi
done < <(find . -type f -name kustomization.yaml -not -path "./.git/*" -print0)

# Cluster-level manifests belong to no overlay, so copy them in as-is.
while IFS= read -r -d $'\0' file; do
  cp "$file" "$BUILD_DIR/cluster_$(echo "${file#./}" | tr '/' '_')"
done < <(find ./clusters -maxdepth 2 -type f -name '*.yaml' -not -name '.sops.yaml' -print0)

# ---------------------------------------------------------------------------
# Fetch third-party CRD schemas for exactly the API groups this repo uses.
#
# The group list is derived from the built manifests rather than hardcoded, so
# adding an app with a new CRD needs no edit here. The full catalog is 222MB
# across 585 groups; this repo needs about a dozen.
# ---------------------------------------------------------------------------

mapfile -t groups < <(
  grep -rhE '^apiVersion:' "$BUILD_DIR" |
    sed 's/^apiVersion:[[:space:]]*//' |
    grep '/' | cut -d/ -f1 | sort -u
)

echo "INFO - Fetching CRD schemas for ${#groups[@]} API groups"
if [[ ! -d $SCHEMA_CRDS/.git ]]; then
  git clone --depth 1 --filter=blob:none --sparse -q \
    https://github.com/datreeio/CRDs-catalog.git "$SCHEMA_CRDS"
fi
# Re-run every invocation: a no-op when the group set is unchanged, and picks up
# a newly added group without needing the cache cleared by hand.
git -C "$SCHEMA_CRDS" sparse-checkout set "${groups[@]}"

# ---------------------------------------------------------------------------
# Validate. kubeconform exits non-zero on an invalid resource; capture that
# rather than letting it kill the run, so every problem is reported at once.
# ---------------------------------------------------------------------------

echo "INFO - Validating manifests"
set +e
kubeconform "${kubeconform_flags[@]}" "$BUILD_DIR"/*.yaml >"$REPORT"
kubeconform_rc=$?
set -e

# The coverage gate is the reason this script was rewritten. The previous
# version paired -ignore-missing-schemas with a per-resource network schema
# location; when that fetch was rate-limited every third-party CRD silently
# became "skipped" and the script still reported success. Coverage swung between
# 93% and 85% run to run with no signal, and two broken configs reached the
# cluster that way, caught only by flux-diff. Now anything not actually
# validated fails the run.
python3 - "$REPORT" "$kubeconform_rc" "${ALLOWED_SKIP_KINDS[@]}" <<'PY'
import json, sys, collections

report_path, rc = sys.argv[1], int(sys.argv[2])
allowed = set(sys.argv[3:])

with open(report_path) as fh:
    resources = json.load(fh).get("resources", [])

counts = collections.Counter(r.get("status") for r in resources)
total = len(resources)
valid = counts.get("statusValid", 0)
skipped = counts.get("statusSkipped", 0)
pct = (100 * valid // total) if total else 0

print(f"INFO - {total} resources: {valid} validated ({pct}%), {skipped} skipped")

failed = False

bad = [r for r in resources if r.get("status") in ("statusInvalid", "statusError")]
if bad:
    failed = True
    print(f"\nERROR - {len(bad)} resource(s) failed validation:")
    for r in bad:
        print(f"  {r.get('kind')}/{r.get('name')} in {r.get('filename')}")
        print(f"    {r.get('msg')}")

unexpected = [
    r for r in resources
    if r.get("status") == "statusSkipped" and r.get("kind") not in allowed
]
if unexpected:
    failed = True
    by_kind = collections.Counter(r["kind"] for r in unexpected)
    print(f"\nERROR - {len(unexpected)} resource(s) had no schema and went unvalidated:")
    for kind, n in by_kind.most_common():
        print(f"  {n:3d}  {kind}")
    print("\n  These were skipped, not checked. Either the CRD schema catalog is")
    print("  missing this API group (did the fetch above succeed?), or the kind")
    print("  belongs in ALLOWED_SKIP_KINDS with a documented reason.")

if rc != 0 and not failed:
    failed = True
    print(f"\nERROR - kubeconform exited {rc} with no failure recorded in its report")

sys.exit(1 if failed else 0)
PY

# ---------------------------------------------------------------------------
# Secrets are excluded from schema validation, so check them directly: any
# Secret carrying a data payload must be SOPS-encrypted.
#
# A Secret with no data/stringData is fine in plaintext — the only one here is
# homepage's kubernetes.io/service-account-token, whose payload Kubernetes
# populates at runtime.
# ---------------------------------------------------------------------------

echo "INFO - Checking Secrets are SOPS-encrypted"
python3 - <<'PY'
import pathlib, re, sys

DOC_SPLIT = re.compile(r"^---\s*$", re.M)
leaked = []

for path in sorted(pathlib.Path(".").rglob("*.yaml")):
    if ".git" in path.parts:
        continue
    try:
        text = path.read_text()
    except (UnicodeDecodeError, OSError):
        continue
    if "kind: Secret" not in text:
        continue
    for doc in DOC_SPLIT.split(text):
        if not re.search(r"^kind:\s*Secret\s*$", doc, re.M):
            continue
        if re.search(r"^(data|stringData):", doc, re.M) and not re.search(r"^sops:", doc, re.M):
            name = re.search(r"^\s+name:\s*(\S+)", doc, re.M)
            leaked.append(f"{path}  (Secret {name.group(1) if name else '?'})")

if leaked:
    print(f"\nERROR - {len(leaked)} Secret(s) carry an unencrypted data payload:")
    for item in leaked:
        print(f"  {item}")
    print("\n  Encrypt with SOPS before committing — see CLAUDE.md.")
    sys.exit(1)
PY

# ---------------------------------------------------------------------------
# Every SOPS-encrypted file must be covered by a Flux Kustomization that has
# decryption enabled.
#
# When it is not, Flux applies the file verbatim and the literal
# ENC[AES256_GCM,...] string is stored as the value. Flux reports Ready, the
# workload starts, and its password is simply the ciphertext — no error is
# raised anywhere. This happened on staging: clusters/staging/monitoring.yaml
# had its decryption block commented out, which stayed invisible until the
# first encrypted Secret landed under monitoring/controllers/staging.
# ---------------------------------------------------------------------------

echo "INFO - Checking SOPS files are covered by a decrypting Kustomization"
python3 - <<'PY'
import pathlib, re, sys, yaml

# Map each Flux Kustomization's path -> whether it decrypts.
kustomizations = []
for path in pathlib.Path("clusters").rglob("*.yaml"):
    if path.name == ".sops.yaml":
        continue
    try:
        docs = list(yaml.safe_load_all(path.read_text()))
    except Exception:
        continue
    for doc in docs:
        if not isinstance(doc, dict) or doc.get("kind") != "Kustomization":
            continue
        if "kustomize.toolkit.fluxcd.io" not in str(doc.get("apiVersion", "")):
            continue
        spec = doc.get("spec", {}) or {}
        target = (spec.get("path") or "").lstrip("./").rstrip("/")
        if target:
            kustomizations.append((
                target,
                (spec.get("decryption") or {}).get("provider") == "sops",
                f"{path}:{doc.get('metadata',{}).get('name')}",
            ))

uncovered = []
for path in sorted(pathlib.Path(".").rglob("*.yaml")):
    if ".git" in path.parts or path.parts[0] == "clusters":
        continue
    try:
        text = path.read_text()
    except (UnicodeDecodeError, OSError):
        continue
    if not re.search(r"^sops:", text, re.M):
        continue
    rel = str(path)
    # longest matching path wins, mirroring how the trees nest
    matches = sorted(
        [k for k in kustomizations if rel.startswith(k[0] + "/")],
        key=lambda k: len(k[0]), reverse=True,
    )
    if not matches:
        uncovered.append((rel, "no Kustomization covers this path"))
    elif not matches[0][1]:
        uncovered.append((rel, f"{matches[0][2]} has no decryption.provider: sops"))

if uncovered:
    print(f"\nERROR - {len(uncovered)} SOPS file(s) would be applied without decryption:")
    for rel, why in uncovered:
        print(f"  {rel}")
        print(f"    {why}")
    print("\n  Flux would store the literal ENC[...] ciphertext as the value and")
    print("  still report Ready. Add a decryption block to that Kustomization.")
    sys.exit(1)
PY


# ---------------------------------------------------------------------------
# Credentials in manifests that are not Secrets.
#
# The check above proves Secrets are encrypted; it says nothing about a
# credential written into a ConfigMap, which is how the Immich API key leaked
# (#315).
#
# Narrow on purpose: only values under credential-shaped keys, ignoring
# references and placeholders. Entropy-scanning every string produces enough
# noise to get switched off.
# ---------------------------------------------------------------------------

echo "INFO - Checking for credentials outside Secrets"
python3 - <<'PY'
import pathlib, re, sys, yaml

SENSITIVE = re.compile(
    r"(^|_|-)(password|passwd|secret|token|apikey|api_key|credential)s?$"
    r"|^key$|^client_secret$|^secretkey$|^accesskey$",
    re.I,
)

# A value that is a reference, a placeholder or a public identifier, not a
# credential. Anything matching is not reported.
SAFE = (
    lambda v: len(v) < 12,                      # too short to be a real secret
    lambda v: "{{" in v or "${" in v or "$__env{" in v,  # templated at runtime
    lambda v: v.startswith(("ENC[", "/", "http://", "https://", "age1", "sha256:")),
    lambda v: v.lower() in ("true", "false", "none", "null", "changeme"),
    lambda v: v.isdigit(),
)

findings = []

def walk(node, path, doc_kind, file):
    if isinstance(node, dict):
        # {name, key} is a key reference (SecretKeySelector and friends) and
        # {key, operator} a label selector — neither is a credential.
        is_key_ref = "name" in node or "operator" in node
        for k, v in node.items():
            if isinstance(v, str) and isinstance(k, str) and SENSITIVE.search(k):
                if k.lower() == "key" and is_key_ref:
                    pass
                elif not any(pred(v) for pred in SAFE):
                    findings.append((file, doc_kind, "%s.%s" % (path, k) if path else k, v))
            walk(v, "%s.%s" % (path, k) if path else str(k), doc_kind, file)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            walk(v, "%s[%d]" % (path, i), doc_kind, file)

for root in ("clusters", "infrastructure", "apps", "monitoring"):
    for path in sorted(pathlib.Path(root).rglob("*.yaml")):
        try:
            docs = list(yaml.safe_load_all(path.read_text()))
        except Exception:
            continue  # rendered elsewhere, or not parseable standalone
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            kind = doc.get("kind", "?")
            if kind == "Secret":
                continue  # covered by the encryption check above
            # ConfigMap payloads are strings containing more YAML; parse them.
            if kind == "ConfigMap":
                for name, blob in (doc.get("data") or {}).items():
                    if not isinstance(blob, str):
                        continue
                    try:
                        inner = list(yaml.safe_load_all(blob))
                    except Exception:
                        continue
                    for d in inner:
                        walk(d, "data.%s" % name, kind, path)
            walk(doc, "", kind, path)

if findings:
    print("\nERROR - %d credential-shaped value(s) outside a Secret:" % len(findings))
    for file, kind, where, value in findings:
        print("  %s  (%s)  %s = %s…" % (file, kind, where, value[:6]))
    print("\n  Move the value into a SOPS-encrypted Secret and reference it")
    print("  from the manifest — see apps/production/homepage for the pattern.")
    sys.exit(1)
PY

echo "INFO - Validation passed"
