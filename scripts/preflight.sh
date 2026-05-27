#!/usr/bin/env bash
# preflight.sh — run ralph-doctor inside a freshly built image to
# verify it is safe to deploy. The CI pipeline calls this between
# build_image.sh and the push step.
#
# The image's baked ENV RALPH_GIT_HOST tells ralph-doctor which
# host to probe — preflight does NOT pass a host argument; it
# trusts what was baked at build time.
#
# Usage: preflight.sh <image-tag>
#
# Exit codes:
#   0  doctor passed
#   2  doctor failed (image must NOT be promoted)
#   3  could not run the container (docker error)
#   4  doctor binary not present (image too old to gate)
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: preflight.sh <image-tag>" >&2
  exit 2
fi

IMAGE="$1"

echo "Preflight: running ralph-doctor against ${IMAGE}" >&2
echo "  (doctor probes the host baked into the image's RALPH_GIT_HOST env)" >&2

OUT_DIR="$(mktemp -d -t ralph-preflight-XXXXXX)"
trap 'rm -rf "${OUT_DIR}"' EXIT

set +e
docker run --rm --entrypoint ralph-executor "${IMAGE}" doctor --json \
  >"${OUT_DIR}/stdout.json" 2>"${OUT_DIR}/stderr.log"
DOCKER_EXIT=$?
set -e

if [[ "${DOCKER_EXIT}" -eq 127 ]]; then
  echo "preflight: ralph-executor doctor not present in image" >&2
  cat "${OUT_DIR}/stderr.log" >&2 || true
  exit 4
fi

if [[ "${DOCKER_EXIT}" -ne 0 ]]; then
  echo "preflight: doctor failed with exit ${DOCKER_EXIT}" >&2
  echo "--- doctor stdout ---" >&2
  cat "${OUT_DIR}/stdout.json" >&2 || true
  echo "--- doctor stderr ---" >&2
  cat "${OUT_DIR}/stderr.log" >&2 || true
  exit 2
fi

echo "preflight: doctor passed" >&2
echo "--- doctor report ---" >&2
cat "${OUT_DIR}/stdout.json" >&2 || true

exit 0
