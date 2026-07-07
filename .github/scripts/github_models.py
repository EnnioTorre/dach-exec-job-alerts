"""
Shared GitHub Models (OpenAI-compatible) client helper.

All three AI steps (ai_enrich, ai_pre_rank_enrich, ai_suggest_sources) called
the GitHub Models endpoint with the same boilerplate: check GITHUB_TOKEN,
import the OpenAI SDK, build a client, issue one JSON-mode chat completion, and
swallow errors. That duplication lived in three places; it now lives here.

Authenticated via GITHUB_TOKEN (no separate secret needed). Returns the raw
response content string so each caller keeps its own parsing (some tolerate
truncated JSON, others json.loads directly).
"""

import os

MODELS_BASE_URL = "https://models.inference.ai.azure.com"
DEFAULT_MODEL = "gpt-4o-mini"


def complete_json(
    prompt: str,
    *,
    context: str = "AI request",
    model: str = DEFAULT_MODEL,
    max_tokens: int = 2000,
    temperature: float = 0.3,
) -> str | None:
    """
    Issue one JSON-mode chat completion and return the raw content string.

    Returns None (after logging a message that includes ``context``) when
    GITHUB_TOKEN is missing or the API call raises. Callers are responsible for
    parsing the returned content.
    """
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        print(f"GITHUB_TOKEN not set — skipping {context}")
        return None

    try:
        from openai import OpenAI  # installed by the workflow step

        client = OpenAI(base_url=MODELS_BASE_URL, api_key=token)
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content or ""
    except Exception as exc:
        print(f"{context} failed: {exc}")
        return None
