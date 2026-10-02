"""Tests for the douyin adapter: pure param/cookie/url logic + signed request
shape via MockTransport (no real requests)."""


import httpx
import pytest

from sidecar.download.api import (
    DouyinApiError,
    build_detail_params,
    build_signed_request,
    cookie_value,
    fake_ms_token,
    merge_params,
    parse_cookie,
    play_url,
    sanitize_title,
)
from sidecar.download.linkparser import Link, LinkParseError, parse_douyin_url, resolve_link

COOKIE = "ttwid=1; uifid=abc-123; sessionid_ss=xyz; msToken=m%20t=="


class TestParams:
    def test_merge_overrides_keep_position(self):
        merged = merge_params([("a", "1"), ("b", "2")], [("b", "9"), ("c", "3")])
        assert merged == [("a", "1"), ("b", "9"), ("c", "3")]

    def test_detail_params(self):
        assert build_detail_params("735")[0] == ("aweme_id", "735")

    def test_fake_ms_token_length(self):
        assert len(fake_ms_token()) == 156


class TestCookie:
    def test_parse_and_lookup(self):
        jar = parse_cookie(COOKIE)
        assert jar["uifid"] == "abc-123"
        assert cookie_value(jar, "SESSIONID_SS") == "xyz"  # case-insensitive

    def test_bare_artifact_ignored(self):
        assert "douyin.com" not in parse_cookie("douyin.com; a=1")


class TestSignedRequest:
    def test_get_request_shape(self):
        request = build_signed_request(
            api_path="aweme/v1/web/aweme/detail/",
            business_params=build_detail_params("7350000000000000001"),
            cookie_str=COOKIE,
            referer="https://www.douyin.com/video/7350000000000000001",
        )
        assert request["method"] == "GET"
        assert request["url"].startswith(
            "https://www.douyin.com/aweme/v1/web/aweme/detail/?device_platform=webapp"
        )
        # protected path + uifid -> websign applied
        assert "x-secsdk-web-signature=" in request["url"]
        assert request["headers"]["uifid"] == "abc-123"
        assert request["headers"]["Cookie"] == COOKIE

    def test_unprotected_path_skips_websign(self):
        request = build_signed_request(
            api_path="aweme/v1/web/user/profile/other/",
            business_params=[("sec_user_id", "x")],
            cookie_str=COOKIE,
            referer="https://www.douyin.com/user/x",
        )
        assert "x-secsdk-web-signature" not in request["url"]
        assert "a_bogus=" in request["url"]


class TestClientMocked:
    def make_client(self, handler) -> tuple:
        from sidecar.download.api import DouyinClient

        requests: list[httpx.Request] = []

        def transport(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return handler(request)

        return DouyinClient(COOKIE, transport=httpx.MockTransport(transport)), requests

    def test_detail_success(self):
        payload = {"status_code": 0, "aweme_detail": {"desc": "hi", "video": {}}}
        client, _ = self.make_client(lambda r: httpx.Response(200, json=payload))
        detail = client.fetch_aweme_detail("735")
        assert detail["desc"] == "hi"
        client.close()

    def test_risk_control_redirect_is_error(self):
        client, _ = self.make_client(lambda r: httpx.Response(200, text="<html>verify</html>"))
        with pytest.raises(DouyinApiError, match="JSON"):
            client.fetch_aweme_detail("735")
        client.close()

    def test_status_code_nonzero_is_error(self):
        client, _ = self.make_client(lambda r: httpx.Response(200, json={"status_code": 8}))
        with pytest.raises(DouyinApiError, match="status_code=8"):
            client.fetch_aweme_detail("735")
        client.close()

    def test_check_login_invalid_without_session(self):
        from sidecar.download.api import DouyinClient

        client = DouyinClient("ttwid=1", transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={})))
        result = client.check_login()
        assert result["status"] == "invalid"
        client.close()

    def test_check_login_valid_with_probe_pass(self):
        ok = {"status_code": 0, "aweme_list": [], "has_more": 0}
        client, _ = self.make_client(lambda r: httpx.Response(200, json=ok))
        assert client.check_login()["status"] == "valid"
        client.close()


class TestHelpers:
    def test_play_url(self):
        detail = {"video": {"play_addr": {"url_list": ["https://cdn/x.mp4"]}}}
        assert play_url(detail) == "https://cdn/x.mp4"

    def test_play_url_missing_raises(self):
        with pytest.raises(DouyinApiError):
            play_url({})

    def test_sanitize_title(self):
        assert sanitize_title("a/b:c?d e") == "abcd_e"


class TestLinkParser:
    def test_direct_video(self):
        assert parse_douyin_url("https://www.douyin.com/video/7350000000000000001?x=1") == Link(
            "video", "7350000000000000001"
        )

    def test_share_text_with_short_link(self):
        class FakeResponse:
            url = "https://www.douyin.com/video/7350000000000000002?a=b"

        text = "8.97 复制打开抖音 https://v.douyin.com/AbCdEf/ 大家快看"
        assert resolve_link(text, fetch_impl=lambda url: FakeResponse()).id == "7350000000000000002"

    def test_unrecognized_raises(self):
        with pytest.raises(LinkParseError):
            resolve_link("不是链接", fetch_impl=lambda url: None)
