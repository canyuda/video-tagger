"""Douyin web API client — port of lib/api.js on the ported signing core.

All network I/O goes through an injectable httpx transport, so the signing
logic stays zero-cost testable (MockTransport) and real requests happen only
in the download adapter.
"""

from __future__ import annotations

import random
import re
import string
from pathlib import Path
from urllib.parse import quote, quote_plus

import httpx

from sidecar.download.abogus import ABogus
from sidecar.download.websign import normalize_query, sign

USERAGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)
DOMAIN = "https://www.douyin.com/"
POST_API_PATH = "aweme/v1/web/aweme/post/"
DETAIL_API_PATH = "aweme/v1/web/aweme/detail/"

SIGN_PROTECTED_PATHS = frozenset({
    "/aweme/v1/web/aweme/detail/",
    "/aweme/v1/web/aweme/post/",
    "/aweme/v1/web/aweme/favorite/",
    "/aweme/v1/web/aweme/listcollection/",
    "/aweme/v1/web/mix/aweme/",
    "/aweme/v1/web/tab/feed/",
    "/aweme/v1/web/mix/list/",
    "/aweme/v1/web/music/aweme/",
    "/aweme/v1/web/music/list/",
    "/aweme/v1/web/mix/detail/",
    "/aweme/v1/web/mix/listcollection/",
    "/aweme/v1/web/music/detail/",
    "/aweme/v1/web/collects/list/",
    "/aweme/v1/web/collects/video/list/",
})

BASE_PARAMS: list[tuple[str, str]] = [
    ("device_platform", "webapp"),
    ("aid", "6383"),
    ("channel", "channel_pc_web"),
    ("update_version_code", "170400"),
    ("pc_client_type", "1"),
    ("pc_libra_divert", "Mac"),
    ("support_h265", "1"),
    ("support_dash", "1"),
    ("version_code", "290100"),
    ("version_name", "29.1.0"),
    ("cookie_enabled", "true"),
    ("screen_width", "1536"),
    ("screen_height", "864"),
    ("browser_language", "zh-CN"),
    ("browser_platform", "MacIntel"),
    ("browser_name", "Chrome"),
    ("browser_version", "146.0.0.0"),
    ("browser_online", "true"),
    ("engine_name", "Blink"),
    ("engine_version", "146.0.0.0"),
    ("os_name", "Mac OS"),
    ("os_version", "10.15.7"),
    ("cpu_core_num", "16"),
    ("device_memory", "8"),
    ("platform", "PC"),
    ("downlink", "10"),
    ("effective_type", "4g"),
    ("round_trip_time", "200"),
]


class DouyinApiError(Exception):
    pass


def build_detail_params(aweme_id: str) -> list[tuple[str, str]]:
    return [
        ("aweme_id", str(aweme_id)),
        ("version_code", "190500"),
        ("version_name", "19.5.0"),
    ]


def build_post_params(sec_user_id: str, cursor: int, count: int) -> list[tuple[str, str]]:
    return [
        ("sec_user_id", sec_user_id),
        ("max_cursor", str(cursor)),
        ("locate_query", "false"),
        ("show_live_replay_strategy", "1"),
        ("need_time_list", "1"),
        ("time_list_query", "0"),
        ("whale_cut_token", ""),
        ("cut_version", "1"),
        ("count", str(count)),
        ("publish_video_strategy_type", "2"),
        ("from_user_page", "1"),
    ]


def merge_params(
    base_pairs: list[tuple[str, str]], business_pairs: list[tuple[str, str]]
) -> list[tuple[str, str]]:
    """Python dict-merge semantics (base | business): overridden keys keep
    their position; new keys append in business order."""
    overrides = dict(business_pairs)
    base_keys = {k for k, _ in base_pairs}
    pairs = [(k, overrides.get(k, v)) for k, v in base_pairs]
    pairs += [(k, v) for k, v in business_pairs if k not in base_keys]
    return pairs


