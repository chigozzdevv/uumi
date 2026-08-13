#!/bin/bash
set -euo pipefail

metadata="http://metadata.google.internal/computeMetadata/v1/instance/attributes"
header="Metadata-Flavor: Google"
get() {
  curl --fail --silent --show-error --header "$header" "$metadata/$1"
}

organisation="$(get firekey-organisation)"
session="$(get firekey-session)"
project="$(get firekey-project)"
capability_public="$(get firekey-capability-public)"
evidence="$(get firekey-evidence)"
region="$(get firekey-region)"
image="$(get firekey-worker-image)"

docker-credential-gcr configure-docker --registries="${region}-docker.pkg.dev"
docker pull "$image"
docker run --detach --restart=no --network=host --name=firekey-browser \
  --read-only --tmpfs /tmp:rw,noexec,nosuid,size=512m --shm-size=1g \
  --security-opt=no-new-privileges --cap-drop=ALL \
  --env FIREKEY_PROJECT_ID="$project" \
  --env FIREKEY_ORGANISATION_ID="$organisation" \
  --env FIREKEY_SESSION_ID="$session" \
  --env FIREKEY_CAPABILITY_PUBLIC_KEY="$capability_public" \
  --env FIREKEY_EVIDENCE_BUCKET="$evidence" \
  --env FIREKEY_REGION="$region" \
  "$image"
