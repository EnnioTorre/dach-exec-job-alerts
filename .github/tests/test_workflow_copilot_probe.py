"""Security posture of the manual Copilot CLI probe (ai-enrichment-reliability S1, BAC-6).
Text-assert only (`pyyaml` is not a CI dependency); comment lines are stripped first."""

import re
from pathlib import Path

_PROBE = Path(__file__).resolve().parents[1] / "workflows" / "copilot-cli-probe.yml"
_DENY = "--deny-tool='shell,write,read,url,memory'"


def _code_lines() -> list[str]:
    lines = _PROBE.read_text(encoding="utf-8").splitlines()
    return [ln for ln in lines if not ln.lstrip().startswith("#")]


def _top_level_block(lines: list[str], key: str) -> list[str]:
    idx = lines.index(f"{key}:")
    block = []
    for ln in lines[idx + 1:]:
        if ln and not ln.startswith(" "):
            break
        block += [ln.strip()] if ln.strip() else []
    return block


def test_copilot_probe_workflow_is_dispatch_only():
    lines = _code_lines()
    text = "\n".join(lines)
    assert "workflow_dispatch:" in _top_level_block(lines, "on")
    for trigger in ("schedule:", "push:", "pull_request", "workflow_run"):
        assert trigger not in text, f"probe must not trigger on {trigger!r}"


def test_copilot_probe_grants_only_contents_read_and_copilot_requests():
    lines = _code_lines()
    text = "\n".join(lines)

    assert _top_level_block(lines, "permissions") == ["contents: read", "copilot-requests: write"]
    assert "models:" not in text

    # Pinned CLI, never an unpinned install.
    assert re.search(r"@github/copilot@\d+\.\d+\.\d+", text)
    assert not re.search(r"@github/copilot(?!@\d+\.\d+\.\d+)", text)

    # No tool grants; every invocation denies all built-in tool kinds.
    for grant in ("--allow-tool", "--allow-all", "--yolo", "COPILOT_ALLOW_ALL"):
        assert grant not in text, f"probe must not grant tools via {grant!r}"
    invocations = text.count("copilot -p")
    assert invocations >= 1
    for flag in (_DENY, "--no-ask-user", "--no-auto-update"):
        assert text.count(flag) == invocations, f"{flag} must accompany every copilot -p"

    # Never print a credential.
    assert "set -x" not in text
    assert "printenv" not in text
    assert not re.search(r"\benv\s*\|", text)
    assert not re.search(r"echo[^\n]*\$\{?(GITHUB_TOKEN|GH_TOKEN|COPILOT_GITHUB_TOKEN)\b", text)
