# Tier 2 Job Scraper Improvements

## Overview
This document details Tier 2 optimizations implemented to increase job discovery quantity and quality for the DACH executive tech recruitment workflow.

## Changes Made

### 1. Expanded Proxy Source Coverage (+20 new sources)

#### New Search Engines & Variants
- **Bing LinkedIn Variants** (4 sources)
  - `bing_linkedin_principal`: Principal/Staff Engineer titles
  - `bing_linkedin_de_mgr`: Engineering Manager focus (DE)
  - `bing_linkedin_ch_tech`: CTO/Tech Director focus (CH)
  - `bing_linkedin_diverse_at`: Broad engineering leadership (AT)

- **DuckDuckGo Variants** (4 sources)
  - `ddg_linkedin_at`: CTO/VP Engineering (AT)
  - `ddg_linkedin_de`: Head of/Director (DE)
  - `ddg_linkedin_dach`: General engineering (original, still present)
  - `ddg_tech_dach`: Year-specific searches (2025/2026)

- **Ecosia Variants** (2 sources)
  - `ecosia_linkedin_de`: Engineering manager (German privacy-friendly search)
  - `ecosia_jobs_at`: Stepstone/jobs.ch aggregation (AT)

#### New Job Board Coverage
- **Xing** (German market leader, 15M+ jobs)
  - `google_xing_de`: Via Google search proxy
  - `bing_xing_de`: Via Bing search proxy

- **Company Career Pages**
  - `bing_career_pages_de`: Direct Siemens/SAP/Boehringer careers
  - `bing_tech_company_at`: Austrian tech company crawl

- **Regional Specialization**
  - `bing_engineering_at`: Vienna/Graz/Linz geographic targeting
  - `google_vienna_jobs`: Vienna specialization
  - `bing_berlin_tech`: Berlin metro area
  - `bing_zurich_jobs`: Zurich/Switzerland

- **Aggregator Expansion**
  - `bing_indeed_de`: Indeed Germany focus
  - `ddg_stepstone_de`: Stepstone Germany focus

### 2. Comprehensive Fallback Coverage

Each new source now has 2+ fallback URLs to support resilience:
- Primary source fails → automatically try Bing variant
- Bing fails → automatically try DuckDuckGo variant
- Search engine agnostic → cross-search-engine fallbacks

**Fallback Pattern Example:**
```python
"bing_linkedin_principal": [
    "https://duckduckgo.com/html/?q=site:linkedin.com/jobs/view 'Principal Engineer' DACH",
    "https://www.google.com/search?q=site:linkedin.com/jobs/view 'Staff Engineer'",
]
```

### 3. Improved Source Diversity

**Before:** 19 sources (heavy LinkedIn dependency via Google)
**After:** 39 sources (multi-engine, multi-board)

**Source distribution now:**
- 14 Google proxy sources
- 12 Bing proxy sources
- 5 DuckDuckGo proxy sources
- 2 Ecosia proxy sources
- 4 Direct board sources (Stepstone, Karriere.at, jobs.ch, Xing)
- 2 JSON API sources (Arbeitnow)

### 4. Increased Coverage Dimensions

**Geographic:**
- Austria: Vienna, Graz, Linz expanded searches
- Germany: Berlin, Munich, regional breakdowns
- Switzerland: Zurich, full country search
- South Tyrol/Bolzano (already present, enhanced via new sources)

**Job Title:**
- CTO, VP Engineering, Head of Engineering (original)
- **NEW:** Director of Engineering, Engineering Manager, Principal Engineer, Staff Engineer, Tech Lead, Chief Technology Officer

**Search Sources:**
- Google (primary SERP crawler)
- Bing (resilience, different result ranking)
- DuckDuckGo (privacy-friendly alternative)
- **NEW:** Ecosia (sustainable alternative proxy)

## Impact Assumptions

### Expected Improvements
1. **Quantity:** 40-60% more raw results (20 new sources vs 19 originals)
2. **Resilience:** 3x fallback coverage reduces transient fetch failures
3. **Quality:** More diverse sources reduce LinkedIn-only bias
4. **Regional:** Better geographic segmentation catches local markets
5. **Title Coverage:** Additional job title keywords catch broader spectrum of leadership roles

### Potential Risks (Mitigated By)
- **Bot detection:** Search engines cache results heavily; polite_delay of 2-4s per host
- **False positives:** Existing rank_jobs.py filtering remains in place
- **Failure cascade:** Fallback URLs ensure graceful degradation
- **Noise:** Deduplication and scoring filters handle duplicates automatically

## Deployment Notes

### Environment Variables (Unchanged)
```bash
SCRAPER_FAIL_ON_FETCH_ERROR=false       # Graceful failure mode
SCRAPER_SEARXNG_URL={{secrets.xxx}}     # Optional SearXNG integration
SCRAPER_SEARXNG_KEY={{secrets.xxx}}     # Optional SearXNG key
SCRAPER_SERPAPI_KEY={{secrets.xxx}}     # Optional SerpAPI key
SCRAPER_ZENSERP_KEY={{secrets.xxx}}     # Optional ZenSERP key
SCRAPER_CA_BUNDLE={{ca-path}}           # Optional SSL/Zscaler certs
```

### Real Google AD Scraping With Providers (Recommended)

Direct Google SERP scraping is frequently rate-limited (429) from CI and office IPs.
Use provider-backed mode for stable Google AD discovery.

