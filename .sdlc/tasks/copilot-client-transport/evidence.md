---
id: evidence-copilot-client-transport
spec: ./spec.md
status: pass
created: 2026-11-24
revision: d8c90aa230db4f720527474ef5a9b68d52b3c5af
---

# Evidence Pack: Shared Copilot client: switch, transport, error classification

## Verdict

**Overall:** pass — 3 of 3 locally available checks

## Checks that ran

| Check | Tool | Result | Detail |
|-------|------|--------|--------|
| Lint | `ruff check .github/scripts/copilot_client.py .github/tests/test_copilot_client.py` | pass | 0 violations |
| Build | `python3 -m py_compile .github/scripts/copilot_client.py .github/tests/test_copilot_client.py` | pass | Both new Python files compiled |
| Tests | `pytest -o addopts= -q` | pass | 430 passed in 24.47 seconds after rebase onto current main |
| Diff whitespace | `git diff --check HEAD^ HEAD` | pass | No whitespace errors |

## Not verified

| Lane | Why | Where it would be caught instead |
|------|-----|--------------------------------|
| Coverage | No coverage threshold or coverage runner is configured for this project | No configured gate |
| Dependency CVE | `pip-audit` is not installed and no dependency CVE scan is wired into the workflow; no dependencies changed | No configured gate |
| Secret scan | `gitleaks` is not installed locally | `.github/workflows/quality.yml` runs `gitleaks/gitleaks-action@v2` on pull requests |
| SAST | No local SAST tool or SAST workflow was found | No configured gate identified |

## For the reviewer

The rate-limit and allowance fixtures follow the pinned Copilot CLI 1.0.109 event schema and message strings; neither failure was observed live in Story 1. Replace or augment those fixtures when a real failure is first captured.

The spec records 453 added lines against the 200-line target: 223 client lines, 228 test lines, and two documentation rows. The slice retains all error branches and security-path tests in one independently deployable client change.
