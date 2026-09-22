"""KisClient: 오류 응답은 예외, 토큰 무효 시 캐시 폐기."""

import json

import httpx
import pytest

from app.core.errors import BrokerUnavailable
from app.infra.kis_client import KisClient


def make(handler, tmp_path):
    c = KisClient("https://x", "k", "s", rate_per_sec=1000, token_cache=tmp_path / "tok.json")
    c._http = httpx.Client(transport=httpx.MockTransport(handler), timeout=5)
    return c


def test_non_transient_error_raises(tmp_path):
    def handler(req):
        if req.url.path.endswith("tokenP"):
            return httpx.Response(200, json={"access_token": "T", "access_token_token_expired": "2099-01-01 00:00:00"})
        return httpx.Response(200, json={"rt_cd": "1", "msg_cd": "OPSQ2000", "msg1": "모의투자 TR 이 아닙니다."})

    with pytest.raises(BrokerUnavailable, match="OPSQ2000"):
        make(handler, tmp_path).get("/x", "CTCA0903R", {})


def test_invalid_token_drops_cache_and_reissues(tmp_path):
    calls = {"token": 0}

    def handler(req):
        if req.url.path.endswith("tokenP"):
            calls["token"] += 1
            return httpx.Response(
                200, json={"access_token": f"T{calls['token']}", "access_token_token_expired": "2099-01-01 00:00:00"}
            )
        if req.headers["authorization"] == "Bearer OLD":
            return httpx.Response(500, json={"error_description": "기간이 만료된 token 입니다."})
        return httpx.Response(200, json={"rt_cd": "0", "output": {}})

    (tmp_path / "tok.json").write_text(json.dumps({"token": "OLD", "expires_at": "2099-01-01T00:00:00+09:00"}))
    body = make(handler, tmp_path).get("/x", "TR", {})
    assert (
        body["rt_cd"] == "0"
        and calls["token"] == 1
        and json.loads((tmp_path / "tok.json").read_text())["token"] == "T1"
    )
