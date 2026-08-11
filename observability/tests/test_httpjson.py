"""httpjson helper coverage (stdlib only)."""

from __future__ import annotations

import io
import urllib.error
from typing import Any

import pytest
from triage.httpjson import HttpError, get_json, post_json, request_json


def test_request_json_posts_and_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        def read(self) -> bytes:
            return b'{"ok": true}'

        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def fake_urlopen(request: Any, timeout: int = 0) -> _Resp:
        assert request.get_method() == "POST"
        assert timeout == 5
        return _Resp()

    monkeypatch.setattr("triage.httpjson.urllib.request.urlopen", fake_urlopen)
    assert post_json("https://example.test/x", {"a": 1}, {"X": "1"}, timeout=5) == {"ok": True}


def test_get_json_empty_body(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        def read(self) -> bytes:
            return b""

        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr("triage.httpjson.urllib.request.urlopen", lambda *a, **k: _Resp())
    assert get_json("https://example.test/x") == {}


def test_request_json_wraps_list(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        def read(self) -> bytes:
            return b'[{"n":1}]'

        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr("triage.httpjson.urllib.request.urlopen", lambda *a, **k: _Resp())
    assert request_json("GET", "https://example.test/x") == {"data": [{"n": 1}]}


def test_request_json_raises_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_http(*_a: Any, **_k: Any) -> Any:
        raise urllib.error.HTTPError(
            "https://example.test/x",
            500,
            "err",
            hdrs=None,  # type: ignore[arg-type]
            fp=io.BytesIO(b"nope"),
        )

    monkeypatch.setattr("triage.httpjson.urllib.request.urlopen", raise_http)
    with pytest.raises(HttpError) as exc:
        get_json("https://example.test/x")
    assert exc.value.status == 500


def test_request_json_raises_on_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_url(*_a: Any, **_k: Any) -> Any:
        raise urllib.error.URLError("dns failed")

    monkeypatch.setattr("triage.httpjson.urllib.request.urlopen", raise_url)
    with pytest.raises(HttpError) as exc:
        get_json("https://example.test/x")
    assert exc.value.status == 0
