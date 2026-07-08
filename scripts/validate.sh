#!/usr/bin/env bash
# Validates all Kubernetes manifests and kustomize overlays with kubeconform.
# Based on https://github.com/fluxcd/flux2-kustomize-helm-example/blob/main/scripts/validate.sh
#
# Prerequisites: kustomize, kubeconform
# Flux CRD schemas are downloaded to /tmp/flux-crd-schemas on each run.
# Core Kubernetes schemas are downloaded to /tmp/k8s-json-schemas on each run.

set -euo pipefail

# SOPS-encrypted Secrets carry a top-level `sops:` field that fails strict
# validation, so Secrets are skipped entirely.
kubeconform_flags=("-skip=Secret")
# kubeconform's "default" schema location fetches one file per resource from
# raw.githubusercontent.com, which rate-limits (429) shared CI egress IPs —
# and this script invokes kubeconform once per kustomization dir, so the same
# core-k8s schema (ConfigMap, Secret, ...) would otherwise be re-fetched over
# the network for every app. Both schema sets below are instead bulk-fetched
# once up front (git/tarball, not per-file HTTP) so validation runs fully
# offline; "default" is kept only as a last-resort fallback. -cache also
# makes any resource ultimately served by "default" download once per run.
kubeconform_config=(
  "-strict"
  "-ignore-missing-schemas"
  "-cache" "/tmp/kubeconform-schema-cache"
  "-schema-location" "/tmp/flux-crd-schemas"
  "-schema-location" "/tmp/k8s-json-schemas"
  "-schema-location" "https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json"
  "-schema-location" "default"
  "-summary"
)

mkdir -p /tmp/kubeconform-schema-cache

if [[ ! -d /tmp/flux-crd-schemas/master-standalone-strict ]]; then
  echo "INFO - Downloading Flux OpenAPI schemas"
  mkdir -p /tmp/flux-crd-schemas/master-standalone-strict
  curl -sL https://github.com/fluxcd/flux2/releases/latest/download/crd-schemas.tar.gz |
    tar zxf - -C /tmp/flux-crd-schemas/master-standalone-strict
fi

if [[ ! -d /tmp/k8s-json-schemas/master-standalone-strict ]]; then
  echo "INFO - Downloading core Kubernetes OpenAPI schemas"
  git clone --depth 1 --filter=blob:none --sparse -q \
    https://github.com/yannh/kubernetes-json-schema.git /tmp/k8s-json-schemas
  git -C /tmp/k8s-json-schemas sparse-checkout set master-standalone-strict
fi

echo "INFO - Validating cluster manifests"
find ./clusters -maxdepth 2 -type f -name '*.yaml' -not -name '.sops.yaml' -print0 |
  xargs -0 kubeconform "${kubeconform_flags[@]}" "${kubeconform_config[@]}"

echo "INFO - Validating kustomize overlays"
find . -type f -name kustomization.yaml -not -path "./.git/*" -print0 |
  while IFS= read -r -d $'\0' file; do
    dir="$(dirname "$file")"
    echo "INFO - Building $dir"
    kustomize build "$dir" |
      kubeconform "${kubeconform_flags[@]}" "${kubeconform_config[@]}"
  done

echo "INFO - Validation passed"
