"""AES-256-GCM with versioned keys and session-bound associated data."""

import secrets
from collections.abc import Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.session_port import EncryptedTokens


class TokenCipher:
    def __init__(self, keys: Mapping[str, bytes], *, primary: str):
        if primary not in keys or any(not key or len(value) != 32 for key, value in keys.items()):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid token encryption keyring")
        self._keys = dict(keys)
        self._primary = primary

    def encrypt(self, plaintext: bytes, *, context: bytes) -> EncryptedTokens:
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(self._keys[self._primary]).encrypt(nonce, plaintext, context)
        return EncryptedTokens(self._primary, nonce + ciphertext)

    def decrypt(self, encrypted: EncryptedTokens, *, context: bytes) -> bytes:
        if encrypted.key_id not in self._keys or len(encrypted.ciphertext) < 28:
            raise PortError(ErrorCode.UNAUTHORIZED, "token envelope unavailable")
        try:
            return AESGCM(self._keys[encrypted.key_id]).decrypt(
                encrypted.ciphertext[:12], encrypted.ciphertext[12:], context
            )
        except (InvalidTag, ValueError):
            raise PortError(ErrorCode.UNAUTHORIZED, "token envelope invalid") from None
