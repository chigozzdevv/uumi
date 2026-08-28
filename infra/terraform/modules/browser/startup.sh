#!/bin/bash
set -euo pipefail

metadata="http://metadata.google.internal/computeMetadata/v1/instance/attributes"
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
model_armor_template="$(get uumi-model-armor-template)"
model_armor_response_template="$(get uumi-model-armor-response-template)"
setup="$(maybe uumi-setup)"

docker-credential-gcr configure-docker --registries="${region}-docker.pkg.dev"
docker pull "$image"

args=(
  --detach --restart=no --init --network=host --name=uumi-browser
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

docker run "${args[@]}" "$image"
