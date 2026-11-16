from __future__ import annotations

import os
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode(value: int, length: int) -> str:
    chars = ["0"] * length
    for i in range(length - 1, -1, -1):
        chars[i] = _CROCKFORD[value & 31]
        value >>= 5
    return "".join(chars)


class UlidGenerator:
    def new_id(self) -> str:
        ms = int(time.time() * 1000)
        entropy = int.from_bytes(os.urandom(10), "big")
        return _encode(ms, 10) + _encode(entropy, 16)


class SequencedIdGenerator:
    def __init__(self, prefix: str = "01TEST") -> None:
        self._n = 0
        self._prefix = prefix

    def new_id(self) -> str:
        self._n += 1
        return f"{self._prefix}{self._n:020d}"[:26]
