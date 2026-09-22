---
doc_id: QBOT-MS-001
type: MS
title: 퀀트 리밸런싱 봇 미니스펙
status: draft
upstream: [QBOT-DOM-002, QBOT-SEQ-001, QBOT-API-001]
---

# MINISPEC

## 0. 이 문서가 다루는 것

시퀀스에 나오는 함수와, 검증 재현성이 걸린 순수 함수의 입력·처리·출력·예외를 정한다. 단순 조회는 간략형이다. 내부 타입(DTO)은 [[QBOT-DOM-002]]에 있는 이름을 쓴다.

2026-09-22에 구현(슬라이스 A~F)에서 드러난 오류를 반영했다. 굵게 쓴 곳이 처음 명세와 달라진 부분이고, 대부분 "그대로 따르면 조용히 잘못되는" 것들이었다.

## 1. 함수 목록

| 함수 | 종류 | 근거 |
|---|---|---|
| PointInTime.filings_before | 순수 | [[QBOT-SEQ-001#SEQ-2]] |
| MarketDataService.financials | 조회 | [[QBOT-SEQ-001#SEQ-2]] |
| MarketDataService.bars | 조회 | [[QBOT-SEQ-001#SEQ-2]] |
| MarketDataService.collect_daily | 쓰기 | [[QBOT-SEQ-001#SEQ-1]] |
| Scorer.score | 순수 | [[QBOT-SEQ-001#SEQ-2]] |
| Scorer.select | 순수 | [[QBOT-SEQ-001#SEQ-2]] |
| DecisionService.decide_month_end | 쓰기 | [[QBOT-SEQ-001#SEQ-2]] |
| DecisionService.replay | 쓰기 | [[QBOT-API-001#replay]] |
| OrderGate.check | 순수에 가까움 | [[QBOT-SEQ-001#SEQ-3]] |
| OrderExecutor.send | 쓰기·외부 | [[QBOT-SEQ-001#SEQ-3]] |
| OrderExecutor.reconcile_unknown | 쓰기·외부 | [[QBOT-SEQ-001#SEQ-5]] |
| TradingService.execute | 쓰기·외부 | [[QBOT-SEQ-001#SEQ-4]] |
| RiskService.record_valuation | 쓰기 | [[QBOT-SEQ-001#SEQ-1]] |
| RiskService.gate_approve | 쓰기·외부 | [[QBOT-SEQ-001#SEQ-6]] |

## 2. 함수

#### PointInTime.filings_before 공시 조회 상한일

**시그니처** `filings_before() -> date`

**처리**
1. `asof - 1일`을 돌려준다. 거래일 여부는 보지 않는다. 접수 일자는 달력일이기 때문이다

**테스트 관점** `asof=2025-05-30`이면 `2025-05-29`. 같은 날 접수 공시가 조회에서 빠지는지는 `financials` 테스트가 본다.

근거: [[QBOT-PRD-001#R3]]

#### MarketDataService.financials 시점 고정 재무 조회

**시그니처** `financials(pit: PointInTime) -> DataFrame`

**입력** `pit`. 그 외 인자 없음. 날짜를 직접 받는 공개 함수는 없다.

**처리**
1. `filing.rcept_date <= pit.filings_before()` 인 filing만 조인한다
2. `financial_snapshot`을 종목·`period_end`·`period_kind`로 묶고, 묶음마다 `rcept_date`가 가장 늦은 행(같으면 `rcept_no`가 큰 행) 하나를 남긴다
3. `if consolidated 행과 별도 행이 둘 다 있음 → consolidated 우선 · else → 있는 것`
4. 종목별 최신 연간·분기 행을 고른 **뒤에** `pit.asof - period_end > 500일`이면 버린다. 조회 단계에서 걸면 직전 연도 행까지 사라져 성장률을 계산할 수 없다
5. 종목별로 최신 연간 행 1개와 **정확히 1년 전 같은 결산기** 행, 최신 분기 행과 **정확히 1년 전 같은 분기** 행을 골라 한 행으로 편다. 짝이 없으면 NaN으로 두어 성장률 계산에서 빠지게 한다

**출력** 종목 코드를 인덱스로, 컬럼 `revenue, operating_income, net_income, equity, op_annual_prev, op_q, op_q_prev, shares`

**예외** | `pit`가 아닌 인자 | TypeError |

**호출하는 것** MarketDataCrud.filings_until, MarketDataCrud.snapshots_for

**테스트 관점** 기준일 당일 접수된 공시가 결과에 없어야 한다. 정정 공시가 기준일 뒤에 접수됐으면 원본 값이 나와야 한다. 같은 결산 기간에 원본과 정정이 둘 다 기준일 전이면 정정 값이 나와야 한다.

근거: [[QBOT-UC-001#UC-S1]] · [[QBOT-DOM-003#filing]]

#### MarketDataService.bars 시점 고정 일봉 조회

**시그니처** `bars(pit: PointInTime, lookback_days: int) -> DataFrame`

**처리**
1. 종목마다 `series_no`가 최대인 판만 고른다. `if pit.asof가 판이 생긴 날보다 이전 → 그 시점에 있던 판` (재현용)
2. `trade_date <= pit.bars_until()` 인 행 중 최근 `lookback_days`개
3. 종목 × 날짜의 종가·시가·거래량·거래대금 DataFrame으로 편다

**테스트 관점** 액면분할 후 새 판이 들어와도 분할 전 날짜를 기준으로 재현하면 옛 판이 나와야 한다.

근거: [[QBOT-DOM-003#daily_bar]] · [[QBOT-INFRA-001#C10]]

#### MarketDataService.collect_daily 거래일 수집

**시그니처** `collect_daily(d: date) -> CollectResult`

**입력** `d`. 보통 오늘.

**처리**
1. `if trading_calendar에 d가 없음 → 증권사 calendar()로 이번 달을 받아 저장`
2. `if d가 거래일 아님 → CollectResult(skipped=True) 반환`
3. 증권사 `instrument_list()`(마스터 파일 1회 내려받기)로 종목 목록·지정 상태·상장주식수를 한 번에 받아 새 종목은 추가, 폐지 종목은 `delisted_on` 채움, 코드 변경은 `prev_code`로 잇는다. **종목마다 현재가를 부르지 않는다**
4. 종목마다 `daily_bars(code, 마지막 저장일, d)`를 받는다. 초당 호출 한도는 어댑터가 지킨다. 실패한 종목은 `failed`에 담고 계속한다
5. 종목마다 저장된 어제 종가와 이번 응답의 어제 종가를 비교한다. `if 다름 → series_no + 1 로 그 종목의 과거 전체를 다시 받아 추가`
6. `index_close`로 코스피·코스피200 종가를 저장한다
7. 전자공시 `list_filings(d - 14일, d)`로 공시를 받고(반영이 며칠 늦는다), 각각 `financials(rcept_no)`로 수치를 받아 filing과 snapshot을 추가한다. **수치가 아직 없으면 공시도 넣지 않는다** — 넣어 두면 다음 수집이 건너뛰어 영원히 빠진다
8. 보유 종목의 공시 제목에 돌발 키워드가 있으면 `alerts`에 담는다
9. **단계마다·종목마다 커밋한다.** 3,500종목을 한 트랜잭션으로 잡으면 증권사를 부르는 몇 분 동안 적재도 메신저 명령도 전부 DB 잠금에 막힌다

**출력** `CollectResult(skipped, bars_ok, bars_failed, filings_added, series_bumped, alerts)`

**예외** | 증권사 접속 자체가 안 됨 | BrokerUnavailable. 호출자가 알림 후 다음 실행에서 채움 |

**호출하는 것** BrokerPort 전부, FilingPort 전부, MarketDataCrud

**테스트 관점** 가짜 BrokerPort로 어제 종가가 다른 응답을 주면 `series_bumped`에 그 종목이 들어가고 옛 판 행이 남아 있어야 한다.

근거: [[QBOT-UC-001#UC-A1]] · [[QBOT-INFRA-001]] 7장

#### Scorer.score 지표와 점수

**시그니처** `score(bars: DataFrame, fin: DataFrame, mcap: Series) -> DataFrame`

**입력** `bars`는 종목 × 최근 252거래일. `fin`은 `financials` 출력. `mcap`은 종목별 시가총액(종가 × 주식 수).

**처리**
1. 지표를 계산한다. `EP = net_income / mcap`, `SP = revenue / mcap`, `ROE = net_income / equity (equity > 0)`, `OPG = (operating_income - op_annual_prev) / |op_annual_prev| (op_annual_prev > 0)`, `OPG_Q = (op_q - op_q_prev) / |op_q_prev| (|op_q_prev| >= 1억)`, `VOL60 = 최근 60일 일수익률 표준편차`, `TURN20 = 최근 20일 거래대금 합 / mcap`
2. `OPG`, `OPG_Q`는 [-3, 5]로 자른다 (검증 quarterly.py·build_panel.py 기준)
3. 설정의 지표 7개 중 하나라도 NaN인 종목은 **백분위 계산 전에** 뺀다. 나중에 빼면 분모가 달라져 검증 코드와 순위가 어긋난다
4. 지표마다 `rank(pct=True)`를 구한다. 방향이 "낮을수록"이면 `1 - pct`
5. 백분위 7개의 합이 `total`. `total` 내림차순, 같으면 종목 코드 오름차순으로 `rank`

**출력** 종목 코드 인덱스, 컬럼 지표 7개, 백분위 7개, `total`, `rank`

**예외** 없음. 계산 불가 종목은 3단계에서 빠진다.

**테스트 관점** `docs/research/05-paper-run-1m/scripts/simulate.py`와 같은 입력 파일로 돌려 `rank`가 전 종목 일치해야 한다. 같은 입력을 두 번 돌리면 바이트 단위로 같은 출력이어야 한다.

근거: [[QBOT-PRD-001#R4]] · [[QBOT-UC-001#UC-S2]]

#### Scorer.select 종목 선정

**시그니처** `select(scored: DataFrame, budget_per_slot: int, n: int, excluded: set[str], last_price: Series) -> Selection`

**처리**
1. `rank` 순으로 순회한다
2. `if code in excluded → skip_reason = status · elif last_price[code] > budget_per_slot → skip_reason = price · else → 선정`
3. 선정이 `n`개가 되면 멈춘다. 순회한 종목까지의 `skip_reason`을 남긴다

**출력** `Selection(picked: list[str], skipped: dict[str, str], top60: DataFrame)`. `top60`은 상위 60에 더해 **선정·건너뛴 행 전부**를 담는다. 소자본에서는 1주 가격 때문에 60위 밖까지 내려가는데, 그 행이 빠지면 판단 기록에 뽑힌 종목이 없다

**테스트 관점** `n`보다 후보가 적으면 있는 만큼만 돌려주고 예외를 내지 않는다.

근거: [[QBOT-UC-001#UC-A2]]

#### DecisionService.decide_month_end 월말 판단

**시그니처** `decide_month_end(asof: date, mode: str) -> Decision`

**입력** `asof`는 월말 거래일. `mode`는 paper 또는 live.

**처리**
1. `if 같은 asof·mode의 실제 판단이 이미 있음 → 그것을 돌려주고 끝`
2. `pit = PointInTime(asof)`, `cfg = 현재 StrategyConfig`
3. `universe(pit, cfg.filters)`, `bars(pit, 252)`, `financials(pit)`, `statuses_on(pit)`
4. `mcap = 종가 × shares_outstanding`. 지금은 **현재 주식 수**를 쓴다(과거 주식 수를 보관하지 않는다. 3장 미결)
5. `scored = Scorer.score(...)`
6. `equity = trading.equity_snapshot().total`, `budget = equity × (1 - cfg.index_weight) / cfg.n_holdings`
7. `excluded = statuses_on의 managed·warning·danger·halted·liquidation 종목`
8. `sel = Scorer.select(scored, budget, cfg.n_holdings, excluded, 종가)`
9. `if cfg.trend_filter and 코스피 월말 종가가 "있는 만큼(최대 10개) 월말 종가의 평균"보다 높지 않으면 → sel.picked = [], cash_switch = 1` (검증 simulate.py 15행. 10개월이 안 차도 있는 만큼으로 판정한다)
10. `if cfg.index_weight > 0 and |현재 지수 비중 - cfg.index_weight| > 0.05 → index_rebalance = 1`
11. Decision(pending)과 Score(top60, selected 표시)를 한 트랜잭션으로 저장한다. `code_version`은 git 커밋 해시
12. 매도 예정(보유 - picked)과 매수 예정(picked - 보유)을 알림으로 보낸다

**출력** 저장된 Decision

**예외** | 오늘 시세 수집이 실패한 상태 | DataNotReady. 판단하지 않고 알림 | | 후보가 0개 | 예외 아님. picked 비고 알림 |

**호출하는 것** MarketDataService 4개, Scorer 2개, TradingService.equity_snapshot, DecisionCrud, OpsService.notify

**테스트 관점** 같은 `asof`로 두 번 부르면 두 번째는 저장하지 않고 첫 결과를 돌려준다. `replay`와 같은 `pit`로 돌린 결과가 `picked`까지 같아야 한다.

근거: [[QBOT-SEQ-001#SEQ-2]] · [[QBOT-UC-001#UC-A2]]

#### DecisionService.replay 과거 재현

**시그니처** `replay(asof: date, compare_to: str = "stored", portfolio: Portfolio | None = None, store: bool = True) -> ReplayResult`

**처리**
1. `decide_month_end`의 2~10단계를 `FrozenClock(asof)`로 실행한다. 자금은 **그날 저장된 판단의 예산**을 되살려 쓴다. 없으면 고정값(설정의 기준 자금). **오늘 계좌 잔고를 쓰면 안 된다** — 같은 날짜가 부를 때마다 다른 답을 낸다
2. Decision을 `status = replay`로 저장한다. 알림·주문 없음. `store=False`면 저장도 하지 않는다(관문 점검이 월마다 부르므로 그때마다 쌓이면 안 된다)
3. `if compare_to == stored → 같은 asof의 실제 판단과 picked 비교 · elif research_file → docs/research 의 선정 파일과 상위 60 순위 비교 · else → 비교 없음`

**출력** `ReplayResult(picked, compared_with, matched: bool | None, diff: list)`. 비교 대상이 없으면 `matched = None`이다(거짓이 아니라 "모른다")

**테스트 관점** 재현 저장이 실제 판단의 UNIQUE를 건드리지 않아야 한다. 재현 행이 "모의 운용 기간" 집계를 늘리지 않아야 한다(관문이 그 값을 본다).

근거: [[QBOT-API-001#replay]] · [[QBOT-UC-001#UC-H4]]

#### OrderGate.check 주문 허용 판정

**시그니처** `check(req: OrderRequest, snapshot: EquitySnapshot) -> GateVerdict`

**입력** `req`는 종목·방향·수량·예상 가격. `snapshot`은 현금·보유·총 평가액·오늘 체결 금액·이번 달 매수 금액.

**처리**
1. `state = bot_state(id=1)`
2. `if state.halted → 거부(halted)`
3. `if state.orders_blocked → 거부(reconcile)`
4. `if state.mode == live and state.gate_record_id is None → 거부(no_gate)`
5. `if req.side == buy and state.buy_suspended → 거부(drawdown)`
6. `if req.side == buy and snapshot.total <= 0 → 거부(no_equity)`. 평가액을 모르면 비중을 판정할 수 없다
7. `if req.side == buy and (보유 평가액[code] + req.qty × req.price) / snapshot.total > 0.15 → 거부(concentration)`
8. `if snapshot.today_order_amount + req.qty × req.price > snapshot.total × 2 → 거부(daily_cap)`
9. `if req.side == buy and state.first_month_cap is not None and 이번 달 매수 누계 + req.qty × req.price > state.first_month_cap → 거부(first_month)`
10. 허용

**출력** `GateVerdict(allowed: bool, reason: str | None)`

**예외** | `bot_state` 행이 없음 | 거부(no_state). 예외를 올리지 않는다. 행을 **만들지도 않는다** — 관문이 상태를 만들면 "상태가 없다"를 영영 못 잡는다 |

**테스트 관점** 조건 9개 각각을 켠 입력에서 그 이유로 거부돼야 한다. 매도는 5·6·7·9(매수에만 걸리는 것)를 건너뛴다.

근거: [[QBOT-UC-001#UC-S6]] · [[QBOT-PRD-001#R9]]

#### OrderExecutor.send 주문 하나 보내기

**시그니처** `send(decision_id: int, instrument_id: int, code: str, side: str, qty: int, price: int, order_type: str = "market", attempt: int = 0, wait: bool = True) -> Order`

`price`는 시장가면 예상 체결가(관문이 금액을 판정하는 데 쓴다), 지정가면 주문 가격이다. `attempt`는 같은 판단에서 같은 종목을 다시 팔 때 붙는 번호다(부분 체결의 남은 수량). `wait=False`는 보내고 체결을 기다리지 않는다 — 장 시작 전 동시호가 주문은 09시까지 체결되지 않으므로 기다리면 제한 시간에 걸려 취소된다.

**처리**
1. `key = f"{decision_id}:{code}:{side}"` (+ `attempt > 0`이면 `#{attempt}`)
2. `existing = crud.order_by_key(key)`
3. `if existing and existing.status in (filled, partial, cancelled) → existing 반환`. **`rejected`는 여기 없다** — 거부는 "확실히 체결되지 않았다"이고 정지·대조 중처럼 그때의 상태 때문이므로, 다시 부르면 다시 판정해야 한다. 최종으로 두면 상태를 풀어도 그 달 리밸런싱이 조용히 비어 버린다
4. `if existing and existing.status in (unknown, sent) → 체결 확인부터 (10단계)`
5. `verdict = gate.check(req, trading.equity_snapshot())`. `if not allowed → 행을 rejected로(있으면 그 행을 고쳐서) 남기고 반환`
6. `INSERT status=planned` (UNIQUE 충돌이면 2단계로 돌아간다)
7. `UPDATE status=unknown` 하고 **커밋한다**. 여기서 죽어도 흔적이 남아야 재시작 때 확인할 수 있다
8. `no = broker.place_order(req)`. 이 호출은 재시도하지 않는다
9. `UPDATE broker_order_no=no, status=sent`
10. `wait=False`면 여기서 끝낸다(체결 확인은 09:05에). 아니면 제한 시간 동안 `order_status(no)`를 폴링한다. 증권사는 누적 체결과 누적 평균가만 주므로 **늘어난 수량과 그 구간 가격만** `fill`로 넣는다(키는 `{증권사주문번호}:{누적수량}`). `if 전량 체결 → filled · elif 제한 시간 초과 → cancel_order로 **취소를 확인한 뒤에만** 재주문(확인 못 하면 살아 있는 주문 위에 또 내는 것이라 두 번 산다). 재시도는 남은 수량을 시장가로, 전송 전에 `unknown`으로 커밋하고 `attempt`를 올린다 · else → partial/cancelled`

**출력** 최종 상태의 Order

**예외** 증권사 응답을 셋으로 가른다. | 접속 실패·타임아웃(도달 여부 모름) | BrokerSendFailed. 상태 unknown 유지 | | 200 + rt_cd≠0 중 일시 오류(초당 한도 EGW00201 등) | BrokerUnavailable. 상태를 planned로 되돌린다(확실히 미체결) | | 그 밖의 rt_cd≠0 | 예외 아님. status=rejected |

**호출하는 것** OrderGate.check, OrderBrokerPort.place_order·order_status·cancel_order, TradingCrud

**테스트 관점** 7단계 뒤 예외를 강제로 내면 행이 `unknown`으로 남아야 한다. 같은 인자로 두 번 부르면 증권사 호출은 한 번이어야 한다. 취소가 확인되지 않으면 재주문하지 않아야 한다.

근거: [[QBOT-SEQ-001#SEQ-3]] · [[QBOT-INFRA-001#C8]]

#### OrderExecutor.reconcile_unknown 미확정 주문 확정

**시그니처** `reconcile_unknown() -> list[Order]`

**처리**
1. `unknowns = crud.orders_by_status(unknown)`. `if 비어 있음 → []`
2. `today = broker.orders_today()`
3. 각 unknown에 대해 `if today에 같은 종목·방향이고 수량이 원래 수량 **또는 남은 수량**인 주문이 있음 → broker_order_no 저장, status=sent · else → status=planned`. "아직 매칭 안 된"은 **그날** 쓰인 주문번호만 본다 — 증권사 주문번호는 날마다 새로 매겨져 어제 번호가 오늘 다시 나온다. 날짜로 좁히지 않으면 멀쩡한 짝을 놓치고 같은 주문이 두 번 나간다
4. `sent`로 바뀐 것은 체결 확인(send의 10단계)을 이어서 한다

**출력** 확정된 Order 목록

**테스트 관점** 증권사 내역에 같은 주문이 둘 있고 unknown이 하나면 하나만 매칭돼야 한다. 어제 쓴 번호가 오늘 짝을 막으면 안 된다.

근거: [[QBOT-SEQ-001#SEQ-5]] · [[QBOT-UC-001#UC-A4]]

#### TradingService.execute 판단 실행

**시그니처** `execute(decision: Decision, phase: str = "all") -> ExecutionResult`

**처리**
1. `if decision.status not in (pending, running, partial) → 예외 InvalidState`. 이어서 봇 상태를 본다: `if halted or orders_blocked → 예외 OrdersBlocked`
2. `rec = reconcile()`. `if rec.result == mismatch → 예외 OrdersBlocked`
3. `UPDATE decision.status=running`
4. `targets = decision의 selected 종목 + (index_weight > 0이면 지수 ETF)`
5. 매도 목록 = 보유 - targets. 각각 `if 오늘 거래정지 → status=held 저장 · else → executor.send(sell)`. **일부만 체결된 매도의 남은 수량도 보류로 남긴다**(다음 시도 번호의 행으로). 남겨 두지 않으면 같은 멱등 키가 최종 상태라 그 달 내내 못 판다
6. `bal = broker.balance()`, `budget = bal.total_equity × (1 - cfg.index_weight) / cfg.n_holdings`. **매도 뒤에 다시 조회한다** — 예산은 판단 시점 평가액이 아니라 매도 대금이 들어온 실제 계좌에서 나온다
7. 매수 목록 = targets - 보유, 점수 순. 각각 `qty = floor(min(budget, bal.cash) / 현재가)`. `if qty == 0 → 건너뜀 · else → executor.send(buy)`, 성공 시 `bal.cash` 차감
8. `if held·오류·관문 거부가 하나라도 있음 → status=partial · else → status=done`. **전부 거부당한 판단을 done으로 닫으면 안 된다** — done은 다시 실행할 수 없어 그 달 리밸런싱이 통째로 사라진다
9. 체결 요약을 알림으로 보낸다
10. `phase`로 나눠 부를 수 있다: `sell`(08:40 동시호가에 매도만, 기다리지 않음), `buy`(09:05 체결 확인 후 매수), `all`(한 번에). 스케줄이 [[QBOT-INFRA-001]] 8.1 표대로 나눠 부른다

**출력** `ExecutionResult(sold, bought, held, skipped, errors)`

**예외** | 관문 거부로 매수가 모두 막힘 | 예외 아님. skipped에 사유. 다만 status는 partial |

**테스트 관점** 매도 후 `balance()`가 매도 대금이 반영된 값을 주는 가짜 BrokerPort로, 매수 수량이 그 현금 기준으로 계산되는지 본다.

근거: [[QBOT-SEQ-001#SEQ-4]] · [[QBOT-UC-001#UC-A3]]

#### RiskService.record_valuation 일별 평가액

**시그니처** `record_valuation(d: date) -> Valuation`

**처리**
1. `snap = trading.equity_snapshot()`
2. `flow = 그날 사람이 "이만큼은 입출금"이라고 밝힌 금액`. 계좌 대조에서 `reconcile_accept --external-flow`로 받는다. 차액 전부를 입출금으로 세면 안 된다 — 거기에는 우리가 놓친 매매·수수료도 섞여 있고, 그것까지 기준선을 옮기면 **그 손실이 손실 한도에서 사라진다**
3. `already = 같은 날 Valuation의 external_flow (없으면 0)`, `new_flow = flow - already`. 같은 날 두 번 돌려도(재시작·수동 재실행) 두 번 더해지면 안 된다
4. `state.principal += new_flow`, `state.peak_equity += new_flow`. **입출금만큼 고점 기준선을 같이 옮긴다.** "평가액 - 입금"을 고점과 비교하면 1,000만원을 넣은 뒤에는 평가액이 늘 고점 위라 폭락해도 한도에 걸리지 않는다
5. `if snap.total > state.peak_equity → state.peak_equity = snap.total`
6. `drawdown = snap.total / state.peak_equity - 1`, `pnl = snap.total / state.principal - 1`
7. Valuation 저장(같은 날이면 갱신), bot_state 갱신
8. `if drawdown <= -0.30 and not state.buy_suspended → state.buy_suspended = 1, 알림`

**출력** 저장된 Valuation

**테스트 관점** 100만원 입금 직후 평가액이 100만원 늘어도 drawdown이 0이어야 한다. 그리고 그 뒤 폭락하면 **입금 전과 같은 비율에서** 한도에 걸려야 한다. 같은 날 두 번 불러도 원금·고점이 같아야 한다. 한도에 걸린 뒤 관문이 실제로 매수를 거부해야 한다(기록만 하면 소용없다).

근거: [[QBOT-UC-001#UC-A5]] · [[QBOT-PRD-001#R9]]

#### RiskService.gate_approve 실전 전환 승인

**시그니처** `gate_approve(capital: int, first_month_ratio: float, command: Command) -> GateRecord`

**처리**
1. `rec = 가장 최근 GateRecord`. `if rec is None or rec.verdict != pass → 예외 GateFailed`. 이미 승인된 기록이면 Precondition
2. `if settings.live 접속 정보가 비어 있음 → 예외 Precondition`
3. **실전 키로** `balance()` 접속 시험. 실패면 `BrokerUnavailable`, 아무것도 바뀌지 않는다. 모의 키로 시험하면 오타난 실전 계좌가 그대로 통과한다
4. `rec.approved_by_command_id = command.id, approved_at = now, capital, first_month_ratio` 저장
5. **실전 데이터베이스를 만들어** 통과 기록과 상태(`mode=live, gate_record_id, live_since, first_month_cap = capital × first_month_ratio, principal = 0, peak_equity = 0`)를 심는다. 모의와 실전은 파일이 다르므로([[QBOT-INFRA-001]] 6장) 모의 쪽에만 남기면 실전으로 시작할 때 빈 파일을 열어 영영 거부된다. **돌고 있는 모의 상태는 건드리지 않는다** — 재시작 전까지 모의가 첫 달 상한에 막히거나 손실 기준선이 지워지면 안 된다
6. 알림 "실전 전환 승인. 재시작해야 실전 주문이 나간다"

**출력** 승인된 GateRecord

**테스트 관점** `verdict == fail`인 기록만 있으면 모드가 바뀌지 않아야 한다. 접속 시험이 실패하면 실전 DB가 만들어지지 않아야 한다. 실전 DB가 이미 있으면 덮어쓰지 않아야 한다.

근거: [[QBOT-SEQ-001#SEQ-6]] · [[QBOT-API-001#gate_approve]]

## 3. 미결사항

- [x] `record_valuation`의 입출금 감지 → 사람이 계좌 대조에서 밝힌 금액만 센다(`reconciliation.external_flow`). 증권사 입출금 내역 조회에 기대지 않는다
- [x] `OrderExecutor.send`의 재시도 가격 폭 → v1은 "남은 수량 시장가 재주문". 호가 2단계는 호가 조회가 필요해 v1 범위를 넘는다
- [ ] `Scorer.score`의 시가총액에 쓸 주식 수. 지금은 **현재 주식 수**를 과거 월말에도 그대로 쓴다(과거 주식 수를 보관하지 않아서). 과거 재현의 EP·SP·TURN20에 영향이 있다. 상장주식수 이력을 남길지 결정 필요
- [ ] 실전 첫 달 뒤 "체결 오차 1%p 이내면 나머지 투입"([[QBOT-PRD-001#R8]])을 실행할 함수가 없다. 첫 달 상한을 푸는 경로가 필요하다
