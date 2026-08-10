"""Minimal JSON-over-HTTP helper.

The Lambda deliberately ships with no third-party dependencies: ``urllib`` and
the runtime-provided ``boto3`` are enough, which keeps the deployment artifact
a few kilobytes and removes the need for a build step or layer.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class HttpError(RuntimeError):
    def __init__(self, status: int, body: str, url: str) -> None:
        super().__init__(f"HTTP {status} from {url}: {body[:500]}")
        self.status = status
        self.body = body
        self.url = url


def post_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: int = 30,
) -> dict[str, Any]:
    data = json.dumps(payload).encode()
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json")
    for key, value in headers.items():
        request.add_header(key, value)

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode()
    except urllib.error.HTTPError as exc:
        raise HttpError(exc.code, exc.read().decode(errors="replace"), url) from exc
    except urllib.error.URLError as exc:
        raise HttpError(0, str(exc.reason), url) from exc

    if not body:
        return {}
    parsed: Any = json.loads(body)
    return parsed if isinstance(parsed, dict) else {"data": parsed}
