"""Shared content integrity rules for ObjectStorePort implementations."""

from hashlib import sha256

from qtrade_domain.ports import ErrorCode, PortError


def validate_object(key: str, content: bytes | None, expected_sha256: str) -> None:
    if (
        not key
        or key.startswith("/")
        or any(p in {"", ".", ".."} for p in key.split("/"))
        or len(key) > 256
        or "\\" in key
        or any(ord(character) < 32 for character in key)
    ):
        raise PortError(ErrorCode.INVALID_INPUT, "invalid object key")
    if len(expected_sha256) != 64 or any(c not in "0123456789abcdef" for c in expected_sha256):
        raise PortError(ErrorCode.INVALID_INPUT, "invalid SHA-256")
    if content is not None and sha256(content).hexdigest() != expected_sha256:
        raise PortError(ErrorCode.CHECKSUM_MISMATCH, "object checksum mismatch")
