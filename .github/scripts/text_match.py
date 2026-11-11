"""
Shared word-boundary keyword matchers.

Used by both the role filter (``rank_jobs``) and the relevance scorer
(``scoring``); kept in their own module so both can import them without a
circular dependency.
"""

import re


def _contains_keyword(text: str, keyword: str) -> bool:
    # Match keyword as a token/phrase, not a loose substring.
    pattern = r"\b" + re.escape(keyword).replace(r"\ ", r"\s+") + r"\b"
    return bool(re.search(pattern, text))


def _contains_any(text: str, keywords: list[str]) -> bool:
    return any(_contains_keyword(text, kw) for kw in keywords)
