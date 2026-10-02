"""SM3 hash — direct port of lib/sm3.js (which mirrors TikTokDownloader's
sm3.py). Operates on 32-bit words with explicit masking like the JS version."""

from __future__ import annotations

MASK = 0xFFFFFFFF
IV = [
    0x7380166F, 0x4914B2B9, 0x172442D7, 0xDA8A0600,
    0xA96F30BC, 0x163138AA, 0xE38DEE4D, 0xB0FB0E4E,
]
T0 = 0x79CC4519
T1 = 0x7A879D8A


def _rotl(value: int, bits: int) -> int:
    bits %= 32
    if bits == 0:
        return value & MASK
    return ((value << bits) | (value >> (32 - bits))) & MASK


def _ff(index: int, x: int, y: int, z: int) -> int:
    if index < 16:
        return (x ^ y ^ z) & MASK
    return (x & y) | (x & z) | (y & z)


def _gg(index: int, x: int, y: int, z: int) -> int:
    if index < 16:
        return (x ^ y ^ z) & MASK
    return (x & y) | ((~x & MASK) & z)


def _expand(block: bytes) -> tuple[list[int], list[int]]:
    w = [int.from_bytes(block[i * 4:i * 4 + 4], "big") for i in range(16)]
    for i in range(16, 68):
        x = (w[i - 16] ^ w[i - 9] ^ _rotl(w[i - 3], 15)) & MASK
        x = (x ^ _rotl(x, 15) ^ _rotl(x, 23)) & MASK
        w.append((x ^ _rotl(w[i - 13], 7) ^ w[i - 6]) & MASK)
    w1 = [(w[i] ^ w[i + 4]) & MASK for i in range(64)]
    return w, w1


def _compress(state: list[int], block: bytes) -> list[int]:
    w, w1 = _expand(block)
    a, b, c, d, e, f, g, h = state
    for j in range(64):
        t = T0 if j < 16 else T1
        ss1 = _rotl((_rotl(a, 12) + e + _rotl(t, j)) & MASK, 7)
        ss2 = (ss1 ^ _rotl(a, 12)) & MASK
        tt1 = (_ff(j, a, b, c) + d + ss2 + w1[j]) & MASK
        tt2 = (_gg(j, e, f, g) + h + ss1 + w[j]) & MASK
        d = c
        c = _rotl(b, 9)
        b = a
        a = tt1
        h = g
        g = _rotl(f, 19)
        f = e
        e = (tt2 ^ _rotl(tt2, 9) ^ _rotl(tt2, 17)) & MASK
    updated = [a, b, c, d, e, f, g, h]
    return [(v ^ u) & MASK for v, u in zip(state, updated, strict=True)]


def _pad(message: bytes) -> bytes:
    bit_length = len(message) * 8
    out = bytearray(message)
    out.append(0x80)
    while len(out) % 64 != 56:
        out.append(0)
    out += bit_length.to_bytes(8, "big")
    return bytes(out)


def sm3_hash(message: bytes) -> bytes:
    state = IV
    padded = _pad(message)
    for offset in range(0, len(padded), 64):
        state = _compress(state, padded[offset:offset + 64])
    out = bytearray(32)
    for i, word in enumerate(state):
        out[i * 4:i * 4 + 4] = word.to_bytes(4, "big")
    return bytes(out)


def sm3_to_array(data: str | bytes) -> list[int]:
    """String -> UTF-8 bytes, bytes -> raw; returns 32 ints."""
    raw = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    return list(sm3_hash(raw))
