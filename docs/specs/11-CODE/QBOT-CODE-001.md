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
| 구현 | 폴더 구조 · `pyproject.toml`(uv) · Settings · Clock · PointInTime · SQLAlchemy 모델 22개 · Alembic 초기 마이그레이션 · `.env.example` · 비밀값 커밋 훅 · ruff · `docs/research/`에 검증 자료 이동 |
| 테스트 | 마이그레이션이 빈 DB에 적용된다. 필수 환경 변수가 없으면 시작이 거부된다. PointInTime.filings_before. FrozenClock KST 변환 |
| 선행 | 없음 |
| 완료 | main `4665f56` (2026-09-22). 리뷰 반영: created_at을 KST ISO 문자열로, FrozenClock이 어떤 시간대든 KST로 변환, alembic이 `~/.qbot` 생성과 MODE 반영, 훅의 40-hex 패턴 제거(커밋 해시 오탐), fill.broker_fill_no NOT NULL(SQLite NULL은 UNIQUE 중복 허용) |

#### B1 시점 고정 조회

| 항목 | 내용 |
|---|---|
| 근거 | [[QBOT-SCN-001#S11]] · [[QBOT-UC-001#UC-S1]] |
| 구현 함수 | [[QBOT-MS-001#MarketDataService.financials]] · [[QBOT-MS-001#MarketDataService.bars]] · MarketDataCrud · statuses_on |
| API | — |
| 테스트 | financials·bars의 테스트 관점 전부. 검증 자료의 재무 파일 표본(5종목)을 접수일=avail로 적재해 2025-05-30 기준 조회가 검증 스크립트의 선택 로직과 같은지 |
| 선행 | A |
| 완료 | main `a6c7c74` (2026-09-22). 테스트 11개. 리뷰 반영: 500일 규칙을 연간뿐 아니라 분기에도 적용, 직전 값은 "정확히 1년 전 같은 결산기"만, 일봉 거래대금 NULL을 NaN으로, 윤년 처리. 작성 중 발견: 500일 규칙을 조회 단계에서 걸면 직전 연도 행까지 사라져 성장률이 계산 불가 → 최신 행 선택 뒤에 적용 |

#### B2 점수와 선정

| 항목 | 내용 |
|---|---|
| 근거 | [[QBOT-SCN-001#S2]] · [[QBOT-UC-001#UC-S2]] |
| 구현 함수 | [[QBOT-MS-001#Scorer.score]] · [[QBOT-MS-001#Scorer.select]] · compute_factors · apply_universe · score_from_factors |
| API | — |
| 테스트 | 검증 패널 표본(월말 5개)으로 점수·상위 60 순서·선정이 검증 `simulate.py`의 식과 100% 일치. 21개월 전체는 로컬 파일을 `QBOT_RESEARCH_PANEL`로. 같은 입력 두 번 → 바이트 동일 |
| 선행 | B1 |
| 완료 | main `d2395d8` (2026-09-22). 테스트 8개. 작성 중 발견 2건: 지표가 하나라도 없는 종목을 백분위 계산 **전에** 빼야 분모가 같다. 동점 순서는 검증 코드가 임의였고(한 월말에 동점 75쌍) 봇은 명세대로 코드 순. 리뷰 반영: 0원 종가 마스킹, 거래대금 NaN 분모 제외, 인덱스 이름 고정, 빈 일봉은 DataNotReady |

#### B3 월말 판단과 재현

| 항목 | 내용 |
|---|---|
| 근거 | [[QBOT-SCN-001#S2]] · [[QBOT-SCN-001#S11]] · [[QBOT-SEQ-001#SEQ-2]] |
| 구현 함수 | [[QBOT-MS-001#DecisionService.decide_month_end]] · [[QBOT-MS-001#DecisionService.replay]] · DecisionCrud · seed_default_config · ToolRegistry · cli 입구 · `app/main.py` 조립 |
| API | [[QBOT-API-001#replay]] |
| 화면 | — |
| 테스트 | decide_month_end·replay의 테스트 관점을 가짜 시장 데이터로. ToolRegistry의 인가·스키마·confirm·cli_only. 명령줄 `replay` 실행 |
| 선행 | B2 |
| 완료 | main `cf34399` (2026-09-22). 테스트 15개. 리뷰 반영: 60위 밖에서 뽑힌 종목이 Score 저장에서 빠지던 문제 → 선정·건너뛴 행 전부 저장, 추세 필터를 검증과 같이, stored 비교 대상이 없으면 research_file로 폴백, diff 길이 오보, cli `--k=v` 파싱 |

#### C1 증권사·전자공시 어댑터와 수집

| 항목 | 내용 |
|---|---|
| 근거 | [[QBOT-SCN-001#S1]] · [[QBOT-SCN-001#S12]] · [[QBOT-SEQ-001#SEQ-1]] |
| 구현 함수 | [[QBOT-MS-001#MarketDataService.collect_daily]] · MarketDataService.backfill · Collector · KisAdapter(조회) · DartAdapter · `infra/kis_client.py` · `infra/kis_master.py` · `infra/dart_client.py` |
| API | [[QBOT-API-001#backfill]] · [[QBOT-API-001#install]] |
| 테스트 | collect_daily 테스트 관점(가짜 포트) 10개. 클라이언트 4개. 실제 API 연기 테스트(`QBOT_LIVE_TESTS=1`) |
| 선행 | A |
| 완료 | main `86b3927`, `93abad2`, `a257d4d` (2026-09-22). 발견: 종목 마스터 파일 하나에 전 종목의 이름·구분·상태·상장주식수가 있다(227/221자). 휴장일 조회는 실전 키 필요. 전자공시는 접수 일자만 준다. 리뷰 9건 반영(회사코드 정규식이 `<list>` 경계를 넘던 문제 등). **전자공시에 초당 간격이 없어 적재 중 IP 차단**(연결 리셋) → 초당 2건, 5·15·45·135초 재시도. 후속: 30일치를 한 트랜잭션으로 잡아 적재 중 봇의 모든 쓰기가 막히던 문제 → 공시 하나마다 커밋 |

#### C2 알림과 메신저 입구

| 항목 | 내용 |
|---|---|
| 근거 | [[QBOT-SCN-001#S7]] · [[QBOT-UC-001#UC-S5]] · [[QBOT-UC-001#UC-H1]] |
| 구현 함수 | OpsService.notify · heartbeat · record_command · status · TelegramAdapter · telegram 입구 |
| API | [[QBOT-API-001#status]] · [[QBOT-API-001#halt]] · [[QBOT-API-001#resume]] |
| 테스트 | 허용되지 않은 보낸 사람의 명령이 기록만 되고 실행되지 않는다. 계좌번호가 가려진다. 전송 실패가 예외를 올리지 않는다 |
| 선행 | A |
| 완료 | main `76a588d` (2026-09-22). 테스트 76개 통과, 실제 전송 확인. 리뷰 6건 반영: **메시지에 `confirm=true`를 직접 넣으면 확인 코드를 건너뛰던 문제**, 도구 실패 뒤 `session.rollback`이 없어 한 번의 DB 오류로 `/halt`까지 죽던 문제, 스티커가 오프셋을 안 넘겨 반복 수신, 밀린 알림 무한 재전송, `resume reset_peak`의 거짓 응답, `result_text` 미기록 |

#### D1 주문 관문과 주문 하나 보내기

| 항목 | 내용 |
|---|---|
| 근거 | [[QBOT-SCN-001#S3]] · [[QBOT-SCN-001#S5]] · [[QBOT-SEQ-001#SEQ-3]] · [[QBOT-SEQ-001#SEQ-5]] |
| 구현 함수 | [[QBOT-MS-001#OrderGate.check]] · [[QBOT-MS-001#OrderExecutor.send]] · [[QBOT-MS-001#OrderExecutor.reconcile_unknown]] · KisOrderAdapter · TradingCrud |
| API | — |
| 테스트 | 세 함수의 테스트 관점 전부. 모의 계좌로 실제 주문 1주 매수·매도 1회 |
| 선행 | C1, C2 |
| 완료 | main `ce8ed32` (2026-09-22). 테스트 40개. **조회 포트와 주문 포트를 나눴다**(조회는 실전 키, 주문은 모드 키 → [[QBOT-DOM-002]] 6장 미결 2 해소). 상태 흐름 planned → unknown(커밋) → 전송 → sent → filled/partial/cancelled. 증권사 응답을 셋으로 나눈다: 일시 오류(EGW00201)는 재시도 가능, 그 밖의 rt_cd≠0은 확실한 거부, 접속 실패는 도달 여부 불명. 체결은 누적값만 오므로 늘어난 수량과 그 구간 가격만 Fill로. 실호출: 잔고·주문내역 OK, 주문 전송은 `40580000 모의투자 장종료`(거래 ID·계좌·해시키·본문은 통과) — **체결 확인은 다음 거래일 장중 과제**. 자체 5건 + 리뷰 11건 반영(관문 거부가 멱등 키를 영구히 막던 문제, 잔량 칸 없을 때 살아 있는 주문을 취소로 읽던 문제, 취소 확인 없이 재주문, 주문번호가 날마다 새로 매겨지는데 어제 번호를 쓰던 문제 등) |

#### D2 판단 실행과 계좌 대조

| 항목 | 내용 |
|---|---|
| 근거 | [[QBOT-SCN-001#S3]] · [[QBOT-SCN-001#S4]] · [[QBOT-SCN-001#S13]] · [[QBOT-SEQ-001#SEQ-4]] |
| 구현 함수 | [[QBOT-MS-001#TradingService.execute]] · reconcile · accept_reconciliation · positions · equity_snapshot · retry_held_sells · resume_after_restart |
| API | [[QBOT-API-001#reconcile]] · [[QBOT-API-001#reconcile_accept]] · [[QBOT-API-001#positions]] |
| 테스트 | execute 테스트 관점. 모의 계좌에서 월말 판단 1회 실행. 앱에서 1주 팔고 저녁 대조가 불일치를 잡는지 |
| 선행 | B3, D1 |
| 완료 | main `6d793b6` (2026-09-22). 테스트 30개. 흐름은 SEQ-4대로 상태 확인 → 대조 → 매도 → **잔고 재조회** → 매수 → 지수 비중 조정이고, 핵심은 예산을 매도 대금이 들어온 실제 계좌에서 계산하는 것이다. 거래정지·부분 체결로 못 판 수량은 보류로 남기고 매일 다시 시도한다(멱등 키에 시도 번호 `#1`). 대조의 예수금 기준점은 시각이 아니라 체결 번호(`reconciliation.last_fill_id`) — 같은 초의 대조·체결은 시각으로 앞뒤를 못 가린다. 실호출: `qbot reconcile` ok(예수금 1,000만원). 실데이터 재현(2023-04-28) 0종목은 코드가 아니라 공시 부족(op_annual_prev 13행) 때문임을 확인. 리뷰 10건 반영(치명 2): **불일치 다음 대조가 저절로 ok가 되던 문제**, **관문이 전부 거부해도 done으로 닫아 그 달이 사라지던 문제** |

#### E 손실 한도, 스케줄, 보고

| 항목 | 내용 |
|---|---|
| 근거 | [[QBOT-SCN-001#S1]] · [[QBOT-SCN-001#S6]] · [[QBOT-SCN-001#S9]] · [[QBOT-SCN-001#S10]] · [[QBOT-INFRA-001]] 8장 |
| 구현 함수 | [[QBOT-MS-001#RiskService.record_valuation]] · RiskService.block_orders·unblock_orders · ReportingService.monthly·daily_summary · DecisionService.review_rules·approve_review · `entry/schedule/jobs.py` · 백업 · 자동 시작 등록 |
| API | [[QBOT-API-001#review_run]] · [[QBOT-API-001#review_approve]] |
| 테스트 | record_valuation 테스트 관점. 가짜 평가액으로 -30%를 만들어 buy_suspended가 켜지고 관문이 매수를 거부하는지. 월간 보고가 검증 자료와 같은 기준으로 나오는지 |
| 선행 | D2 |
| 완료 | main `ceca1be` (2026-09-22). 테스트 29개 추가(전체 179 통과). 스케줄은 INFRA 8.1 표 그대로 9개를 등록하고 `run` 도구가 상주 프로세스로 돌린다. **명세대로 짜면 안 되는 것 하나**: [[QBOT-MS-001#RiskService.record_valuation]] 4단계는 "고점과 (평가액-입금)을 비교"인데 그러면 1,000만원을 넣은 뒤에는 폭락해도 평가액이 늘 고점 위라 손실 한도가 영영 안 걸린다 → 입출금만큼 고점·원금 기준선을 같이 옮긴다. 외부 유입은 대조에서 사람이 "이만큼이 입출금"이라고 밝힌 금액만 센다(`reconciliation.external_flow` 추가). 08:40 동시호가 매도는 보내고 기다리지 않는다(기다리면 제한 시간에 걸려 취소된다) → `execute(phase=)`. 생존 신호는 매시 기록하되 메시지는 09시 한 번(24통이면 진짜 알림이 묻힌다). 백업은 SQLite 백업 API(WAL이라 파일 복사는 반쪽이 될 수 있다), 30일 보관. 자체 점검 3건: 스케줄 스레드와 메신저 입구가 같은 세션을 쓰던 경쟁(자물쇠 하나로 직렬화, 실행기 1개), 월간 수익률 시작값을 이 달 첫 기록으로 잡던 문제, WAL에서 읽다가 쓰기로 올라가면 busy_timeout이 듣지 않아 적재 중 모든 명령이 죽던 문제(BEGIN IMMEDIATE). 리뷰 13건 반영(높음 5): **도구 등록이 main.py에 반영되지 않아 스케줄·재점검을 부를 방법이 아예 없었다**(편집 실패를 못 잡았다), 자동 시작도 문구만 있었다, 같은 날 두 번 기록하면 입출금이 두 번 더해져 고점이 부풀던 문제, "계좌가 옳다"로 받아들인 차액 전부를 입출금으로 세어 놓친 손실이 손실 한도에서 사라지던 문제, `closes_on`이 판을 임의로 골라 재점검 t값이 수정주가와 원주가를 섞던 문제. 보통 5: 스케줄이 자물쇠 없이 Jobs를 만들던 문제, 동시호가 매도 결과를 버리던 문제, 오래된 partial 판단이 다음 달들을 가리던 문제, 첫날 입금 이중 계산, 재점검이 우주 필터 없이 t값을 내던 문제 |

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
| S11 과거 날짜 재현 | B3, C1 | 2025-05~2026-08 월말 20개 전부 검증 파일(`top60_by_month.csv`)과 일치. **대기 중**: 일봉 574만 행·지수·상태·달력(2026-11-06까지)은 끝. 공시는 2026-09-22 16:48 전자공시 차단이 풀려 이어받는 중(2023-05까지 진행). 하루 한도 2만 건이라 며칠 걸린다 |
| S12 처음 설치 | A, C1, C2 | 빈 컴퓨터에서 install → backfill → replay → 자동 시작까지. install은 실계좌로 확인(증권사·주문 계좌·전자공시·전략 설정 전부 OK) |
| S1 거래일 저녁 | C1, E | 실제 거래일에 수집·대조·평가액·요약이 순서대로 돈다. 평가액 기록은 실계좌로 1회 확인 |
| S2 + S3 월말 판단과 실행 | B3, D2 | 모의 계좌에서 판단 다음 날 10종목 매수, 다음 달 교체. 공시 적재가 끝나야 후보가 생긴다 |
| S5 재시작 | D1 | 주문 전송 직후 강제 종료 → 재시작 → 중복 주문 0건 |
| S6 + S7 손실 한도와 정지 | E, C2 | 한도 도달 시 매수 거부(테스트로 확인), `/halt` 뒤 미체결 취소, 재시작 후 정지 유지 |
| S13 계좌 불일치 | D2 | 앱에서 직접 매매 → 저녁 대조 불일치 → 주문 멈춤 → `/reconcile_accept`. 가짜 계좌로는 통과, 실계좌 확인은 S2·S3 뒤 |
| S8 실전 전환 | F | 관문 미달 거부, 통과 후 승인, 첫 달 매수 상한 |

## 3. 커밋·PR 목록

슬라이스 카드의 `완료` 행에 기록한다. 브랜치 이름은 `feat/<슬라이스>`이다. 머지는 main에 squash.

## 4. 미결사항

되먹일 것(A~C2): [[QBOT-DOM-003#fill]]의 `broker_fill_no`를 NOT NULL로. [[QBOT-DOM-001]] 1장의 "개념 20개"는 21개. [[QBOT-MS-001#MarketDataService.financials]] 4단계의 500일 규칙은 "최신 연간·분기 행"에만 적용. 같은 함수 5단계의 "전년 같은 분기"를 연간에도 "정확히 1년 전 결산기"로 통일. [[QBOT-MS-001#Scorer.score]] 1~2단계: `OPG_Q` 절단 범위는 [-3, 5], 임계값은 `|op_q_prev| >= 1억`, `OPG`는 `op_annual_prev > 0`일 때만. 3단계 뒤에 "이 제외를 백분위 계산 전에" 명시. [[QBOT-MS-001#DecisionService.decide_month_end]] 9단계는 "있는 만큼(최대 10개)의 평균". [[QBOT-MS-001#Scorer.select]]의 `top60`은 "상위 60 + 선정·건너뛴 행 전부". [[QBOT-PRD-001#R2]]의 "잠정실적"은 v1 미수집. collect_daily 4·6단계는 마스터 파일 1회로 대체. [[QBOT-INFRA-001]] 5장에 "휴장일 조회는 실전 키 필요", "조회는 실전 키·주문은 모드". 결산월 12월 가정. [[QBOT-API-001#resume]]에 "메신저의 `confirm` 인자는 무시". OpsService.notify의 밀린 알림은 최근 10건.

D1: [[QBOT-DOM-002]] 4.2에서 주문 함수를 빼고 4.4에 OrderBrokerPort를 둔다(6장 미결 2 해소). [[QBOT-MS-001#OrderExecutor.send]]에 `price`·`order_type` 추가, `rejected`는 최종 상태에서 빼고 다시 판정, 재시도 가격은 "남은 수량 시장가"(3장 미결 해소), 재시도도 `unknown` 선커밋. [[QBOT-MS-001#OrderGate.check]]에 "총평가액 0이면 매수 거부(no_equity)". [[QBOT-MS-001#OrderExecutor.reconcile_unknown]] 매칭에 "남은 수량"을 넣고 주문번호는 **그날** 것만 본다. [[QBOT-DOM-003#trade_order]]에 예상 체결가 칸이 없어 하루 주문 총액을 체결 금액 합으로만 계산한다. `fill.fee`·`tax`는 추정값. [[QBOT-INFRA-001]] 5장에 주문 거래 ID 표와 "모의투자는 장중에만 주문을 받는다", "전자공시는 초당 간격 없이 부르면 IP를 차단한다".

D2: [[QBOT-MS-001#TradingService.execute]] 1단계 앞에 "정지·주문 멈춤이면 실행하지 않는다", 8단계를 "보류·오류·관문 거부가 있으면 partial"로. 5단계 보류에 "일부만 체결된 매도의 남은 수량"도 포함하고 멱등 키에 시도 번호. reconcile의 판정 기준(보유 수량 차이 또는 "마지막으로 맞은 대조 + 그 뒤 우리 체결"로 설명되지 않는 예수금 차이, 허용 오차 max(1만원, 총평가액 1%))을 적는다. [[QBOT-DOM-003#reconciliation]]에 `last_fill_id`. [[QBOT-API-001]]에 명령줄 전용 `decide`·`execute`를 추가하고 날짜 인자에 `pattern`. [[QBOT-DOM-003#strategy_config]]에 지수 ETF 종목 코드 칸이 없어 상수(069500)로 뒀다.

E: [[QBOT-MS-001#RiskService.record_valuation]] 4단계를 "고점·원금을 입출금만큼 옮긴다"로 고친다(현재 문구대로면 입금 뒤 손실 한도가 무력화된다). 2단계의 "입출금 감지"는 "사람이 대조에서 밝힌 금액"으로 확정하고(3장 미결 해소) [[QBOT-DOM-003#reconciliation]]에 `external_flow`를 추가한다. 같은 날 두 번 기록해도 안전해야 한다는 조건을 넣는다. [[QBOT-API-001#reconcile_accept]]에 `external_flow` 인자 추가. [[QBOT-API-001]]에 `run`(상주 프로세스, 명령줄 전용) 추가. [[QBOT-INFRA-001]] 8.1의 "매시 생존 신호"는 "기록은 매시, 메시지는 하루 한 번". 8.2 자동 시작은 systemd 사용자 유닛으로 확정(9장 미결 해소). 3장에 "SQLite는 BEGIN IMMEDIATE + busy_timeout으로 쓴다"를 추가. [[QBOT-DOM-002]] 4.6 ReportingService의 `factor_effects`는 월간 보고에서 빼고 연 1회 재점검에만 둔다(월말마다 36개월치를 다시 계산하면 너무 느리다). [[QBOT-DOM-003#bot_state]]에 주문 멈춤 사유 칸이 없어 `status`가 최근 불일치 대조를 대신 보여 준다.

- [x] B2의 일치 테스트 입력 크기 → 월말 5개 표본, 21개월은 `QBOT_RESEARCH_PANEL`
- [x] C1 조회 키 → 실전 키로 수집, 주문은 모드에 따라
- [x] D1의 실제 주문 테스트 종목 → 삼성전자 1주, 장 시간이 아니면 건너뛴다
- [ ] 2019~2022년 재무는 호출량이 커서 v1 적재 범위(2023~)에서 뺐다. 2023~2026도 하루 한도 때문에 며칠 걸린다 — 일괄 파일에는 접수번호가 없어 시점 고정에 쓸 수 없으므로 API가 유일한 길이다
- [ ] 재점검의 시가총액은 현재 주식 수로 계산한다(과거 주식 수 미보관). 과거 월말 EP·SP·TURN20에 영향. 상장주식수 이력을 남길지 결정 필요
