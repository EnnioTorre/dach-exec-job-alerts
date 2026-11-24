"""Shared Copilot CLI transport; inert until stage wiring in S4 (retry policy in S3)."""

import json
import os
import re
import subprocess

_DEFAULT_MODEL = "gpt-6-luna"
_DEFAULT_TIMEOUT_SECONDS = 300
_DEFAULT_RATE_LIMIT_WAIT_SECONDS = 120
_TOKEN_VARS = ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")
_ENABLED_VALUES = {"on", "true", "1", "yes"}
_CONFIG_MARKERS = (
    "no authentication information found",
    "authentication failed",
    "access denied by policy settings",
    "not licensed to use copilot",
    "not authorized to use this copilot feature",
    "does not have copilot requests",
    "trials have been temporarily paused",
    "copilot pro trial paused",
)
_QUOTA_CODES = {
    "quota_exceeded",
    "session_quota_exceeded",
    "free_quota_exceeded",
    "custom_quota_exceeded",
    "overage_limit_reached",
    "additional_spend_limit_reached",
}


def _scrub(value: str) -> str:
    for name in _TOKEN_VARS:
        token = os.environ.get(name)
        if token:
            value = value.replace(token, "***")
    return value


class AIError(RuntimeError):
    def __init__(self, context: str, cause: str):
        self.context = _scrub(str(context))
        self.cause = _scrub(str(cause))
        super().__init__(_scrub(f"{self.context}: {self.cause}"))


class AITransientError(AIError):
    pass


class AIRateLimited(AIError):
    def __init__(self, context: str, cause: str, wait_seconds: int):
        self.wait_seconds = max(1, wait_seconds)
        super().__init__(context, cause)


class AIConfigError(AIError):
    pass


class AIAllowanceExhausted(AIError):
    pass


class AIUnreadableResponse(AIError):
    pass


def ai_enabled() -> bool:
    return os.getenv("AI_ENRICHMENT", "").strip().lower() in _ENABLED_VALUES


def _timeout_seconds(env: dict[str, str]) -> int:
    raw = env.get("AI_TIMEOUT_SECONDS", str(_DEFAULT_TIMEOUT_SECONDS))
    try:
        timeout = int(raw)
    except (TypeError, ValueError):
        raise ValueError("AI_TIMEOUT_SECONDS must be a positive integer") from None
    if timeout <= 0:
        raise ValueError("AI_TIMEOUT_SECONDS must be a positive integer")
    return timeout


def _build_argv(prompt: str) -> list[str]:
    model = os.getenv("AI_MODEL", "").strip() or _DEFAULT_MODEL
    wrapped = "Answer with a single JSON object only: no prose, no markdown code fences, no tool use.\n\n" + prompt
    return [
        "copilot",
        "-p",
        wrapped,
        "-s",
        "--output-format=json",
        "--no-ask-user",
        "--no-auto-update",
        "--disable-builtin-mcps",
        "--no-custom-instructions",
        "--deny-tool=shell,write,read,url,memory",
        f"--model={model}",
    ]


def _run_copilot(argv: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        env=env,
        capture_output=True,
        text=True,
        timeout=_timeout_seconds(env),
        check=False,
    )


def _records(stdout: str) -> tuple[list[dict], str | None]:
    records = []
    answer = None
    for line in stdout.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        records.append(record)
        data = record.get("data")
        content = data.get("content") if isinstance(data, dict) else None
        if record.get("type") == "assistant.message" and isinstance(content, str) and content.strip():
            answer = content.strip()
    return records, answer


def _rate_wait(text: str) -> int:
    match = re.search(
        r"\b(?:in|after)\s+~?(\d+)\s*(seconds?|secs?|s|minutes?|mins?|m)\b",
        text,
        re.IGNORECASE,
    )
    if not match:
        return _DEFAULT_RATE_LIMIT_WAIT_SECONDS
    amount = int(match.group(1))
    unit = match.group(2).lower()
    return max(1, amount * (60 if unit.startswith("m") else 1))


def request_once(prompt: str, *, context: str) -> str:
    env = os.environ.copy()
    try:
        _timeout_seconds(env)
    except ValueError as exc:
        raise AIConfigError(context, str(exc)) from None
    try:
        result = _run_copilot(_build_argv(prompt), env)
    except subprocess.TimeoutExpired:
        raise AITransientError(
            context, f"Copilot CLI timed out after {env.get('AI_TIMEOUT_SECONDS', _DEFAULT_TIMEOUT_SECONDS)}s"
        ) from None
    except OSError as exc:
        reason = exc.strerror or "unable to launch Copilot CLI"
        raise AIConfigError(context, f"Copilot CLI could not be started: {reason}") from None

    records, answer = _records(result.stdout or "")
    if result.returncode == 0:
        if answer:
            try:
                parsed = json.loads(answer)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                return answer
            reason = "unreadable Copilot response: no JSON object in answer"
        else:
            reason = "unreadable Copilot response: no answer"
        raise AIUnreadableResponse(context, reason)

    errors = [
        data
        for record in records
        if record.get("type") == "session.error" and isinstance((data := record.get("data")), dict)
    ]
    messages = [str(error["message"]) for error in errors if error.get("message")]
    text = "\n".join(messages + [result.stderr or ""]).lower()
    codes = {str(error.get("errorCode", "")).lower() for error in errors}
    types = {str(error.get("errorType", "")).lower() for error in errors}
    statuses = {error.get("statusCode") for error in errors}
    cause_line = next(
        (line.strip()[:300] for line in (messages + (result.stderr or "").splitlines()) if line.strip()), ""
    )

    config = types & {"authentication", "authorization", "policy"}
    config = config or "billing_not_configured" in codes
    config = config or any(marker in text for marker in _CONFIG_MARKERS)
    config = config or re.search(r'model ".*?" is not available', text, re.DOTALL) is not None
    if config:
        cause = f"Copilot access/config error: {cause_line} (check COPILOT_GITHUB_TOKEN/GH_TOKEN/GITHUB_TOKEN, workflow permission copilot-requests: write, AI_MODEL)"
        raise AIConfigError(context, cause)

    if (
        "quota" in types
        or codes & _QUOTA_CODES
        or any(
            phrase in text
            for phrase in (
                "run out of your included ai credits",
                "additional usage limit",
                "used all your copilot free",
                "spending limit",
            )
        )
    ):
        raise AIAllowanceExhausted(context, f"Copilot AI-credit allowance exhausted: {cause_line}")

    if (
        "rate_limit" in types
        or any(code.endswith("rate_limited") for code in codes)
        or 429 in statuses
        or "rate limit" in text
        or "rate-limit" in text
    ):
        wait = _rate_wait(text)
        raise AIRateLimited(context, f"Copilot rate limited (wait {wait}s): {cause_line}", wait)

    cause = f"Copilot CLI failed (exit {result.returncode}): {cause_line or 'unknown error'}"
    raise AITransientError(context, cause)
