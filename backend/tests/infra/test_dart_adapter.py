from app.domains.marketdata.adapters.dart import classify


def test_classify_titles():
    assert classify("사업보고서 (2024.12)") == ("annual", "11011", "2024")
    assert classify("반기보고서 (2025.06)") == ("half", "11012", "2025")
    assert classify("분기보고서 (2025.03)") == ("quarter", "11013", "2025")
    assert classify("[첨부추가]분기보고서 (2025.09)") == ("quarter", "11014", "2025")
    assert classify("[기재정정]사업보고서 (2024.12)") == ("correction", "11011", "2024")
    assert classify("주요사항보고서(유상증자결정)") == ("other", None, None)


def test_parse_corp_codes_does_not_cross_entries():
    from app.infra.dart_client import parse_corp_codes

    xml = """<result>
    <list><corp_code>00000001</corp_code><corp_name>비상장A</corp_name><stock_code> </stock_code></list>
    <list><corp_code>00000002</corp_code><corp_name>상장B</corp_name><stock_code>005930</stock_code></list>
    <list><corp_code>00000003</corp_code><corp_name>비상장C</corp_name><stock_code> </stock_code></list>
    </result>"""
    assert parse_corp_codes(xml) == {"005930": "00000002"}


def test_client_waits_between_calls_and_backs_off():
    """전자공시는 빠르게 몰아 부르면 연결을 끊는다. 간격과 긴 재시도가 있어야 한다."""
    import time as _t

    import httpx
    import pytest

    from app.core.errors import BrokerUnavailable
    from app.infra.dart_client import DartClient

    c = DartClient("k", rate_per_sec=20.0)
    c._http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"status": "000"})))
    c._get("list.json")
    t0 = _t.monotonic()
    c._get("list.json")
    assert _t.monotonic() - t0 >= 0.04  # 0.05초 간격

    slept = []
    c2 = DartClient("k", rate_per_sec=1000.0, retries=3)

    def boom(request):
        raise httpx.RemoteProtocolError("Server disconnected without sending a response.")

    c2._http = httpx.Client(transport=httpx.MockTransport(boom))
    import app.infra.dart_client as mod

    real_sleep = mod.time.sleep
    mod.time.sleep = slept.append
    try:
        with pytest.raises(BrokerUnavailable):
            c2._get("list.json")
    finally:
        mod.time.sleep = real_sleep
    assert [x for x in slept if x >= 1] == [5, 15, 45]  # 점점 길게 쉰다 (짧은 값은 간격 조절)
