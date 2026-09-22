"""마스터 파일 파서: 고정폭 규격 합과 표본 줄."""

from app.infra import kis_master as m


def test_width_sums_match_tail():
    assert sum(m.KOSPI_WIDTHS) == 227 and sum(m.KOSDAQ_WIDTHS) == 221


def test_parse_sample_line():
    head = "005930   KR7005930003삼성전자"
    tail = list(" " * 227)
    tail[0:2] = "ST"
    tail[34 + 0] = "N"  # halted
    tail[35 + 0] = "N"
    tail[36 + 0] = "Y"  # managed
    # 위치 계산: 앞 필드 폭의 누적합
    pos = [0]
    for w in m.KOSPI_WIDTHS:
        pos.append(pos[-1] + w)
    t = list(" " * 227)
    t[pos[0] : pos[1]] = "ST"
    t[pos[34]] = "N"
    t[pos[36]] = "Y"
    t[pos[37] : pos[38]] = "02"
    t[pos[49] : pos[50]] = "19750611"
    t[pos[50] : pos[51]] = "000000005846278"
    t[pos[54]] = "0"
    line = (head + "".join(t)).encode("cp949") + b"\n"
    rows = m.parse(line, "KOSPI")
    assert len(rows) == 1
    r = rows[0]
    assert (r.code, r.name, r.kind, r.managed, r.halted, r.warn_code) == (
        "005930",
        "삼성전자",
        "common",
        True,
        False,
        "02",
    )
    assert r.listed_on == "1975-06-11" and r.shares_outstanding == 5_846_278_000
