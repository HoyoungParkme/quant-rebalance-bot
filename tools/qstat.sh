#!/usr/bin/env bash
# 한눈에 보는 봇 상태. `bash ~/.qbot/qstat.sh`
cd ~/dev/personal/quant-rebalance-bot/backend || exit 1
echo "=== 프로세스 ==="
pgrep -f "qbot run$" >/dev/null && echo "  봇: 돌고 있음 (pid $(pgrep -f 'qbot run$'))" || echo "  봇: 꺼져 있음"
pgrep -f "qbot backfill" >/dev/null && echo "  적재: 돌고 있음" || echo "  적재: 없음"
echo "=== 데이터 ==="
.venv/bin/python - <<'PY'
import pathlib, sqlite3
c = sqlite3.connect(f"file:{pathlib.Path.home()}/.qbot/qbot-paper.sqlite3?mode=ro", uri=True)
q = lambda s: c.execute(s).fetchone()
print("  일봉 {:,}행 ({} ~ {})".format(*q("select count(*), min(trade_date), max(trade_date) from daily_bar")))
print("  공시 {:,}건 (~{})  재무 {:,}".format(q("select count(*) from filing")[0],
      q("select max(rcept_date) from filing")[0], q("select count(*) from financial_snapshot")[0]))
v = q("select date, total, drawdown from valuation order by date desc limit 1")
print("  평가액 {} {:,}원 (고점대비 {:+.1%})".format(*v) if v else "  평가액 기록 없음")
print("  판단 {} · 주문 {} · 보유 {}".format(q("select count(*) from decision")[0],
      q("select count(*) from trade_order")[0], q("select count(*) from position where qty>0")[0]))
for r in c.execute("select sent_at, kind, substr(text,1,60) from alert order by id desc limit 3"):
    print(f"  알림 [{r[0][11:16]}] {r[1]}: {r[2]}")
PY
echo "=== 최근 로그 ==="
tail -2 ~/.qbot/bot.log; tail -1 ~/.qbot/backfill-filings.log
