"""Authentication policy for the C2 server.

Centralizes the two credential checks that used to live inline in ``C2.C2``:

* API key check for the REST API (``C2APIHandler._check_auth``).
* Client password check during the handshake (``ClientManager.add_client``).

Both use ``hmac.compare_digest`` for constant-time comparison so credential
length/prefix timing cannot leak. This module is the single seam to upgrade
later (hashing, per-session tokens, rate limiting) without touching the
handler or the connection loop.
"""

from __future__ import annotations

import hmac


def check_api_key(provided: str, expected: str) -> bool:
    """Constant-time comparison of an API key against the configured key.

    Empty expected keys fail closed (``compare_digest`` returns False for an
    empty comparison against a non-empty input, and an operator must always
    configure a key via ``PHANTOMLINK_API_KEY``).
    """
    if not expected:
        return False
    try:
        return hmac.compare_digest(provided, expected)
    except (TypeError, ValueError):
        return False


def check_client_password(provided: str, expected: str) -> bool:
    """Constant-time comparison of the client handshake password.

    ``provided`` must already be decoded/trimmed by the caller (the handshake
    decodes the received bytes with UTF-8 and strips whitespace first).
    """
    if not expected:
        return False
    try:
        return hmac.compare_digest(provided, expected)
    except (TypeError, ValueError):
        return False
