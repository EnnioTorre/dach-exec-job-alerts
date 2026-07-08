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
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from github_models import complete_json

# Response must hold one JSON object per input row. Keep this comfortably above
# BATCH_SIZE * (~150 tokens/row) so the model never truncates mid-JSON.
MAX_TOKENS = 4000

RAW_IN = "/tmp/jobs/jobs_raw.json"
RAW_AI_OUT = "/tmp/jobs/jobs_raw_ai.json"

MAX_JOBS = 120
BATCH_SIZE = 20


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


def _extract_json_updates(content: str) -> list[dict]:
    """
    Parse the model response into a list of update dicts.

    Tolerates truncated or fence-wrapped JSON: if the full document does not
    parse (e.g. the response was cut off mid-string by the token limit), we
    salvage every complete object from the "updates" array instead of
    discarding the whole batch.
    """
    content = (content or "").strip()
    if not content:
        return []

    # Strip a leading ```json / ``` code fence if the model added one.
    if content.startswith("```"):
        content = re.sub(r"^```[a-zA-Z]*\s*", "", content)
        content = re.sub(r"\s*```$", "", content).strip()

    # Fast path: well-formed JSON.
    try:
        data = json.loads(content)
        if isinstance(data, dict) and isinstance(data.get("updates"), list):
            return [u for u in data["updates"] if isinstance(u, dict)]
        if isinstance(data, list):
            return [u for u in data if isinstance(u, dict)]
    except json.JSONDecodeError:
        pass

    # Salvage path: scan complete {...} objects inside the updates array.
    m = re.search(r'"updates"\s*:\s*\[', content)
    idx = m.end() if m else content.find("[") + 1
    if idx <= 0:
        return []

    updates: list[dict] = []
    decoder = json.JSONDecoder()
    n = len(content)
    while idx < n:
        while idx < n and content[idx] in " \t\r\n,":
            idx += 1
        if idx >= n or content[idx] == "]":
            break
        try:
            obj, end = decoder.raw_decode(content, idx)
        except json.JSONDecodeError:
            break  # reached the truncated / incomplete tail
        if isinstance(obj, dict):
            updates.append(obj)
        idx = end
    return updates


def call_models(prompt: str) -> dict | None:
    content = complete_json(
        prompt, context="AI pre-rank enrichment", max_tokens=MAX_TOKENS, temperature=0.2
    )
    if content is None:
        return None
    updates = _extract_json_updates(content)
    if not updates:
        print("AI pre-rank enrichment: no parsable updates in response")
        return None
    return {"updates": updates}


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
        # Do NOT override the deterministic language_hint from the scraper: it
        # is derived from the actual posting body (LinkedIn guest fetch) plus
        # German gender markers / umlauts, whereas the model only sees the
        # short title/company line and reliably mislabels German postings with
        # English-looking titles as "en". Use the model only to fill a gap.
        if lang in {"en", "de"} and not (j.get("language_hint") or "").strip():
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
