"""Pull the source code behind an error out of the bundled repository copy.

The Lambda zip carries a copy of ``apps/`` and ``config/`` under ``repo/``, so
triage can quote the exact lines that raised instead of asking the model to
guess from a log line. Selection is driven by the traceback: sending the whole
repository on every invocation would blow up both context limits and cost.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

# Matches CPython traceback frames: `  File "/app/apps/notes/views.py", line 42, in get_queryset`
_FRAME_RE = re.compile(r'File "(?P<path>[^"]+)", line (?P<lineno>\d+)(?:, in (?P<func>\S+))?')

# Runtime prefixes that never exist inside the bundle.
_STRIP_PREFIXES = ("/app/", "/var/task/", "./")

_VENDOR_MARKERS = ("/site-packages/", "/dist-packages/", "/usr/lib/python", "<frozen")

_ROUTE_MODULE_RE = re.compile(r"/api/(?P<module>[a-z0-9_-]+)")

# Ordered by how often each file explains a request-time failure.
_FALLBACK_FILENAMES = ("views.py", "serializers.py", "services.py", "models.py", "permissions.py")


@dataclass(frozen=True)
class Frame:
    path: str
    lineno: int
    function: str


@dataclass(frozen=True)
class CodeExcerpt:
    path: str
    start_line: int
    end_line: int
    focus_line: int | None
    text: str


def parse_frames(traceback_text: str) -> list[Frame]:
    """Extract traceback frames, dropping third-party and stdlib ones."""
    frames: list[Frame] = []
    for match in _FRAME_RE.finditer(traceback_text or ""):
        path = match.group("path")
        if any(marker in path for marker in _VENDOR_MARKERS):
            continue
        frames.append(
            Frame(
                path=path,
                lineno=int(match.group("lineno")),
                function=match.group("func") or "",
            )
        )
    return frames


def to_relative(path: str) -> str:
    """Turn a runtime absolute path into a bundle-relative one."""
    normalized = path.replace("\\", "/")
    for prefix in _STRIP_PREFIXES:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
            break
    return normalized.lstrip("/")


def resolve_in_bundle(repo_root: str, path: str) -> str | None:
    """Find ``path`` inside the bundle, matching on progressively shorter suffixes.

    Container paths (``/app/apps/notes/views.py``) and bundle paths
    (``repo/apps/notes/views.py``) only share a suffix, so an exact match is
    not enough.
    """
    relative = to_relative(path)
    if not relative:
        return None

    candidate = os.path.join(repo_root, relative)
    if os.path.isfile(candidate):
        return relative

    parts = relative.split("/")
    for index in range(1, len(parts)):
        suffix = "/".join(parts[index:])
        candidate = os.path.join(repo_root, suffix)
        if os.path.isfile(candidate):
            return suffix
    return None


def read_window(
    repo_root: str,
    relative_path: str,
    focus_line: int | None,
    radius: int,
) -> CodeExcerpt | None:
    """Read a line-numbered window of a file centred on ``focus_line``."""
    full_path = os.path.join(repo_root, relative_path)
    try:
        with open(full_path, encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return None
    if not lines:
        return None

    if focus_line is None:
        start, end = 1, min(len(lines), radius * 2)
    else:
        start = max(1, focus_line - radius)
        end = min(len(lines), focus_line + radius)

    width = len(str(end))
    body = "\n".join(f"{number:>{width}} | {lines[number - 1]}" for number in range(start, end + 1))
    return CodeExcerpt(
        path=relative_path,
        start_line=start,
        end_line=end,
        focus_line=focus_line,
        text=body,
    )


def build_index(repo_root: str, limit: int = 200) -> str:
    """Compact listing of bundled Python files, for orientation in the prompt."""
    entries: list[str] = []
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = sorted(d for d in dirnames if d not in {"__pycache__", "migrations"})
        for filename in sorted(filenames):
            if not filename.endswith(".py"):
                continue
            relative = os.path.relpath(os.path.join(dirpath, filename), repo_root)
            entries.append(relative.replace("\\", "/"))
    entries.sort()
    if len(entries) > limit:
        entries = entries[:limit] + [f"... ({len(entries) - limit} more files)"]
    return "\n".join(entries)


def _fallback_paths(repo_root: str, route: str) -> list[str]:
    """Guess relevant files from the failing route when no frame is usable."""
    match = _ROUTE_MODULE_RE.search(route or "")
    if not match:
        return []
    module = match.group("module").replace("-", "_")
    app_dir = os.path.join(repo_root, "apps", module)
    if not os.path.isdir(app_dir):
        return []
    return [
        f"apps/{module}/{filename}"
        for filename in _FALLBACK_FILENAMES
        if os.path.isfile(os.path.join(app_dir, filename))
    ]


def collect_context(
    repo_root: str,
    traceback_text: str,
    route: str,
    *,
    max_chars: int = 24000,
    window_lines: int = 40,
) -> list[CodeExcerpt]:
    """Select the source excerpts most likely to explain the failure.

    Traceback frames come first and in reverse order, because the deepest frame
    is where the exception actually surfaced.
    """
    excerpts: list[CodeExcerpt] = []
    seen: set[tuple[str, int]] = set()
    budget = max_chars

    if not os.path.isdir(repo_root):
        return []

    for frame in reversed(parse_frames(traceback_text)):
        relative = resolve_in_bundle(repo_root, frame.path)
        if relative is None:
            continue
        key = (relative, frame.lineno)
        if key in seen:
            continue
        excerpt = read_window(repo_root, relative, frame.lineno, window_lines)
        if excerpt is None or len(excerpt.text) > budget:
            continue
        seen.add(key)
        excerpts.append(excerpt)
        budget -= len(excerpt.text)

    if excerpts:
        return excerpts

    for relative in _fallback_paths(repo_root, route):
        excerpt = read_window(repo_root, relative, None, window_lines)
        if excerpt is None or len(excerpt.text) > budget:
            continue
        excerpts.append(excerpt)
        budget -= len(excerpt.text)
    return excerpts


def render_context(excerpts: list[CodeExcerpt]) -> str:
    if not excerpts:
        return "(no source files could be resolved for this error)"
    blocks = [
        f"--- {excerpt.path} (lines {excerpt.start_line}-{excerpt.end_line})"
        + (f", error at line {excerpt.focus_line}" if excerpt.focus_line else "")
        + f" ---\n{excerpt.text}"
        for excerpt in excerpts
    ]
    return "\n\n".join(blocks)
