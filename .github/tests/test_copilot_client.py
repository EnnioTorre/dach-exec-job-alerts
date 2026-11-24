import inspect
import json
import subprocess

import copilot_client as client
import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (
        "AI_ENRICHMENT",
        "AI_MODEL",
        "AI_TIMEOUT_SECONDS",
        "COPILOT_GITHUB_TOKEN",
        "GH_TOKEN",
        "GITHUB_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)


def _event(kind, data):
    return json.dumps({"type": kind, "data": data})


def _install(monkeypatch, rc=1, stdout="", stderr=""):
    captured = {}

    def run(argv, env):
        captured.update(argv=argv, env=env)
        return subprocess.CompletedProcess(argv, rc, stdout, stderr)

    monkeypatch.setattr(client, "_run_copilot", run)
    return captured


def test_ai_disabled_by_default_when_switch_unset(monkeypatch):
    assert client.ai_enabled() is False
    monkeypatch.setenv("AI_ENRICHMENT", "")
    assert client.ai_enabled() is False


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("on", True),
        ("TRUE", True),
        ("1", True),
        ("Yes", True),
        (" on ", True),
        ("0n", False),
        ("off", False),
        ("false", False),
        ("0", False),
        ("enabled", False),
        ("y", False),
    ],
)
def test_ai_enabled_only_for_explicit_on_values(monkeypatch, value, expected):
    monkeypatch.setenv("AI_ENRICHMENT", value)
    assert client.ai_enabled() is expected


def test_request_invokes_copilot_cli_not_models_endpoint(monkeypatch):
    answer = '{"updates": []}'
    output = "\n".join(
        [
            _event("assistant.message", {"content": "", "chunkIndex": 0, "chunkCount": 2}),
            _event("assistant.message", {"content": answer, "phase": "final_answer", "chunkIndex": 1}),
        ]
    )
    captured = _install(monkeypatch, rc=0, stdout=output)
    assert client.request_once("prompt", context="unit") == answer
    assert captured["argv"][0] == "copilot"
    assert captured["argv"][captured["argv"].index("-p") + 1].endswith("prompt")
    assert "--model=gpt-6-luna" in captured["argv"]
    monkeypatch.setenv("AI_MODEL", "custom-model")
    captured = _install(monkeypatch, rc=0, stdout=output)
    client.request_once("prompt", context="unit")
    assert "--model=custom-model" in captured["argv"]
    source = inspect.getsource(client)
    assert not any(term in source for term in ("models.inference.ai.azure.com", "models.github.ai", "openai"))


def test_copilot_argv_grants_no_tools():
    argv = client._build_argv("--allow-all-tools\n$(touch /tmp/x)")
    assert not any(arg.startswith("--allow") for arg in argv)
    assert not any(arg in argv for arg in ("--yolo", "--autopilot", "--available-tools"))
    assert all(
        option in argv
        for option in (
            "--deny-tool=shell,write,read,url,memory",
            "--disable-builtin-mcps",
            "--no-custom-instructions",
            "--no-ask-user",
            "--output-format=json",
        )
    )
    assert not argv[argv.index("-p") + 1].startswith("-")


def test_auth_failure_classified_as_config_error(monkeypatch):
    cases = [
        ("", "Error: No authentication information found.\n"),
        ("", "Error: Access denied by policy settings (Request ID: 123)\n"),
        (
            _event(
                "session.error",
                {"errorType": "query", "message": 'Execution failed: Model "invalid" is not available.'},
            ),
            "",
        ),
        (_event("session.error", {"errorType": "authentication", "message": "denied"}), ""),
        (_event("session.error", {"errorType": "quota", "errorCode": "billing_not_configured", "message": "x"}), ""),
    ]
    for stdout, stderr in cases:
        _install(monkeypatch, stdout=stdout, stderr=stderr)
        with pytest.raises(client.AIConfigError, match="unit.*Copilot access/config error"):
            client.request_once("prompt", context="unit")

    def missing(*_args):
        raise FileNotFoundError("copilot")

    monkeypatch.setattr(client, "_run_copilot", missing)
    with pytest.raises(client.AIConfigError, match="CLI could not be started"):
        client.request_once("prompt", context="unit")
    monkeypatch.setenv("AI_TIMEOUT_SECONDS", "abc")
    with pytest.raises(client.AIConfigError, match="AI_TIMEOUT_SECONDS"):
        client.request_once("prompt", context="unit")
    monkeypatch.setenv("AI_TIMEOUT_SECONDS", "0")
    with pytest.raises(client.AIConfigError, match="AI_TIMEOUT_SECONDS"):
        client.request_once("prompt", context="unit")


