#!/bin/bash
set -euo pipefail

metadata_root="http://metadata.google.internal/computeMetadata/v1"
metadata="$metadata_root/instance/attributes"
header="Metadata-Flavor: Google"
get() {
  curl --fail --silent --show-error --header "$header" "$metadata/$1"
}
maybe() {
  curl --fail --silent --header "$header" "$metadata/$1" 2>/dev/null || true
}

organisation="$(get uumi-organisation)"
session="$(get uumi-session)"
project="$(get uumi-project)"
capability_public="$(get uumi-capability-public)"
evidence="$(get uumi-evidence)"
region="$(get uumi-region)"
image="$(get uumi-worker-image)"
runtime_cidr="$(get uumi-runtime-cidr)"
model_armor_template="$(get uumi-model-armor-template)"
model_armor_response_template="$(get uumi-model-armor-response-template)"
setup="$(maybe uumi-setup)"

iptables -I INPUT -p tcp -s "$runtime_cidr" --dport 8080 -j ACCEPT

export DOCKER_CONFIG="/mnt/stateful_partition/uumi-docker"
mkdir -p "$DOCKER_CONFIG"
docker-credential-gcr configure-docker --registries="${region}-docker.pkg.dev"
docker pull "$image"

if ! curl --fail --silent --show-error --header "$header" \
  "$metadata_root/instance/service-accounts/default/token" >/dev/null; then
  echo "Uumi browser worker cannot obtain an attached service-account token." >/dev/console
  exit 1
fi

args=(
  --restart=no --init --network=host --name=uumi-browser
  --read-only --tmpfs /tmp:rw,noexec,nosuid,size=512m --shm-size=1g
  --security-opt=no-new-privileges --cap-drop=ALL
  --env "UUMI_PROJECT_ID=$project"
  --env "UUMI_ORGANISATION_ID=$organisation"
  --env "UUMI_SESSION_ID=$session"
  --env "UUMI_CAPABILITY_PUBLIC_KEY=$capability_public"
  --env "UUMI_EVIDENCE_BUCKET=$evidence"
  --env "UUMI_REGION=$region"
  --env "UUMI_MODEL_ARMOR_TEMPLATE=$model_armor_template"
  --env "UUMI_MODEL_ARMOR_RESPONSE_TEMPLATE=$model_armor_response_template"
  --env UUMI_TELEMETRY_ENABLED=true
)
if [[ "$setup" == "true" ]]; then
  args+=(
    --env UUMI_SETUP=true
    --env "UUMI_SETUP_TOKEN_HASH=$(get uumi-setup-token-hash)"
    --env "UUMI_SETUP_DOMAINS=$(get uumi-setup-domains)"
    --env "UUMI_SETUP_STORAGE_DOMAINS=$(get uumi-setup-storage-domains)"
    --env "UUMI_SETUP_SECRET=$(get uumi-setup-secret)"
  )
fi


docker run "${args[@]}" "$image" \
  uvicorn browser.workerapp:app --host 0.0.0.0 --port 8080 --no-access-log
