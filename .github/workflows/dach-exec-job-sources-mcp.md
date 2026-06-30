Generate additional job source URLs for the DACH exec-tech workflow.

Goal:
- Read /tmp/jobs/jobs_ranked.json.
- If first-pass quality is already sufficient, do not create /tmp/jobs/extra_sources.json.
- If first-pass quality is thin, use the searxng MCP tool to discover additional publicly accessible job-board/search URLs and write /tmp/jobs/extra_sources.json in the expected schema.

Thresholds:
- Sufficient if total_deduped >= 8 and ranked source diversity >= 2.

Rules for source suggestions:
- Focus on DACH (AT/DE/CH) engineering leadership roles: Engineering Manager, Head of Engineering, Director of Engineering, VP Engineering, CTO.
- Prefer pages that are scrapeable without login and without JavaScript-only rendering.
- Prefer direct board URLs or stable search URLs that can be parsed by HTML/RSS patterns.
- Avoid duplicates of existing source names in source_stats unless clearly improved.
- Keep suggestions concise and actionable.

Output requirements:
- When thin: write /tmp/jobs/extra_sources.json with this exact JSON shape:
  {
    "triggered_because": "string",
    "new_sources": [
      {
        "name": "short_snake_case_id",
        "url": "https://...",
        "region": "AT|DE|CH|DACH",
        "rationale": "one sentence"
      }
    ],
    "source_improvements": [
      {
        "source": "existing_source_name",
        "improved_url": "https://...",
        "note": "one sentence"
      }
    ]
  }
- Write JSON only to the file (valid UTF-8).
- Do not print secrets.

Implementation notes:
- Use python for file IO and JSON serialization.
- Keep total new_sources to 3-5 items.
- If no valid suggestions are found, do not create the file.
