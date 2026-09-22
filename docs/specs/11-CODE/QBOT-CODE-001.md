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
| 구현 | 폴더 구조 · `pyproject.toml`(uv) · Settings · Clock · PointInTime · SQLAlchemy 모델 22개 · Alembic 초기 마이그레이션 · `.env.example` · 비밀값 커밋 훅(gitleaks 있으면 그것, 없으면 내장 패턴) · ruff · `docs/research/`에 검증 자료 이동 |
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
| 완료 | main `a6c7c74` (2026-09-22). 테스트 11개. 리뷰 반영: 500일 규칙을 연간뿐 아니라 분기에도 적용, 직전 값은 "정확히 1년 전 같은 결산기"만(빠지면 NaN → 성장률 제외), 일봉 거래대금 NULL을 0이 아닌 NaN으로, 윤년 2월 29일 처리. 작성 중 발견: 500일 규칙을 조회 단계에서 걸면 직전 연도 행까지 사라져 성장률이 계산 불가 → 최신 행 선택 뒤에 적용 |

#### B2 점수와 선정

| 항목 | 내용 |
|---|---|
| 근거 | [[QBOT-SCN-001#S2]] · [[QBOT-UC-001#UC-S2]] |
| 구현 함수 | [[QBOT-MS-001#Scorer.score]] · [[QBOT-MS-001#Scorer.select]] · compute_factors · apply_universe · score_from_factors |
| API | — |
| 테스트 | 검증 패널 표본(월말 5개, `tests/fixtures/research_panel_sample.csv`)으로 점수·상위 60 순서·선정이 검증 `simulate.py`의 식과 100% 일치. 21개월 전체는 로컬 파일을 `QBOT_RESEARCH_PANEL`로 주면 돈다(통과 확인). 같은 입력 두 번 → 바이트 동일. 지표 식은 `build_panel.py`와 직접 비교 |
| 선행 | B1 |
| 완료 | main `d2395d8` (2026-09-22). 테스트 8개. 작성 중 발견 2건: 지표가 하나라도 없는 종목을 백분위 계산 **전에** 빼야 분모가 같다. 동점 순서는 검증 코드가 정렬 알고리즘에 따라 임의였고(한 월말에 동점 75쌍) 봇은 명세대로 코드 순. 리뷰 반영: 0원 종가를 이전 값으로 마스킹(검증과 동일), 거래대금 NaN을 20일 평균 분모에서 제외, 결과 인덱스 이름을 `code`로 고정, 빈 일봉은 DataNotReady |

#### B3 월말 판단과 재현

| 항목 | 내용 |
|---|---|
| 근거 | [[QBOT-SCN-001#S2]] · [[QBOT-SCN-001#S11]] · [[QBOT-SEQ-001#SEQ-2]] |
| 구현 함수 | [[QBOT-MS-001#DecisionService.decide_month_end]] · [[QBOT-MS-001#DecisionService.replay]] · DecisionCrud · seed_default_config · ToolRegistry · cli 입구 · `app/main.py` 조립 |
| API | [[QBOT-API-001#replay]] |
| 화면 | — |
| 테스트 | decide_month_end·replay의 테스트 관점을 가짜 시장 데이터(종목 15개, 일봉 260일, 연간·분기 재무)로. ToolRegistry의 인가·스키마·confirm·cli_only. 명령줄 `replay` 실행. **실제 데이터로 `qbot replay --asof 2025-05-30 --compare-to research_file`이 matched=True인지는 C1 적재 뒤 S11 통합 테스트에서** |
| 선행 | B2 |
| 완료 | main `cf34399` (2026-09-22). 테스트 15개. 평가액·지수 부분은 D2 전이라 `portfolio_fn` 주입(지금은 계획 자금 300만원), 알림은 C2 전이라 `notifier` 콜백. 검증 상위 60 순위 파일 `docs/research/05-paper-run-1m/top60_by_month.csv`(21개월) 추가. 리뷰 반영: 60위 밖에서 뽑힌 종목이 Score 저장에서 빠지던 문제(소자본에서 실제 발생) → 선정·건너뛴 행 전부 저장, 추세 필터를 검증과 같이 "있는 달만큼의 평균"으로, stored 비교 대상이 없으면 research_file로 폴백, diff 길이 오보, cli `--k=v`와 도구 앞 옵션 파싱 |

#### C1 증권사·전자공시 어댑터와 수집

| 항목 | 내용 |
|---|---|
| 근거 | [[QBOT-SCN-001#S1]] · [[QBOT-SCN-001#S12]] · [[QBOT-SEQ-001#SEQ-1]] |
| 구현 함수 | [[QBOT-MS-001#MarketDataService.collect_daily]] · MarketDataService.backfill · Collector(`marketdata/collect.py`) · KisAdapter(조회) · DartAdapter · `infra/kis_client.py` · `infra/kis_master.py` · `infra/dart_client.py` |
| API | [[QBOT-API-001#backfill]] · [[QBOT-API-001#install]] |
| 테스트 | collect_daily 테스트 관점(가짜 포트) 10개: 종목·상태·일봉·지수, 상태 종료와 상장폐지, 수정주가 판 상승, 4분기 = 연간 - 3분기 누적, 다른 본의 숫자 거부, 재무 없는 공시는 다음 수집에 재시도, 3주 공백 뒤 구멍 없이 채움, 마스터 급감 시 폐지 처리 안 함, backfill 이어받기. 클라이언트 4개(오류 응답 예외, 토큰 무효 시 캐시 폐기, 마스터 규격, 회사코드 블록 파싱). 실제 API 연기 테스트 4개(`QBOT_LIVE_TESTS=1`): 일봉·지수·종목 목록·휴장일·전자공시 공시 목록·재무 → 통과 |
| 선행 | A. 모의 계좌 앱 키와 전자공시 키가 있어야 실제 호출 테스트를 돌린다 |
| 완료 | main `86b3927`, `93abad2`, `a257d4d` (2026-09-22). 발견: 종목 마스터 파일(kospi/kosdaq_code.mst) 하나에 전 종목의 이름·구분·관리종목·거래정지·경고·상장주식수가 있어 종목별 현재가 호출이 필요 없다. 공식 파서는 개행 포함 228/222자를 자르므로 개행을 뗀 여기서는 227/221자. 휴장일 조회(CTCA0903R)는 모의 키에서 "모의투자 TR이 아닙니다" → 조회는 실전 키가 필요. 전자공시 API는 접수 일자만 준다. 리뷰 9건 반영: 회사코드 표 정규식이 `<list>` 경계를 넘어 비상장사 코드가 상장사에 붙던 문제(재무가 엉뚱한 종목에 저장될 수 있었음), 오류 응답을 "자료 없음"으로 삼키던 문제, 토큰 무효 시 캐시 재사용, 재무 없는 공시가 영구 누락, 10일 고정 되돌아보기의 구멍, 잘린 달의 월말 오표시, 4분기 유도 시 연결·별도 혼용, 마스터 파일 비정상 시 대량 폐지, collect_daily 미커밋. 후속 2건: backfill 스레드에 ORM 객체 전달로 SQLite 스레드 오류 → 문자열만 전달. **전자공시에 초당 간격이 없어 적재 중 IP 차단을 당했다**(연결 리셋, API·일괄파일 모두) → 초당 2건 간격과 5·15·45·135초 재시도 추가 |

#### C2 알림과 메신저 입구

| 항목 | 내용 |
|---|---|
| 근거 | [[QBOT-SCN-001#S7]] · [[QBOT-UC-001#UC-S5]] · [[QBOT-UC-001#UC-H1]] |
| 구현 함수 | OpsService.notify · heartbeat · record_command · status · TelegramAdapter · telegram 입구(가져오기, 인가, confirm 코드) |
| API | [[QBOT-API-001#status]] · [[QBOT-API-001#halt]] · [[QBOT-API-001#resume]] |
| 테스트 | 허용되지 않은 보낸 사람의 명령이 기록만 되고 실행되지 않는다. 계좌번호가 가려진다. 전송 실패가 예외를 올리지 않는다 |
| 선행 | A. 텔레그램 봇 토큰 필요 |
| 완료 | main `76a588d` (2026-09-22). 테스트 76개 통과(전체), 실제 텔레그램 전송 확인. 구조: `infra/telegram_client.py`(sendMessage·getUpdates 롱폴링) → `ops/adapters/telegram.py`(NotifierPort 구현) → `ops/service.py`(Masker, OpsService) / `risk/service.py`(halt·resume) / `entry/telegram/poller.py`(`/도구 k=v` 구문, 6자리 확인 코드 5분). 명령 기록은 인가 실패도 남긴다. 알림은 `[모의]/[실전]` 접두어. 리뷰 6건 반영: **메시지에 `confirm=true`를 직접 넣으면 확인 코드를 건너뛰던 문제**(폴러가 confirm 인자를 버린다), 도구 실패·폴러 예외 뒤 `session.rollback`이 없어 한 번의 DB 오류로 `/halt`까지 죽던 문제, 글자 없는 메시지(스티커)가 오프셋을 안 넘겨 같은 메시지를 반복 수신, 밀린 알림을 무한정 재전송하던 것을 새 알림 뒤 최근 10건으로, `resume reset_peak`가 고점을 실제로 재설정하지 않으면서 답은 재설정이라 하던 문제(D2 전까지 고점 0으로 두고 답에 그대로 적음), `command.result_text` 미기록. 발견: 확인 코드는 `needs_confirm` 도구의 스키마 검증 **뒤**에 요구해야 잘못된 인자를 코드 입력 전에 알려줄 수 있다 |

#### D1 주문 관문과 주문 하나 보내기

| 항목 | 내용 |
|---|---|
| 근거 | [[QBOT-SCN-001#S3]] · [[QBOT-SCN-001#S5]] · [[QBOT-SEQ-001#SEQ-3]] · [[QBOT-SEQ-001#SEQ-5]] |
| 구현 함수 | [[QBOT-MS-001#OrderGate.check]] · [[QBOT-MS-001#OrderExecutor.send]] · [[QBOT-MS-001#OrderExecutor.reconcile_unknown]] · KisOrderAdapter · TradingCrud · TradingService.equity_snapshot·cancel_open |
| API | — |
| 테스트 | 세 함수의 테스트 관점 전부. 모의 계좌로 실제 주문 1주 매수·매도 1회. 7단계 뒤 프로세스를 죽이고 재시작해 unknown이 확정되는지 |
| 선행 | C1, C2 |
| 완료 | main `ce8ed32` (2026-09-22). 테스트 40개 추가(전체 119 통과). 구조: `trading/ports.py`(OrderBrokerPort·OrderRequest·BrokerOrder·Balance·EquitySnapshot) → `trading/adapters/kis.py` → `trading/service.py`(OrderExecutor·TradingService), 관문은 `risk/service.py`의 OrderGate. **조회 포트와 주문 포트를 나눴다**(조회는 실전 키, 주문은 모드 키라 클라이언트가 다르다 → [[QBOT-DOM-002]] 6장 미결 2 해소). 상태 흐름 planned → unknown(커밋) → 전송 → sent → filled/partial/cancelled. 증권사 응답을 셋으로 나눈다: 200+rt_cd≠0 중 일시 오류(EGW00201 등)는 재시도 가능, 그 밖의 rt_cd≠0은 확실한 거부, 접속 실패·타임아웃은 도달 여부 불명(unknown 유지). 체결은 누적값만 오므로 늘어난 수량과 그 구간 가격만 Fill로 넣고 키는 `{증권사주문번호}:{누적수량}`. 실호출 확인: 잔고·오늘 주문내역 OK(모의 예수금 1,000만원), 주문 전송은 15:37 시도라 `40580000 모의투자 장종료` — 거래 ID·계좌·해시키·본문은 통과했고 **체결 확인만 다음 거래일 장중 과제**(`QBOT_LIVE_TESTS=1 QBOT_LIVE_ORDER=1 pytest tests/live -k roundtrip`). 자체 점검 5건: 거부가 planned 행에 안 남던 문제, 정지가 planned 주문을 안 닫던 문제, 취소에 주문 채번 지점 번호 누락, code_of의 3,500행 반복 조회, **증권사 주문번호가 날마다 새로 매겨지는데 어제 번호를 "사용 중"으로 보아 전송된 주문을 planned로 되돌리던 문제(중복 주문 위험)**. 리뷰 11건 반영(높음 4): 관문 거부가 멱등 키를 영구히 막아 재개 뒤 그 달 리밸런싱이 통째로 비던 문제 → rejected는 다시 판정, 초당 한도 초과를 주문 거부로 닫던 문제, 체결조회에 잔량 칸이 없을 때 살아 있는 주문을 취소로 읽어 재주문하던 문제, 취소 확인 없이 남은 수량을 다시 내던 문제. 보통 3: 재시도 전송도 unknown 선커밋(미확정 정리는 남은 수량으로도 짝을 찾는다), 해시키 호출이 한도를 건너뜀, 조회·주문 클라이언트가 같은 앱 키 한도를 따로 셈 → 앱 키 기준 공유. 낮음 4: 잔고 이어보기가 예수금을 0으로 덮어씀, 부분 체결을 누적 평균가로 기록, 주문번호 없이 57초 대기, 이름과 다른 orders_on 제거 |

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
| S11 과거 날짜 재현 | B3, C1 | 2025-05~2026-08 월말 20개 전부 검증 파일(`top60_by_month.csv`)과 일치. **막혀 있음**: 일봉 574만 행(2019-01-02~2026-09-22)·지수·상태는 적재 끝, 공시는 2023-04-03에서 전자공시 IP 차단으로 중단(1,613건). 차단이 풀리면 이어받는 대기 스크립트(`~/.qbot/resume-filings.sh`)를 걸어 뒀다. 하루 한도 2만 건이라 2023~2026 공시는 며칠 걸린다 |
| S12 처음 설치 | A, C1, C2 | 빈 컴퓨터에서 install → backfill → replay → 자동 시작까지. `install`과 `backfill`은 실제로 돌려 확인 |
| S1 거래일 저녁 | C1, E | 실제 거래일에 수집·대조·평가액·요약이 순서대로 돈다 |
| S2 + S3 월말 판단과 실행 | B3, D2 | 모의 계좌에서 판단 다음 날 10종목 매수, 다음 달 교체 |
| S5 재시작 | D1 | 주문 전송 직후 강제 종료 → 재시작 → 중복 주문 0건 |
| S6 + S7 손실 한도와 정지 | E, C2 | 한도 도달 시 매수 거부, `/halt` 뒤 미체결 취소, 재시작 후 정지 유지 |
| S13 계좌 불일치 | D2 | 앱에서 직접 매매 → 저녁 대조 불일치 → 주문 멈춤 → `/reconcile_accept` |
| S8 실전 전환 | F | 관문 미달 거부, 통과 후 승인, 첫 달 매수 상한 |

## 3. 커밋·PR 목록

슬라이스 카드의 `완료` 행에 기록한다. 브랜치 이름은 `feat/<슬라이스>`이다. 머지는 main에 squash.

## 4. 미결사항

되먹일 것: [[QBOT-DOM-003#fill]]의 `broker_fill_no`를 NOT NULL로. [[QBOT-DOM-001]] 1장의 "개념 20개"는 21개. [[QBOT-MS-001#MarketDataService.financials]] 4단계의 500일 규칙은 "최신 연간·분기 행"에만 적용하고 직전 연도 행은 남긴다고 고쳐야 한다. 같은 함수 5단계의 "전년 같은 분기"를 연간에도 "정확히 1년 전 결산기"로 통일. [[QBOT-MS-001#Scorer.score]] 1~2단계: `OPG_Q`의 절단 범위는 [-1, 5]가 아니라 [-3, 5]이고 임계값은 `|op_q_prev| >= 1억`, `OPG`는 `op_annual_prev > 0`일 때만 (검증 quarterly.py·build_panel.py 기준). 같은 함수 3단계 뒤에 "이 제외를 백분위 계산 전에 한다"를 명시. [[QBOT-MS-001#DecisionService.decide_month_end]] 9단계: "최근 10개월 월말 종가 평균"은 "있는 만큼(최대 10개)의 평균보다 높지 않으면 현금"으로(검증 simulate.py 15행). [[QBOT-MS-001#Scorer.select]] 출력의 `top60`은 "상위 60 + 선정·건너뛴 행 전부"로. [[QBOT-PRD-001#R2]]의 "잠정실적"은 v1에서 수집하지 않는다(거래소 공시, 다음 버전). [[QBOT-MS-001#MarketDataService.collect_daily]] 4단계 "종목마다 daily_bars"와 6단계 "instrument_status"는 마스터 파일 1회 다운로드로 대체. [[QBOT-INFRA-001]] 5장 인증 표에 "휴장일 조회는 실전 키 필요"와 "조회는 실전 키, 주문은 모드에 따라" 추가. 결산월 12월을 가정해 결산기 종료일을 정한다(비12월 결산 법인은 어긋남). [[QBOT-API-001#resume]]에 "메신저에서 온 `confirm` 인자는 무시하고 확인 코드로만 채운다"를 명시. [[QBOT-MS-001]] OpsService.notify: 밀린 알림은 새 알림 뒤에 최근 10건만.

D1에서 더해진 것: [[QBOT-DOM-002]] 4.2 BrokerPort에서 주문 함수 5개를 빼고 4.4에 OrderBrokerPort(`trading/ports.py`)를 둔다(6장 미결 2 해소). [[QBOT-MS-001#OrderExecutor.send]] 시그니처에 `price`·`order_type` 추가(관문이 금액을 알아야 판정한다), 3단계의 `rejected`는 "확실히 미체결"이므로 최종 상태에서 빼고 다시 판정하게 고친다, 10단계 재시도 가격은 "남은 수량 시장가 재주문"으로 확정(3장 미결 해소), 재시도 전송도 `unknown` 선커밋. [[QBOT-MS-001#OrderGate.check]] 6단계 앞에 "총평가액이 0이면 매수 거부(no_equity)" 추가. [[QBOT-MS-001#OrderExecutor.reconcile_unknown]] 3단계 매칭에 "남은 수량"도 후보로 넣고, "이미 쓰인 주문번호"는 **그날** 것만 본다(주문번호는 날마다 새로 매겨진다). [[QBOT-DOM-003#trade_order]]에 주문 시 예상 체결가 칸이 없어 하루 주문 총액을 체결 금액 합으로만 계산한다(미체결 주문 금액은 세지 못함) — 칸 추가 검토. `fill.fee`·`tax`는 증권사 응답에 없어 추정한다(수수료 0.014%, 매도세 0.18%) → D2 대조가 실제와 맞춘다. [[QBOT-INFRA-001]] 5장에 주문 거래 ID 표(모의 VTTC0802U·0801U·0803U·8001R·8434R / 실전 TTTC…)와 "모의투자는 장중에만 주문을 받는다(장 종료 뒤 40580000)", "전자공시는 초당 간격 없이 부르면 IP를 차단한다(초당 2건)"를 추가.

- [x] B2의 일치 테스트 입력 크기 → 월말 5개 표본(689KB)을 저장소에, 21개월 전체는 로컬 파일을 환경 변수 `QBOT_RESEARCH_PANEL`
- [x] C1 조회 키 → 실전 키로 수집(초당 15건으로 제한), 주문은 모드에 따라. `Settings.query_credentials`
- [x] D1의 실제 주문 테스트 종목 → 삼성전자(005930) 1주. `tests/live/test_live_smoke.py::test_paper_order_roundtrip`, 장 시간이 아니면 건너뛴다
- [ ] 2019~2022년 재무는 전자공시 API 호출량(2,500사 × 4보고서 × 4년 ≈ 4만 건)이 커서 v1 적재 범위(2023~)에서 뺐다. 연 1회 재점검(R13)에 필요해지면 금감원 일괄 파일로 채운다. 2023~2026도 하루 한도(2만 건) 때문에 며칠 걸린다 — 일괄 파일에는 접수번호가 없어 시점 고정에 쓸 수 없으므로 API가 유일한 길이다
