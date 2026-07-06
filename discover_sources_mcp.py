#!/usr/bin/env python3
"""
Local MCP-powered source discovery for DACH executive tech jobs.

This script uses the Copilot agent (or local LLM) to discover additional
job sources beyond what's codified in scrape_jobs.py.

Usage:
    python3 discover_sources_mcp.py [--output suggested_sources.json]

Requirements:
    - GITHUB_TOKEN environment variable (for Copilot API)
    - or: gh CLI installed and authenticated
    - or: LOCAL_LLM_URL pointing to local LLM endpoint

Output:
    - suggested_sources.json with structure matching extra_sources.json schema
"""

import json
import os
import sys
from pathlib import Path
from datetime import date

# Try importing openai for GitHub Models API
try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False


MODELS_BASE_URL = "https://models.inference.ai.azure.com"
MODEL = "gpt-4o-mini"
MAX_TOKENS = 2000

OUTPUT_FILE = Path("suggested_sources.json")


def discover_via_github_models(prompt: str) -> dict | None:
    """Query GitHub Models (via Azure) for source suggestions."""
    if not HAS_OPENAI:
        print("⚠ openai package not installed; install: pip install openai")
        return None

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("❌ GITHUB_TOKEN not set (required for GitHub Models)")
        return None

    try:
        client = OpenAI(base_url=MODELS_BASE_URL, api_key=token)
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.5,  # slightly higher than auto-suggestions for creativity
            max_tokens=MAX_TOKENS,
        )
        content = response.choices[0].message.content or ""
        return json.loads(content)
    except Exception as exc:
        print(f"❌ GitHub Models API failed: {exc}")
        return None


def discover_via_local_llm(prompt: str, url: str) -> dict | None:
    """Query a local LLM endpoint for source suggestions."""
    try:
        import requests
        response = requests.post(
            f"{url}/v1/chat/completions",
            json={
                "model": "local",
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
                "temperature": 0.5,
                "max_tokens": MAX_TOKENS,
            },
            timeout=30,
        )
        if response.status_code == 200:
            data = response.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            return json.loads(content) if content else None
    except Exception as exc:
        print(f"❌ Local LLM failed: {exc}")
    return None


def build_discovery_prompt() -> str:
    """Build a comprehensive source discovery prompt."""
    today = date.today()
    return f"""You are a DACH tech recruitment specialist with deep knowledge of job boards.

Today is {today}. A job scraper for executive technology roles in DACH (Austria, Germany, Switzerland) 
is being optimized. We have a baseline of 39 job sources covering LinkedIn, Indeed, Stepstone, Xing, 
Arbeitnow, and various search engine proxies.

Your task: Suggest 5-10 ADDITIONAL or LESSER-KNOWN job sources that:

1. Are publicly accessible (no login wall required)
2. Cover DACH markets (AT≥50%, DE≥30%, CH≥20%)
3. Focus on executive/technical leadership roles:
   - CTO, VP Engineering, Head of Engineering/Platform/Cloud
   - Director of Engineering, Engineering Manager, Principal Engineer, Staff Engineer
4. Have scrapeable HTML or RSS feeds (not JS-only SPAs if possible)
5. Are regionally diverse (not just Vienna, Berlin, Zurich)

Examples of good sources to consider:
- Regional job boards (e.g., Kununu, Glassdoor, Gehalt.de for DACH)
- Startup job boards (e.g., AngelList, Wellfound, Product Hunt)
- Industry-specific boards (e.g., SaaS-specific, fintech job boards)
- Professional networks (e.g., The Dots, LinkedIn [alternate access], XING [covered but check for variants])
- Tech community sites (dev.to, techcrunch jobs, hacker news / whoishiring threads)
- Niche DACH boards (e.g., Austria-specific startup job boards, Munich tech boards)
- Public APIs (Github jobs [if still active], RemoteOK, etc.)
- University/research institution job boards (high-caliber candidates)
- Government/public sector tech roles (e.g., Austria's e-governance modernization, German digital ministry)
- Recruiter aggregators or job feed consolidators

For each suggestion provide:
- Source name (short, snake_case, max 40 chars)
- URL (direct to job search, ideally with query pre-filled for "CTO" OR "VP Engineering" etc.)
- Region (AT, DE, CH, or DACH for multi-region)
- Rationale (why this covers a gap in the current source list)
- Parser hint (if known: "html", "rss", "json_api", or "search_proxy")

RESPOND WITH VALID JSON ONLY (no markdown):
{{
  "suggested_sources": [
    {{
      "name": "source_name",
      "url": "https://...",
      "region": "AT|DE|CH|DACH",
      "rationale": "one sentence on why this fills a gap",
      "parser_hint": "html|rss|json_api|search_proxy"
    }}
  ],
  "notes": "optional strategic observations about source gaps or opportunities"
}}

Prioritize sources that are:
1. NOT already covered by LinkedIn proxy + search engine variants
2. High-quality job listings (executive/leadership tier)
3. Bot-friendly or easily scrapeable
4. Actively maintained (not abandoned/dead boards)
"""


def main() -> None:
    """Discover and output suggested sources."""
    output_file = Path(os.environ.get("DISCOVER_OUTPUT_FILE", OUTPUT_FILE))

    print(f"🔍 Starting source discovery ({date.today()})")
    print(f"📍 Target: DACH executive tech jobs")
    print(f"📤 Output: {output_file}")
    print()

    prompt = build_discovery_prompt()
    result = None

    # Try GitHub Models first
    if os.environ.get("GITHUB_TOKEN"):
        print("🚀 Attempting GitHub Models API (gpt-4o-mini)...")
        result = discover_via_github_models(prompt)

    # Fall back to local LLM if available
    if not result and (local_url := os.environ.get("LOCAL_LLM_URL")):
        print(f"🚀 Attempting local LLM at {local_url}...")
        result = discover_via_local_llm(prompt, local_url)

    if not result:
        print("❌ No discovery backend available")
        print("   Set GITHUB_TOKEN (for Copilot) or LOCAL_LLM_URL (for local LLM)")
        sys.exit(1)

    # Parse and validate
    sources = result.get("suggested_sources", [])
    notes = result.get("notes", "")

    if not sources:
        print("⚠ No sources suggested by LLM")
        sys.exit(1)

    print(f"✅ {len(sources)} sources discovered:")
    for src in sources:
        print(f"  • {src.get('name')} ({src.get('region')})")
        print(f"    → {src.get('rationale')}")
        print(f"    🔗 {src.get('url')}")

    if notes:
        print(f"\n📝 Notes: {notes}")

    # Write output
    output = {
        "generated_date": str(date.today()),
        "method": "local_mcp_discovery",
        "suggested_sources": sources,
        "notes": notes,
    }

    with open(output_file, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Wrote {output_file}")
    print("\n📋 Next steps:")
    print(f"  1. Review {output_file}")
    print(f"  2. Add validated sources to scrape_jobs.py SOURCES list")
    print(f"  3. Add fallback URLs to SOURCE_URL_FALLBACKS")
    print(f"  4. Test: python3 .github/scripts/scrape_jobs.py")


if __name__ == "__main__":
    main()
