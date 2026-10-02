"""a_bogus signature — direct port of lib/abogus.js.

All constants, the field order, and the bit-level mixing must stay identical
to the JS reference: 对拍 (same rng + timestamp -> same signature) is the
acceptance gate (D29).
"""

from __future__ import annotations

import time
from collections.abc import Callable

from sidecar.download.sm3 import sm3_to_array

ALPHABETS = {
    "s3": "ckdp1h4ZKsUB80/Mfvw36XIgR25+WQAlEi7NLboqYTOPuzmFjJnryx9HVGDaStCe",
    "s4": "Dkdpgh2ZmsQB80/MfvV36XI1R45-WUAlEixNLwoqYTOPuzKFjJnry79HbGcaStCe",
}

SALT = "dhzx"
HEADER_MAGIC = (3, 82)
SDK_VERSION = (1, 0, 1, 0)
PAYLOAD_KEY = 0xD3
FORTNIGHT_EPOCH_MS = 1721836800000

PAGE_ID = 6241
AID = 6383

NOISE_MASKS = (0x91, 0x42, 0x2C)
DATA_MASKS = (0x6E, 0xBD, 0xD3)

FIELD_ORDER = [
    "L34", "L44", "L56", "L61", "L73", "L29", "L70", "L45", "L35", "L49",
    "L38", "L66", "L51", "L68", "L28", "L48", "L64", "L47", "L30", "L71",
    "L26", "L55", "L31", "L69", "L59", "L40", "L62", "L63", "L27", "L72",
    "L41", "L74", "L57", "L52", "L42", "L39", "L33", "L67", "L53", "L43",
    "L65", "L46", "L36", "L24", "L60", "L32", "L79", "L80", "L84", "L85",
]

CANARIES = [
    (3, 11, 12),
    (4, 8, 9),
    (5, 12, 13),
]

DIGEST_CHAINS = {
    "query": {"slots": ("L48", "L49", "L51"), "indices": (9, 18), "canary": CANARIES[0]},
    "body": {"slots": ("L52", "L53", "L55"), "indices": (10, 19), "canary": CANARIES[1]},
    "user_agent": {"slots": ("L56", "L57", "L59"), "indices": (11, 21), "canary": CANARIES[2]},
}

ENV_FLAGS = 1
DETECT_FLAGS = 14
NR_FLAGS = 0x21
NR_TAG = (0, 0, 0, 0)
TRIPWIRE_LOCKED = 3
CALL_BUCKET = 6
DEFAULT_BROWSER_INFO = "1536|742|1536|864|1536|864|1536|864|MacIntel"

HEADER_NOISE_BANDS = {"chrome": 0, "firefox": 40, "safari": 81, "edge": 125, "huawei": 170,
                      "other": 210}
TRIPWIRE_SET = 0xB2
TRIPWIRE_FREE = 0x4D


def family_of(user_agent: str) -> str:
    ua = user_agent.lower()
    for name in ("edg", "huawei", "firefox", "chrome", "safari"):
        if name in ua:
            return "edge" if name == "edg" else name
    return "other"


def header_noise(user_agent: str, rng: Callable[[], float]) -> int:
    base = HEADER_NOISE_BANDS.get(family_of(user_agent), HEADER_NOISE_BANDS["other"])
    return (base + int(rng() * 40)) & 0xFF


def probe_noise(rng: Callable[[], float]) -> int:
    value = int(rng() * 240)
    return value + (value % 2) + 1 if value > 109 else value


def tripwire_noise(rng: Callable[[], float]) -> int:
    return (int(rng() * 255) & TRIPWIRE_FREE) | TRIPWIRE_SET


def js_bytes(text: str) -> bytes:
    """JS charCodeAt rule: UTF-16 units below U+0100 emit one byte, higher
    units emit two big-endian bytes (hanzi = 2 bytes, not UTF-8's 3)."""
    out = bytearray()
    for ch in text:
        code = ord(ch)
        if code & 0xFF00:
            out.append(code >> 8)
        out.append(code & 0xFF)
    return bytes(out)


