---
doc_id: QBOT-API-001
type: API
title: 퀀트 리밸런싱 봇 운영자 명령 API (MCP 도구 형식)
status: draft
upstream: [QBOT-UC-001, QBOT-DOM-001, QBOT-INFRA-001]
---

# API 명세 — MCP 도구 형식

## 0. 이 문서가 다루는 것

이 봇에는 HTTP 입구가 없다([[QBOT-INFRA-001#C4]]). 사람이 봇에게 시키는 일은 메신저 명령과 명령줄 명령 둘뿐이고, 둘은 같은 도구 목록을 부른다([[QBOT-INFRA-001#C11]]). 그래서 REST가 아니라 MCP 도구 형식으로 적는다. 도구 이름이 메신저에서는 `/이름 인자`, 명령줄에서는 `qbot 이름 --인자` 로 나타난다.

스케줄이 스스로 부르는 일(수집, 판단, 주문)은 도구가 아니다. 그것은 시퀀스 문서가 다룬다. 다만 스케줄이 생기기 전에 손으로 돌릴 수 있도록 `decide`·`execute`를 명령줄 전용 도구로 둔다.

## 1. 규칙

- 도구 이름은 소문자와 밑줄. 입력은 JSON 스키마로 검증하고, 검증에 실패하면 실행하지 않는다
- 날짜 인자는 `format: date`만으로는 걸러지지 않는다. `pattern`을 함께 준다
- 메신저에서는 허용된 대화 번호 하나에서 온 명령만 받는다. 아닌 것은 기록하고 버린다([[QBOT-UC-001#UC-H1]])
- 돈을 움직이거나 상태를 바꾸는 도구는 `confirm` 인자가 `true`여야 실행된다. 메신저에서는 봇이 6자리 확인 코드를 보내고 그 코드를 받아 채운다. **메시지에 직접 쓴 `confirm`은 버린다** — 안 버리면 확인 절차가 통째로 우회된다
- 확인 코드는 스키마 검증을 **통과한 뒤에** 요구한다. 그래야 잘못된 인자를 코드 입력 전에 알려 줄 수 있다
- 모든 호출은 Command로 기록된다([[QBOT-DOM-001#Command]]). 인가 실패도 기록한다
- 응답은 사람이 읽는 텍스트 한 덩어리다. 명령줄에서는 `--json`을 주면 같은 내용을 JSON으로 준다
- 명령줄 전용 도구(`install`, `backfill`, `run`, `decide`, `execute`, `gate_check`, `telegram`)는 메신저에서 부를 수 없다. 설치 전에는 메신저가 없고, 오래 걸리는 작업은 메신저에서 부르면 그동안 스케줄이 막힌다

## 2. 에러

| 코드 | 뜻 | 도구가 하는 것 |
|---|---|---|
| `unauthorized` | 허용되지 않은 보낸 사람 | 기록만 하고 응답하지 않는다 |
| `invalid_args` | 스키마 검증 실패 | 어느 인자가 왜 틀렸는지 답한다 |
| `not_confirmed` | `confirm`이 없음 | 요약과 확인 코드를 보낸다 |
| `precondition` | 상태가 맞지 않음 | 현재 상태를 답한다 |
| `gate_failed` | 관문 조건 미달 | 미달 조건과 수치를 답한다 |
| `broker_unavailable` | 증권사 접속 불가 | 재시도 안내. 상태는 바꾸지 않는다 |
| `orders_blocked` | 계좌 불일치로 주문이 멈춰 있음 | 대조 내용을 답한다 |

도구가 실패하면 세션을 되돌린 뒤 기록한다. 되돌리지 않으면 한 번의 오류로 세션이 망가져 그 뒤 `halt`까지 듣지 않는다.

## 3. 도구

### 3.1 상태와 정지

#### status 현재 상태

유스케이스 [[QBOT-UC-001#UC-S5]] · 서비스 `OpsService.status`

모드, 정지·매수 중단·주문 멈춤 여부, 대기 중인 판단, 최근 생존 신호를 답한다. 주문이 멈춰 있으면 최근 불일치 대조도 같이 보여 준다.

```json
{"name":"status","inputSchema":{"type":"object","properties":{},"additionalProperties":false}}
```

#### halt 정지

유스케이스 [[QBOT-UC-001#UC-H1]] · 서비스 `RiskService.halt`

새 주문을 막고 미체결 주문을 취소한다. 수집과 기록은 계속한다.

```json
{"name":"halt","inputSchema":{"type":"object","properties":{"reason":{"type":"string","maxLength":200},"confirm":{"type":"boolean"}},"required":["confirm"],"additionalProperties":false}}
```

#### resume 재개

유스케이스 [[QBOT-UC-001#UC-H1]] · 서비스 `RiskService.resume`

정지와 매수 중단을 푼다. 손실 한도로 멈춘 상태였으면 `reset_peak`가 필수이고, 고점을 다음 평가 때 새로 잡는다([[QBOT-PRD-001#R9]]).

```json
{"name":"resume","inputSchema":{"type":"object","properties":{"reset_peak":{"type":"boolean"},"confirm":{"type":"boolean"}},"required":["confirm"],"additionalProperties":false}}
```

### 3.2 계좌

#### reconcile 계좌 대조 실행

유스케이스 [[QBOT-UC-001#UC-S4]] · 서비스 `TradingService.reconcile`

지금 계좌를 조회해 봇 기록과 비교한 결과를 답한다. 기록을 고치지는 않는다. 다르면 주문을 멈춘다.

```json
{"name":"reconcile","inputSchema":{"type":"object","properties":{},"additionalProperties":false}}
```

#### reconcile_accept 계좌 기준으로 맞춤

유스케이스 [[QBOT-UC-001#UC-H6]] · 서비스 `TradingService.accept_reconciliation`

가장 최근 불일치 대조를 계좌 기준으로 받아들여 보유 기록을 고치고 주문 멈춤을 푼다. `external_flow`는 그 차액 중 **진짜 입출금인 금액**이다. 기본값 0은 "우리 기록이 틀렸다"는 뜻이고, 그때는 원금·고점 기준선을 건드리지 않아 손실이 손실 한도에 그대로 남는다.

```json
{"name":"reconcile_accept","inputSchema":{"type":"object","properties":{"reason":{"type":"string","minLength":1,"maxLength":200},"external_flow":{"type":"integer"},"confirm":{"type":"boolean"}},"required":["reason","confirm"],"additionalProperties":false}}
```

#### positions 보유 종목

서비스 `TradingService.positions`

계좌 기준 종목별 수량, 평균 매입가, 현재가, 평가액, 손익을 답한다. 매수일은 봇 기록에서 가져온다.

```json
{"name":"positions","inputSchema":{"type":"object","properties":{},"additionalProperties":false}}
```

### 3.3 실전 전환

#### gate_check 관문 점검 (명령줄 전용)

유스케이스 [[QBOT-UC-001#UC-H2]] · 서비스 `LiveGate.check`

모의 운용 집계(기간, 교체 횟수, 주문 오류, 선정 일치율, 월 수익 차이)와 통과 여부를 답하고 GateRecord를 남긴다. 월마다 재현을 한 번씩 돌리므로 몇 분 걸린다. **못 잰 조건은 통과가 아니라 미달이다.**

```json
{"name":"gate_check","inputSchema":{"type":"object","properties":{},"additionalProperties":false}}
```

#### gate_approve 실전 전환 승인

유스케이스 [[QBOT-UC-001#UC-H2]] · 서비스 `LiveGate.approve`

가장 최근 통과한 GateRecord를 승인하고 **실전 데이터베이스를 만든다**. 실전 접속 정보가 환경 변수에 있어야 하고, 실전 키로 한 접속 시험이 성공해야 한다. 승인해도 돌고 있는 프로세스는 그대로 모의로 주문한다 — `QBOT_MODE=live`로 다시 시작해야 실전이다.

```json
{"name":"gate_approve","inputSchema":{"type":"object","properties":{"capital_krw":{"type":"integer","minimum":1000000},"first_month_ratio":{"type":"number","minimum":0.1,"maximum":1.0,"default":0.3},"confirm":{"type":"boolean"}},"required":["capital_krw","confirm"],"additionalProperties":false}}
```

### 3.4 규칙

#### review_run 규칙 재점검 실행

유스케이스 [[QBOT-UC-001#UC-H3]] · 서비스 `DecisionService.review_rules`

기준일까지의 데이터로 후보 지표의 월별 상위-하위 십분위 차이와 t값을 계산해 RuleReview를 만들고 결과를 답한다. 설정은 바꾸지 않는다.

```json
{"name":"review_run","inputSchema":{"type":"object","properties":{"asof":{"type":"string","format":"date","pattern":"^\\d{4}-\\d{2}-\\d{2}$"},"months":{"type":"integer","minimum":7,"maximum":120}},"additionalProperties":false}}
```

#### review_approve 재점검 결과 승인

유스케이스 [[QBOT-UC-001#UC-H3]] · 서비스 `DecisionService.approve_review`

지정한 RuleReview의 채택 목록으로 새 StrategyConfig를 만든다. 다음 날부터 적용된다. 채택 목록이 비어 있으면 거부한다.

```json
{"name":"review_approve","inputSchema":{"type":"object","properties":{"review_id":{"type":"integer","minimum":1},"confirm":{"type":"boolean"}},"required":["review_id","confirm"],"additionalProperties":false}}
```

### 3.5 재현과 설치

#### replay 과거 날짜 재현

유스케이스 [[QBOT-UC-001#UC-H4]] · 서비스 `DecisionService.replay`

과거 월말을 기준일로 판단만 실행하고, 그날 저장된 판단(없으면 검증 파일)과 비교한 결과를 답한다. 주문은 나가지 않고 Decision은 "재현" 상태로 저장된다. 자금은 그날 판단의 예산을 되살려 쓴다(오늘 계좌 잔고를 쓰면 같은 날짜가 매번 다른 답을 낸다).

```json
{"name":"replay","inputSchema":{"type":"object","properties":{"asof":{"type":"string","format":"date","pattern":"^\\d{4}-\\d{2}-\\d{2}$"},"compare_to":{"type":"string","enum":["stored","research_file","none"],"default":"stored"}},"required":["asof"],"additionalProperties":false}}
```

#### decide 월말 판단 실행 (명령줄 전용)

서비스 `DecisionService.decide_month_end`

스케줄이 평소에 부르는 것을 손으로 부른다. 그날 종목을 정해 "대기" 상태로 저장한다.

```json
{"name":"decide","inputSchema":{"type":"object","properties":{"asof":{"type":"string","format":"date","pattern":"^\\d{4}-\\d{2}-\\d{2}$"}},"required":["asof"],"additionalProperties":false}}
```

#### execute 판단 실행 (명령줄 전용)

서비스 `TradingService.execute`

대기 중인 판단을 주문으로 옮긴다. 스케줄은 이것을 08:40(매도)과 09:05(매수)로 나눠 부른다.

```json
{"name":"execute","inputSchema":{"type":"object","properties":{"asof":{"type":"string","format":"date","pattern":"^\\d{4}-\\d{2}-\\d{2}$"},"confirm":{"type":"boolean"}},"required":["confirm"],"additionalProperties":false}}
```

#### run 상주 실행 (명령줄 전용)

스케줄과 메신저 입구를 함께 돌린다([[QBOT-INFRA-001#C3]]). 자동 시작이 실행하는 것이 이것이다.

```json
{"name":"run","inputSchema":{"type":"object","properties":{},"additionalProperties":false}}
```

#### backfill 과거 데이터 적재 (명령줄 전용)

유스케이스 [[QBOT-UC-001#UC-H5]] · 서비스 `MarketDataService.backfill`

시작일부터 오늘까지 시세, 공시, 재무, 종목 상태를 받는다. 끊기면 이어서 받는다. 전자공시 하루 한도에 걸리면 다음 날 같은 명령을 다시 돌린다.

```json
{"name":"backfill","inputSchema":{"type":"object","properties":{"from":{"type":"string","format":"date","pattern":"^\\d{4}-\\d{2}-\\d{2}$"},"sources":{"type":"array","items":{"type":"string","enum":["bars","filings","status","calendar","index"]}},"research_prices_dir":{"type":"string"}},"required":["from"],"additionalProperties":false}}
```

#### install 설치 점검 (명령줄 전용)

유스케이스 [[QBOT-UC-001#UC-H5]] · 서비스 `OpsService.install_check`

환경 변수, 증권사 조회·주문 접속, 전자공시, 전략 설정, 데이터베이스를 점검하고 빠진 것을 답한다. `register_autostart`를 주면 systemd 사용자 유닛을 만든다.

```json
{"name":"install","inputSchema":{"type":"object","properties":{"register_autostart":{"type":"boolean","default":false}},"additionalProperties":false}}
```

## 4. 에이전트 순서 (운영자가 도구를 부르는 순서)

| 상황 | 순서 |
|---|---|
| 처음 설치 | `install` → `backfill --from 2019-01-01 --sources calendar,status,index,bars` → `backfill --from 2023-01-01 --sources filings` → `replay --asof … --compare-to research_file` (몇 개) → `install --register-autostart` → `run` |
| 정지하고 싶다 | `halt --confirm` → 상황 확인 → `resume --confirm` |
| 손실 한도 알림을 받았다 | `positions` → 판단 → `resume --reset-peak --confirm` 또는 그대로 둠 |
| 계좌 불일치 알림을 받았다 | `reconcile` → 원인 확인 → `reconcile_accept --reason "…" [--external-flow 금액] --confirm` |
| 실전으로 넘어간다 | `gate_check` → 통과 확인 → 실전 키를 환경 변수에 넣고 → `gate_approve --capital-krw 3000000 --confirm` → `QBOT_MODE=live`로 재시작 |
| 연초 | `review_run` → 결과를 읽고 `review_approve --review-id N --confirm` 또는 무시 |

## 5. 미결사항

- [x] 메신저에서 `confirm`을 받는 방식 → 6자리 확인 코드(5분). 메시지에 직접 쓴 `confirm`은 버린다
- [ ] `halt` 상태에서 스케줄의 월말 판단을 계산까지는 할지 아예 건너뛸지. 지금은 계산하고 저장하되 주문은 관문이 막는다
- [ ] 실전 첫 달 뒤 나머지 자금을 투입하는 도구가 없다([[QBOT-PRD-001#R8]] 마지막 줄). 첫 달 상한을 푸는 경로가 필요하다
