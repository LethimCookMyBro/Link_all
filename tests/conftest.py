"""Hermeticity helpers for the test suite.

config.py loads ``.env`` with ``override=True``, so an operator's real
``.env`` (e.g. a rotated ``PHANTOMLINK_PASSWORD``) changes the value
``C2.C2`` validates client credentials against. The committed tests exercise
that path with the legacy default password ``"PhantomLink"`` via mocked
sockets — they pass only when the server's configured password equals that
literal.

This module pins the shared config values to their legacy defaults at
conftest import time, i.e. *before* any test module imports ``config``, so
every test (including the regression guards that read ``config.CLIENT_PASSWORD``
directly) sees one deterministic value regardless of the ambient environment.

Note: this affects the test session only. Runtime auth still reads the real
``.env`` through the normal config path.
"""

import config

# The committed tests send b"PhantomLink" over their mocked sockets; the
# regression-guard tests import CLIENT_PASSWORD from config. Pin both sides
# to the same value so the suite is deterministic.
config.CLIENT_PASSWORD = "PhantomLink"