def le_bytes(value: int, count: int) -> list[int]:
    return [(value >> (8 * i)) & 0xFF for i in range(count)]


def canary(digest: list[int], offset: int, sentinel: int, fallback: int) -> int:
    for i in range(offset, len(digest)):
        if digest[i] != sentinel:
            return digest[i]
    return fallback


def digest_of(text: str) -> list[int]:
    """SM3(SM3(text + SALT)) — 32 ints."""
    return sm3_to_array(bytes(sm3_to_array(text + SALT)))


def rc4(key: bytes, data: bytes) -> bytes:
    """The SDK's RC4: S-box initialized descending, multiplicative key schedule."""
    box = [0] * 256
    for i in range(256):
        box[255 - i] = i
    j = 0
    for i in range(256):
        j = (j * box[i] + j + key[i % len(key)]) % 256
        box[i], box[j] = box[j], box[i]
    out = bytearray(len(data))
    i = j = 0
    for index in range(len(data)):
        i = (i + 1) % 256
        j = (j + box[i]) % 256
        box[i], box[j] = box[j], box[i]
        out[index] = data[index] ^ box[(box[i] + box[j]) % 256]
    return bytes(out)


def encode_base64(data: bytes, alphabet: str = "s4") -> str:
    """Base64 on the SDK alphabet: literal '=' padding, partial groups emit
    len+1 digits."""
    table = ALPHABETS[alphabet]
    out: list[str] = []
    for offset in range(0, len(data), 3):
        chunk_len = min(3, len(data) - offset)
        block = 0
        for k in range(3):
            block = block * 256 + (data[offset + k] if k < chunk_len else 0)
        digits = ((block >> 18) & 0x3F, (block >> 12) & 0x3F, (block >> 6) & 0x3F, block & 0x3F)
        for k in range(chunk_len + 1):
            out.append(table[digits[k]])
    text = "".join(out)
    pad = (4 - len(text) % 4) % 4
    return text + "=" * pad