def parse_cookie(cookie_str: str) -> dict[str, str]:
    jar: dict[str, str] = {}
    for segment in cookie_str.split(";"):
        trimmed = segment.strip()
        if not trimmed or "=" not in trimmed:
            continue
        name, value = trimmed.split("=", 1)
        jar[name.strip()] = value.strip()
    return jar


def cookie_value(jar: dict[str, str], name: str) -> str:
    for key, value in jar.items():
        if key.lower() == name.lower():
            return value
    return ""


def fake_ms_token(size: int = 156) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(random.choices(alphabet, k=size))


def _urlencode(pairs: list[tuple[str, str]]) -> str:
    return "&".join(
        f"{quote_plus(str(k))}={quote_plus(str(v))}" for k, v in pairs
    )


def _is_sign_protected(api_path: str) -> bool:
    path = api_path if api_path.startswith("/") else f"/{api_path}"
    if not path.endswith("/"):
        path += "/"
    return path in SIGN_PROTECTED_PATHS


def build_signed_request(
    *,
    api_path: str,
    business_params: list[tuple[str, str]] | None = None,
    cookie_str: str,
    referer: str,
    method: str = "GET",
    body_pairs: list[tuple[str, str]] | None = None,
) -> dict:
    """Build {method, url, headers, body} for one signed API request
    (signing chain identical to the JS reference)."""
    business_params = business_params or []
    jar = parse_cookie(cookie_str)
    uifid = cookie_value(jar, "uifid")
    ms_token = cookie_value(jar, "msToken") or fake_ms_token()

    pairs = merge_params(
        BASE_PARAMS,
        [*business_params, ("uifid", uifid), ("msToken", ms_token)],
    )
    signed_query = _urlencode(pairs)

    protected_and_uifid = bool(uifid) and _is_sign_protected(api_path)
    if protected_and_uifid:
        signed_query = normalize_query(signed_query)

    a_bogus = ABogus(USERAGENT).get_value(signed_query)
    signed_query += f"&a_bogus={quote(a_bogus, safe='')}"

    if protected_and_uifid:
        signed_query = sign(signed_query, uifid)[0]

    headers = {
        "Accept": "*/*",
        "Accept-Encoding": "*/*",
        "Content-Type": "text/plain;charset=UTF-8",
        "Referer": referer,
        "User-Agent": USERAGENT,
        "Cookie": cookie_str,
    }
    if uifid:
        headers["uifid"] = uifid

    if method == "POST":
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        return {
            "method": method,
            "url": f"{DOMAIN}{api_path}?{signed_query}",
            "headers": headers,
            "body": _urlencode(body_pairs or []),
        }
    return {"method": "GET", "url": f"{DOMAIN}{api_path}?{signed_query}", "headers": headers}


