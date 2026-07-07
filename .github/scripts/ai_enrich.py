"""
Optional AI enrichment step — uses GitHub Models API (OpenAI-compatible).

Authenticated via GITHUB_TOKEN (no separate secret needed).
Rate-limited separately from Copilot agentic workflow quota.
Calls the model ONCE with a focused prompt; entire step is continue-on-error.

Reads  /tmp/jobs/jobs_ranked.json
Writes /tmp/jobs/jobs_enriched.json  (always, even on AI failure)

AI tasks:
  1. Re-rank top candidates with qualitative insight ("why apply" per role)
  2. Rate each source for quality today + suggest keep / boost / replace
"""

import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))
from github_models import complete_json

RANKED_PATH = "/tmp/jobs/jobs_ranked.json"
ENRICHED_PATH = "/tmp/jobs/jobs_enriched.json"

MAX_TOKENS = 2000


def load_ranked() -> dict:
    with open(RANKED_PATH, encoding="utf-8") as f:
        return json.load(f)


def build_prompt(data: dict) -> str:
    today = data.get("date", str(date.today()))
    jobs = data["jobs"][:15]  # send only top 15 to stay within token budget
    stats = data.get("source_stats", {})

    jobs_lines = "\n".join(
        f"{i}. {j['title']} @ {j['company']} | {j['location']} "
        f"| score={j['score']} | salary={j.get('salary_text') or 'N/A'}"
        for i, j in enumerate(jobs, 1)
    )
    stats_lines = "\n".join(
        f"  {k}: {v} listings" for k, v in sorted(stats.items(), key=lambda x: -x[1])
    )

    return f"""You are a DACH-region tech recruiter assistant. Today is {today}.

Source performance today:
{stats_lines}

Top {len(jobs)} deterministically-ranked exec tech roles:
{jobs_lines}

Tasks:
1. Re-rank these roles: select the 5-8 strongest opportunities for a senior engineering leader
   in DACH (Austria preferred). Consider company reputation, role seniority, salary potential,
   and English-friendly environment.
2. For each top role, write one concise "why apply" sentence (max 15 words).
3. Rate each source 1-5 for quality today and recommend: keep | boost | replace.
   "boost" = add more queries from this source; "replace" = swap for a better source.

Respond with JSON only, no markdown fences:
{{
  "top_jobs": [
    {{"rank": 1, "title": "...", "company": "...", "location": "...", "why_apply": "...", "ai_score": 4.2}},
    ...
  ],
  "source_analysis": [
    {{"source": "...", "listings_today": 0, "quality_rating": 3, "recommendation": "keep", "note": "..."}},
    ...
  ]
}}"""


def call_github_models(prompt: str) -> dict | None:
    content = complete_json(
        prompt, context="AI enrichment", max_tokens=MAX_TOKENS, temperature=0.3
    )
    if content is None:
        return None
    try:
        return json.loads(content)
    except Exception as exc:
        print(f"AI enrichment failed: {exc}")
        return None


def main() -> None:
    data = load_ranked()
    prompt = build_prompt(data)

    print("Calling GitHub Models API for AI enrichment …")
    result = call_github_models(prompt)

    if result:
        top = result.get("top_jobs", [])
        src_analysis = result.get("source_analysis", [])
        print(f"AI enriched: {len(top)} jobs re-ranked")
        print("Source analysis:")
        for s in src_analysis:
            print(f"  {s.get('source')}: quality={s.get('quality_rating')} → {s.get('recommendation')}")
        data["ai_enrichment"] = result
        data["ai_enriched"] = True
    else:
        print("Falling back to deterministic ranking only")
        data["ai_enriched"] = False

    with open(ENRICHED_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Wrote {ENRICHED_PATH}")


if __name__ == "__main__":
    main()
