"""Strict JOSE encoding helpers shared by trusted_keys and identity_assertion.

Private to the identity package. These helpers are stricter than
general-purpose JOSE libraries:
  - base64url must be unpadded and canonical: re-encoding the decoded
    bytes must reproduce the input exactly, so the spare low bits of the
    final character cannot carry a second spelling of the same value;
  - JSON must be UTF-8, must be an object, must not repeat a member
    name, and must not use NaN or Infinity.
A repeated member is the classic parser-differential hole: two parsers
reading the same token can disagree about which "sub" it carries.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from typing import Any, Dict, List, Tuple

_B64URL_ALPHABET = re.compile(r"^[A-Za-z0-9_-]*$")


class MalformedEncodingError(ValueError):
    """A base64url segment or JSON object failed strict parsing."""


def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64url_decode_canonical(segment: str) -> bytes:
    failed = False
    raw = b""
    if not isinstance(segment, str) or not _B64URL_ALPHABET.match(segment):
        failed = True
    elif len(segment) % 4 == 1:
        failed = True
    else:
        try:
            raw = base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
        except (binascii.Error, ValueError):
            failed = True
    if failed or b64url_encode(raw) != segment:
        raise MalformedEncodingError("Not canonical unpadded base64url.")
    return raw


def _reject_duplicates(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MalformedEncodingError("Repeated JSON member name.")
        result[key] = value
    return result


def _reject_constant(_: str) -> Any:
    raise MalformedEncodingError("Non-finite JSON number.")


def strict_json_object(raw: bytes) -> Dict[str, Any]:
    failed = False
    parsed: Any = None
    try:
        text = raw.decode("utf-8")
        parsed = json.loads(
            text,
            object_pairs_hook=_reject_duplicates,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        # MalformedEncodingError is a ValueError, so hook failures land here.
        failed = True
    if failed or not isinstance(parsed, dict):
        raise MalformedEncodingError("Not a strict JSON object.")
    return parsed
