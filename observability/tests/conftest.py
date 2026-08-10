"""Make the Lambda package importable the same way it is inside the zip.

In AWS the function root is on ``sys.path``, so modules import as
``triage.logs`` rather than ``observability.triage.logs``. Tests mirror that.
"""

from __future__ import annotations

import os
import sys

LAMBDA_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if LAMBDA_ROOT not in sys.path:
    sys.path.insert(0, LAMBDA_ROOT)
