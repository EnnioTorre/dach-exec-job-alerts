"""
Optional AI pre-ranking enrichment for raw job rows.

Uses GitHub Models API to infer and correct per-job metadata before deterministic ranking:
  - language_hint (en/de)
  - location
  - salary_text
  - relevance label (high/medium/low/reject)
  - URL quality (job_ad/listing/search/unknown)

Reads  /tmp/jobs/jobs_raw.json
Writes /tmp/jobs/jobs_raw_ai.json

This step is best-effort and safe to skip.
"""

import json
import os
from datetime import date
from pathlib import Path

MODELS_BASE_URL = "https://models.inference.ai.azure.com"
MODEL = "gpt-4o-mini"
MAX_TOKENS = 1400

RAW_IN = "/tmp/jobs/jobs_raw.json"
RAW_AI_OUT = "/tmp/jobs/jobs_raw_ai.json"

MAX_JOBS = 120
BATCH_SIZE = 30


def load_raw() -> dict:
    with open(RAW_IN, encoding="utf-8") as f:
        return json.load(f)


def _job_to_line(job_id: str, j: dict) -> str:
    return (
        f"{job_id} | title={j.get('title', '')} | company={j.get('company', '')} "
        f"| location={j.get('location', '')} | salary={j.get('salary_text', '')} "
        f"| url={j.get('application_url') or j.get('source_url') or ''}"
    )


def build_prompt(today: str, lines: list[str]) -> str:
    joined = "\n".join(lines)
    return f"""You are classifying DACH engineering leadership job postings.
Today is {today}.

For each input row:
1) Set language_hint to en or de.
2) Infer best location string (city/country in DACH) if possible.
3) Infer salary_text only if clearly present; else empty string.
4) Set relevance:
   - high: clear senior engineering leadership in software/platform/cloud
   - medium: relevant tech role but less leadership/seniority
   - low: partially related but weak fit
   - reject: non-relevant (e.g., electrical/industrial sales/quality/etc.)
5) Set url_quality:
   - job_ad: specific job posting
   - listing: category/list page
   - search: search-engine result page
   - unknown: cannot tell

Input rows:
{joined}

Return JSON only:
{{
  "updates": [
    {{
      "id": "j0",
      "language_hint": "en|de",
      "location": "...",
      "salary_text": "...",
      "relevance": "high|medium|low|reject",
      "url_quality": "job_ad|listing|search|unknown",
      "note": "short reason"
    }}
  ]
}}
"""


def call_models(prompt: str) -> dict | None:
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        print("GITHUB_TOKEN not set - skipping AI pre-rank enrichment")
        return None

    try:
        from openai import OpenAI

        client = OpenAI(base_url=MODELS_BASE_URL, api_key=token)
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=MAX_TOKENS,
        )
        content = resp.choices[0].message.content or ""
        return json.loads(content)
    except Exception as exc:
        print(f"AI pre-rank enrichment failed: {exc}")
        return None


def main() -> None:
    data = load_raw()
    jobs: list[dict] = data.get("jobs", [])
    today = data.get("date", str(date.today()))

    if not jobs:
        with open(RAW_AI_OUT, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"No jobs in input; wrote passthrough {RAW_AI_OUT}")
        return

    max_jobs = int(os.getenv("PRE_RANK_AI_MAX_JOBS", str(MAX_JOBS)))
    batch_size = int(os.getenv("PRE_RANK_AI_BATCH_SIZE", str(BATCH_SIZE)))
    rows = jobs[:max_jobs]

    id_to_idx: dict[str, int] = {}
    updates_merged: dict[str, dict] = {}

    for start in range(0, len(rows), batch_size):
        chunk = rows[start:start + batch_size]
        lines: list[str] = []
        for offset, j in enumerate(chunk):
            idx = start + offset
            jid = f"j{idx}"
            id_to_idx[jid] = idx
            lines.append(_job_to_line(jid, j))

        result = call_models(build_prompt(today, lines))
        if not result:
            continue

        for upd in result.get("updates", []):
            if not isinstance(upd, dict):
                continue
            jid = str(upd.get("id", "")).strip()
            if jid in id_to_idx:
                updates_merged[jid] = upd

    applied = 0
    for jid, upd in updates_merged.items():
        idx = id_to_idx[jid]
        j = jobs[idx]

        lang = (upd.get("language_hint") or "").strip().lower()
        if lang in {"en", "de"}:
            j["language_hint"] = lang

        loc = (upd.get("location") or "").strip()
        if loc:
            j["location"] = loc

        sal = (upd.get("salary_text") or "").strip()
        if sal and not (j.get("salary_text") or "").strip():
            j["salary_text"] = sal

        rel = (upd.get("relevance") or "").strip().lower()
        if rel in {"high", "medium", "low", "reject"}:
            j["ai_relevance"] = rel

        url_q = (upd.get("url_quality") or "").strip().lower()
        if url_q in {"job_ad", "listing", "search", "unknown"}:
            j["ai_url_quality"] = url_q

        note = (upd.get("note") or "").strip()
        if note:
            j["ai_note"] = note

        applied += 1

    data["ai_pre_rank_enriched"] = applied > 0
    data["ai_pre_rank_updates"] = applied

    with open(RAW_AI_OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"AI pre-rank updates applied: {applied}")
    print(f"Wrote {RAW_AI_OUT}")


if __name__ == "__main__":
    main()
