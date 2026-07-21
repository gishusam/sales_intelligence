#!/usr/bin/env bash
#
# Sales Intelligence — API-only Cloud Run deployment
#
# Run this script from the repository root:
#   chmod +x ./deploy-api-only.sh
#   ./deploy-api-only.sh
#
# This script:
#   - deploys ONLY the Cloud Run API service
#   - NEVER builds, updates, or executes the sales-scraper Cloud Run Job
#   - builds from backend/Dockerfile using backend/ as the Cloud Build context
#   - authenticates an unauthenticated gcloud CLI session
#   - preserves the current service environment, secrets, IAM, CPU, memory,
#     port, concurrency, timeout, and service account
#   - backs up the current Cloud Run service configuration
#   - verifies /health and the /api/leads/mine OpenAPI contract
#
# Defaults can be overridden with environment variables:
#   PROJECT_ID, REGION, API_SERVICE, BUILD_CONTEXT, BACKUP_DIR
#
# Options:
#   --yes            Skip confirmation prompts
#   --skip-tests     Do not attempt local pytest execution
#   --no-browser     Authenticate without automatically opening a browser
#   --project ID     Override GCP project
#   --region REGION  Override GCP region
#   --service NAME   Override Cloud Run service
#   --help           Show help

set -Eeuo pipefail

PROJECT_ID="${PROJECT_ID:-sales-intelligens}"
REGION="${REGION:-europe-west1}"
API_SERVICE="${API_SERVICE:-sales-intelligence-api}"
BUILD_CONTEXT="${BUILD_CONTEXT:-backend}"
DOCKERFILE="${DOCKERFILE:-${BUILD_CONTEXT}/Dockerfile}"
BACKUP_DIR="${BACKUP_DIR:-deployment-backups}"
EXPECTED_OPENAPI_PATH="${EXPECTED_OPENAPI_PATH:-/api/leads/mine}"

AUTO_YES=0
SKIP_TESTS=0
NO_BROWSER=0
DEPLOY_ATTEMPTED=0
PREVIOUS_REVISION=""
NEW_REVISION=""
API_IMAGE=""
API_URL=""

bold=""
reset=""
green=""
yellow=""
red=""
if [[ -t 1 ]]; then
  bold=$'\033[1m'
  reset=$'\033[0m'
  green=$'\033[32m'
  yellow=$'\033[33m'
  red=$'\033[31m'
fi

info() { printf '%s\n' "${bold}==>${reset} $*"; }
ok()   { printf '%s\n' "${green}✓${reset} $*"; }
warn() { printf '%s\n' "${yellow}!${reset} $*" >&2; }
die()  { printf '%s\n' "${red}ERROR:${reset} $*" >&2; exit 1; }

usage() {
  sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
}

confirm() {
  local prompt="$1"
  if [[ "$AUTO_YES" -eq 1 ]]; then
    return 0
  fi
  printf '%s [y/N] ' "$prompt"
  read -r answer
  case "$answer" in
    y|Y|yes|YES) return 0 ;;
    *) return 1 ;;
  esac
}

on_error() {
  local exit_code=$?
  printf '\n' >&2
  warn "Deployment script stopped with exit code ${exit_code}."
  if [[ "$DEPLOY_ATTEMPTED" -eq 1 && -n "$PREVIOUS_REVISION" ]]; then
    warn "The previous revision is still available. Roll back with:"
    printf '  gcloud run services update-traffic %q --project %q --region %q --to-revisions %q\n' \
      "$API_SERVICE" "$PROJECT_ID" "$REGION" "${PREVIOUS_REVISION}=100" >&2
  fi
  exit "$exit_code"
}
trap on_error ERR

