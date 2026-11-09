"""Decoding of the typed hex values returned by the SENEC lala.cgi endpoint.

Derived from pysenec_ha/util.py of ha-senec-v3
(https://github.com/marq24/ha-senec-v3), Copyright 2023 Matthias Marquardt,
licensed under the Apache License 2.0.

Modified: signed integers are decoded as two's complement, malformed values are
returned unchanged instead of raising, and the input is not mutated.
"""

from __future__ import annotations

from struct import unpack
from typing import Any


def decode_value(raw: str) -> Any:
    """Decode a single value such as ``fl_43708000`` or ``u8_01``.

    Values without a known type prefix, for example ``VARIABLE_NOT_FOUND``, are
    returned unchanged.
    """
    prefix, separator, payload = raw.partition("_")
    if not separator:
        return raw

    try:
        if prefix == "fl":
            return unpack(">f", bytes.fromhex(payload))[0]
        if prefix == "st":
            return payload
        if prefix[:1] == "u" and prefix[1:].isdigit():
            return int(payload, 16)
        if prefix[:1] == "i" and prefix[1:].isdigit():
            value = int(payload, 16)
            bits = len(payload) * 4
            return value - (1 << bits) if value >= 1 << (bits - 1) else value
    except ValueError:
        return raw
    return raw


def decode_tree(raw: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of a lala.cgi response with all values decoded."""
    decoded: dict[str, Any] = {}
    for key, value in raw.items():
        if isinstance(value, str):
            decoded[key] = decode_value(value)
        elif isinstance(value, dict):
            decoded[key] = decode_tree(value)
        elif isinstance(value, list):
            decoded[key] = [decode_value(item) if isinstance(item, str) else item for item in value]
        else:
            decoded[key] = value
    return decoded