def user_agent_digest(user_agent: str) -> list[int]:
    key = bytes([ENV_FLAGS // 256, ENV_FLAGS % 256, DETECT_FLAGS % 256])
    payload = js_bytes(user_agent.strip())
    sealed = rc4(key, payload)
    return sm3_to_array(encode_base64(sealed, "s3").encode("utf-8"))


def chain_bytes(chain: dict, digest: list[int]) -> list[int]:
    first, second = chain["indices"]
    return [digest[first], digest[second], canary(digest, *chain["canary"])]


def mask_pair(
    pair: tuple[int, int] | list[int],
    rng: Callable[[], float],
    low: int | None = None,
    high: int | None = None,
) -> list[int]:
    noise = int(rng() * 65535)
    lo = noise & 0xFF if low is None else low & 0xFF
    hi = (noise >> 8) & 0xFF if high is None else high & 0xFF
    return [
        (lo & 0xAA) | (pair[0] & 0x55),
        (lo & 0x55) | (pair[0] & 0xAA),
        (hi & 0xAA) | (pair[1] & 0x55),
        (hi & 0x55) | (pair[1] & 0xAA),
    ]


def expand_noise(body: bytes, rng: Callable[[], float]) -> list[int]:
    out: list[int] = []
    for offset in range(0, len(body), 3):
        group = body[offset:offset + 3]
        if len(group) < 3:
            out.append(group[0])
            if len(group) > 1 and group[1]:
                out.append(group[1])
            continue
        noise = int(rng() * 1000) & 0xFF
        for k in range(3):
            out.append((noise & NOISE_MASKS[k]) | (group[k] & DATA_MASKS[k]))
        out.append(
            (group[0] & NOISE_MASKS[0]) | (group[1] & NOISE_MASKS[1]) | (group[2] & NOISE_MASKS[2])
        )
    return out


class ABogus:
    """Compute a_bogus for a single browser identity. user_agent must equal
    the User-Agent actually sent — the third digest chain binds it."""

    def __init__(
        self,
        user_agent: str,
        *,
        browser_info: str = DEFAULT_BROWSER_INFO,
        page_id: int = PAGE_ID,
        aid: int = AID,
        rng: Callable[[], float] | None = None,
    ) -> None:
        import random

        self.user_agent = user_agent
        self.browser_info = browser_info
        self.page_id = page_id
        self.aid = aid
        self.rng = rng or random.random

    def fields(self, query: str, body: str, now_ms: int) -> dict[str, int]:
        digests = {
            "query": digest_of(query),
            "body": digest_of(body),
            "user_agent": user_agent_digest(self.user_agent),
        }
        ink = now_ms - 1
        fortnights = (now_ms - FORTNIGHT_EPOCH_MS) // (1000 * 60 * 60 * 24 * 14)
        info_bytes = js_bytes(self.browser_info)
        tail_bytes = js_bytes(f"{(now_ms + 3) & 0xFF},")

        fields: dict[str, int] = {
            "L24": 41,
            "L26": fortnights,
            "L27": CALL_BUCKET,
            "L28": 3,
            "L35": ENV_FLAGS & 0xFF,
            "L36": (ENV_FLAGS // 256) & 0xFF,
            "L38": NR_FLAGS & 0xFF,
            "L39": (NR_FLAGS >> 8) & 0xFF,
            "L66": TRIPWIRE_LOCKED,
            "L79": len(info_bytes) & 0xFF,
            "L80": (len(info_bytes) >> 8) & 0xFF,
            "L84": len(tail_bytes) & 0xFF,
            "L85": (len(tail_bytes) >> 8) & 0xFF,
        }
        for index, byte in enumerate(le_bytes(now_ms, 6)):
            fields[f"L{29 + index}"] = byte
        for index, byte in enumerate(le_bytes(DETECT_FLAGS, 4)):
            fields[f"L{44 + index}"] = byte
        for index, byte in enumerate(NR_TAG):
            fields[f"L{40 + index}"] = byte
        for index, byte in enumerate(le_bytes(ink, 6)):
            fields[f"L{60 + index}"] = byte
        for index, byte in enumerate(le_bytes(self.page_id, 4)):
            fields[f"L{67 + index}"] = byte
        for index, byte in enumerate(le_bytes(self.aid, 4)):
            fields[f"L{71 + index}"] = byte
        for key, chain in DIGEST_CHAINS.items():
            written = chain_bytes(chain, digests[key])
            for slot, value in zip(chain["slots"], written, strict=True):
                fields[slot] = value
        return fields

    def get_value(
        self,
        query: str,
        *,
        body: str = "",
        content_type: str = "",
        now_ms: int | None = None,
    ) -> str:
        if "multipart/form-data" in content_type.lower():
            body = ""
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        if now < FORTNIGHT_EPOCH_MS:
            raise ValueError(f"clock is before the a_bogus epoch: {now}")
        fields = self.fields(query, body, now)

        version = [
            *mask_pair(SDK_VERSION[:2], self.rng),
            *mask_pair(SDK_VERSION[2:], self.rng, probe_noise(self.rng), tripwire_noise(self.rng)),
        ]
        checksum = 0
        for byte in version:
            checksum ^= byte
        for name in FIELD_ORDER:
            checksum ^= fields[name]

        payload = bytes(
            [fields[name] for name in FIELD_ORDER]
            + list(js_bytes(self.browser_info))
            + list(js_bytes(f"{(now + 3) & 0xFF},"))
            + [checksum & 0xFF]
        )

        header = mask_pair(HEADER_MAGIC, self.rng, None, header_noise(self.user_agent, self.rng))
        frame = expand_noise(payload, self.rng)
        sealed = rc4(bytes([PAYLOAD_KEY]), bytes(version + frame))
        return encode_base64(bytes(header) + sealed, "s4")