while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes|-y)
      AUTO_YES=1
      shift
      ;;
    --skip-tests)
      SKIP_TESTS=1
      shift
      ;;
    --no-browser)
      NO_BROWSER=1
      shift
      ;;
    --project)
      [[ $# -ge 2 ]] || die "--project requires a value"
      PROJECT_ID="$2"
      shift 2
      ;;
    --region)
      [[ $# -ge 2 ]] || die "--region requires a value"
      REGION="$2"
      shift 2
      ;;
    --service)
      [[ $# -ge 2 ]] || die "--service requires a value"
      API_SERVICE="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      die "Unknown option: $1"
      ;;
  esac
done

ensure_command() {
  command -v "$1" >/dev/null 2>&1 || die "Required command '$1' was not found."
}

ensure_gcloud() {
  if command -v gcloud >/dev/null 2>&1; then
    return 0
  fi

  if [[ "$(uname -s)" == "Darwin" ]]; then
    warn "Google Cloud CLI is not installed."
    if command -v brew >/dev/null 2>&1; then
      confirm "Install Google Cloud CLI with Homebrew now?" || \
        die "Install it with: brew update && brew install --cask gcloud-cli"
      brew update
      brew install --cask gcloud-cli
      export PATH="$(brew --prefix)/bin:$(brew --prefix)/share/google-cloud-sdk/bin:${PATH}"
      hash -r
    else
      die "Homebrew is not installed. Install Homebrew, then run: brew install --cask gcloud-cli"
    fi
  else
    die "Google Cloud CLI is not installed."
  fi

  command -v gcloud >/dev/null 2>&1 || \
    die "Google Cloud CLI installation completed, but gcloud is not on PATH. Open a new terminal and rerun this script."
}

authenticate_gcloud() {
  local active_account=""
  active_account="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | head -n 1 || true)"

  if [[ -z "$active_account" ]] || ! gcloud auth print-access-token >/dev/null 2>&1; then
    info "No usable active Google Cloud login was found."
    if [[ "$NO_BROWSER" -eq 1 ]]; then
      gcloud auth login --no-launch-browser
    else
      gcloud auth login
    fi
  fi

  active_account="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | head -n 1 || true)"
  [[ -n "$active_account" ]] || die "Authentication did not produce an active Google Cloud account."
  ok "Authenticated as ${active_account}"
}

strip_image_tag_or_digest() {
  local ref="$1"
  ref="${ref%@sha256:*}"
  printf '%s' "$ref" | sed -E 's#:[^/:]+$##'
}

run_local_tests_if_available() {
  if [[ "$SKIP_TESTS" -eq 1 ]]; then
    warn "Local tests were skipped by request."
    return 0
  fi

  local python_cmd=""
  local test_targets=()

  [[ -d "${BUILD_CONTEXT}/tests" ]] && test_targets+=("${BUILD_CONTEXT}/tests")
  [[ -d "tests" ]] && test_targets+=("tests")

  if [[ ${#test_targets[@]} -eq 0 ]]; then
    warn "No local tests directory was found; continuing without local pytest."
    return 0
  fi

  if [[ -x ".venv/bin/python" ]]; then
    python_cmd=".venv/bin/python"
  elif [[ -x "${BUILD_CONTEXT}/.venv/bin/python" ]]; then
    python_cmd="${BUILD_CONTEXT}/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1 && python3 -c 'import pytest' >/dev/null 2>&1; then
    python_cmd="python3"
  fi

  if [[ -z "$python_cmd" ]]; then
    warn "Tests exist, but no Python environment with pytest is available."
    confirm "Continue without running local tests?" || die "Deployment cancelled."
    return 0
  fi

  info "Running local API tests"
  "$python_cmd" -m pytest -q "${test_targets[@]}"
  ok "Local tests passed"
}

verify_no_sensitive_build_files() {
  local sensitive_files=""
  sensitive_files="$(
    find "$BUILD_CONTEXT" -type f \
      \( -name '.env' -o -name '.env.*' -o -name '*.pem' -o -name '*.key' \
         -o -name '*credentials*.json' -o -name '*service-account*.json' \) \
      ! -name '.env.example' ! -name '.env.sample' ! -name '.env.template' \
      -print 2>/dev/null || true
  )"

  if [[ -n "$sensitive_files" ]]; then
    printf '%s\n' "$sensitive_files" >&2
    die "Potential secret files exist inside '${BUILD_CONTEXT}', which is the uploaded Cloud Build context. Remove or relocate them before deploying."
  fi
}

verify_health() {
  local attempts=6
  local body=""
  local attempt=1

  info "Checking ${API_URL}/health"
  while [[ "$attempt" -le "$attempts" ]]; do
    if body="$(curl --fail --silent --show-error --max-time 45 "${API_URL}/health" 2>/dev/null)"; then
      printf '%s\n' "$body"
      if printf '%s' "$body" | grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"'; then
        ok "Health check passed"
        return 0
      fi
      warn "Health endpoint responded, but did not report status=ok."
    else
      warn "Health attempt ${attempt}/${attempts} failed; retrying after cold-start delay."
    fi
    attempt=$((attempt + 1))
    sleep 10
  done

  die "Health verification failed."
}

verify_openapi() {
  local tmp_file
  tmp_file="$(mktemp)"
  trap 'rm -f "$tmp_file"' RETURN

  info "Checking OpenAPI contract"
  curl --fail --silent --show-error --max-time 45 \
    "${API_URL}/openapi.json" -o "$tmp_file"

  if command -v jq >/dev/null 2>&1; then
    jq -e --arg path "$EXPECTED_OPENAPI_PATH" '
      .paths[$path].get.parameters as $params
      | ($params | map(.name) | index("page")) != null
        and ($params | map(.name) | index("limit")) != null
    ' "$tmp_file" >/dev/null
  elif command -v python3 >/dev/null 2>&1; then
    python3 - "$tmp_file" "$EXPECTED_OPENAPI_PATH" <<'PY'
import json
import sys

filename, expected_path = sys.argv[1], sys.argv[2]
with open(filename, encoding="utf-8") as handle:
    data = json.load(handle)

operation = data.get("paths", {}).get(expected_path, {}).get("get")
if operation is None:
    raise SystemExit(f"{expected_path} is missing from OpenAPI")

names = {parameter.get("name") for parameter in operation.get("parameters", [])}
missing = {"page", "limit"} - names
if missing:
    raise SystemExit(f"OpenAPI is missing parameters: {sorted(missing)}")
PY
  else
    grep -Fq "\"${EXPECTED_OPENAPI_PATH}\"" "$tmp_file" || \
      die "${EXPECTED_OPENAPI_PATH} is missing from OpenAPI."
    grep -Fq '"page"' "$tmp_file" || die "OpenAPI does not contain the page parameter."
    grep -Fq '"limit"' "$tmp_file" || die "OpenAPI does not contain the limit parameter."
  fi

  ok "OpenAPI contains ${EXPECTED_OPENAPI_PATH} with page and limit"
  rm -f "$tmp_file"
  trap - RETURN
}

ensure_command git
ensure_command curl
ensure_gcloud

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[[ -n "$REPO_ROOT" ]] || die "This directory is not inside a Git repository."

CURRENT_DIR="$(pwd -P)"
REPO_ROOT="$(cd "$REPO_ROOT" && pwd -P)"
[[ "$CURRENT_DIR" == "$REPO_ROOT" ]] || \
  die "Run this script from the repository root: ${REPO_ROOT}"

[[ -f "$DOCKERFILE" ]] || \
  die "Expected API Dockerfile was not found at '${DOCKERFILE}'. The API build context must be '${BUILD_CONTEXT}'."

verify_no_sensitive_build_files
authenticate_gcloud

info "Configuring project ${PROJECT_ID} and region ${REGION}"
gcloud config set project "$PROJECT_ID" >/dev/null
gcloud config set run/region "$REGION" >/dev/null

info "Reading the existing Cloud Run API service"
gcloud run services describe "$API_SERVICE" \
  --project "$PROJECT_ID" \
  --region "$REGION" >/dev/null

TIMESTAMP="$(date -u +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"

PREVIOUS_REVISION="$(
  gcloud run services describe "$API_SERVICE" \
    --project "$PROJECT_ID" \
    --region "$REGION" \
    --format='value(status.latestReadyRevisionName)'
)"
CURRENT_IMAGE="$(
  gcloud run services describe "$API_SERVICE" \
    --project "$PROJECT_ID" \
    --region "$REGION" \
    --format='value(spec.template.spec.containers[0].image)'
)"
API_URL="$(
  gcloud run services describe "$API_SERVICE" \
    --project "$PROJECT_ID" \
    --region "$REGION" \
    --format='value(status.url)'
)"

[[ -n "$PREVIOUS_REVISION" ]] || die "Could not determine the current ready revision."
[[ -n "$CURRENT_IMAGE" ]] || die "Could not determine the current API image."

info "Backing up the current API service"
gcloud run services describe "$API_SERVICE" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --format=export > "${BACKUP_DIR}/api-${TIMESTAMP}.yaml"

API_IMAGE_BASE="$(strip_image_tag_or_digest "$CURRENT_IMAGE")"
GIT_SHA="$(git rev-parse --short HEAD)"
DIRTY_STATUS="$(git status --short -- "$BUILD_CONTEXT" || true)"

if [[ -n "$DIRTY_STATUS" ]]; then
  warn "The API build context has uncommitted or untracked changes:"
  printf '%s\n' "$DIRTY_STATUS"
  confirm "Build and deploy these local backend changes?" || die "Deployment cancelled."
  BUILD_TAG="${GIT_SHA}-dirty-${TIMESTAMP}"
else
  BUILD_TAG="${GIT_SHA}-${TIMESTAMP}"
fi

API_IMAGE="${API_IMAGE_BASE}:${BUILD_TAG}"

cat > "${BACKUP_DIR}/api-${TIMESTAMP}-before.txt" <<EOF
project=${PROJECT_ID}
region=${REGION}
service=${API_SERVICE}
previous_revision=${PREVIOUS_REVISION}
previous_image=${CURRENT_IMAGE}
service_url=${API_URL}
build_context=${BUILD_CONTEXT}
dockerfile=${DOCKERFILE}
EOF

run_local_tests_if_available

printf '\n'
printf '%s\n' "${bold}API-only deployment summary${reset}"
printf '  Account:          %s\n' "$(gcloud auth list --filter=status:ACTIVE --format='value(account)' | head -n 1)"
printf '  Project:          %s\n' "$PROJECT_ID"
printf '  Region:           %s\n' "$REGION"
printf '  Service:          %s\n' "$API_SERVICE"
printf '  Previous revision:%s\n' " $PREVIOUS_REVISION"
printf '  Current image:    %s\n' "$CURRENT_IMAGE"
printf '  Build context:    %s\n' "$BUILD_CONTEXT"
printf '  Dockerfile:       %s\n' "$DOCKERFILE"
printf '  New image:        %s\n' "$API_IMAGE"
printf '  Worker job:       %s\n' "NOT TOUCHED"
printf '\n'

confirm "Build and deploy this API revision?" || die "Deployment cancelled."

info "Building and pushing the API image through Cloud Build"
gcloud builds submit "$BUILD_CONTEXT" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --tag "$API_IMAGE"
ok "Cloud Build completed successfully"

info "Deploying the new image to the existing API service"
DEPLOY_ATTEMPTED=1
gcloud run services update "$API_SERVICE" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --image "$API_IMAGE" \
  --max 1 \
  --max-instances 1 \
  --quiet

NEW_REVISION="$(
  gcloud run services describe "$API_SERVICE" \
    --project "$PROJECT_ID" \
    --region "$REGION" \
    --format='value(status.latestReadyRevisionName)'
)"
API_URL="$(
  gcloud run services describe "$API_SERVICE" \
    --project "$PROJECT_ID" \
    --region "$REGION" \
    --format='value(status.url)'
)"

[[ -n "$NEW_REVISION" ]] || die "Cloud Run did not report a ready revision."
[[ "$NEW_REVISION" != "$PREVIOUS_REVISION" ]] || \
  die "Cloud Run still reports the previous revision as latest ready."

ok "Cloud Run revision ${NEW_REVISION} is ready"

verify_health
verify_openapi

info "Recent API logs"
gcloud run services logs read "$API_SERVICE" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --limit 50 || warn "Could not read recent logs."

cat > "${BACKUP_DIR}/api-${TIMESTAMP}-deployed.txt" <<EOF
project=${PROJECT_ID}
region=${REGION}
service=${API_SERVICE}
previous_revision=${PREVIOUS_REVISION}
new_revision=${NEW_REVISION}
new_image=${API_IMAGE}
service_url=${API_URL}
health=passed
openapi_path=${EXPECTED_OPENAPI_PATH}
openapi_contract=passed
rollback_command=gcloud run services update-traffic ${API_SERVICE} --project ${PROJECT_ID} --region ${REGION} --to-revisions ${PREVIOUS_REVISION}=100
EOF

trap - ERR
printf '\n'
ok "API-only deployment completed"
printf '  Revision: %s\n' "$NEW_REVISION"
printf '  Image:    %s\n' "$API_IMAGE"
printf '  URL:      %s\n' "$API_URL"
printf '  Record:   %s\n' "${BACKUP_DIR}/api-${TIMESTAMP}-deployed.txt"
printf '\n'
printf '%s\n' "Rollback command:"
printf '  gcloud run services update-traffic %q --project %q --region %q --to-revisions %q\n' \
  "$API_SERVICE" "$PROJECT_ID" "$REGION" "${PREVIOUS_REVISION}=100"
