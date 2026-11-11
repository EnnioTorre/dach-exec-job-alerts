"""
Score-and-pool stage: score every scraped row with the shared deterministic
scorer, then build a bounded candidate pool that guarantees each
under-represented family (linkedin / google) a minimum share drawn from its own
top-scored rows, filling the remainder strictly by global score.

Reads  /tmp/jobs/jobs_raw.json   (all deduped rows — the scrape cap was removed)
Writes /tmp/jobs/jobs_pool.json  (scored, pooled rows + pool-contribution counts)

The pool replaces the old order-based ``all_jobs[:200]`` cut (which dropped
LinkedIn/Google rows by source order before they were ever scored) with a
merit-based selection that never alters a row's score. See ADR-0003.

Fail-loud (ADR-0002): a Nominatim outage during scoring raises
``GeocodeUnavailable``; the run fails rather than shipping a poisoned pool. The
geocode cache is persisted first (``finally``) so a retry reuses resolved coords.
"""

import json
import os
from collections import Counter, defaultdict

from scoring import (
    GeocodeUnavailable,
    _load_geocode_cache,
    _save_geocode_cache,
    score_job,
)
from scrape_jobs import _count_by, _source_family

# Coarse families the pool reasons about. ``board`` (karriere.at, StepStone,
# Arbeitnow, jobs.ch, …) is never starved, so only linkedin/google get a
# guaranteed minimum; board fills purely on merit.
_MIN_FAMILIES = ("linkedin", "google")
_GOOGLE_SOURCE_PREFIXES = ("google_", "bing_", "ddg_", "ecosia_")

_INPUT_PATH = "/tmp/jobs/jobs_raw.json"
_OUTPUT_PATH = "/tmp/jobs/jobs_pool.json"


def _pool_family(job: dict) -> str:
    """Coarse pool family: ``google`` (search-proxy source), ``linkedin``
    (destination host), else ``board``. Google must be detected from the
    source_name prefix first, because a google_proxy row resolves to its real
    job host (e.g. stepstone) in ``_source_family``."""
    name = (job.get("source_name") or "").lower()
    if name.startswith(_GOOGLE_SOURCE_PREFIXES):
        return "google"
    if _source_family(job) == "linkedin":
        return "linkedin"
    return "board"


def _target_size() -> int:
    raw = os.getenv("POOL_TARGET_SIZE") or "200"
    try:
        return max(0, int(raw))
    except ValueError:
        return 200


def _family_min() -> int:
    raw = os.getenv("POOL_FAMILY_MIN") or "20"
    try:
        return max(0, int(raw))
    except ValueError:
        return 20


def _score(row: dict) -> float:
    s = row.get("score")
    return float(s) if isinstance(s, (int, float)) else 0.0


def score_all(jobs: list[dict]) -> list[dict]:
    """Score every row in place (adds ``score`` + ``pool_family``). No row is
    dropped by arrival order. Propagates ``GeocodeUnavailable`` (fail-loud)."""
    for job in jobs:
        job["score"] = score_job(job)
        job["pool_family"] = _pool_family(job)
    return jobs


def build_pool(jobs: list[dict], target: int, fam_min: int) -> tuple[list[dict], dict[int, str]]:
    """Select the pool from pre-scored rows. Returns ``(pool, phase_by_index)``
    where phase is ``"min"`` (per-family guarantee) or ``"merit"`` (global fill).

    Deterministic: ties break by original row order (stable), so runs reproduce.
    ``board`` is exempt from the minimum; an empty/short family simply yields
    fewer than ``fam_min`` rows and its slots are reclaimed by the merit fill.
    """
    indexed = list(enumerate(jobs))
    by_family: dict[str, list[tuple[int, dict]]] = defaultdict(list)
    for i, row in indexed:
        by_family[row.get("pool_family", "board")].append((i, row))

    phase_by_index: dict[int, str] = {}
    order: list[int] = []

    # Phase 1 — per-family minimum from each family's OWN top-scored rows (BAC-7).
    for family in _MIN_FAMILIES:
        ranked = sorted(by_family.get(family, []), key=lambda t: (-_score(t[1]), t[0]))
        for i, _row in ranked[:fam_min]:
            if i not in phase_by_index:
                phase_by_index[i] = "min"
                order.append(i)

    # Phase 2 — fill the remainder strictly by global score (BAC-7).
    remainder = sorted(
        (t for t in indexed if t[0] not in phase_by_index),
        key=lambda t: (-_score(t[1]), t[0]),
    )
    for i, _row in remainder[: max(0, target - len(order))]:
        phase_by_index[i] = "merit"
        order.append(i)

    index_to_row = dict(indexed)
    pool = [index_to_row[i] for i in order]
    return pool, phase_by_index


def _pool_stats(jobs: list[dict], phase_by_index: dict[int, str]) -> dict[str, dict[str, int]]:
    scored = Counter(j.get("pool_family", "board") for j in jobs)
    min_filled: Counter = Counter()
    merit_filled: Counter = Counter()
    for i, phase in phase_by_index.items():
        family = jobs[i].get("pool_family", "board")
        (min_filled if phase == "min" else merit_filled)[family] += 1
    families = set(scored) | set(min_filled) | set(merit_filled)
    return {
        family: {
            "scored": scored.get(family, 0),
            "min_filled": min_filled.get(family, 0),
            "merit_filled": merit_filled.get(family, 0),
            "contributed": min_filled.get(family, 0) + merit_filled.get(family, 0),
        }
        for family in sorted(families)
    }


def _build_envelope(raw: dict, jobs: list[dict]) -> dict:
    target, fam_min = _target_size(), _family_min()
    pool, phase_by_index = build_pool(jobs, target, fam_min)
    return {
        "date": raw.get("date"),
        "stats": raw.get("stats", {}),
        "family_stats": raw.get("family_stats", {}),
        "deduped_source_stats": raw.get("deduped_source_stats", {}),
        "jobs": pool,
        "pool_contribution": {
            "by_source": _count_by(pool, lambda j: j.get("source_name", "unknown")),
            "by_family": _count_by(pool, lambda j: j.get("pool_family", "board")),
        },
        "pool_stats": _pool_stats(jobs, phase_by_index),
        "pool_target_size": target,
        "pool_family_min": fam_min,
    }


def main() -> None:
    # Share the persistent geocode cache with the final rank (and warm it).
    _load_geocode_cache()
    try:
        with open(_INPUT_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        jobs = raw.get("jobs", [])
        print(f"Scoring {len(jobs)} rows for the pool")
        score_all(jobs)  # fail-loud: GeocodeUnavailable propagates past the finally
        envelope = _build_envelope(raw, jobs)
        os.makedirs(os.path.dirname(_OUTPUT_PATH) or ".", exist_ok=True)
        with open(_OUTPUT_PATH, "w", encoding="utf-8") as f:
            json.dump(envelope, f, indent=2, ensure_ascii=False)
        print(
            f"Wrote {_OUTPUT_PATH}: {len(envelope['jobs'])} pooled "
            f"(target {envelope['pool_target_size']}, family-min {envelope['pool_family_min']}); "
            f"contribution {envelope['pool_contribution']['by_family']}"
        )
    except GeocodeUnavailable as exc:
        # A Nominatim outage must fail the run, not ship a poisoned pool (ADR-0002).
        print(f"pool stage failed: geocoding unavailable — {exc}")
        raise
    finally:
        _save_geocode_cache()


if __name__ == "__main__":
    main()
