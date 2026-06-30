"""
Live scraper validation loop.

Runs scrape -> rank repeatedly and validates output quality.
Stops early on success, otherwise exits non-zero after max attempts.

Validation checks:
- jobs_raw.json exists and has at least MIN_RAW jobs
- jobs_ranked.json exists and has at least MIN_RANKED jobs
- at least MIN_ACTIVE_SOURCES sources returned >0 listings
- ranked output contains at least MIN_RANKED_SOURCES distinct sources
- ranked output contains at least MIN_NON_KARRIERE_RANKED_SOURCES non-Karriere sources
- ranked output contains at least MIN_NON_KARRIERE_RANKED_JOBS non-Karriere jobs
- no parser crash

Use env vars to tune:
- TEST_ATTEMPTS (default 3)
- TEST_MIN_RAW (default 5)
- TEST_MIN_RANKED (default 1)
- TEST_MIN_ACTIVE_SOURCES (default 2)
- TEST_MIN_RANKED_SOURCES (default 2)
- TEST_MIN_NON_KARRIERE_RANKED_SOURCES (default 1)
- TEST_MIN_NON_KARRIERE_RANKED_JOBS (default 1)
- TEST_AUTO_ENABLE_INSECURE_SSL_ON_FAIL (default true)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW = Path("/tmp/jobs/jobs_raw.json")
RANKED = Path("/tmp/jobs/jobs_ranked.json")


def _run(cmd: list[str], extra_env: dict[str, str] | None = None) -> tuple[int, str, str]:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT, env=env)
    return p.returncode, p.stdout, p.stderr


def _has_ssl_ca_fail(log_text: str) -> bool:
    t = log_text.lower()
    return (
        "certificate_verify_failed" in t
        or "unable to get local issuer certificate" in t
        or "ssl" in t and "verify" in t
    )


def _read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def validate(
    min_raw: int,
    min_ranked: int,
    min_active_sources: int,
    min_ranked_sources: int,
    min_non_karriere_ranked_sources: int,
    min_non_karriere_ranked_jobs: int,
) -> tuple[bool, str]:
    if not RAW.exists():
        return False, "missing /tmp/jobs/jobs_raw.json"
    if not RANKED.exists():
        return False, "missing /tmp/jobs/jobs_ranked.json"

    raw = _read_json(RAW)
    ranked = _read_json(RANKED)

    raw_jobs = raw.get("jobs", [])
    ranked_jobs = ranked.get("jobs", [])
    stats = raw.get("stats", {})
    active_sources = sum(1 for v in stats.values() if isinstance(v, int) and v > 0)
    ranked_sources = {
        j.get("source_name", "unknown")
        for j in ranked_jobs
        if j.get("source_name")
    }
    non_karriere_ranked_sources = {
        s for s in ranked_sources if not s.lower().startswith("karriere_")
    }
    non_karriere_ranked_jobs = [
        j for j in ranked_jobs if not (j.get("source_name", "").lower().startswith("karriere_"))
    ]

    if len(raw_jobs) < min_raw:
        return False, f"raw jobs too low: {len(raw_jobs)} < {min_raw}"
    if len(ranked_jobs) < min_ranked:
        return False, f"ranked jobs too low: {len(ranked_jobs)} < {min_ranked}"
    if active_sources < min_active_sources:
        return False, f"active sources too low: {active_sources} < {min_active_sources}"
    if len(ranked_sources) < min_ranked_sources:
        return False, (
            f"ranked source diversity too low: {len(ranked_sources)} < "
            f"{min_ranked_sources}"
        )
    if len(non_karriere_ranked_sources) < min_non_karriere_ranked_sources:
        return False, (
            f"non-karriere ranked source coverage too low: "
            f"{len(non_karriere_ranked_sources)} < {min_non_karriere_ranked_sources}"
        )
    if len(non_karriere_ranked_jobs) < min_non_karriere_ranked_jobs:
        return False, (
            f"non-karriere ranked jobs too low: "
            f"{len(non_karriere_ranked_jobs)} < {min_non_karriere_ranked_jobs}"
        )

    return True, (
        f"valid result: raw={len(raw_jobs)} ranked={len(ranked_jobs)} "
        f"active_sources={active_sources} ranked_sources={len(ranked_sources)} "
        f"non_karriere_ranked_sources={len(non_karriere_ranked_sources)} "
        f"non_karriere_ranked_jobs={len(non_karriere_ranked_jobs)}"
    )


def main() -> int:
    attempts = int(os.getenv("TEST_ATTEMPTS", "3"))
    min_raw = int(os.getenv("TEST_MIN_RAW", "5"))
    min_ranked = int(os.getenv("TEST_MIN_RANKED", "1"))
    min_active_sources = int(os.getenv("TEST_MIN_ACTIVE_SOURCES", "2"))
    min_ranked_sources = int(os.getenv("TEST_MIN_RANKED_SOURCES", "2"))
    min_non_karriere_ranked_sources = int(os.getenv("TEST_MIN_NON_KARRIERE_RANKED_SOURCES", "1"))
    min_non_karriere_ranked_jobs = int(os.getenv("TEST_MIN_NON_KARRIERE_RANKED_JOBS", "1"))
    auto_insecure_on_ssl = os.getenv("TEST_AUTO_ENABLE_INSECURE_SSL_ON_FAIL", "true").lower() in {"1", "true", "yes"}
    insecure_enabled = os.getenv("SCRAPER_SSL_FALLBACK_INSECURE", "false").lower() in {"1", "true", "yes"}

    os.makedirs("/tmp/jobs", exist_ok=True)

    last_reason = ""
    for i in range(1, attempts + 1):
        print(f"\n=== Validation attempt {i}/{attempts} ===")

        scrape_env = {}
        if insecure_enabled:
            scrape_env["SCRAPER_SSL_FALLBACK_INSECURE"] = "true"
        rc, out, err = _run(["python3", ".github/scripts/scrape_jobs.py"], extra_env=scrape_env)
        scrape_log = out + "\n" + err
        print(out)
        if err.strip():
            print(err)
        if rc != 0:
            last_reason = f"scrape step failed with exit {rc}"
            print(last_reason)
            continue

        rc, out, err = _run(["python3", ".github/scripts/rank_jobs.py"])
        print(out)
        if err.strip():
            print(err)
        if rc != 0:
            last_reason = f"rank step failed with exit {rc}"
            print(last_reason)
            continue

        ok, reason = validate(
            min_raw,
            min_ranked,
            min_active_sources,
            min_ranked_sources,
            min_non_karriere_ranked_sources,
            min_non_karriere_ranked_jobs,
        )
        print(f"validation: {reason}")
        if ok:
            return 0
        last_reason = reason

        # If CA trust is broken, auto-enable insecure fallback for subsequent attempts.
        if auto_insecure_on_ssl and not insecure_enabled and _has_ssl_ca_fail(scrape_log):
            insecure_enabled = True
            print("detected SSL CA validation failure; enabling insecure SSL fallback for next attempt")

    print(f"\nFAILED after {attempts} attempts: {last_reason}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
