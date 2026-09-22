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

스케줄이 스스로 부르는 일(수집, 판단, 주문)은 도구가 아니다. 그것은 시퀀스 문서가 다룬다.

## 1. 규칙

- 도구 이름은 소문자와 밑줄. 입력은 JSON 스키마로 검증하고, 검증에 실패하면 실행하지 않는다
- 메신저에서는 허용된 대화 번호 하나에서 온 명령만 받는다. 아닌 것은 기록하고 버린다([[QBOT-UC-001#UC-H1]])
- 돈을 움직이거나 상태를 바꾸는 도구는 `confirm` 인자가 `true`여야 실행된다. 메신저에서는 봇이 먼저 요약을 보여 주고 확인 답을 받아 채운다
- 모든 호출은 Command로 기록된다([[QBOT-DOM-001#Command]])
- 응답은 사람이 읽는 텍스트 한 덩어리다. 명령줄에서는 `--json`을 주면 같은 내용을 JSON으로 준다
- 명령줄 전용 도구(`install`, `backfill`)는 메신저에서 부를 수 없다. 설치 전에는 메신저가 없기 때문이다

## 2. 에러

| 코드 | 뜻 | 도구가 하는 것 |
|---|---|---|
| `unauthorized` | 허용되지 않은 보낸 사람 | 기록만 하고 응답하지 않는다 |
| `invalid_args` | 스키마 검증 실패 | 어느 인자가 왜 틀렸는지 답한다 |
| `not_confirmed` | `confirm`이 없음 | 요약을 보여 주고 확인을 요청한다 |
| `precondition` | 상태가 맞지 않음 (예: 정지 상태가 아닌데 재개) | 현재 상태를 답한다 |
| `gate_failed` | 관문 조건 미달 | 미달 조건과 수치를 답한다 |
| `broker_unavailable` | 증권사 접속 불가 | 재시도 안내. 상태는 바꾸지 않는다 |
| `busy` | 주문 실행 중이라 상태 변경 불가 | 끝난 뒤 다시 시도하라고 답한다 |

## 3. 도구

### 3.1 상태와 정지

#### status 현재 상태

유스케이스 [[QBOT-UC-001#UC-S5]] · 서비스 `OpsService.status`

모드, 정지·매수 중단·주문 멈춤 여부, 오늘 평가액, 고점 대비 손실, 보유 종목, 대기 중인 판단, 최근 생존 신호를 답한다.

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

정지와 매수 중단을 푼다. 손실 한도로 멈춘 상태였으면 `reset_peak`가 필수이고, 고점을 현재 평가액으로 다시 잡는다([[QBOT-PRD-001#R9]]).

```json
{"name":"resume","inputSchema":{"type":"object","properties":{"reset_peak":{"type":"boolean"},"confirm":{"type":"boolean"}},"required":["confirm"],"additionalProperties":false}}
```

### 3.2 계좌

#### reconcile 계좌 대조 실행

유스케이스 [[QBOT-UC-001#UC-S4]] · 서비스 `TradingService.reconcile`

지금 계좌를 조회해 봇 기록과 비교한 결과를 답한다. 기록을 고치지는 않는다.

```json
{"name":"reconcile","inputSchema":{"type":"object","properties":{},"additionalProperties":false}}
```

#### reconcile_accept 계좌 기준으로 맞춤

유스케이스 [[QBOT-UC-001#UC-H6]] · 서비스 `TradingService.accept_reconciliation`

가장 최근 불일치 대조를 계좌 기준으로 받아들여 보유 기록을 고치고 주문 멈춤을 푼다.

```json
{"name":"reconcile_accept","inputSchema":{"type":"object","properties":{"reason":{"type":"string","minLength":1,"maxLength":200},"confirm":{"type":"boolean"}},"required":["reason","confirm"],"additionalProperties":false}}
```

#### positions 보유 종목

서비스 `TradingService.positions`

종목별 수량, 평균 매입가, 현재가, 손익, 매수일을 답한다.

```json
{"name":"positions","inputSchema":{"type":"object","properties":{},"additionalProperties":false}}
```

### 3.3 실전 전환

#### gate_check 관문 점검

유스케이스 [[QBOT-UC-001#UC-H2]] · 서비스 `RiskService.gate_check`

모의 운용 집계(기간, 교체 횟수, 주문 오류, 선정 일치율, 월 수익 차이)와 통과 여부를 답하고 GateRecord를 남긴다.

```json
{"name":"gate_check","inputSchema":{"type":"object","properties":{},"additionalProperties":false}}
```

#### gate_approve 실전 전환 승인

유스케이스 [[QBOT-UC-001#UC-H2]] · 서비스 `RiskService.gate_approve`

가장 최근 통과한 GateRecord를 승인하고 실전 모드로 바꾼다. 실전 접속 정보가 환경 변수에 있어야 하고 접속 시험이 성공해야 한다.

```json
{"name":"gate_approve","inputSchema":{"type":"object","properties":{"capital_krw":{"type":"integer","minimum":1000000},"first_month_ratio":{"type":"number","minimum":0.1,"maximum":1.0,"default":0.3},"confirm":{"type":"boolean"}},"required":["capital_krw","confirm"],"additionalProperties":false}}
```

### 3.4 규칙

#### review_run 규칙 재점검 실행

유스케이스 [[QBOT-UC-001#UC-H3]] · 서비스 `DecisionService.review_rules`

기준일까지의 데이터로 후보 지표의 효과를 계산해 RuleReview를 만들고 결과를 답한다. 설정은 바꾸지 않는다.

```json
{"name":"review_run","inputSchema":{"type":"object","properties":{"asof":{"type":"string","format":"date"}},"additionalProperties":false}}
```

#### review_approve 재점검 결과 승인

유스케이스 [[QBOT-UC-001#UC-H3]] · 서비스 `DecisionService.approve_review`

지정한 RuleReview의 채택 목록으로 새 StrategyConfig를 만든다. 다음 월말 판단부터 적용된다.

```json
{"name":"review_approve","inputSchema":{"type":"object","properties":{"review_id":{"type":"integer"},"confirm":{"type":"boolean"}},"required":["review_id","confirm"],"additionalProperties":false}}
```

### 3.5 재현과 설치

#### replay 과거 날짜 재현

유스케이스 [[QBOT-UC-001#UC-H4]] · 서비스 `DecisionService.replay`

과거 월말을 기준일로 판단만 실행하고, 그날 저장된 판단(없으면 검증 파일)과 비교한 결과를 답한다. 주문은 나가지 않고 Decision은 "재현" 상태로 저장된다.

```json
{"name":"replay","inputSchema":{"type":"object","properties":{"asof":{"type":"string","format":"date"},"compare_to":{"type":"string","enum":["stored","research_file","none"],"default":"stored"}},"required":["asof"],"additionalProperties":false}}
```

#### backfill 과거 데이터 적재 (명령줄 전용)

유스케이스 [[QBOT-UC-001#UC-H5]] · 서비스 `MarketDataService.backfill`

시작일부터 오늘까지 시세, 공시, 재무, 종목 상태를 받는다. 끊기면 이어서 받는다.

```json
{"name":"backfill","inputSchema":{"type":"object","properties":{"from":{"type":"string","format":"date"},"sources":{"type":"array","items":{"type":"string","enum":["bars","filings","status","calendar","index"]},"default":["bars","filings","status","calendar","index"]},"research_prices_dir":{"type":"string","description":"상장폐지 종목의 과거 시세를 채울 검증 파일 폴더"}},"required":["from"],"additionalProperties":false}}
```

#### install 설치 점검 (명령줄 전용)

유스케이스 [[QBOT-UC-001#UC-H5]] · 서비스 `OpsService.install_check`

환경 변수, 증권사·전자공시·메신저 접속, 데이터베이스 파일, 자동 시작 등록을 점검하고 빠진 것을 답한다.

```json
{"name":"install","inputSchema":{"type":"object","properties":{"register_autostart":{"type":"boolean","default":false}},"additionalProperties":false}}
```

## 4. 에이전트 순서 (운영자가 도구를 부르는 순서)

| 상황 | 순서 |
|---|---|
| 처음 설치 | `install` → `backfill --from 2019-01-01 --research-prices-dir …` → `replay --asof 2025-05-30 --compare-to research_file` (몇 개 더) → `install --register-autostart` |
| 정지하고 싶다 | `halt --confirm` → 상황 확인 → `resume --confirm` |
| 손실 한도 알림을 받았다 | `positions` → 판단 → `resume --reset-peak --confirm` 또는 그대로 둠 |
| 계좌 불일치 알림을 받았다 | `reconcile` → 원인 확인 → `reconcile_accept --reason "…" --confirm` |
| 실전으로 넘어간다 | `gate_check` → 통과 확인 → 실전 키를 환경 변수에 넣고 재시작 → `gate_approve --capital-krw 3000000 --confirm` |
| 연초 | 봇이 `review_run` 결과를 보냄 → 읽고 `review_approve --review-id N --confirm` 또는 무시 |

## 5. 미결사항

- [ ] 메신저에서 `confirm`을 받는 방식. 제안은 봇이 요약과 함께 6자리 확인 코드를 보내고 운영자가 그 코드를 답하는 방식
- [ ] `halt` 상태에서 스케줄의 월말 판단을 계산까지는 할지 아예 건너뛸지. 제안은 계산하고 저장하되 주문은 내지 않음
