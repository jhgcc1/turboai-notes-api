"""Deployed-origin allowlist guards for CORS / CSRF trusted origins."""

from __future__ import annotations

import pytest

from config.settings import _assert_deployed_origin_allowlist


def test_local_skips_allowlist_guard() -> None:
    _assert_deployed_origin_allowlist("local", "CORS_ALLOWED_ORIGINS", ["http://localhost:3000"])
    _assert_deployed_origin_allowlist("local", "CORS_ALLOWED_ORIGINS", [])


def test_staging_requires_non_empty() -> None:
    with pytest.raises(ValueError, match="must be set"):
        _assert_deployed_origin_allowlist("staging", "CORS_ALLOWED_ORIGINS", [])


def test_staging_rejects_wildcard() -> None:
    with pytest.raises(ValueError, match="wildcards"):
        _assert_deployed_origin_allowlist("staging", "CORS_ALLOWED_ORIGINS", ["*"])


def test_production_rejects_localhost() -> None:
    with pytest.raises(ValueError, match="localhost"):
        _assert_deployed_origin_allowlist(
            "production", "CSRF_TRUSTED_ORIGINS", ["http://localhost:3000"]
        )


def test_staging_accepts_cloudfront_web_origin() -> None:
    _assert_deployed_origin_allowlist(
        "staging",
        "CORS_ALLOWED_ORIGINS",
        ["https://d1qdib1mcwro0s.cloudfront.net"],
    )
