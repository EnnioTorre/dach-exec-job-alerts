#!/usr/bin/env bash
#
# Local test harness for the SearXNG-backed scrape (mirrors the GitHub
# workflow's "Start SearXNG" + "Scrape jobs" steps).
#
# What it does:
#   1. Starts a local SearXNG container using .github/searxng/settings.yml
#   2. Waits until /search?format=json responds
#   3. Probes whether SearXNG can actually return upstream results
#      (this is where a corporate proxy like Zscaler typically blocks it)
#   4. Runs scrape_jobs.py with SCRAPER_SEARXNG_URL pointed at the container
#   5. Cleans up the container on exit
#
# Usage:
#   .github/scripts/run_searxng_local.sh                # start, probe, scrape
#   SEARXNG_KEEP=1 .github/scripts/run_searxng_local.sh # leave container running
#   SEARXNG_PROBE_ONLY=1 .github/scripts/run_searxng_local.sh  # just probe, no scrape
#
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SETTINGS_FILE="${ROOT_DIR}/.github/searxng/settings.yml"
ENV_FILE="${SCRAPER_ENV_FILE:-${ROOT_DIR}/.env.google-providers}"
CONTAINER="${SEARXNG_CONTAINER:-searxng-local}"
PORT="${SEARXNG_PORT:-8080}"
URL="http://localhost:${PORT}"
LOG_FILE="${1:-/tmp/jobs/scrape_searxng_local.log}"

mkdir -p /tmp/jobs

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker not found. Install Docker (or Docker Desktop / WSL integration)." >&2
  exit 2
fi
if ! docker info >/dev/null 2>&1; then
  echo "ERROR: Docker daemon is not running." >&2
  echo "  Try: sudo service docker start   (or start Docker Desktop)" >&2
  exit 2
fi
if [[ ! -f "${SETTINGS_FILE}" ]]; then
  echo "ERROR: missing ${SETTINGS_FILE}" >&2
  exit 2
fi

cleanup() {
  if [[ "${SEARXNG_KEEP:-0}" != "1" ]]; then
    echo "Cleaning up container ${CONTAINER}..."
    docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true
  else
    echo "SEARXNG_KEEP=1 set — leaving ${CONTAINER} running at ${URL}"
  fi
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Start SearXNG
# ---------------------------------------------------------------------------
docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true

echo "Starting SearXNG container '${CONTAINER}' on ${URL}..."
docker run -d --name "${CONTAINER}" \
  -p "${PORT}:8080" \
  -v "${SETTINGS_FILE}:/etc/searxng/settings.yml:ro" \
  -e "SEARXNG_SECRET=$(head -c 32 /dev/urandom | base64)" \
  searxng/searxng:latest >/dev/null

echo -n "Waiting for SearXNG JSON endpoint"
ready=""
for _ in $(seq 1 45); do
  if curl -sf "${URL}/search?q=test&format=json" >/dev/null 2>&1; then
    ready="yes"; break
  fi
  echo -n "."
  sleep 2
done
echo
if [[ -z "${ready}" ]]; then
  echo "ERROR: SearXNG did not become ready. Container logs:" >&2
  docker logs "${CONTAINER}" 2>&1 | tail -40 >&2 || true
  exit 1
fi
echo "SearXNG is up."

# ---------------------------------------------------------------------------
# Probe: can SearXNG actually return upstream results here?
#   Behind a corporate proxy (Zscaler), upstream engines are usually blocked,
#   so results will be empty even though the endpoint responds.
# ---------------------------------------------------------------------------
echo
echo "Probing upstream result availability..."
probe_count="$(curl -sf "${URL}/search?q=site:linkedin.com/jobs/view%20CTO%20Austria&format=json" \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print(len(d.get("results",[])))' 2>/dev/null || echo 0)"
echo "  SearXNG returned ${probe_count} results for a sample query."
if [[ "${probe_count}" == "0" ]]; then
  echo "  WARNING: 0 results. On this machine that usually means the corporate"
  echo "           proxy is blocking SearXNG's upstream engine calls."
  echo "           The scrape will likely fall back to Google CSE (if configured)."
fi

if [[ "${SEARXNG_PROBE_ONLY:-0}" == "1" ]]; then
  echo "SEARXNG_PROBE_ONLY=1 set — stopping before scrape."
  exit 0
fi

# ---------------------------------------------------------------------------
# Run the scrape pointed at the local SearXNG
# ---------------------------------------------------------------------------
# Load CA bundle / mode settings from env file (but override SearXNG URL).
if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi
export SCRAPER_SEARXNG_URL="${URL}"
export SCRAPER_SEARXNG_KEY=""

cd "${ROOT_DIR}"
echo
echo "Running scrape_jobs.py with SCRAPER_SEARXNG_URL=${URL}"
echo "  log: ${LOG_FILE}"
python3 .github/scripts/scrape_jobs.py 2>&1 | tee "${LOG_FILE}"

echo
echo "=== Provider usage ==="
grep -E "SearXNG:|Google CSE:|SerpAPI:|Zenserp:" "${LOG_FILE}" | head -20 \
  || echo "No provider usage lines found."

echo
echo "=== Totals ==="
grep -E "Total raw:|After raw dedup:|Google/Bing/DDG proxy sources total" "${LOG_FILE}" || true
