"""Source-code selection driven by traceback frames."""

from __future__ import annotations

import os
from pathlib import Path

from triage import repo

TRACEBACK = """Traceback (most recent call last):
  File "/usr/lib/python3.12/site-packages/django/core/handlers/base.py", line 197, in _run
    response = wrapped_callback(request, *callback_args, **callback_kwargs)
  File "/app/apps/notes/views.py", line 8, in get_queryset
    return boom()
ValueError: exploded
"""


def _bundle(tmp_path: Path) -> str:
    target = tmp_path / "repo" / "apps" / "notes"
    target.mkdir(parents=True)
    (target / "views.py").write_text(
        "\n".join(f"line {number}" for number in range(1, 41)),
        encoding="utf-8",
    )
    (target / "serializers.py").write_text("serializer code\n", encoding="utf-8")
    return str(tmp_path / "repo")


def test_parse_frames_skips_site_packages() -> None:
    frames = repo.parse_frames(TRACEBACK)
    assert len(frames) == 1
    assert frames[0].path == "/app/apps/notes/views.py"
    assert frames[0].lineno == 8
    assert frames[0].function == "get_queryset"


def test_parse_frames_handles_empty_input() -> None:
    assert repo.parse_frames("") == []


def test_to_relative_strips_runtime_prefixes() -> None:
    assert repo.to_relative("/app/apps/notes/views.py") == "apps/notes/views.py"
    assert repo.to_relative("/var/task/repo/config/settings.py") == "repo/config/settings.py"


def test_resolve_in_bundle_matches_on_suffix(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    assert repo.resolve_in_bundle(root, "/app/apps/notes/views.py") == "apps/notes/views.py"


def test_resolve_in_bundle_returns_none_for_unknown(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    assert repo.resolve_in_bundle(root, "/app/apps/ghost/views.py") is None
    assert repo.resolve_in_bundle(root, "") is None


def test_read_window_numbers_lines_around_focus(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    excerpt = repo.read_window(root, "apps/notes/views.py", 20, 3)
    assert excerpt is not None
    assert excerpt.start_line == 17
    assert excerpt.end_line == 23
    assert "20 | line 20" in excerpt.text


def test_read_window_handles_missing_file(tmp_path: Path) -> None:
    assert repo.read_window(str(tmp_path), "nope.py", 1, 5) is None


def test_collect_context_uses_traceback_frames(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    excerpts = repo.collect_context(root, TRACEBACK, "/api/notes/", window_lines=5)
    assert [excerpt.path for excerpt in excerpts] == ["apps/notes/views.py"]
    assert excerpts[0].focus_line == 8


def test_collect_context_falls_back_to_route(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    excerpts = repo.collect_context(root, "no frames here", "/api/notes/", window_lines=5)
    assert {excerpt.path for excerpt in excerpts} == {
        "apps/notes/views.py",
        "apps/notes/serializers.py",
    }


def test_collect_context_respects_char_budget(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    assert repo.collect_context(root, TRACEBACK, "/api/notes/", max_chars=5) == []


def test_collect_context_without_bundle() -> None:
    assert repo.collect_context("/nonexistent-path", TRACEBACK, "/api/notes/") == []


def test_build_index_lists_python_files(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    index = repo.build_index(root)
    assert "apps/notes/views.py" in index


def test_build_index_truncates(tmp_path: Path) -> None:
    root = tmp_path / "many"
    root.mkdir()
    for number in range(10):
        (root / f"mod{number}.py").write_text("x\n", encoding="utf-8")
    index = repo.build_index(str(root), limit=3)
    assert "more files" in index


def test_render_context_without_excerpts() -> None:
    assert "no source files" in repo.render_context([])


def test_render_context_includes_paths(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    excerpts = repo.collect_context(root, TRACEBACK, "/api/notes/", window_lines=2)
    rendered = repo.render_context(excerpts)
    assert "apps/notes/views.py" in rendered
    assert "error at line 8" in rendered


def test_bundle_layout_matches_settings_default() -> None:
    """The default REPO_ROOT must point at the bundled copy next to handler.py."""
    from triage.settings import Settings

    os.environ.update(
        {
            "CW_LOG_GROUP": "lg",
            "DEDUP_TABLE": "table",
            "LLM_SECRET_ID": "secret",
            "SNS_TOPIC_ARN": "arn",
        }
    )
    settings = Settings.from_env()
    assert settings.repo_root.endswith("repo")
