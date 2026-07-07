"""
Unit tests for the shared GitHub Models client helper.

The OpenAI SDK is never actually imported/called here — we exercise the two
behaviours the callers rely on: the missing-token short-circuit and the
error-swallowing contract. The SDK path is verified by monkeypatching a fake
``openai`` module into sys.modules.
"""

import sys
import types

import github_models


def test_missing_token_returns_none(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert github_models.complete_json("hi", context="unit test") is None
    assert "skipping unit test" in capsys.readouterr().out


def test_returns_content_on_success(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")

    class _Msg:
        content = '{"ok": true}'

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    class _Completions:
        def create(self, **kwargs):
            return _Resp()

    class _Chat:
        completions = _Completions()

    class _FakeClient:
        def __init__(self, *a, **k):
            self.chat = _Chat()

    fake_openai = types.ModuleType("openai")
    fake_openai.OpenAI = _FakeClient
    monkeypatch.setitem(sys.modules, "openai", fake_openai)

    assert github_models.complete_json("prompt", context="unit test") == '{"ok": true}'


def test_swallows_api_error_and_returns_none(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")

    class _FakeClient:
        def __init__(self, *a, **k):
            raise RuntimeError("boom")

    fake_openai = types.ModuleType("openai")
    fake_openai.OpenAI = _FakeClient
    monkeypatch.setitem(sys.modules, "openai", fake_openai)

    assert github_models.complete_json("prompt", context="unit test") is None
    assert "unit test failed" in capsys.readouterr().out
