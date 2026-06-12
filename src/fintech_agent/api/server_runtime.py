"""Process-level runtime identity.

SERVER_INSTANCE_ID is generated once when the server process starts and stays
constant for that process. It changes on every restart, so clients can detect
a fresh server ("software restarted") and reset client-side state (e.g. the
customer chat conversation) instead of restoring a chat the backend no longer
has context for.
"""

from __future__ import annotations

import uuid

# New value on every process start / restart.
SERVER_INSTANCE_ID: str = uuid.uuid4().hex