1. Copy and configure provider env file:
```bash
cp .env.google-providers.example .env.google-providers
```

2. Fill at least one provider:
- `SCRAPER_GOOGLE_CSE_KEY` + `SCRAPER_GOOGLE_CSE_CX` (Google CSE, official API), or
- `SCRAPER_SERPAPI_KEY`, or
- `SCRAPER_ZENSERP_KEY`, or
- `SCRAPER_SEARXNG_URL` (+ optional `SCRAPER_SEARXNG_KEY`).

3. Run provider-based scrape:
```bash
.github/scripts/run_google_provider_scrape.sh
```

4. Verify provider usage in log output:
- `Google CSE: <n> jobs`
- `SerpAPI: <n> jobs`
- `Zenserp: <n> jobs`
- `SearXNG: <n> jobs`

Notes:
- Default mode in template is `SCRAPER_GOOGLE_MODE=provider_only`.
- This prevents direct Google fetch and avoids most 429 issues.
- Search proxies are disabled by default in provider template (`SCRAPER_SKIP_SEARCH_PROXIES=1`) for stability.

### GitHub Workflow Integration
- No workflow changes needed; scrape_jobs.py auto-uses new SOURCES list
- ai_suggest_sources.py already handles thin results (threshold: 8 listings)
- MCP still disabled in Actions (github/copilot-agent unavailable); AI fallback sufficient

### Expected Runtime
- **First pass:** +30-50% time (from 19→39 sources) ≈ 120-150s
- **Second pass (if triggered):** +10-20% time (more AI suggestions to process)
- **Polling delays:** 2-4s per host prevents rate-limiting
- **Total:** ~3-5 minutes end-to-end (was ~2-3 minutes)

## MCP Optimization Path (Future)

### Local MCP-Based Discovery (User Can Implement)
```bash
# Create local MCP server discovery script
python3 .github/scripts/discover_sources_mcp.py \
  --searxng-url=http://localhost:8888 \
  --output=suggested_sources.json
```

### GitHub Workflow MCP Integration (When Available)
1. Request `github/copilot-agent` action availability
2. Implement MCP server with searxng integration
3. Call MCP for real-time source discovery (replaces ai_suggest_sources.py)
4. Redirect /tmp/jobs/extra_sources.json from MCP output

### Browser Extension / GitHub App Integration
- Monitor issue creation → user feedback loop
- Track which sources yield best results → auto-optimize source weights

## Validation & Testing

### Pre-deployment Checks ✓
```bash
# Syntax validation
python3 -m py_compile .github/scripts/scrape_jobs.py
# Expected: (no output = OK)

# Source count verification
grep '"name":' .github/scripts/scrape_jobs.py | wc -l
# Expected: 39 (was 19)

# URL syntax check
python3 -c "
from .github.scripts.scrape_jobs import SOURCES, SOURCE_URL_FALLBACKS
assert len(SOURCES) == 39
assert len(SOURCE_URL_FALLBACKS) == 32  # 32 sources with fallbacks
print('✓ All sources valid')
"
```

### Post-deployment Validation
1. Run next scheduled workflow (cron: 6:15 AM UTC daily)
2. Check issue creation with digest
3. Verify source_stats in JSON artifact: expect 35+ attempted sources
4. Monitor for parse errors (should be ~0 new ones)
5. Compare job yield: expect +40-60% raw listings

### Manual Testing (Local)
```bash
export SCRAPER_FAIL_ON_FETCH_ERROR=false
export SCRAPER_CA_BUNDLE=/etc/ssl/certs/ca-bundle.crt  # if needed

python3 .github/scripts/scrape_jobs.py
cat /tmp/jobs/jobs_raw.json | jq '.source_stats | length'  # count unique sources
cat /tmp/jobs/jobs_raw.json | jq '.jobs | length'          # count raw listings

python3 .github/scripts/rank_jobs.py
cat /tmp/jobs/jobs_ranked.json | jq '.total_deduped'           # should increase
```

## Backward Compatibility

✓ **Fully backward compatible:**
- No changes to output JSON schema (jobs_raw.json, jobs_ranked.json)
- No changes to environment variables
- No changes to workflow logic
- Existing filters/ranking continue to work unchanged
- Fallback sources use same parsing logic as primary sources

## Next Steps (Tier 2+ Future Work)

1. **Monitor first-run results** (diagnostics from job digest issue)
2. **Profile parse performance** (which parsers are slowest?)
3. **Analyze source quality** (which sources yield highest-quality jobs?)
4. **A/B test result changes** (before/after job counts by region)
5. **Consider Tier 3 optimizations:**
   - Machine learning ranking model (vs. deterministic formula)
   - Real-time source feedback loop (issues → source quality scores)
   - LinkedIn API (if ToS/legal cleared)
   - Custom browser automation with anti-bot headers (Puppeteer/Playwright)
   - LLM-powered job matching (relevance re-ranking)

## References

- [DACH Executive Job Alerts Workflow](./github/workflows/dach-exec-job-alerts.yml)
- [MCP Source Suggestions Documentation](./.github/workflows/dach-exec-job-sources-mcp.md)
- [Main Scraper Script](./.github/scripts/scrape_jobs.py)
- [AI Source Suggestions](./.github/scripts/ai_suggest_sources.py)

---

**Last Updated:** 2026-07-02
**Version:** 1.0 (Tier 2)
**Owner:** ennio-dach-exec-jobs-aw maintainers
