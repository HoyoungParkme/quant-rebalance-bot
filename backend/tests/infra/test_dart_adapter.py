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
