# quant-rebalance-bot (QBOT)

개인용 국내 주식 월 1회 리밸런싱 봇. 과거 데이터로 검증한 재무·위험 지표 7개로 종목을 고르고, 집 컴퓨터에서 혼자 돈다. 언어모델과 GPU를 쓰지 않는다.

- 명세: `docs/specs/` (싱크독 11단계). 읽는 순서는 폴더 번호 순
- 검증 근거: `docs/research/`
- 코드: `backend/` (파이썬 3.12, uv)

판단은 전부 산수다. 모델도 언어모델도 판단 경로에 없다. 같은 날짜로 다시 돌리면 같은 답이 나와야 하고(`replay`), 그것이 이 봇을 믿을 수 있는 유일한 근거다.

## 설치

```bash
cd backend && uv venv && uv pip install -e ".[dev]"
mkdir -p ~/.qbot && cp ../.env.example ~/.qbot/.env   # 값을 채운다. 저장소에는 절대 넣지 않는다
bash ../tools/install-hooks.sh     # 커밋 전 비밀값 검사
.venv/bin/pytest -q
.venv/bin/alembic upgrade head     # ~/.qbot/qbot-paper.sqlite3 생성
.venv/bin/qbot install             # 증권사·전자공시·메신저·전략 설정 점검
```

## 처음 한 번: 과거 데이터 적재

```bash
.venv/bin/qbot backfill --from 2019-01-01 --sources calendar,status,index,bars   # 몇 시간
.venv/bin/qbot backfill --from 2023-01-01 --sources filings                      # 며칠 (전자공시 하루 한도 2만 건)
.venv/bin/qbot replay --asof 2025-05-30 --compare-to research_file               # 검증 파일과 같은지
```

전자공시는 초당 간격 없이 부르면 IP를 차단한다(클라이언트가 초당 2건으로 제한한다). 하루 한도에 걸리면 다음 날 같은 명령을 다시 돌리면 이어서 받는다.

## 운용

```bash
.venv/bin/qbot run                     # 상주 프로세스: 스케줄 + 메신저 명령
.venv/bin/qbot install --register-autostart   # 부팅 때 뜨게 (systemd 사용자 유닛)
```

스케줄(한국 시간, 거래일): 08:20 준비 · 08:40 장 시작 전 매도 · 09:05 체결 확인과 매수 · 15:40 미체결 취소 · 18:30 수집·대조·평가액·요약 · 18:50 월말 판단 · 19:00 월간 보고 · 19:30 백업.

상태를 한눈에 보려면 `bash tools/qstat.sh` (프로세스·데이터·최근 알림).

## 명령

메신저(`/이름 인자`)와 명령줄(`qbot 이름 --인자`)이 같은 도구를 부른다. 돈이 움직이거나 상태를 바꾸는 것은 확인이 필요하다(메신저는 6자리 코드).

| 도구 | 하는 일 |
|---|---|
| `status` | 모드·정지·주문 멈춤·대기 판단·생존 신호 |
| `positions` | 보유 종목, 평단, 손익 |
| `reconcile` | 계좌와 기록을 맞춰 본다 (고치지는 않는다) |
| `reconcile_accept --reason … [--external-flow 금액]` | 계좌를 옳다고 보고 기록을 고친다. 입출금이면 금액을 밝힌다 |
| `halt` / `resume [--reset-peak]` | 비상 정지 / 재개 |
| `replay --asof … [--compare-to stored\|research_file\|none]` | 과거 날짜 재현 |
| `review_run [--asof …]` / `review_approve --review-id N` | 연 1회 지표 재점검과 승인 |
| `gate_check` / `gate_approve --capital-krw … ` | 실전 전환 관문 점검과 승인 (명령줄) |
| `decide --asof …` / `execute` | 월말 판단·실행을 손으로 (스케줄이 평소에 부른다) |
| `backfill`, `install`, `run`, `telegram` | 명령줄 전용 |

## 안전 규칙

- 실전 주문은 모의 운용 관문(QBOT-PRD-001 R8)을 통과하고 운영자가 승인해야만 나간다. 설정 파일의 모드만 실전으로 바꾸면 봇이 시작을 거부한다
- 주문을 보내는 호출은 자동으로 다시 보내지 않는다. "보냈는지 모름" 상태로 적어 두고 재시작 때 증권사 내역과 맞춘다
- 미체결 주문을 장 마감 뒤까지 남기지 않는다 (봇이 꺼져 있는 동안 체결되지 않게)
- 고점 대비 -30%에서 새 매수를 멈춘다. 보유 종목을 자동으로 팔지는 않는다
- 계좌가 기록과 다르면 주문을 멈추고 사람을 부른다
- 비밀값은 `~/.qbot/.env`에만 둔다. `.env.example`은 이름만 있다
