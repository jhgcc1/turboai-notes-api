"""Stable error fingerprints used to deduplicate CloudWatch-driven triage reports."""

from __future__ import annotations

import hashlib
import re
import traceback
from types import TracebackType

# Path segments that vary per request would otherwise split one recurring bug
# into a new fingerprint (and a new report) on every call.
_NUMERIC_SEGMENT = re.compile(r"/\d+(?=/|$)")
_UUID_SEGMENT = re.compile(
    r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?=/|$)",
    re.IGNORECASE,
)


def normalize_path(path: str) -> str:
    """Collapse identifiers so ``/api/notes/42/`` and ``/api/notes/43/`` match."""
    route = _UUID_SEGMENT.sub("/{uuid}", path)
    return _NUMERIC_SEGMENT.sub("/{id}", route)


def _origin_frame(tb: TracebackType | None) -> str:
    """Deepest frame belonging to this codebase, falling back to the deepest frame."""
    frames = traceback.extract_tb(tb)
    if not frames:
        return "unknown"
    for frame in reversed(frames):
        if "/site-packages/" not in frame.filename:
            return f"{frame.filename}:{frame.lineno}"
    last = frames[-1]
    return f"{last.filename}:{last.lineno}"


def build_fingerprint(exc: BaseException | None, route: str, status_code: int) -> str:
    """Short, stable hash identifying one distinct failure mode.

    Deliberately excludes the exception message: messages usually embed ids,
    emails or values that differ between occurrences of the same bug.
    """
    if exc is None:
        parts = ["http", route, str(status_code)]
    else:
        parts = [type(exc).__name__, route, _origin_frame(exc.__traceback__)]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]