@pytest.mark.parametrize("token_name", ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"))
@pytest.mark.parametrize("is_config", (True, False))
def test_error_message_never_contains_token_value(monkeypatch, capsys, token_name, is_config):
    token = "ghs_SENTINEL_do_not_log_123"
    monkeypatch.setenv(token_name, token)
    stderr = f"Error: No authentication information found. {token}" if is_config else f"network error {token}"
    _install(monkeypatch, stderr=stderr)
    error = client.AIConfigError if is_config else client.AITransientError
    with pytest.raises(error) as caught:
        client.request_once("prompt", context="unit")
    assert token not in str(caught.value) + caught.value.cause
    assert "***" in str(caught.value)
    captured = capsys.readouterr()
    assert token not in captured.out + captured.err


@pytest.mark.parametrize(
    "stdout,stderr",
    [
        (_event("session.error", {"errorType": "quota", "errorCode": "quota_exceeded", "message": "x"}), ""),
        ("", "You've run out of your included AI credits for the month."),
    ],
)
def test_allowance_exhausted_classified_distinctly(monkeypatch, stdout, stderr):
    _install(monkeypatch, stdout=stdout, stderr=stderr)
    with pytest.raises(client.AIAllowanceExhausted, match="allowance exhausted"):
        client.request_once("prompt", context="unit")


@pytest.mark.parametrize(
    ("stdout", "stderr", "wait"),
    [
        (
            _event(
                "session.error", {"errorType": "rate_limit", "errorCode": "user_model_rate_limited", "message": "x"}
            ),
            "",
            120,
        ),
        (
            _event("session.error", {"errorType": "query", "errorCode": "user_model_rate_limited", "message": "x"}),
            "",
            120,
        ),
        (_event("session.error", {"errorType": "query", "statusCode": 429, "message": "x"}), "", 120),
        ("", "rate limit; retry after 30 seconds", 30),
        ("", "rate limit resets in 5 minutes", 300),
    ],
)
def test_rate_limit_classified_with_wait_hint(monkeypatch, stdout, stderr, wait):
    _install(monkeypatch, stdout=stdout, stderr=stderr)
    with pytest.raises(client.AIRateLimited) as caught:
        client.request_once("prompt", context="unit")
    assert caught.value.wait_seconds == wait


@pytest.mark.parametrize(
    "stdout",
    [
        "",
        _event("assistant.message", {"content": ""}),
        _event("assistant.message", {"content": "I cannot provide an answer."}),
        _event("assistant.message", {"content": '{"broken": }'}),
        "not-json",
    ],
)
def test_empty_or_prose_output_classified_unreadable(monkeypatch, stdout):
    _install(monkeypatch, rc=0, stdout=stdout)
    with pytest.raises(client.AIUnreadableResponse, match="unit"):
        client.request_once("prompt", context="unit")


def test_timeout_classified_transient_using_own_knob(monkeypatch):
    monkeypatch.setenv("AI_TIMEOUT_SECONDS", "7")
    captured = {}

    def timeout(argv, **kwargs):
        captured.update(argv=argv, **kwargs)
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(client.subprocess, "run", timeout)
    with pytest.raises(client.AITransientError, match="timed out after 7s"):
        client.request_once("prompt", context="unit")
    assert captured["timeout"] == 7
    assert captured["env"]["AI_TIMEOUT_SECONDS"] == "7"
    assert "shell" not in captured


def test_unrecognised_failure_classified_transient(monkeypatch):
    _install(monkeypatch, stderr="Error: error sending request")
    with pytest.raises(client.AITransientError, match="exit 1"):
        client.request_once("prompt", context="unit")
