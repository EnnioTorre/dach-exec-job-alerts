"""
AI-powered source suggestion — runs between the first rank pass and the second scrape.

Reads  /tmp/jobs/jobs_ranked.json
Writes /tmp/jobs/extra_sources.json  (only when suggestions are produced)

Behaviour:
- If total_deduped >= ENOUGH_THRESHOLD: prints a skip message and exits 0 without
  writing extra_sources.json (scrape_extra.py will be a no-op).
- Otherwise: calls GitHub Models once with a focused prompt, asking for 3-5 new
  concrete DACH job-board URLs targeting exec tech roles that were thin/missing
  today, plus an optional improvement hint for each existing source.
- continue-on-error in the workflow — if AI fails the second pass is skipped.
"""

import json
import os
import sys
from datetime import date
from pathlib import Path

RANKED_PATH = "/tmp/jobs/jobs_ranked.json"
EXTRA_SOURCES_PATH = "/tmp/jobs/extra_sources.json"

MODELS_BASE_URL = "https://models.inference.ai.azure.com"
MODEL = "gpt-4o-mini"
MAX_TOKENS = 800

# Only call AI and attempt a second scrape when fewer than this many relevant,
# deduped listings were found in the first pass.
ENOUGH_THRESHOLD = 8
MIN_SOURCE_DIVERSITY = 2


def load_ranked() -> dict:
    with open(RANKED_PATH, encoding="utf-8") as f:
        return json.load(f)


def build_prompt(data: dict) -> str:
    today = data.get("date", str(date.today()))
    stats: dict = data.get("source_stats", {})
    total_deduped: int = data.get("total_deduped", 0)
    ranked_jobs: list[dict] = data.get("jobs", [])
    ranked_sources = sorted({j.get("source_name", "") for j in ranked_jobs if j.get("source_name")})

    stats_lines = "\n".join(
        f"  {src}: {count} listings"
        for src, count in sorted(stats.items(), key=lambda x: -x[1])
    )
    thin_sources = [src for src, count in stats.items() if count < 3]
    failed_sources = [src for src, count in stats.items() if count == 0]

    return f"""You are a DACH tech recruitment specialist. Today is {today}.

A job scraper ran and found only {total_deduped} relevant exec-level tech roles
after filtering and deduplication. This is below the quality threshold.

Ranked result source diversity: {len(ranked_sources)} distinct sources -> {ranked_sources}

Source results today:
{stats_lines}

Sources that returned 0 listings: {failed_sources}
Sources that returned fewer than 3 listings: {thin_sources}

Target roles: Software Engineering Manager, Head of Software Engineering,
Head of Platform Engineering, Head of Cloud Engineering, Director of Engineering,
Director of Platform/Cloud Engineering, VP Engineering, CTO.
Target market: DACH (Austria ≥ 50%, Germany ≥ 30%, Switzerland ≥ 20%).
Language: prefer English-friendly roles.

Suggest 3–5 alternative or additional job-board URLs to scrape.
Rules:
- Use only real, publicly accessible URLs (no login required, no JavaScript-only SPAs).
- Prefer sources with RSS or structured HTML listings over infinite-scroll pages.
- Vary the sources (do NOT repeat any source already in the list above).
- Optionally suggest an improved search query for any existing source that was thin.

Respond with JSON only, no markdown:
{{
  "new_sources": [
    {{
      "name": "short_snake_case_id",
      "url": "https://...",
      "region": "AT|DE|CH",
      "rationale": "one sentence"
    }}
  ],
  "source_improvements": [
    {{
      "source": "existing_source_name",
      "improved_url": "https://...",
      "note": "one sentence"
    }}
  ]
}}"""


def call_github_models(prompt: str) -> dict | None:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GITHUB_TOKEN not set — cannot call AI for source suggestions")
        return None

    try:
        from openai import OpenAI

        client = OpenAI(base_url=MODELS_BASE_URL, api_key=token)
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.4,
            max_tokens=MAX_TOKENS,
        )
        content = response.choices[0].message.content or ""
        return json.loads(content)
    except Exception as exc:
        print(f"AI source suggestion failed: {exc}")
        return None


def main() -> None:
    data = load_ranked()
    total_deduped: int = data.get("total_deduped", 0)
    ranked_jobs: list[dict] = data.get("jobs", [])
    ranked_sources = {j.get("source_name", "") for j in ranked_jobs if j.get("source_name")}

    if total_deduped >= ENOUGH_THRESHOLD and len(ranked_sources) >= MIN_SOURCE_DIVERSITY:
        print(
            f"Enough listings found ({total_deduped} >= {ENOUGH_THRESHOLD}) and "
            f"source diversity is acceptable ({len(ranked_sources)} >= {MIN_SOURCE_DIVERSITY}) — "
            "skipping AI source suggestions"
        )
        # Ensure no stale extra_sources.json from a previous run influences scrape_extra
        Path(EXTRA_SOURCES_PATH).unlink(missing_ok=True)
        sys.exit(0)

    print(
        f"Insufficient first-pass quality: listings={total_deduped}, "
        f"ranked_sources={len(ranked_sources)} — asking AI for additional sources …"
    )

    prompt = build_prompt(data)
    result = call_github_models(prompt)

    if not result:
        print("No AI suggestions — second scrape pass will be skipped")
        sys.exit(1)  # continue-on-error in workflow; scrape_extra will skip if file absent

    new_sources = result.get("new_sources", [])
    improvements = result.get("source_improvements", [])

    if not new_sources and not improvements:
        print("AI returned no actionable suggestions")
        sys.exit(1)

    print(f"AI suggested {len(new_sources)} new sources and {len(improvements)} improvements:")
    for s in new_sources:
        print(f"  + {s.get('name')} ({s.get('region')}): {s.get('url')}")
        print(f"    reason: {s.get('rationale')}")
    for s in improvements:
        print(f"  ~ {s.get('source')} → {s.get('improved_url')}")

    payload = {
        "triggered_because": (
            f"only {total_deduped} listings and {len(ranked_sources)} ranked sources in first pass"
        ),
        "new_sources": new_sources,
        "source_improvements": improvements,
    }
    with open(EXTRA_SOURCES_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"Wrote {EXTRA_SOURCES_PATH}")


if __name__ == "__main__":
    main()
