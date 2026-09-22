---
doc_id: QBOT-CODE-001
type: CODE
title: 퀀트 리밸런싱 봇 구현 계획
status: draft
upstream: [QBOT-MS-001, QBOT-SEQ-001, QBOT-SCN-001]
---

# 구현 계획

## 0. 이 문서가 다루는 것

구현을 슬라이스로 나누고 순서를 정한다. 슬라이스 하나는 하나의 브랜치, 하나의 PR이다. `완료` 행에 커밋을 기록한다.

순서의 원칙은 셋이다. 검증 코드와 같은 결과가 나오는 것을 먼저 확인한다. 실제 돈이 걸리는 부분은 마지막이다. 증권사 키 없이 할 수 있는 일을 앞에 둔다.

## 1. 슬라이스

#### A 기반

| 항목 | 내용 |
|---|---|
| 근거 | [[QBOT-INFRA-001]] · [[QBOT-DOM-002]] 1장 · [[QBOT-DOM-003]] |
| 구현 | 폴더 구조 · `pyproject.toml`(uv) · Settings · Clock · PointInTime · SQLAlchemy 모델 20개 · Alembic 초기 마이그레이션 · `.env.example` · gitleaks 커밋 훅 · ruff · `docs/research/`에 검증 자료 이동 |
| 테스트 | 마이그레이션이 빈 DB에 적용된다. 필수 환경 변수가 없으면 시작이 거부된다. PointInTime.filings_before |
| 선행 | 없음 |
| 완료 | — |

#### B1 시점 고정 조회

