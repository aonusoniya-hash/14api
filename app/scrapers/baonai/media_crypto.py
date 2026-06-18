"""Baonai CDN media decryption (images and encrypted payloads)."""

from __future__ import annotations

IMAGE_DECRYPT_KEY = b"2019ysapp7527"
IMAGE_XOR_LENGTH = 100
BAONAI_REFERER = "https://d2eabzntayzi4t.cloudfront.net/"

_JPEG_MAGIC = bytes([0xFF, 0xD8, 0xFF])
_PNG_MAGIC = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
_GIF_MAGIC = bytes([0x47, 0x49, 0x46])


def decrypt_xor_payload(data: bytes, *, limit: int = IMAGE_XOR_LENGTH) -> bytes:
    """Decrypt CDN image/m3u8 payloads (first N bytes XOR with site key)."""
    if not data:
        return data
    out = bytearray(data)
    key_len = len(IMAGE_DECRYPT_KEY)
    xor_limit = min(limit, len(out))
    for start in range(0, xor_limit, key_len):
        for offset in range(key_len):
            idx = start + offset
            if idx < xor_limit:
                out[idx] ^= IMAGE_DECRYPT_KEY[offset]
    return bytes(out)


def is_plain_image(data: bytes) -> bool:
    if len(data) >= 3 and data[:3] == _JPEG_MAGIC:
        return True
    if len(data) >= 8 and data[:8] == _PNG_MAGIC:
        return True
    if len(data) >= 3 and data[:3] == _GIF_MAGIC:
        return True
    return False


def detect_image_media_type(data: bytes) -> str:
    if len(data) >= 3 and data[:3] == _JPEG_MAGIC:
        return "image/jpeg"
    if len(data) >= 8 and data[:8] == _PNG_MAGIC:
        return "image/png"
    if len(data) >= 3 and data[:3] == _GIF_MAGIC:
        return "image/gif"
    return "application/octet-stream"


def decrypt_image(data: bytes) -> bytes:
    if is_plain_image(data):
        return data
    decrypted = decrypt_xor_payload(data)
    if is_plain_image(decrypted):
        return decrypted
    return data