class DouyinClient:
    """Signed request execution + video download. Adapter-shaped (D29):
    the whole class can be swapped without touching the pipeline."""

    def __init__(self, cookie_str: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self.cookie_str = cookie_str
        self._client = httpx.Client(timeout=20.0, transport=transport,
                                    follow_redirects=False)

    def request_json(self, *, api_path: str, business_params=None, referer: str,
                     method: str = "GET", body_pairs=None) -> dict:
        request = build_signed_request(
            api_path=api_path, business_params=business_params,
            cookie_str=self.cookie_str, referer=referer,
            method=method, body_pairs=body_pairs,
        )
        response = self._client.request(
            request["method"], request["url"],
            headers=request["headers"], content=request.get("body"),
        )
        text = response.text
        if response.status_code != 200:
            raise DouyinApiError(f"HTTP {response.status_code}; 响应摘录: {text[:300]}")
        import json

        try:
            data = json.loads(text)
        except ValueError as e:
            raise DouyinApiError(
                f"响应不是 JSON(可能被风控重定向); 响应摘录: {text[:300]}"
            ) from e
        if data.get("status_code", 0) not in (0, None):
            raise DouyinApiError(
                f"status_code={data['status_code']}; 响应摘录: {text[:300]}"
                "(可能原因: 签名失效或 Cookie 失效)"
            )
        return data

    def fetch_aweme_detail(self, aweme_id: str) -> dict:
        data = self.request_json(
            api_path=DETAIL_API_PATH,
            business_params=build_detail_params(aweme_id),
            referer=f"{DOMAIN}video/{aweme_id}",
        )
        detail = data.get("aweme_detail")
        if not detail:
            raise DouyinApiError(f"详情接口未返回 aweme_detail; 摘录: {str(data)[:300]}")
        return detail

    def fetch_user_posts_page(self, sec_user_id: str, cursor: int = 0,
                              count: int = 20) -> dict:
        data = self.request_json(
            api_path=POST_API_PATH,
            business_params=build_post_params(sec_user_id, cursor, count),
            referer=f"{DOMAIN}user/{sec_user_id}",
        )
        raw = data.get("aweme_list") or []
        items = raw[:count]
        next_cursor = int(data.get("max_cursor") or 0)
        if len(raw) > len(items) and items:
            next_cursor = int(items[-1].get("create_time") or 0) * 1000
        return {
            "items": items,
            "total": int(data.get("total") or 0),
            "cursor": next_cursor,
            "has_more": bool(data.get("has_more")),
        }

    def check_login(self) -> dict:
        """Paste-time cookie validation (semantics from the JS reference)."""
        jar = parse_cookie(self.cookie_str)
        if not cookie_value(jar, "sessionid_ss"):
            return {"status": "invalid",
                    "reason": "Cookie 中缺少登录态字段 sessionid_ss(未登录或已过期)"}
        probe_user = "MS4wLjABAAAA9y04iBlVdeMQqTJbqsQZKb-tqWqWW29jPVJqideHT70"
        try:
            self.fetch_user_posts_page(probe_user, 0, 1)
        except DouyinApiError as e:
            return {"status": "invalid", "reason": f"平台拒绝了该 Cookie: {e}"}
        if not cookie_value(jar, "uifid"):
            return {"status": "warn",
                    "reason": "Cookie 缺少 uifid, 已降级为无 WebSign 发送(可用, 稳定性略低)"}
        return {"status": "valid", "reason": ""}

    def download_video(self, url: str, file_path: Path, *, retries: int = 2) -> None:
        """Stream to <target>.part then rename (no half-written mp4)."""
        part_path = file_path.with_suffix(file_path.suffix + ".part")
        for attempt in range(retries + 1):
            try:
                with self._client.stream(
                    "GET", url,
                    headers={
                        "Accept": "*/*",
                        "Range": "bytes=0-",
                        "Referer": f"{DOMAIN}?recommend=1",
                        "User-Agent": USERAGENT,
                    },
                ) as response:
                    response.read()
                    if response.status_code not in (200, 206):
                        raise DouyinApiError(f"HTTP {response.status_code}")
                    part_path.write_bytes(response.content)
                part_path.replace(file_path)
                return
            except Exception:
                part_path.unlink(missing_ok=True)
                if attempt >= retries:
                    raise

    def close(self) -> None:
        self._client.close()


def play_url(detail: dict) -> str:
    """Extract the best play URL from an aweme_detail object."""
    play_addr = ((detail.get("video") or {}).get("play_addr") or {})
    url_list = play_addr.get("url_list") or []
    for url in url_list:
        if url.startswith("http"):
            return url
    raise DouyinApiError("详情里没有可用的播放地址")


def sanitize_title(title: str, limit: int = 50) -> str:
    cleaned = re.sub(r"[\x00-\x1f\x7f]", "", title or "")
    cleaned = re.sub(r'[/\\:*?"<>|]', "", cleaned)
    cleaned = re.sub(r"\s+", "_", cleaned).strip("_")
    return cleaned[:limit]
