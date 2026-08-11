"""Main-user-query identifier encoding and validation."""

from __future__ import annotations

from urllib.parse import quote, unquote_to_bytes


def encode_query_identifier(main_user_query: str) -> str:
    """Encode a main user query using the approved OSHA_ID grammar.

    UTF-8 bytes are percent-encoded except for RFC 3986 unreserved
    characters: ASCII letters, digits, ``-``, ``.``, ``_``, and ``~``.
    ``urllib.parse.quote`` emits uppercase hexadecimal escape digits.
    """
    if not isinstance(main_user_query, str):
        raise TypeError("main_user_query must be a string")

    return quote(main_user_query, safe="-._~", encoding="utf-8", errors="strict")


def decode_query_identifier(encoded_identifier: str) -> str:
    """Decode an OSHA_ID and require valid UTF-8 input."""
    if not isinstance(encoded_identifier, str):
        raise TypeError("encoded_identifier must be a string")

    return unquote_to_bytes(encoded_identifier).decode("utf-8", errors="strict")