| 항목 | 내용 |
|---|---|
| 근거 | [[QBOT-SCN-001#S11]] · [[QBOT-UC-001#UC-S1]] |
| 구현 함수 | [[QBOT-MS-001#MarketDataService.financials]] · [[QBOT-MS-001#MarketDataService.bars]] · MarketDataCrud |
| API | — |
| 테스트 | financials·bars의 테스트 관점 전부. 검증 자료의 재무 파일을 적재해 2025-05-30 기준 조회가 검증 스크립트의 입력과 같은지 |
| 선행 | A |
| 완료 | — |

#### B2 점수와 선정

| 항목 | 내용 |
|---|---|
| 근거 | [[QBOT-SCN-001#S2]] · [[QBOT-UC-001#UC-S2]] |
| 구현 함수 | [[QBOT-MS-001#Scorer.score]] · [[QBOT-MS-001#Scorer.select]] |
| API | — |
| 테스트 | 검증 자료 `05-모의운용/scripts/simulate.py`와 같은 입력으로 20개월 전부 선정 종목 100% 일치. 같은 입력 두 번 → 바이트 동일 |
| 선행 | B1 |
| 완료 | — |

#### B3 월말 판단과 재현

| 항목 | 내용 |
|---|---|
| 근거 | [[QBOT-SCN-001#S2]] · [[QBOT-SCN-001#S11]] · [[QBOT-SEQ-001#SEQ-2]] |
| 구현 함수 | [[QBOT-MS-001#DecisionService.decide_month_end]] · [[QBOT-MS-001#DecisionService.replay]] · DecisionCrud · ToolRegistry · cli 입구 |
| API | [[QBOT-API-001#replay]] |
| 화면 | — |
| 테스트 | decide_month_end·replay의 테스트 관점. `qbot replay --asof 2025-05-30 --compare-to research_file`이 matched=True |
| 선행 | B2 |
| 완료 | — |

#### C1 증권사·전자공시 어댑터와 수집

| 항목 | 내용 |
|---|---|
| 근거 | [[QBOT-SCN-001#S1]] · [[QBOT-SCN-001#S12]] · [[QBOT-SEQ-001#SEQ-1]] |
| 구현 함수 | [[QBOT-MS-001#MarketDataService.collect_daily]] · KisAdapter(조회 부분) · DartAdapter · `infra/kis_client.py`(토큰, 초당 한도, 조회 재시도) · `infra/dart_client.py` · backfill |
| API | [[QBOT-API-001#backfill]] · [[QBOT-API-001#install]] |
| 테스트 | collect_daily 테스트 관점(가짜 포트). 모의 계좌로 실제 호출 1회: 종목 목록·일봉 10종목·거래일. 전자공시 실제 호출 1회: 어제 공시 목록 |
| 선행 | A. 모의 계좌 앱 키와 전자공시 키가 있어야 실제 호출 테스트를 돌린다 |
| 완료 | — |

#### C2 알림과 메신저 입구

| 항목 | 내용 |
|---|---|
| 근거 | [[QBOT-SCN-001#S7]] · [[QBOT-UC-001#UC-S5]] · [[QBOT-UC-001#UC-H1]] |
| 구현 함수 | OpsService.notify · heartbeat · record_command · status · TelegramAdapter · telegram 입구(가져오기, 인가, confirm 코드) |
| API | [[QBOT-API-001#status]] · [[QBOT-API-001#halt]] · [[QBOT-API-001#resume]] |
| 테스트 | 허용되지 않은 보낸 사람의 명령이 기록만 되고 실행되지 않는다. 계좌번호가 가려진다. 전송 실패가 예외를 올리지 않는다 |
| 선행 | A. 텔레그램 봇 토큰 필요 |
| 완료 | — |

#### D1 주문 관문과 주문 하나 보내기

| 항목 | 내용 |
|---|---|
| 근거 | [[QBOT-SCN-001#S3]] · [[QBOT-SCN-001#S5]] · [[QBOT-SEQ-001#SEQ-3]] · [[QBOT-SEQ-001#SEQ-5]] |
| 구현 함수 | [[QBOT-MS-001#OrderGate.check]] · [[QBOT-MS-001#OrderExecutor.send]] · [[QBOT-MS-001#OrderExecutor.reconcile_unknown]] · KisAdapter(주문 부분) · TradingCrud |
| API | — |
| 테스트 | 세 함수의 테스트 관점 전부. 모의 계좌로 실제 주문 1주 매수·매도 1회. 7단계 뒤 프로세스를 죽이고 재시작해 unknown이 확정되는지 |
| 선행 | C1, C2 |
| 완료 | — |

#### D2 판단 실행과 계좌 대조

| 항목 | 내용 |
|---|---|
| 근거 | [[QBOT-SCN-001#S3]] · [[QBOT-SCN-001#S4]] · [[QBOT-SCN-001#S13]] · [[QBOT-SEQ-001#SEQ-4]] |
| 구현 함수 | [[QBOT-MS-001#TradingService.execute]] · TradingService.reconcile · accept_reconciliation · positions · equity_snapshot · retry_held_sells · resume_after_restart |
| API | [[QBOT-API-001#reconcile]] · [[QBOT-API-001#reconcile_accept]] · [[QBOT-API-001#positions]] |
| 테스트 | execute 테스트 관점. 모의 계좌에서 월말 판단 1회를 실제로 실행해 10종목 보유가 되는지. 앱에서 1주 팔고 저녁 대조가 불일치를 잡는지 |
| 선행 | B3, D1 |
| 완료 | — |

#### E 손실 한도, 스케줄, 보고

| 항목 | 내용 |
|---|---|
| 근거 | [[QBOT-SCN-001#S1]] · [[QBOT-SCN-001#S6]] · [[QBOT-SCN-001#S9]] · [[QBOT-SCN-001#S10]] · [[QBOT-INFRA-001]] 8장 |
| 구현 함수 | [[QBOT-MS-001#RiskService.record_valuation]] · RiskService.halt·resume·block_orders · ReportingService.monthly·daily_summary·factor_effects · DecisionService.review_rules·approve_review · schedule 입구(스케줄 표 전부) · 백업 작업 · 자동 시작 등록 |
| API | [[QBOT-API-001#review_run]] · [[QBOT-API-001#review_approve]] |
| 테스트 | record_valuation 테스트 관점. 가짜 평가액으로 -30%를 만들어 buy_suspended가 켜지고 관문이 매수를 거부하는지. 월간 보고가 검증 자료의 2025년 수치와 같은 기준으로 나오는지 |
| 선행 | D2 |
| 완료 | — |

#### F 실전 전환

| 항목 | 내용 |
|---|---|
| 근거 | [[QBOT-SCN-001#S8]] · [[QBOT-SEQ-001#SEQ-6]] |
| 구현 함수 | RiskService.gate_check · [[QBOT-MS-001#RiskService.gate_approve]] · 실전 접속 설정 · first_month_cap 관문 조건 |
| API | [[QBOT-API-001#gate_check]] · [[QBOT-API-001#gate_approve]] |
| 테스트 | gate_approve 테스트 관점. 관문 기록 없이 설정만 live로 바꾸면 봇이 시작을 거부한다. 실전 키 없이 approve하면 모드가 유지된다 |
| 선행 | E, 그리고 모의 운용 3개월 통과 |
| 완료 | — |

## 2. 통합 테스트

| 시나리오 | 슬라이스 | 검증하는 것 |
|---|---|---|
| S11 과거 날짜 재현 | B3 | 2025-05~2026-08 월말 20개 전부 검증 파일과 일치 |
| S12 처음 설치 | A, C1, C2 | 빈 컴퓨터에서 install → backfill → replay → 자동 시작까지 |
| S1 거래일 저녁 | C1, E | 실제 거래일에 수집·대조·평가액·요약이 순서대로 돈다 |
| S2 + S3 월말 판단과 실행 | B3, D2 | 모의 계좌에서 판단 다음 날 10종목 매수, 다음 달 교체 |
| S5 재시작 | D1 | 주문 전송 직후 강제 종료 → 재시작 → 중복 주문 0건 |
| S6 + S7 손실 한도와 정지 | E, C2 | 한도 도달 시 매수 거부, `/halt` 뒤 미체결 취소, 재시작 후 정지 유지 |
| S13 계좌 불일치 | D2 | 앱에서 직접 매매 → 저녁 대조 불일치 → 주문 멈춤 → `/reconcile_accept` |
| S8 실전 전환 | F | 관문 미달 거부, 통과 후 승인, 첫 달 매수 상한 |

## 3. 커밋·PR 목록

슬라이스 카드의 `완료` 행에 기록한다. 브랜치 이름은 `feat/<슬라이스>`이다.

## 4. 미결사항

- [ ] B2의 일치 테스트에 쓸 검증 입력 파일의 크기. 20개월치 패널이 약 100MB라 저장소에 넣기 어렵다. 제안은 월말 5개만 골라 넣고 나머지는 로컬에서 돌린다
- [ ] C1에서 전 종목 일봉을 모의 계좌 한도(초당 1건)로 받으면 약 40분이 걸린다. 실전 키(초당 20건)로 수집하고 주문만 모의로 낼지. 제안은 실전 조회 키를 수집에 쓴다. 조회는 돈이 움직이지 않는다
- [ ] D1의 실제 주문 테스트를 모의 계좌 어느 종목으로 할지. 제안은 거래량 많은 대형주 1주
