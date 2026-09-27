"""ULID generation (26 chars, Crockford base32, time-ordered) without an extra dependency."""

from __future__ import annotations

import os
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_ulid() -> str:
    """Return a new ULID: 48-bit millisecond timestamp + 80 random bits, base32 encoded."""
    ts = int(time.time() * 1000)
    rand = int.from_bytes(os.urandom(10), "big")
    value = (ts << 80) | rand
    chars = []
    for _ in range(26):
        chars.append(_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))
