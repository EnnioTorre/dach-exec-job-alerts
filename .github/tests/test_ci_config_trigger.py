"""
Guards the CI trigger that makes config-only PRs run the test suite.

Story 10 (`ci-validate-config`): `tests.yml` must trigger `pytest` on changes
under `.github/config/**` so an invalid `position_filter.json` edit fails the PR
(via the Story-1 validator) rather than only the nightly run. This test fails if
that trigger glob is ever dropped. Text-assert only — `pyyaml` is not a CI dependency.
"""

from pathlib import Path

_TESTS_WORKFLOW = Path(__file__).resolve().parents[1] / "workflows" / "tests.yml"
_CONFIG_GLOB = ".github/config/**"


def test_tests_workflow_triggers_on_config_paths():
    text = _TESTS_WORKFLOW.read_text(encoding="utf-8")

    # The pull_request trigger is the BAC-12 gate: a config-only PR must run CI.
    pr_block = text.split("pull_request:", 1)[1].split("workflow_dispatch:", 1)[0]
    assert _CONFIG_GLOB in pr_block, (
        f"{_CONFIG_GLOB!r} missing from the pull_request trigger paths in tests.yml"
    )

    # push and pull_request lists are kept symmetric — the glob is in both.
    assert text.count(_CONFIG_GLOB) >= 2, (
        f"{_CONFIG_GLOB!r} should be in both the push and pull_request trigger paths"
    )
