import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def _use_sqlite() -> None:
    os.environ.setdefault("USE_SQLITE", "true")
    os.environ.setdefault("SECRET_KEY", "test-secret")
    os.environ.setdefault("ENVIRONMENT", "local")
