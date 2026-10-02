"""Douyin link parsing — port of lib/linkParser.js."""

from __future__ import annotations

import re
from dataclasses import dataclass

USER_RE = re.compile(r"https://www\.douyin\.com/user/([A-Za-z0-9_-]+)")
MIX_RE = re.compile(r"https://www\.douyin\.com/(?:collection|mix/detail)/(\d{19})")
VIDEO_RE = re.compile(r"https://www\.douyin\.com/(?:video|note|slides)/(\d{19})")
SHORT_LINK_RE = re.compile(
    r"https://(?:v\.douyin\.com/[A-Za-z0-9_-]+/?|www\.iesdouyin\.com/[^\s<>\"']+)"
)


@dataclass(frozen=True)
class Link:
    type: str  # user | mix | video
    id: str


class LinkParseError(Exception):
    pass


def parse_douyin_url(text: str) -> Link | None:
    """Classify a pasted string (URL may be embedded in surrounding share text)."""
    text = (text or "").strip()
    if not text:
        return None
    match = USER_RE.search(text)
    if match:
        return Link("user", match.group(1))
    match = MIX_RE.search(text)
    if match:
        return Link("mix", match.group(1))
    match = VIDEO_RE.search(text)
    if match:
        return Link("video", match.group(1))
    return None


def resolve_link(
    text: str, fetch_impl=None
) -> Link:
    """Resolve any supported input to Link; share short links are followed
    via redirect first. fetch_impl(url) -> response with .url (injectable)."""
    direct = parse_douyin_url(text)
    if direct:
        return direct

    match = SHORT_LINK_RE.search((text or "").strip())
    if match:
        if fetch_impl is None:
            import httpx

            def fetch_impl(url: str):
                return httpx.get(url, follow_redirects=True, timeout=15.0,
                                 headers={"User-Agent": "Mozilla/5.0"})

        response = fetch_impl(match.group(0))
        parsed = parse_douyin_url(str(response.url))
        if parsed:
            return parsed
        raise LinkParseError(f"短链解析后的地址无法识别: {response.url}")

    raise LinkParseError(
        "无法识别的链接: 支持 主页(/user/)、合集、单视频"
        "(/video/ /note/ /slides/) 或 v.douyin.com 短链"
    )
