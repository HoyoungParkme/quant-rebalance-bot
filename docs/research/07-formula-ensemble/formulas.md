# 07. 공식(Formula) 앙상블 후보 조사

> 작성일: 2026-09-23 · 대상: KOSPI+KOSDAQ 월간 리밸런싱(상위 10종목) 봇
> 현재 기준 모델: 7팩터 동일가중 백분위 순위(E/P, S/P, ROE, 연간 영업이익 성장, 분기 YoY 영업이익 성장, 60일 저변동성, 20일 저회전율)

## 3줄 요약

1. **한국에서 근거가 가장 두꺼운 후보**는 매출총이익/총자산(GP/A), 피오트로스키 F-score, 마법공식(특히 ROC 대신 GP/A를 쓴 변형), 자산성장률(낮을수록 좋음, 단 역U자형), 신주발행(적을수록 좋음), 발생액(낮을수록 좋음)이다. 모멘텀과 산업(테마) 모멘텀은 한국에서 약하거나 반대(반전)로 나타나서, 사내 테스트 결과와 맞는다.
2. 한국 이상현상 148개를 재현한 연구에서 t>2.78을 넘은 비율은 27.7%뿐이다([Han·Lee·Kang 2020](https://www.emerald.com/insight/content/doi/10.1108/jdqs-03-2020-0004/full/html)). 우리 표본(약 6~7년)만으로는 t>3을 넘기기 거의 불가능하다. 그래서 **공식을 미리 확정해 두고(pre-registration) 수를 적게 유지하며, 월별 Rank IC 검정과 Deflated Sharpe/PBO로 평가**해야 한다.
3. 추천 설계는 이렇다. **(1) 부실 필터(Altman Z″/K-score)로 걸러낸 뒤 → (2) 서로 겹치지 않는 공식 6~8개가 각각 백분위를 매기고 → (3) "상위 20% 득표 수"로 투표하고 동점은 평균 순위로 가린다.** 학습형 결합(로지스틱/LDA)은 walk-forward에서만 비교군으로 쓴다.

---

## 0. 데이터 전제와 표기

| 약어 | 뜻 (우리 필드명) | 비고 |
|---|---|---|
| TA | 자산총계 | 연간 2018–2025, 분기 BS 여부는 확인 필요 |
| CA / CL | 유동자산 / 유동부채 | |
| TL | 부채총계 | 이자부채(차입금)는 따로 없음 → EV 계산 시 TL로 대신함(보수적) |
| EQ / EQo | 자본총계 / 지배기업 소유주지분 | ROE 분모는 EQo 권장 |
| Cash | 현금및현금성자산 | |
| INV / AR | 재고자산 / 매출채권 | |
| RE | 이익잉여금 | "있을 가능성 높음" → 확인 필요 |
| REV / COGS / GP | 매출액 / 매출원가 / 매출총이익 | 금융업은 매출원가 없음 → 금융업 제외 권장 |
| OI / NI | 영업이익 / 당기순이익 | EBIT 대신 영업이익을 씀 |
| SGA | 판매비와관리비 ≈ GP − OI | 따로 없으면 차이로 계산 |
| MCAP | 시가총액 = 종가 × 상장주식수 | 과거 주식수를 근사하므로 오차가 있음 |
| CFO | 영업활동현금흐름 | **아직 없음** (다른 에이전트가 수집 중) |
| DIV | 배당 | **아직 없음** |

**시점(point-in-time) 공통 주의**

- 사업보고서는 사업연도가 끝난 뒤 90일 안에, 분기·반기보고서는 45일 안에 제출한다([자본시장법 제159조·제160조](https://www.law.go.kr/법령/자본시장과금융투자업에관한법률)). 그래서 **연간 데이터는 해당 보고서의 접수일(rcept_dt)이 지난 뒤 첫 리밸런싱부터만 쓴다.** 접수일을 모르면 보수적으로 4월 말 이후부터 쓴다. 사내 연구 02(공시 이벤트)에서 "신선한 공시" 효과를 확인했으므로 접수일 기준 처리가 특히 중요하다.
- DART 벌크 파일은 **정정공시가 반영된 최종값**일 수 있다. 그러면 당시 시장이 보지 못한 숫자가 섞인다(look-ahead).
- 과거 상장주식수는 근사값이다. 그래서 시가총액, EPS, **신주발행 팩터처럼 주식수 변화를 쓰는 지표는 오차가 크다.** 대안으로 자본금이나 (자본총계 변화 − 당기순이익)을 쓴다.
- 상장폐지 종목이 빠지면(생존 편향) 부실 필터와 F-score의 효과가 작게 보인다.

---

## 1. 공식별 정의·계산·한국 근거

### 1-1. 한눈에 보기

| # | 공식 | 방향 | 지금 계산 가능? | 현재 7팩터와 겹침 | 한국 근거 |
|---|---|---|---|---|---|
| 1 | Novy-Marx GP/A | 높을수록 좋음 | 가능 | 중간(ROE와) | **있음, 양(+)** |
| 2 | 피오트로스키 F-score | 높을수록 좋음 | 9개 중 7개 가능(CFO 2개 빠짐) | 중간 | **있음, 양(+)** |
| 3 | 그린블라트 마법공식 | 순위 합이 낮을수록 좋음 | 근사로 가능(EV·투하자본) | **높음**(E/P, ROE) | 있음, KOSPI 대비 초과, 유의성 약함 |
| 4 | Acquirer's Multiple (EV/영업이익) | 낮을수록 좋음 | 근사로 가능 | **높음**(E/P) | 학술 근거 못 찾음 |
| 5 | 버핏형 퀄리티 | 높을수록 좋음 | 가능(5년 이상 이력 필요) | 중간~높음(ROE, 저변동성) | 직접 근거 없음(구성요소별로는 있음) |
| 6 | AQR QMJ | 높을수록 좋음 | 부분(배당·CFO 빠짐) | 높음 | 퀄리티 전반은 있음 |
| 7 | 그레이엄 방어적 기준 / 그레이엄 수 | 기준 통과 / 가격 < 그레이엄 수 | 부분(배당 기준 빠짐) | 높음(E/P, B/P) | 학술 근거 못 찾음 |
| 8 | NCAV(넷넷) | 시총 < NCAV×(2/3) | 가능 | 낮음(극단 가치주) | 블로그 백테스트 양(+) |
| 9 | Altman Z″ / K-score | 높을수록 안전(필터용) | 가능 | 낮음 | K-score는 한국용 모형 |
| 10 | Beneish M-score | 낮을수록 좋음(필터용) | 부분(감가상각·유형자산·CFO 빠짐) | 낮음 | 한국 적용 연구 거의 없음 |
| 11 | Mohanram G-score | 높을수록 좋음 | **거의 불가**(CFO·R&D·CAPEX·광고비) | 중간 | 한국에서 효과 없음 |
| 12 | 저변동성 / BAB | 낮을수록 좋음 | 가능 | **매우 높음**(60일 저변동성) | 저변동성 양(+), BAB 비용 차감 후 무의미 |
| 13 | Sloan 발생액 | 낮을수록 좋음 | 대차대조표 근사로 가능, 정확히는 CFO 필요 | 낮음 | **있음, 양(+)** |
| 14 | 자산성장률 | 낮을수록 좋음(역U자형) | 가능 | 낮음(영업이익 성장과 반대 방향일 수 있음) | **있음** |
| 15 | 순주식발행 | 낮을수록 좋음 | 근사로 가능(주식수 오차) | 낮음 | **있음, 음(−) 효과 뚜렷** |
| 16 | 가치+모멘텀(Asness) | 둘 다 높을수록 | 가능 | 가치 부분은 높음 | 한국 모멘텀은 약함·반전 |

### 1-2. 상세

#### ① Novy-Marx 매출총이익/총자산 (GP/A)
- **출처**: [Novy-Marx (2013), "The Other Side of Value", JFE](https://doi.org/10.1016/j.jfineco.2013.01.003)
- **계산**: `GP/A = (매출액 − 매출원가) / 자산총계`. 매출원가가 없는 금융업은 제외한다.
- **방향**: 높을수록 좋다. 원 논문에서는 GP/A가 가치(B/M)와 음의 상관이라서, 가치 팩터와 섞으면 분산 효과가 크다.
- **데이터**: 연간 BS+IS면 된다. 분기 TTM(최근 4분기 합)도 가능하다.
- **겹침**: ROE와 중간 정도 겹친다. 분자(매출총이익)가 판관비·이자·세금 전 단계라서 ROE보다 "이익의 원천"에 가깝다.
- **한국 근거**
  - [이민규 (2023), 국내 주식시장에서의 퀄리티 투자전략, 산업경제연구 36(5)](https://www.kci.go.kr/kciportal/ci/sereArticleSearch/ciSereArtiView.kci?sereArticleSearchBean.artiId=ART003011873): F-score, GP, MSCI 퀄리티, Q-score를 비교했다. **GP와 수정 F-score가 유의한 양(+)의 초과수익**을 냈고, 계산이 단순해서 GP가 가장 낫다고 평가했다. 폭락기에는 MSCI 퀄리티가 가장 좋았다.
  - [김민기·정진수·김동석 (2018), 재무관리연구 35(4)](https://www.kci.go.kr/kciportal/landing/article.kci?arti_id=ART002421442): 2001.7–2017.6 KOSPI+KOSDAQ에서 수익성 프리미엄이 존재했다. 위험 보상이 아니라 **투자자의 과소반응** 때문이며, 정보 불확실성이 크고 차익거래가 어려운 종목에서 더 강했다.
  - [Henry's Quantopia 블로그 (2017)](http://henryquant.blogspot.com/2017/01/quality-factor-in-kospi-200.html): KOSPI200, 2001–2016, GP/A·CFOA·GMAR로 만든 퀄리티 5분위의 연수익률이 1분위 16.0%, 5분위 4.2%였다. 롱숏 FF3 알파는 연 15.7%(t=4.84)였다.
  - 반대 근거: [Han·Lee·Kang (2020)](https://www.emerald.com/insight/content/doi/10.1108/jdqs-03-2020-0004/full/html)의 2000–2019 재현에서는 투자·수익성 계열의 재현율이 25% 미만이었다(시가총액 가중 기준).

#### ② 피오트로스키 F-score (9개 신호)
- **출처**: [Piotroski (2000), "Value Investing: The Use of Historical Financial Statement Information…", JAR 38 Suppl.](https://doi.org/10.2307/2672906)
- **계산**: 신호마다 조건을 만족하면 1점, 합계 0–9점이다. ROA는 순이익/기초 총자산이다.

| 신호 | 정의 | 우리 데이터 |
|---|---|---|
| F_ROA | ROA > 0 | 가능 |
| F_CFO | CFO > 0 | **CFO 필요** |
| F_ΔROA | ROA_t > ROA_t−1 | 가능 |
| F_ACCRUAL | CFO/TA > ROA | **CFO 필요** (임시로 대차대조표 발생액 < 0) |
| F_ΔLEVER | 장기부채/평균TA 감소 | 장기부채가 없음 → (TL−CL)/TA 또는 TL/TA로 대신 |
| F_ΔLIQUID | 유동비율(CA/CL) 증가 | 가능 |
| F_EQ_OFFER | 전년에 보통주를 발행하지 않음 | 주식수 근사 → 자본금 증가 여부로 대신 |
| F_ΔMARGIN | 매출총이익률 증가 | 가능 |
| F_ΔTURN | 자산회전율(REV/기초TA) 증가 | 가능 |

- **방향**: 8–9점은 매수, 0–1점은 회피. 원 논문은 **B/M 상위 20% 가치주 안에서** 적용했다.
- **겹침**: ROE와 부분적으로 겹친다. 하지만 "변화(Δ)" 신호가 많아서 현재 팩터와는 성격이 다르다.
- **한국 근거**
  - [Henry's Quantopia (2017)](http://henryquant.blogspot.com/2017/02/f-score-in-kospi.html): KOSPI 1995–2016, 매년 5월 리밸런싱, 수수료 35bp. 동일가중 기준으로 8–9점은 연 13–21%, 1–2점은 연 −3~−5%였다. 시가총액 가중에서는 7–8점이 8.6%, 1–3점이 −2.2%였다.
  - [김규형·임창우·정태규 (2018), 자산운용연구 6(1)](https://www.kci.go.kr/kciportal/ci/sereArticleSearch/ciSereArtiView.kci?sereArticleSearchBean.artiId=ART002364905): F-score는 한국 가치주의 승자와 패자를 구분했지만, **G-score는 실패**했다.
  - [KAIST 학위논문 "한국 주식시장에서 F-SCORE 실증 연구"](https://koasas.kaist.ac.kr/handle/10203/202565) 및 [산업별 조정 Modified F-score 연구](https://koasas.kaist.ac.kr/handle/10203/265744): 초록을 직접 확인하지는 못했다(페이지 로딩 실패).
  - [Han·Lee·Kang (2020)](https://www.emerald.com/insight/content/doi/10.1108/jdqs-03-2020-0004/full/html)에서는 F-score가 "포함되었으나 부진"했다. 시가총액 가중·전체 표본 기준에서는 약하다는 뜻이다.

#### ③ 그린블라트 마법공식
- **출처**: Greenblatt, *The Little Book That Beats the Market* (2005); [magicformulainvesting.com](https://www.magicformulainvesting.com/)
- **계산**
  - 이익수익률 `EY = EBIT / EV`. EV = 시가총액 + 이자부채 − 초과현금. **우리 근사식은 EV ≈ MCAP + TL − Cash**(TL 전체를 부채로 봐서 보수적)이고, EBIT 대신 영업이익을 쓴다.
  - 자본수익률 `ROC = EBIT / (순운전자본 + 순유형자산)`. 근사식은 `순운전자본 ≈ (CA − Cash) − CL`, `순유형자산 ≈ TA − CA`(무형자산·투자자산이 섞임)이다.
  - 최종 점수는 `rank(EY) + rank(ROC)`이고 **낮을수록 좋다**. 원 규칙은 금융주·유틸리티를 제외한다.
- **겹침**: **높다.** EY는 E/P·S/P와, ROC는 ROE와 겹친다.
- **한국 근거**: [우동호·최흥식·김선웅 (2023), 한국산학기술학회논문지 24(3)](https://www.kci.go.kr/kciportal/ci/sereArticleSearch/ciSereArtiView.kci?sereArticleSearchBean.artiId=ART002944269). 2004.4–2022.3 동안 원공식과 GP/A 변형 모두 KOSPI를 앞섰지만, **전체 표본의 알파는 통계적으로 유의하지 않았다.** 소형 1–3분위에서는 유의한 알파가 나왔고, **ROC를 GP/A로 바꾼 변형이 모든 기간에서 더 좋았다.**

#### ④ Acquirer's Multiple (EV / 영업이익)
- **출처**: Carlisle, *The Acquirer's Multiple* (2017), [acquirersmultiple.com](https://acquirersmultiple.com/); 관련 학술 근거는 [Loughran & Wellman (2011), JFQA](https://doi.org/10.1017/S0022109011000445)(enterprise multiple), [Gray & Vogel (2012), JPM](https://jpm.pm-research.com/content/39/1/112)(1971–2010 미국에서 EBITDA/TEV가 가장 좋은 가치지표).
- **계산**: `EV / 영업이익`, 낮을수록 좋다. 영업이익이 0 이하이면 제외한다. 역수(영업이익/EV)로 순위를 매기면 편하다.
- **겹침**: **E/P와 매우 높다.** 차이는 부채와 현금을 반영한다는 점이다. 현금이 많은 한국 중소형주에서 차이가 생길 수 있다.
- **한국 근거**: KCI 등 학술 DB 검색에서 한국 EV/EBIT 팩터 논문은 **찾지 못했다.**

#### ⑤ 버핏형 퀄리티 점수
- **출처**
  - [Frazzini, Kabiller, Pedersen (2018), "Buffett's Alpha", FAJ 74(4)](https://rpc.cfainstitute.org/research/financial-analysts-journal/2018/faj-v74-n4-3) ([NBER 판](https://www.nber.org/papers/w19681)): 버크셔의 샤프비율은 0.79였다. **BAB(저베타)와 QMJ(퀄리티)를 통제하면 알파가 유의하지 않게 된다.** 즉 "싸고, 안전하고, 우량한 주식 + 약 1.7배 레버리지"로 설명된다.
  - 버크셔 인수 기준([1987 서한](https://www.berkshirehathaway.com/letters/1987.html)): "demonstrated consistent earning power", "good returns on equity while employing little or no debt".
  - 유보이익 1달러 테스트: 유보한 1달러마다 시장가치가 1달러 이상 늘어야 한다([Owner's Manual](https://www.berkshirehathaway.com/ownman.pdf)).
- **제안 계산** (각 항목을 백분위로 바꾼 뒤 평균)

| 항목 | 계산 | 방향 |
|---|---|---|
| ROE 수준 | 최근 5년 평균 ROE(= NI / 평균 EQo) | ↑ |
| ROE 일관성 | 5년 중 ROE ≥ 15%인 해의 수, 또는 −표준편차(ROE) | ↑ |
| 저부채 | TL / EQ | ↓ |
| 마진 안정성 | −표준편차(영업이익률, 5년 또는 분기 12개) | ↑ |
| 유보이익 1달러 테스트 | ΔMCAP(5년) / ΔRE(5년) ≥ 1 (RE 필드 확인 필요) | ↑ |
| 저베타(선택) | 252일 KOSPI 베타 | ↓ |

- **데이터**: 2018–2025 연간 자료라서 **5년 창을 쓰면 2023년부터 계산할 수 있다.** 분기 2019–2026을 쓰면 12분기 창으로 더 일찍 시작할 수 있다.
- **겹침**: ROE, 저변동성과 중간~높음.
- **한국 근거**: 버핏형 스크린 자체를 다룬 한국 학술 연구는 **찾지 못했다.** 구성요소(수익성, 저변동성)는 한국에서 근거가 있다(①, ⑫ 참조).

#### ⑥ AQR Quality-Minus-Junk (QMJ)
- **출처**: [Asness, Frazzini, Pedersen (2019), "Quality Minus Junk", RAS 24](https://link.springer.com/article/10.1007/s11142-018-9470-2) ([PDF](http://www.econ.yale.edu/~shiller/behfin/2013_04-10/asness-frazzini-pedersen.pdf)). 24개국 중 23개국에서 양(+)이었다.
- **계산**: 각 변수를 횡단면 순위로 바꾼 뒤 z-score로 만든다. 품질 = z(수익성 + 성장 + 안전 + 배당성향).

| 구성 | 원 정의 | 우리 데이터 |
|---|---|---|
| 수익성 | GPOA, ROE, ROA, CFOA, GMAR, −ACC | CFOA는 CFO 필요, 나머지 가능 |
| 성장 | 위 수익성 지표들의 5년 변화 | 5년 이력이 필요(2023~) |
| 안전 | −BAB 베타, −레버리지, −Ohlson O, +Altman Z, −ROE 변동성 | 대부분 가능(O-score는 일부 근사) |
| 배당성향 | −주식발행(EISS), −부채발행(DISS), 순배당/이익(NPOP) | NPOP은 **배당 필요**, EISS는 주식수 오차 있음 |

- **겹침**: ROE, 성장, 저변동성과 **높다.** 새 공식이라기보다 **현재 7팩터를 체계화한 형태**에 가깝다.
- **한국 근거**: QMJ를 그대로 한국에 적용한 논문은 찾지 못했다. [이민규 (2023)](https://www.kci.go.kr/kciportal/ci/sereArticleSearch/ciSereArtiView.kci?sereArticleSearchBean.artiId=ART003011873)가 Asness Q-score를 비교했는데, GP와 F-score보다 두드러지지 않았다. [Bae (2024), APJFS](https://onlinelibrary.wiley.com/doi/10.1111/ajfs.12475)는 한국 자산가격 종합 검정에서 QMJ를 다뤘다(본문 접근 불가, 403).

#### ⑦ 그레이엄 방어적 기준 / 그레이엄 수
- **출처**: Graham, *The Intelligent Investor* 14장 ([요약](https://www.grahamvalue.com/blog/intelligent-investor-summarized), [GuruFocus 해설](https://www.gurufocus.com/news/704217/the-intelligent-investor-chapter-14-reviewed))
- **7개 기준**: (1) 충분한 규모, (2) 유동비율 ≥ 2(그리고 장기부채 ≤ 순운전자본), (3) 10년 연속 흑자, (4) **20년 연속 배당**, (5) 10년간 EPS(3년 평균 기준) 1/3 이상 성장, (6) PER ≤ 15(3년 평균 이익 기준), (7) PBR ≤ 1.5, 또는 PER×PBR ≤ 22.5.
- **그레이엄 수** = `√(22.5 × EPS × BPS)`. 주가 < 그레이엄 수이면 저평가로 본다.
- **우리 데이터**: (4)는 **배당이 필요**하고 (3)·(5)는 10년 이력이 없다. 그래서 "유동비율 ≥ 2, 최근 N년 흑자, PER×PBR ≤ 22.5" 정도로 줄인 필터만 가능하다.
- **겹침**: E/P와 높다. 가치(B/P)가 새로 들어간다.
- **한국 근거**: 학술 근거는 찾지 못했다.

#### ⑧ NCAV (넷넷)
- **출처**: Graham & Dodd, *Security Analysis* (1934); [Oppenheimer (1986), FAJ](https://doi.org/10.2469/faj.v42.n6.40)
- **계산**: `NCAV = 유동자산 − 부채총계`. 원 규칙은 `시가총액 < (2/3)·NCAV`이고, 한국 실무에서는 `NCAV/시총 > 1.5`를 많이 쓴다(같은 뜻). 연속 점수로 쓰려면 `NCAV / MCAP`를 쓰고 높을수록 좋다.
- **주의**: 조건을 만족하는 종목이 적고 소형·저유동성이다. 지주사·중국기업·금융주 제외가 관례다. **주식수 근사 오차에 민감하다.**
- **겹침**: 낮다(극단적 자산가치).
- **한국 근거(블로그·언론, 학술 아님)**
  - [kangcfa, Steemit](https://steemit.com/kr/@kangcfa/4-ncav-2-21): 2010.5–2018.5, NCAV > 시총 + PER < 10 + 배당 > 0, 최대 20종목, 연 1회 리밸런싱. CAGR 18.9%, MDD 19.9%.
  - [한국경제 (2022)](https://www.hankyung.com/article/202211100992i): 2020년 2분기 기준 NCAV/시총 ≥ 1.5인 22종목이 이후 약 14개월간 +41.9%였다(KOSPI +30.6%, KOSDAQ +21.1%). 기간이 짧아서 참고용이다.
  - 학술: [KISS, "벤자민 그래함의 순유동자산 가치투자에 기초한 주식투자전략"](https://kiss.kstudy.com/Detail/Ar?key=3233118)은 내용을 확인하지 못했다.

#### ⑨ Altman Z-score / 한국형 K-score (부실 필터)
- **출처**: [Altman (1968), JF](https://doi.org/10.1111/j.1540-6261.1968.tb00843.x); 공식 정리는 [Wikipedia](https://en.wikipedia.org/wiki/Altman_Z-score); 한국형은 [Altman, Eom, Kim (1995), "Failure Prediction: Evidence from Korea", JIFMA 6(3)](https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1467-646X.1995.tb00058.x) ([SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=7160))
- **Z″ (비제조·신흥시장용, 권장)**: `Z″ = 3.25 + 6.56·(CA−CL)/TA + 3.26·RE/TA + 6.72·OI/TA + 1.05·EQ/TL`. 안전 > 2.6, 위험 < 1.1(상수 3.25를 뺀 원식 기준 구간).
- **원 Z (상장 제조업)**: `Z = 1.2·X1 + 1.4·X2 + 3.3·X3 + 0.6·MCAP/TL + 1.0·REV/TA`. 안전 > 2.99, 위험 < 1.81.
- **K-score (한국형)**: X1 = ln(총자산), X2 = ln(매출액/총자산), X3 = 이익잉여금/총자산, X4 = 자기자본(시장가치 K1 / 장부가치 K2)/부채총계.
  - `K1 = −17.862 + 1.472·X1 + 3.041·X2 + 14.839·X3 + 1.516·X4(시가)`
  - `K2 = −18.696 + 1.501·X1 + 2.706·X2 + 19.760·X3 + 1.146·X4(장부)`
  - (계수 출처는 [한양대 KOCW 강의자료](http://contents.kocw.or.kr/KOCW/document/2015/hanyang_erica/yisungwook/17-2.pdf) 검색 결과다. 상수가 −17.862와 −17.682로 다르게 옮겨진 사례가 있으므로 **원 논문으로 확인해야 한다.** ln의 밑과 금액 단위(원/천원/백만원)에 따라 절대값이 달라지므로 **절대 판별점보다 횡단면 하위 10% 제외로 쓰는 것이 안전하다.**)
- **방향**: 높을수록 안전하다. 순위 팩터보다 **제외 필터**로 쓴다.
- **겹침**: 낮다. 다만 저변동성과는 약하게 상관할 수 있다.
- **한국 근거**: K-score 자체가 한국 부실기업 표본으로 추정한 모형이다. [서정구·김학열 (2018), 윤리경영연구 18(1)](https://www.kci.go.kr/kciportal/landing/article.kci?arti_id=ART002369642)에서는 분식회계 기업의 F·Z·K 점수가 모두 유의하게 낮았다. 수익률 예측력에 관해서는 [이인로·김동철, 국내 부도위험 이례현상](http://www.korfin.org/korfin_file/forum/2016co-conf20-2.pdf)(한국재무학회 2016 발표)이 있다.

#### ⑩ Beneish M-score (조작 탐지 필터)
- **출처**: [Beneish (1999), FAJ 55(5)](https://doi.org/10.2469/faj.v55.n5.2296); 공식은 [Wikipedia](https://en.wikipedia.org/wiki/Beneish_M-score)
- **계산**: `M = −4.84 + 0.920·DSRI + 0.528·GMI + 0.404·AQI + 0.892·SGI + 0.115·DEPI − 0.172·SGAI + 4.679·TATA − 0.327·LVGI`. M > −1.78이면 조작 의심.
- **우리 데이터**: DSRI(매출채권/매출), GMI(매출총이익률), SGI(매출성장), SGAI(판관비 = GP−OI), LVGI(부채/자산)는 **가능하다.** AQI(유형자산 필요), DEPI(감가상각 필요), TATA(CFO 필요)는 **불가**하거나 근사해야 한다. 아시아 기업은 매출원가와 판관비를 나누지 않는 경우가 많아서 약 19%는 계산할 수 없다는 지적도 있다([GMT Research](https://www.gmtresearch.com/en/accounting-ratio/beneishs-m-score/)).
- **한국 근거**: 한국 상장사 대상 M-score 검증 논문은 **찾지 못했다.**
- **판단**: CFO가 들어온 뒤 부분 버전으로 **극단값 제외 필터** 후보로만 둔다.

#### ⑪ Mohanram G-score
- **출처**: [Mohanram (2005), RAS 10](https://doi.org/10.1007/s11142-005-1526-4)
- **8개 신호**(모두 업종 중앙값과 비교): ROA, CFO/TA, CFO > NI, ROA 분산(낮음), 매출성장 분산(낮음), R&D/TA, CAPEX/TA, 광고비/TA. 대상은 저 B/M(성장주)이다.
- **우리 데이터**: CFO, R&D, CAPEX, 광고비가 없어서 **8개 중 2개만 가능하다 → 제외.**
- **한국 근거**: [김규형 외 (2018)](https://www.kci.go.kr/kciportal/ci/sereArticleSearch/ciSereArtiView.kci?sereArticleSearchBean.artiId=ART002364905)에서 G-score 기반 전략은 **시장을 이기지 못했다.**

#### ⑫ 저변동성 / Betting-Against-Beta
- **출처**: [Ang, Hodrick, Xing, Zhang (2006), JF](https://doi.org/10.1111/j.1540-6261.2006.00836.x); [Blitz & van Vliet (2007), JPM](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=980865); [Frazzini & Pedersen (2014), "Betting Against Beta", JFE](https://doi.org/10.1016/j.jfineco.2013.10.005)
- **계산**: 이미 60일 변동성을 쓰고 있다. 추가 후보는 252일 KOSPI 베타(`β = ρ·σi/σm`, FP식은 상관 5년·변동성 1년 창을 쓰고 0.6·β+0.4로 수축)와 고유변동성(FF3 잔차 표준편차)이다.
- **겹침**: **매우 높다.** 새 공식으로 추가할 필요는 낮다.
- **한국 근거**
  - [고봉찬·김진우, 저변동성 이상현상과 투자전략의 수익성 검증 (DBpia)](https://www.dbpia.co.kr/journal/articleDetail?nodeId=NODE07228265): 1990.1–2012.12, 고유변동성 최저 5분위를 매수하고 최고 5분위를 매도하면 **거래비용 차감 후 월 1.57%**였다.
  - [박종원·엄윤성·엄철준 (2024)](https://www.kci.go.kr/kciportal/ci/sereArticleSearch/ciSereArtiView.kci?sereArticleSearchBean.artiId=ART003056534): 1990–2021에서 고유변동성·MAX 효과는 소형주를 빼도 유의했다. 베타 효과와 **비유동성 프리미엄(우리 저회전율 팩터와 관련)은 소형주를 빼면 사라졌다.**
  - [김성결·어지원 (2025), 한국증권학회지 54(2)](https://www.kci.go.kr/kciportal/ci/sereArticleSearch/ciSereArtiView.kci?sereArticleSearchBean.artiId=ART003198895): **BAB는 거래비용 차감 후 FF3 알파가 유의하지 않았다.** 순위가중 방식이 소형주를 과대 편입한다는 지적도 있다.

#### ⑬ Sloan 발생액 (Accruals)
- **출처**: [Sloan (1996), The Accounting Review 71(3)](https://www.jstor.org/stable/248290)
- **계산**
  - CFO가 있을 때: `ACC = (NI − CFO) / 평균TA`
  - 지금(대차대조표 근사): `ACC ≈ [(ΔCA − ΔCash) − ΔCL] / 평균TA`. 원식에서 빼는 단기차입금·미지급법인세·감가상각은 없으므로 생략한다.
- **방향**: 낮을수록 좋다(이익 중 현금 비중이 높음).
- **겹침**: 낮다. 성장 팩터와 약한 양(+)의 상관이 예상되므로 **성장 팩터의 "질"을 보완한다.**
- **한국 근거**
  - 고봉찬·김진우(2007)는 국내 제조업에서 저발생액 매수·고발생액 매도 제로비용 포트폴리오가 **3년 누적 16% 이상의 초과수익**을 냈다고 보고했다. 원문은 확인하지 못했고, 웹 검색 요약에 나온 선행연구 인용이다. 관련 한국 연구로 [공매도차익거래와 발생액 이상현상(서울대 S-Space)](https://s-space.snu.ac.kr/bitstream/10371/124447/1/000000013306.pdf)과 [국내 발생액·투자 이상현상 실증(DBpia)](https://www.dbpia.co.kr/journal/articleDetail?nodeId=NODE07291137)이 있다.
  - [Han·Lee·Kang (2020)](https://www.emerald.com/insight/content/doi/10.1108/jdqs-03-2020-0004/full/html)에서 발생액 계열 여러 지표가 유의한 음(−)의 관계를 보였다.

#### ⑭ 자산성장률 (Asset Growth)
- **출처**: [Cooper, Gulen, Schill (2008), "Asset Growth and the Cross-Section of Stock Returns", JF](https://doi.org/10.1111/j.1540-6261.2008.01370.x)
- **계산**: `AG = TA_t / TA_t−1 − 1`, 낮을수록 좋다.
- **한국 특이점**: [장욱·김이배 (2016), 회계정보연구 34(4)](https://kci.go.kr/kciportal/ci/sereArticleSearch/ciSereArtiView.kci?sereArticleSearchBean.artiId=ART002182753)와 [대한경영학회지 "국내 주식시장에서의 자산성장효과"](https://www.dbpia.co.kr/journal/articleDetail?nodeId=NODE02304456)는 **역U자형**을 보고했다. 자산이 줄어드는 기업(음의 성장)도 수익률이 낮았다. 원인은 주로 **유동자산 증가와 유상증자(자본금 증가)**였다. 그래서 순위 대신 **"상위 20% 고성장 제외 + 하위 10% 역성장 제외"** 또는 |AG − 중앙값| 형태로 쓰는 것을 권장한다.
- **겹침**: 낮다. 영업이익 성장과는 **다른 방향**일 수 있다(이익은 늘고 자산은 덜 늘어난 기업 = 효율 성장).

#### ⑮ 순주식발행 (Net Share Issuance)
- **출처**: [Pontiff & Woodgate (2008), "Share Issuance and Cross-Sectional Returns", JF](https://doi.org/10.1111/j.1540-6261.2008.01324.x)
- **계산**: `NSI = ln(조정주식수_t / 조정주식수_t−12m)`, 낮을수록 좋다. **과거 주식수가 근사값이므로** 대안으로 `Δ자본금/자본금` 또는 `(ΔEQ − NI)/EQ`(배당이 없으면 유상증자·CB 전환의 근사)를 쓴다.
- **한국 근거(음의 효과가 뚜렷함)**
  - [유상증자 후 장기성과, 재무관리연구](https://koreascience.kr/article/JAKO200008508062661.page?lang=ko): 유상증자 3년 후 보유수익률이 비발행 기업보다 27.7% 낮았다.
  - [유상증자 방식과 시장상황별 장기성과, 대한경영학회지](https://www.kci.go.kr/kciportal/ci/sereArticleSearch/ciSereArtiView.kci?sereArticleSearchBean.artiId=ART002460426): **KOSDAQ의 부진이 더 컸고**, 발행 규모/시총과 M/B가 클수록 부진했다.
  - [Han·Lee·Kang (2020)](https://www.emerald.com/insight/content/doi/10.1108/jdqs-03-2020-0004/full/html): 순주식발행은 유의한 음(−)이었다.
- **겹침**: 낮다. **새 정보라서 추가 가치가 가장 기대된다.**

#### ⑯ 가치 + 모멘텀 (Asness)
- **출처**: [Asness, Moskowitz, Pedersen (2013), "Value and Momentum Everywhere", JF 68(3)](https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12021). 가치와 모멘텀이 서로 음의 상관이라서 50/50 결합이 좋다는 내용이다.
- **한국 근거**: 한국은 모멘텀이 약하거나 반전된다. [Kho(1997)·Chae & Eom(2009)·Lee & Cho(2014)는 음의 모멘텀 수익](https://www.kci.go.kr/kciportal/ci/sereArticleSearch/ciSereArtiView.kci?sereArticleSearchBean.artiId=ART002618717)을 보고했다. 1983–2023 장기 표본에서는 [개별주는 반전, 산업 모멘텀은 무의미](https://www.researchgate.net/publication/388410691_Momentum_and_reversal_effects_in_the_Korean_stock_market)였다(Investment Analysts Journal 54(4), 2025). 반면 [Han·Lee·Kang (2020)](https://www.emerald.com/insight/content/doi/10.1108/jdqs-03-2020-0004/full/html)은 2000–2019 모멘텀 재현율을 t>1.96 기준 67%, t>2.78 기준 27%로 보고해서 기간에 따라 다르다.
- **판단**: 사내 테스트(2020–2026 무효/유해)와 맞으므로 **제외**한다. 가치 부분만 이미 E/P, S/P로 반영되어 있다.

---

## 2. 통계 방법론

### 2-a. 팩터 상관·중복 점검

| 방법 | 계산 | 판정 기준(권장) |
|---|---|---|
| 횡단면 순위상관 | 매월 종목×팩터 백분위 행렬에서 Spearman ρ를 구하고 월별 평균과 표준편차를 낸다 | 평균 \|ρ\| > 0.7이면 합치거나 하나만 남긴다 |
| 팩터 수익률 상관 | 각 공식의 롱숏(상위−하위 5분위) 월수익률 시계열의 상관 | 0.5 이상이면 "같은 베팅" |
| PCA | 순위 행렬의 상관행렬 고유값 λ. 유효 팩터 수 `N_eff = (Σλ)² / Σλ²` | N_eff가 공식 수보다 훨씬 작으면 중복이 많다 |
| 계층적 군집 | 거리 `d = √(2(1−ρ))` 또는 `1−\|ρ\|`, Ward/평균 연결, 덴드로그램 | 한 군집에서 대표 1개만 투표에 참여 |
| 스패닝 회귀 | `r_new,t = α + Σ β_k·f_k,t + ε`(f = 기존 7팩터 합성 수익률, FF 요인) | α의 t > 2(가능하면 3)일 때만 "새 정보"로 인정([Barillas & Shanken 2017](https://doi.org/10.1093/rfs/hhw101)) |

### 2-b. 테마·업종이 "진짜 군집"인가, 그리고 수익률을 예측하는가

1. **수익률 동조화 검정**
   - 60/250일 시장조정 잔차수익률로 종목 쌍 상관을 구한다. `같은 업종 내 평균 ρ − 다른 업종 간 평균 ρ`를 계산하고, 업종 라벨을 무작위로 섞은 **순열검정(1,000회)**으로 p값을 낸다.
   - 잔차 상관으로 계층적 군집을 만든 뒤 업종명과 비교한다(ARI, NMI). 표본 밖(다음 해) 동조화도 비교한다.
   - 참고로 [Chan, Lakonishok, Swaminathan (2007), FAJ](https://rpc.cfainstitute.org/research/financial-analysts-journal/2007/industry-classifications-and-return-comovement)는 **업종 분류가 통계적 군집보다 표본 밖 동질성이 더 높고, 동조화는 대형주에서 더 강하다**고 했다. 한국 네트워크(MST) 연구로는 [주식 시장 네트워크 분석(KISTI)](https://scienceon.kisti.re.kr/srch/selectPORSrchArticle.do?cn=JAKO201336161061759)이 있다.
2. **판별분석(LDA/QDA)**
   - 특성 벡터 x(매출총이익률, 자산회전율, 부채비율, GP/A, 변동성, KOSPI 베타, 잔차수익률 PCA 상위 5개 적재값)로 업종(또는 테마) 라벨을 예측한다.
   - LDA는 공통 공분산 Σ를 가정해서 `δ_k(x) = xᵀΣ⁻¹μ_k − ½μ_kᵀΣ⁻¹μ_k + ln π_k`를 쓰고, QDA는 클래스마다 Σ_k를 따로 둔다. 소표본 업종이 많으면 **Ledoit-Wolf 수축 LDA**를 쓰거나 30종목 미만 업종은 합친다.
   - 평가는 5-fold 교차검증 정확도와 **다수클래스 기준선·라벨 셔플 기준선**을 비교해서 한다. 재무 특성만으로 판별되면 "펀더멘털 군집"이고, 수익률 적재값으로만 판별되면 "수급·테마 군집"이다.
3. **테마 소속이 수익률을 예측하는가**
   - 이론: [Moskowitz & Grinblatt (1999), "Do Industries Explain Momentum?", JF](https://doi.org/10.1111/0022-1082.00146)(과거 6개월 승자 산업이 계속 오름), [Hou (2007), RFS](https://doi.org/10.1093/rfs/hhl026)(산업 내 정보 확산 지연).
   - 한국: 1983–2023에서 **산업 모멘텀은 유의하지 않았다**([IAJ 2025](https://www.researchgate.net/publication/388410691_Momentum_and_reversal_effects_in_the_Korean_stock_market)). 정치테마주는 [곽형신·여은정 (2019), 재무관리연구 36(2)](https://www.kci.go.kr/kciportal/landing/article.kci?arti_id=ART002479364)에서 선거 전 양(+)의 비정상수익 후 소멸했고, 저자들은 이를 **조작이나 비이성적 과열**로 해석했다. [자본시장연구원 이슈보고서 17-04](https://www.kcmi.re.kr/kcmifile/report_data/727/reportpdf_727.PDF)도 대선 테마주 급등 후 하락을 보고했다.
   - 검정: 매월 Fama-MacBeth `r_i,t+1 = a + b·r_업종(i),t−k:t + c·r_i,t−k:t + 통제변수 + e`(k = 1, 6개월)를 돌리고 b의 Newey-West t를 본다. 또는 업종 수익률 5분위 포트폴리오를 비교한다.
   - **예상과 권장**: 사내의 모멘텀 무효 결과와 한국 문헌을 보면 예측력은 없을 가능성이 높다. 그러면 업종·테마는 **알파 신호가 아니라 위험 집중 통제**(예: 같은 업종 최대 3종목)에만 쓴다.

### 2-c. 신호 결합

| 방식 | 계산 | 장점 | 주의 |
|---|---|---|---|
| 순위 평균(현재 방식) | `S_i = (1/K) Σ_k pct_k(i)` | 추정할 것이 없어 과적합이 적다. 동일가중과 결합예측이 표본 밖에서 강하다는 근거가 있다([DeMiguel et al. 2009](https://doi.org/10.1093/rfs/hhm075), [Rapach, Strauss, Zhou 2010](https://academic.oup.com/rfs/article-abstract/23/2/821/1604687)) | 한 공식의 극단값이 묻힌다 |
| 다수결/득표 | `V_i = Σ_k 1[pct_k(i) ≥ 0.8]`, V가 높은 순, 동점은 S_i로 | "여러 관점에서 동시에 좋은" 종목을 고른다. 해석이 쉽다 | 공식끼리 상관이 높으면 사실상 가중치가 커진다 → 2-a의 군집별 1표 원칙 |
| 스태킹(로지스틱/LDA) | `P(다음달 상위 20%) = σ(w₀ + Σ w_k·pct_k)`, LDA는 상위/하위 2클래스 | 공식별 가중을 데이터로 정한다 | 표본이 짧아 w가 불안정하다. **확장창 walk-forward, 매년 재학습, 1개월 퍼지/엠바고**([López de Prado, AFML](https://www.wiley.com/en-us/Advances+in+Financial+Machine+Learning-p-9781119482086)) 필수. 동일가중을 이길 때만 채택 |

### 2-d. 데이터 마이닝 방지

| 방법 | 핵심 공식 | 적용 |
|---|---|---|
| Bonferroni | 기각 기준 `p < α/m` | 가장 보수적 |
| Holm | p를 오름차순 정렬, `p_(i) ≤ α/(m−i+1)`이면 순서대로 기각하다 처음 실패하면 멈춘다([Holm 1979](https://www.jstor.org/stable/4615733)) | 공식 m개의 Rank IC t검정에 적용 |
| Harvey-Liu-Zhu | 새 팩터는 **t > 3.0**([HLZ 2016, RFS 29(1)](https://academic.oup.com/rfs/article/29/1/5/1843824), [PDF](https://people.duke.edu/~charvey/Research/Published_Papers/P118_and_the_cross.PDF)) | 한국 재현 연구도 t > 2.78 기준을 씀 |
| 다중신호 결합 편향 | n개 중 최적 k개를 고르면 nᵏ개 중 최적 1개를 고른 것과 비슷한 편향([Novy-Marx 2015, NBER w21329](https://www.nber.org/papers/w21329)) | **공식 목록과 부호를 먼저 확정한 뒤** 결과를 본다 |
| Deflated Sharpe Ratio | `DSR = Φ( (ŜR − SR₀)·√(T−1) / √(1 − γ₃·ŜR + (γ₄−1)/4·ŜR²) )`, `SR₀ = √V[SR_n]·((1−γ)·Φ⁻¹(1−1/N) + γ·Φ⁻¹(1−1/(N·e)))`, γ ≈ 0.5772([Bailey & López de Prado 2014](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551)) | N = **실제로 시도한 모든 변형 수**를 로그로 남긴다. DSR > 0.95를 채택 기준으로 |
| PBO (CSCV) | T×N 성과행렬을 S개 블록으로 나누고, C(S, S/2) 조합마다 IS 최고 전략의 OOS 상대순위 ω를 구해 `λ = ln(ω/(1−ω))`, `PBO = P(λ ≤ 0)`([Bailey, Borwein, López de Prado, Zhu 2017](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)) | PBO < 0.2 권장. S = 8~12 |
| White Reality Check / SPA | 귀무가설 `max_k E[d_k] ≤ 0`(d_k = 전략 k − 기준 수익률), 통계량 `max_k √n·d̄_k`의 분포를 정상 부트스트랩으로 구한다([White 2000, Econometrica](https://doi.org/10.1111/1468-0262.00152); 개선판 [Hansen 2005 SPA](https://doi.org/10.1198/073500105000000063)) | 기준 = 현재 7팩터 모델 |

**검정력 경고**: 표본이 약 6–7년(72–84개월)이면 연 샤프 0.5짜리 전략의 t ≈ 0.5×√6.5 ≈ 1.3이다. 포트폴리오 수익률만으로는 t > 3에 도달할 수 없다. 그래서 1차 평가지표는 **월별 횡단면 Rank IC(종목 수천 개 × 월)의 평균과 Newey-West t**, 그리고 상위−하위 분위 스프레드로 한다. 상위 10종목 포트폴리오 성과는 2차 확인용으로만 쓴다. 한국 재현 연구에 따르면 **소형주와 동일가중 여부에 따라 결론이 뒤집히므로**([Han·Lee·Kang 2020](https://www.emerald.com/insight/content/doi/10.1108/jdqs-03-2020-0004/full/html)) 시총 하한 필터 유무를 반드시 두 가지 모두 보고한다.

---

## 3. 우선순위 숏리스트 (백테스트 후보 8개)

| 순위 | 공식 | 역할 | 지금 가능? | 선정 이유 |
|---|---|---|---|---|
| 1 | **GP/A** | 순위 | 가능 | 한국 근거가 가장 일관됨(이민규 2023, 김민기 외 2018, 마법공식 변형). 계산이 단순하고 가치와 음의 상관 |
| 2 | **F-score (7신호 판, CFO 오면 9신호)** | 순위(0–9) | 가능 | 한국 근거 있음. "변화" 신호로 현재 팩터와 성격이 다름 |
| 3 | **순주식발행 / 자본금 증가율(낮을수록)** | 순위 또는 제외 | 근사 가능 | 한국 유상증자 장기부진 근거가 강함(특히 KOSDAQ). 현재 팩터와 겹침이 적음 |
| 4 | **자산성장률(역U자 처리)** | 제외 필터(상위 20%·하위 10%) | 가능 | 한국 근거 있음. 성장 팩터를 "효율 성장"으로 보정 |
| 5 | **발생액(대차대조표 근사 → CFO 기준)** | 순위 | 근사 가능 | 한국 근거 있음. 이익의 질이라는 새 차원 |
| 6 | **마법공식(GP/A 변형): rank(OI/EV) + rank(GP/A)** | 순위 | 근사 가능 | 한국 KOSPI 대비 초과(유의성 약함). E/P를 EV 기준으로 대신하는 비교 실험 겸용 |
| 7 | **Altman Z″ / K2-score** | **부실 제외 필터**(하위 10%) | 가능 | KOSDAQ 부실·상폐 위험 회피. 순위가 아닌 안전장치 |
| 8 | **버핏형 퀄리티(ROE 일관성 + 저부채 + 마진 안정)** | 순위 | 2023년~(5년 창) 또는 분기 12개 창 | FKP 2018의 "싸고 안전한 우량주" 논리. QMJ 대체용 |
| (보류) | NCAV | 별도 소형 전략 | 가능 | 종목 수가 적고 주식수 오차에 민감 → 앙상블보다 별도 검증 |
| (제외) | G-score, 모멘텀·산업모멘텀, BAB, 그레이엄 방어적 | – | – | 데이터 부족, 한국 근거 부재·반대, 기존 저변동성과 중복 |

## 4. 제안하는 투표 설계

```
[매월 리밸런싱일 t]
0. 유니버스: KOSPI+KOSDAQ, 금융·지주·관리종목·스팩 제외, 시총 하한(예: 하위 20% 제외) 유무 두 버전
1. 하드 필터(탈락): Z″ 또는 K2 하위 10%, 자산성장 상위 20%, (CFO 도착 후) 발생액 상위 10%
2. 투표자(서로 겹치지 않는 군집에서 1개씩, 2-a 결과로 최종 확정):
   V1 = 현재 7팩터 합성(기준선을 하나의 투표자로 유지)
   V2 = GP/A
   V3 = F-score
   V4 = 마법공식(GP/A 변형)
   V5 = 버핏형 퀄리티
   V6 = −순주식발행
   V7 = −발생액
3. 득표 = Σ 1[pct_k(i) ≥ 0.80]  (각 투표자 상위 20%에 들면 1표)
4. 최종 순위 = 득표 내림차순 → 동점은 평균 백분위 내림차순
5. 상위 10종목, 동일 업종 최대 3종목(위험 통제, 알파 신호 아님)
```

- **비교군(미리 고정)**: (A) 현재 7팩터, (B) 7개 투표자의 순위 평균, (C) 득표 방식, (D) walk-forward 로지스틱 스태킹. 시도 수 N = 4 × 시총필터 2 = 8을 DSR에 반영한다.
- **채택 조건**: 공식별 Rank IC의 Holm 보정 유의, 스패닝 알파 t > 2, (C) 또는 (B)가 (A) 대비 SPA p < 0.10, DSR > 0.95, PBO < 0.2. 하나라도 실패하면 현재 모델을 유지한다.
- **CFO가 도착하면**: F-score 9신호, 발생액 정식 버전, QMJ의 CFOA를 추가한다. 이때는 **새 실험으로 기록하고 N을 늘린다.**
