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

organisation="$(get firekey-organisation)"
session="$(get firekey-session)"
project="$(get firekey-project)"
capability_public="$(get firekey-capability-public)"
evidence="$(get firekey-evidence)"
region="$(get firekey-region)"
image="$(get firekey-worker-image)"
setup="$(maybe firekey-setup)"

docker-credential-gcr configure-docker --registries="${region}-docker.pkg.dev"
docker pull "$image"

args=(
  --detach --restart=no --init --network=host --name=firekey-browser
  --read-only --tmpfs /tmp:rw,noexec,nosuid,size=512m --shm-size=1g
  --security-opt=no-new-privileges --cap-drop=ALL
  --env "FIREKEY_PROJECT_ID=$project"
  --env "FIREKEY_ORGANISATION_ID=$organisation"
  --env "FIREKEY_SESSION_ID=$session"
  --env "FIREKEY_CAPABILITY_PUBLIC_KEY=$capability_public"
  --env "FIREKEY_EVIDENCE_BUCKET=$evidence"
  --env "FIREKEY_REGION=$region"
  --env FIREKEY_TELEMETRY_ENABLED=true
)
if [[ "$setup" == "true" ]]; then
  args+=(
    --env FIREKEY_SETUP=true
    --env "FIREKEY_SETUP_TOKEN_HASH=$(get firekey-setup-token-hash)"
    --env "FIREKEY_SETUP_DOMAINS=$(get firekey-setup-domains)"
    --env "FIREKEY_SETUP_STORAGE_DOMAINS=$(get firekey-setup-storage-domains)"
    --env "FIREKEY_SETUP_SECRET=$(get firekey-setup-secret)"
  )
fi

docker run "${args[@]}" "$image"
