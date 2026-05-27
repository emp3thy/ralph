#!/usr/bin/env bash
# build_image.sh — build a host-specific ralph-executor container
# image. The --host flag is REQUIRED and selects which set of
# host-specific skills the image carries. The resulting image tag
# is suffixed with -<host> so the host is obvious in `docker images`.
#
# Required:
#   --host github|ado   Which git host this image targets. Passes
#                       through to docker build as
#                       --build-arg RALPH_GIT_HOST=<host>.
#
# Optional:
#   --push              Push to RALPH_REGISTRY after building.
#   --help              Print this usage text.
#
# Environment:
#   RALPH_VERSION       Explicit version tag (e.g. 0.1.0). Defaults
#                       to `git rev-parse --short HEAD` or "dev".
#   RALPH_REGISTRY      Fully-qualified registry prefix (e.g. an
#                       ECR URL). If set, the image is also tagged
#                       and (with --push) pushed there.
#   RALPH_IMAGE         Image name. Defaults to ralph-executor.
#
# Outputs:
#   The last line of stdout is the fully-qualified tag of the
#   image that was just built, so callers can pipe it:
#
#       IMG=$(scripts/build_image.sh --host github)
#       scripts/preflight.sh "$IMG"
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: build_image.sh --host github|ado [--push] [--help]

Required:
  --host github|ado   Which git host this image targets.

Optional:
  --push              Push to RALPH_REGISTRY after building.
  --help              Print this usage text.

Environment:
  RALPH_VERSION   Explicit version tag. Defaults to git short SHA.
  RALPH_REGISTRY  Registry prefix; if set, the image is also
                  tagged as $RALPH_REGISTRY/$RALPH_IMAGE:<ver>-<host>
                  and pushed when --push is given.
  RALPH_IMAGE     Image name. Defaults to ralph-executor.

Examples:
  bash scripts/build_image.sh --host github
  bash scripts/build_image.sh --host ado --push
  RALPH_VERSION=0.1.0 bash scripts/build_image.sh --host github
EOF
}

HOST=""
PUSH=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)
      if [[ $# -lt 2 ]]; then
        echo "ERROR: --host requires an argument (github|ado)" >&2
        usage >&2
        exit 2
      fi
      HOST="$2"
      shift 2
      ;;
    --host=*)
      HOST="${1#--host=}"
      shift
      ;;
    --push) PUSH=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "${HOST}" ]]; then
  echo "ERROR: --host is required (github|ado)" >&2
  usage >&2
  exit 2
fi

if [[ "${HOST}" != "github" && "${HOST}" != "ado" ]]; then
  echo "ERROR: --host must be 'github' or 'ado', got '${HOST}'" >&2
  usage >&2
  exit 2
fi

RALPH_IMAGE="${RALPH_IMAGE:-ralph-executor}"
RALPH_VERSION="${RALPH_VERSION:-$(git rev-parse --short HEAD 2>/dev/null || echo "dev")}"
LOCAL_TAG="${RALPH_IMAGE}:${RALPH_VERSION}-${HOST}"
LATEST_TAG="${RALPH_IMAGE}:latest-${HOST}"

echo "Building ${LOCAL_TAG} (RALPH_GIT_HOST=${HOST})" >&2

DOCKER_BUILDKIT=1 docker build \
  --progress=plain \
  --build-arg "RALPH_GIT_HOST=${HOST}" \
  --tag "${LOCAL_TAG}" \
  --tag "${LATEST_TAG}" \
  --file Dockerfile \
  .

FINAL_TAG="${LOCAL_TAG}"
if [[ -n "${RALPH_REGISTRY:-}" ]]; then
  REMOTE_TAG="${RALPH_REGISTRY}/${RALPH_IMAGE}:${RALPH_VERSION}-${HOST}"
  REMOTE_LATEST="${RALPH_REGISTRY}/${RALPH_IMAGE}:latest-${HOST}"
  docker tag "${LOCAL_TAG}"  "${REMOTE_TAG}"
  docker tag "${LATEST_TAG}" "${REMOTE_LATEST}"
  if [[ "${PUSH}" -eq 1 ]]; then
    echo "Pushing ${REMOTE_TAG} and ${REMOTE_LATEST}" >&2
    docker push "${REMOTE_TAG}"
    docker push "${REMOTE_LATEST}"
  fi
  FINAL_TAG="${REMOTE_TAG}"
fi

# Last line of stdout is the tag callers should use.
echo "${FINAL_TAG}"
