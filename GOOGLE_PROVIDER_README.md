# Google Provider Setup

This guide shows how to enable provider-backed Google scraping for the DACH job scraper.

## Why this exists

Direct Google scraping is often rate-limited in this environment. The scraper supports provider-backed Google lookup so it can keep finding LinkedIn and career-page jobs without relying on direct SERP fetches.

## Step-by-step setup

### 1. Copy the example env file

```bash
cp .env.google-providers.example .env.google-providers
```

### 2. Open the new env file

Edit `.env.google-providers` and fill in your values.

### 3. Keep Google in provider mode

Make sure this stays set:

```bash
SCRAPER_GOOGLE_MODE=provider_only
```

This tells the scraper to use provider APIs only and avoid direct Google SERP requests.

### 4. Pick at least one provider

Fill in at least one of these options:

- `SCRAPER_GOOGLE_CSE_KEY` and `SCRAPER_GOOGLE_CSE_CX` for Google Custom Search
- `SCRAPER_SERPAPI_KEY` for SerpAPI
- `SCRAPER_ZENSERP_KEY` for Zenserp
- `SCRAPER_SEARXNG_URL` and optionally `SCRAPER_SEARXNG_KEY` for SearXNG

You only need one provider to start.

### 5. Add the credentials to GitHub Actions

If you want the scheduled Action to use these providers, add the same values as repository secrets:

- `SCRAPER_GOOGLE_CSE_KEY`
- `SCRAPER_GOOGLE_CSE_CX`
- `SCRAPER_SERPAPI_KEY`
- `SCRAPER_ZENSERP_KEY`
- `SCRAPER_SEARXNG_URL`
- `SCRAPER_SEARXNG_KEY`
- `SCRAPER_CA_BUNDLE` if the runner needs a custom trust store

In GitHub, go to `Settings` > `Secrets and variables` > `Actions` > `New repository secret`.

If you manage the workflow YAML yourself, pass the secrets into the scrape step like this:

```yaml
env:
  SCRAPER_GOOGLE_MODE: provider_only
  SCRAPER_AVOID_GOOGLE_FALLBACKS: "1"
  SCRAPER_SKIP_SEARCH_PROXIES: "1"
  SCRAPER_FAIL_ON_FETCH_ERROR: "false"
  SCRAPER_ENRICH_MAX: "0"
  SCRAPER_GOOGLE_CSE_KEY: ${{ secrets.SCRAPER_GOOGLE_CSE_KEY }}
  SCRAPER_GOOGLE_CSE_CX: ${{ secrets.SCRAPER_GOOGLE_CSE_CX }}
  SCRAPER_SERPAPI_KEY: ${{ secrets.SCRAPER_SERPAPI_KEY }}
  SCRAPER_ZENSERP_KEY: ${{ secrets.SCRAPER_ZENSERP_KEY }}
  SCRAPER_SEARXNG_URL: ${{ secrets.SCRAPER_SEARXNG_URL }}
  SCRAPER_SEARXNG_KEY: ${{ secrets.SCRAPER_SEARXNG_KEY }}
  SCRAPER_CA_BUNDLE: ${{ secrets.SCRAPER_CA_BUNDLE }}
```

### 6. Keep the stability defaults on

These settings are recommended in this workspace:

```bash
SCRAPER_AVOID_GOOGLE_FALLBACKS=1
SCRAPER_SKIP_SEARCH_PROXIES=1
SCRAPER_FAIL_ON_FETCH_ERROR=false
SCRAPER_ENRICH_MAX=0
```

### 7. Run the scraper

```bash
.github/scripts/run_google_provider_scrape.sh
```

The script loads `.env.google-providers`, runs the scraper, and writes output to `/tmp/jobs/jobs_raw.json`.

### 8. Check the log output

Successful provider usage will show one of these lines:

- `Google CSE: <n> jobs`
- `SerpAPI: <n> jobs`
- `Zenserp: <n> jobs`
- `SearXNG: <n> jobs`

If you see:

```text
No provider result; skipping direct Google fetch (provider_only mode)
```

then no provider credentials were picked up.

## Recommended Google provider choice

If you want the simplest setup, start with Google Custom Search:

1. Create a Google Custom Search engine.
2. Copy the API key into `SCRAPER_GOOGLE_CSE_KEY`.
3. Copy the search engine ID into `SCRAPER_GOOGLE_CSE_CX`.
4. Run the scraper again.

## Troubleshooting

- If Google results are still zero, confirm `.env.google-providers` exists and contains real values.
- If SSL errors appear, keep `SCRAPER_CA_BUNDLE` pointed at the local CA bundle for this environment.
- If the run is slow, leave `SCRAPER_SKIP_SEARCH_PROXIES=1` enabled while testing provider mode.

## Related files

- `.env.google-providers.example`
- `.github/scripts/run_google_provider_scrape.sh`
- `.github/scripts/scrape_jobs.py`
- `TIER2_IMPROVEMENTS.md`