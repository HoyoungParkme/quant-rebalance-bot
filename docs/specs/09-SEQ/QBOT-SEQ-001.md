---
doc_id: QBOT-SEQ-001
type: SEQ
title: 퀀트 리밸런싱 봇 시퀀스
status: draft
upstream: [QBOT-UC-001, QBOT-DOM-002, QBOT-API-001]
---

# SEQUENCE

## 0. 이 문서가 다루는 것

클래스 명세의 클래스들이 시간 순으로 어떻게 부르는지를 그린다. 흐름 6개만 그린다. 돈이 걸린 흐름과 재현성이 걸린 흐름이다. 나머지 유스케이스는 이 6개의 조합이거나 단순 조회라서 그리지 않는다.

2026-09-29에 구현·전수 시험에서 드러난 순서 오류(매수 단계의 체결 기록과 대조 순서)와 손절 규칙을 반영했다.

### 0.1 생명선

| 생명선 | 약어 | 실체 | 종류 | 정의한 곳 |
|---|---|---|---|---|
| 스케줄 | SCH | `entry/schedule/jobs.py` | 입구 | [[QBOT-DOM-002#ToolRegistry]] |
| 도구 등록표 | TOOLS | ToolRegistry | 입구 | [[QBOT-DOM-002#ToolRegistry]] |
| 시장 데이터 | MD | MarketDataService | 서비스 | [[QBOT-DOM-002#MarketDataService]] |
| 판단 | DS | DecisionService | 서비스 | [[QBOT-DOM-002#DecisionService]] |
| 점수기 | SC | Scorer | 순수 함수 | [[QBOT-DOM-002#Scorer]] |
| 매매 | TS | TradingService | 서비스 | [[QBOT-DOM-002#TradingService]] |
| 주문 실행기 | EX | OrderExecutor | 서비스 | [[QBOT-DOM-002#OrderExecutor]] |
| 관문 | GATE | OrderGate | 서비스 | [[QBOT-DOM-002#OrderGate]] |
| 위험 관리 | RS | RiskService | 서비스 | [[QBOT-DOM-002#RiskService]] |
| 운영 | OPS | OpsService | 서비스 | [[QBOT-DOM-002#OpsService]] |
| 데이터베이스 | DB | SQLite | 저장소 | [[QBOT-DOM-003]] |
| 증권사 | KIS | BrokerPort → KisAdapter | 외부 | [[QBOT-DOM-002#BrokerPort]] |
| 전자공시 | DART | FilingPort → DartAdapter | 외부 | [[QBOT-DOM-002#FilingPort]] |
| 메신저 | TG | NotifierPort → TelegramAdapter | 외부 | [[QBOT-DOM-002#OpsService]] |
| 운영자 | OP | 사람 | 액터 | [[QBOT-UC-001]] 1장 |

## 1. 시퀀스

#### SEQ-1 거래일 저녁 수집과 점검

근거: [[QBOT-UC-001#UC-A1]], [[QBOT-UC-001#UC-A5]], [[QBOT-UC-001#UC-A7]]

```mermaid
sequenceDiagram
  participant SCH
  participant MD
  participant KIS
  participant DART
  participant TS
  participant RS
  participant OPS
  participant DB
  Note over SCH: 20:10 — 당일 봉이 확정되는 20:00 뒤
  SCH->>MD: collect_daily(today)
  MD->>KIS: calendar / instrument_list / daily_bars (마지막 확정 봉 하나 앞부터)
  MD->>DB: daily_bar 추가, 잠정 봉은 확정값으로 덮음, index_level 갱신
  MD->>DART: list_filings(today-14) → financials(rcept_no)
  MD->>DB: filing·financial_snapshot 추가 (정정 90일 규칙)
  MD-->>SCH: CollectResult(실패·새 판·뒤늦게 바뀐 봉)
  SCH->>OPS: notify(error, 실패·새 판·바뀐 봉이 있으면)
  SCH->>TS: reconcile()
  TS->>KIS: balance()
  TS->>DB: reconciliation 저장
  alt 불일치
    TS->>RS: block_orders("reconcile")
    RS->>OPS: notify(error, 차이 목록)
  end
  SCH->>RS: record_valuation(today)
  RS->>TS: equity_snapshot()
  RS->>DB: valuation 저장, bot_state.peak 갱신
  RS->>RS: check_drawdown
  alt 한도 도달
    RS->>DB: bot_state.buy_suspended = 1
    RS->>OPS: notify(limit, …)
  end
  SCH->>TS: mark_stop_losses(today)
  TS->>DB: 보유 × 오늘 종가: close/avg_cost - 1 <= -0.15 → position.stop_loss_on = today
  TS-->>SCH: 손절 예정 목록
  opt 있음
    SCH->>OPS: notify(order, 내일 손절 매도 예정: 종목·평단·종가·손실률)
  end
  SCH->>OPS: notify(summary, 일일 요약)
  OPS->>TG: send
```

**읽을 때 볼 것**: 수집 실패는 흐름을 멈추지 않고 결과 목록에 담겨 알림으로 간다. 계좌 불일치·손실 한도·손절 예정은 여기서 **상태만** 바꾼다. 그 상태를 읽어 주문을 내거나 막는 것은 SEQ-3·SEQ-4다. 손절 표시는 지수 ETF를 건너뛰고, 이미 표시된 종목은 다시 알리지 않는다.

#### SEQ-2 월말 판단

근거: [[QBOT-UC-001#UC-A2]], [[QBOT-UC-001#UC-S1]], [[QBOT-UC-001#UC-S2]]

```mermaid
sequenceDiagram
  participant SCH
  participant DS
  participant MD
  participant SC
  participant TS
  participant DB
  participant OPS
  Note over SCH: 월말 20:40 (SEQ-1 뒤). 확정 봉이 직전 거래일의 95% 미만이면 보류·알림, 다음 날 07:00 재시도
  SCH->>DS: decide_month_end(asof, mode)
  DS->>DS: pit = PointInTime(asof)
  DS->>MD: universe(pit, filters)
  DS->>MD: bars(pit, 252)
  DS->>MD: financials(pit)
  Note over MD,DB: 접수일 < asof 인 filing만. 결산기간별 최신 스냅샷
  DS->>MD: statuses_on(pit)
  DS->>SC: score(bars, fin, mcap)
  SC-->>DS: 점수·순위 DataFrame
  DS->>TS: equity_snapshot()
  DS->>SC: select(scored, budget_per_slot, n, excluded)
  SC-->>DS: Selection(선정, 건너뛴 이유)
  DS->>MD: index_month_ends(pit, KOSPI, 10)
  alt 연속 10개월이 아님
    DS-->>SCH: DataNotReady (판단 거부, 알림)
  else trend_filter 켜짐 and 코스피 <= 10개월 평균
    DS->>DS: 선정 비움, cash_switch = 1
  end
  DS->>DB: decision(pending) + score 60행 저장
  DS->>OPS: notify(order, 내일 매도·매수 목록)
```

**읽을 때 볼 것**: 시장 데이터 조회가 전부 같은 `pit`를 받는다. Scorer는 DB도 시계도 모른다. 이 두 가지가 재현([[QBOT-UC-001#UC-H4]])이 같은 결과를 내는 이유다. 재현은 이 흐름을 FrozenClock으로 돌리고 마지막 두 단계를 "replay 저장, 알림 없음"으로 바꾼 것이다. 판단을 20:40에 하는 이유는 당일 봉이 20:00까지 바뀌기 때문이다([[QBOT-INFRA-001#C10]]) — 잠정 종가로 고르면 재현과 달라져 관문의 선정 일치를 못 채운다.

#### SEQ-3 주문 하나 보내기

근거: [[QBOT-UC-001#UC-S3]], [[QBOT-UC-001#UC-S6]], [[QBOT-INFRA-001#C8]]

```mermaid
sequenceDiagram
  participant TS
  participant EX
  participant DB
  participant GATE
  participant KIS
  TS->>EX: send(decision_id, code, side, qty, wait)
  EX->>EX: key = f"{decision_id}:{code}:{side}"
  EX->>DB: SELECT trade_order WHERE idem_key = key
  alt 이미 종료 상태
    EX-->>TS: 기존 Order
  else 없음 또는 planned
    EX->>GATE: check(req, snapshot)
    alt 거부
      EX->>DB: status = rejected, reject_reason
      EX-->>TS: Order(rejected)
    else 허용
      EX->>DB: INSERT status = planned (UNIQUE idem_key)
      EX->>DB: UPDATE status = unknown (커밋)
      EX->>KIS: place_order(req)  ※ 재시도 없음
      KIS-->>EX: broker_order_no
      EX->>DB: UPDATE broker_order_no, status = sent
      alt wait = False (동시호가)
        EX-->>TS: Order(sent)
      else
        loop 체결 확인 (제한 시간)
          EX->>KIS: order_status(no)
          Note over KIS: 모의는 주문별 내역이 없어 잔고 차이로 재구성
        end
        EX->>DB: fill 추가, status = filled/partial
        EX-->>TS: Order
      end
    end
  end
```

**읽을 때 볼 것**: `planned → unknown → sent` 순서가 핵심이다. `unknown`으로 바꾼 뒤에 전송하므로, 전송 직후 죽어도 재시작 때 "보냈을 수도 있는 주문"으로 남는다. 반대로 전송 전에 죽으면 `planned`라 안전하게 다시 보낼 수 있다. UNIQUE(idem_key)가 두 프로세스가 동시에 같은 주문을 넣는 것도 막는다.

#### SEQ-4 판단 실행 (08:40 매도, 09:05 매수)

근거: [[QBOT-UC-001#UC-A3]], [[QBOT-UC-001#UC-A7]]

```mermaid
sequenceDiagram
  participant SCH
  participant TS
  participant EX
  participant KIS
  participant DB
  participant OPS
  Note over SCH: 08:40 장 시작 전
  SCH->>TS: execute(decision, phase="sell")
  TS->>TS: reconcile() (SEQ-1과 같음)
  TS->>DB: decision.status = running
  TS->>DB: 매도 목록 = (보유 - 목표) ∪ 손절 예정(position.stop_loss_on)
  loop 매도 종목
    alt 거래정지
      TS->>DB: trade_order status = held
    else
      TS->>EX: send(…, sell, qty, wait=False)  (SEQ-3, 동시호가)
    end
  end
  Note over SCH: 09:05 장 시작 뒤
  SCH->>TS: execute(decision, phase="buy")
  TS->>EX: confirm_open_orders(decision)  ← 대조보다 먼저
  EX->>KIS: order_status / balance
  EX->>DB: fill 추가, sent → filled, position 갱신
  TS->>TS: reconcile()
  Note over TS: 순서를 바꾸면 계좌(팔림)와 기록(보유)이 어긋나 주문이 멈춘다
  TS->>KIS: balance()  (매도 대금 반영)
  TS->>TS: budget = equity × (1 - index_weight) / n
  loop 목표 - 보유 (점수 순, 이 판단에서 손절한 종목 제외)
    TS->>EX: send(…, buy, min(budget/price, cash/(price×1.02)))  (SEQ-3)
    Note over TS,EX: 산 만큼(여유 몫 포함) cash 차감. 부족하면 남은 종목 건너뜀
  end
  opt index_weight > 0 and 5%p 넘게 벗어남
    TS->>EX: send(…, 지수 ETF, buy/sell)
  end
  TS->>DB: decision.status = done (보류·거부·오류 있으면 partial)
  TS->>OPS: notify(fill, 체결 요약)
```

**읽을 때 볼 것**: 매도와 매수 사이에 `balance()`를 한 번 더 부른다. 예산을 판단 시점 평가액이 아니라 매도가 반영된 실제 현금으로 계산하기 위해서다. 판단 시점의 `budget_per_slot`는 참고값으로만 저장된다. 09:05 단계의 **첫 일은 체결 기록**이다 — 08:40 매도는 09시 동시호가에 체결되므로 09:05에는 계좌가 이미 팔린 상태이고, 기록을 먼저 맞추지 않고 대조하면 불일치로 봇이 멈춘다(2026-09-28 전수 시험: 매도가 있는 첫 달부터 2년 내내). 손절 예정 종목은 목표에 남아 있어도 판다. 그 자리는 이번 판단에서 다시 채우지 않는다.

#### SEQ-5 재시작 후 이어 하기

근거: [[QBOT-UC-001#UC-A4]]

```mermaid
sequenceDiagram
  participant SCH
  participant TS
  participant EX
  participant DB
  participant KIS
  participant OPS
  SCH->>TS: resume_after_restart()
  TS->>DB: bot_state, decision(running), trade_order(unknown) 읽기
  alt halted
    TS->>OPS: notify(restart, "정지 상태 유지")
  else
    TS->>EX: reconcile_unknown()
    EX->>KIS: orders_today()
    loop unknown 주문
      alt 같은 종목·방향·수량 주문이 증권사에 있음
        EX->>DB: broker_order_no 저장, status = sent
      else 없음
        EX->>DB: status = planned
      end
    end
    TS->>TS: reconcile()
    alt running 판단 있음 and 장중
      TS->>TS: execute(decision) 이어서 (SEQ-4)
    else 장 마감
      TS->>DB: 남은 주문은 내일로 (decision 유지)
    end
    TS->>OPS: notify(restart, 확정 결과)
  end
```

**읽을 때 볼 것**: `unknown` 주문을 확정하기 전에는 어떤 주문도 나가지 않는다. `orders_today()`는 조회 호출이라 재시도된다. 이 흐름이 끝나야 스케줄이 평소 작업을 다시 등록한다. 모의투자는 `orders_today()`가 비어 있으므로 확정은 저녁 대조(계좌 기준)로 넘어간다.

#### SEQ-6 실전 전환

근거: [[QBOT-UC-001#UC-H2]], [[QBOT-API-001#gate_check]], [[QBOT-API-001#gate_approve]]

```mermaid
sequenceDiagram
  participant OP
  participant TG
  participant TOOLS
  participant RS
  participant DB
  participant KIS
  OP->>TG: /gate_check
  TG->>TOOLS: run("gate_check", {}, sender)
  TOOLS->>TOOLS: 인가 확인, 스키마 검증, command 기록
  TOOLS->>RS: gate_check()
  RS->>DB: 모의 기간·교체·오류·일치율·수익차 집계
  RS->>DB: gate_record 저장 (pass/fail)
  RS-->>OP: 집계와 판정
  OP->>OP: 실전 키를 환경 변수에 넣고 봇 재시작
  OP->>TG: /gate_approve capital=3000000
  TG->>TOOLS: run("gate_approve", …)
  TOOLS-->>OP: 요약 + 확인 코드
  OP->>TG: 확인 코드
  TOOLS->>RS: gate_approve(capital, ratio, command)
  RS->>DB: 최근 gate_record.verdict == pass 확인
  RS->>KIS: balance() (실전 접속 시험)
  RS->>DB: bot_state.mode = live, gate_record_id, first_month_cap
  RS-->>OP: "실전 모드. 다음 월말부터 실제 주문"
```

**읽을 때 볼 것**: 관문 통과 기록을 확인하는 곳은 `gate_approve`와 SEQ-3의 관문 둘이다. 설정 파일만 바꿔서는 SEQ-3의 관문이 거부한다.

## 2. 대응표

| 유스케이스 | 시퀀스 |
|---|---|
| UC-A1 거래일 저녁 수집과 점검 | SEQ-1 |
| UC-A2 월말 판단 | SEQ-2 |
| UC-A3 판단 실행 | SEQ-4, SEQ-3 |
| UC-A4 재시작 후 이어 하기 | SEQ-5 |
| UC-A5 손실 한도 감시 | SEQ-1 |
| UC-A6 월간 성과 보고 | 단순 집계. 그리지 않음 |
| UC-A7 손절 감시 | SEQ-1 (표시), SEQ-4 (매도) |
| UC-H1 봇 정지와 재개 | SEQ-6의 도구 실행 경로와 같음. 그리지 않음 |
| UC-H2 실전 전환 | SEQ-6 |
| UC-H3 규칙 재점검 승인 | SEQ-6과 같은 경로. 그리지 않음 |
| UC-H4 과거 날짜 재현 | SEQ-2 변형 |
| UC-H5 설치와 적재 | SEQ-1의 수집 부분을 기간으로 반복. 그리지 않음 |
| UC-H6 계좌 기준으로 맞춤 | SEQ-6과 같은 경로. 그리지 않음 |
| UC-S1 시점 고정 조회 | SEQ-2 |
| UC-S2 종목 점수 계산 | SEQ-2 |
| UC-S3 주문 하나 보내기 | SEQ-3 |
| UC-S4 계좌 대조 | SEQ-1, SEQ-4, SEQ-5 |
| UC-S5 알림 | 모든 시퀀스의 OPS |
| UC-S6 주문 허용 판정 | SEQ-3 |
| UC-S7 외부 호출 재시도 | KIS·DART·TG 화살표 안에 숨어 있음 |

## 3. 되먹일 것

| 대상 | 내용 |
|---|---|
| [[QBOT-DOM-002#OrderExecutor]] | `send`가 `planned → unknown → sent` 세 번 상태를 바꾼다. "unknown으로 바꾼 뒤 전송"이 핵심이다 |
| [[QBOT-DOM-002#TradingService]] | 매도와 매수 사이에 `balance()`를 다시 부른다. 매수 단계의 첫 일은 체결 기록이다. `mark_stop_losses`가 새로 생겼다 |
| [[QBOT-DOM-003]] | `decision.budget_per_slot`은 참고값이다. `position.stop_loss_on`(손절 표시 날짜)이 새로 필요하다. `filing.numbers_rcept_no`(숫자를 가져온 공시)가 생겼다 |

## 4. 미결사항

- [x] SEQ-3의 체결 확인 제한 시간과 재시도 가격 폭 → v1은 남은 수량 시장가 재주문
- [x] SEQ-4에서 매도 체결이 부분 체결로 끝났을 때 매수 예산에 반영할지 → 실제 현금 기준이므로 자동 반영
- [ ] 모의 체결 재구성의 "보내기 전 잔고"가 프로세스 메모리에만 있다. 조회 시간 초과 뒤 다른 프로세스가 확인하면 체결을 못 본다(2026-09-29 실주문에서 겪음). DB에 남기는 것을 검토
- [ ] 조회(읽기) 호출의 시간 초과 재시도. 지금은 재시도 없이 바로 실패한다(UC-S7 1b1과 다르다)
