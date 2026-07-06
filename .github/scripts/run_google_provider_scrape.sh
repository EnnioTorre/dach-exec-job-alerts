#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOG_FILE="${1:-/tmp/jobs/scrape_google_provider.log}"
ENV_FILE="${SCRAPER_ENV_FILE:-${ROOT_DIR}/.env.google-providers}"

mkdir -p /tmp/jobs

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing env file: ${ENV_FILE}" >&2
  echo "Create it from ${ROOT_DIR}/.env.google-providers.example" >&2
  exit 2
fi

set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

cd "${ROOT_DIR}"

echo "Running provider-based Google scrape..."
echo "  env file: ${ENV_FILE}"
echo "  log file: ${LOG_FILE}"

python3 .github/scripts/scrape_jobs.py 2>&1 | tee "${LOG_FILE}"

echo
if grep -q "Google CSE:\|SerpAPI:\|Zenserp:\|SearXNG:" "${LOG_FILE}"; then
  echo "Provider usage detected:"
  grep "Google CSE:\|SerpAPI:\|Zenserp:\|SearXNG:" "${LOG_FILE}" | head -20
else
  echo "No provider usage detected. Check provider credentials in ${ENV_FILE}."
fi

echo
if grep -q "No provider result; skipping direct Google fetch (provider_only mode)" "${LOG_FILE}"; then
  echo "Some google_proxy sources had no provider result."
fi

echo
if grep -q "Google/Bing/DDG proxy sources total" "${LOG_FILE}"; then
  grep -E "Google/Bing/DDG proxy sources total|LinkedIn destination ADs|Karriere.at destination ADs|Total raw:|After raw dedup:" "${LOG_FILE}" || true
fi
