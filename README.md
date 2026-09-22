# quant-rebalance-bot (QBOT)

개인용 국내 주식 월 1회 리밸런싱 봇. 과거 데이터로 검증한 재무·위험 지표 7개로 종목을 고르고, 집 컴퓨터에서 혼자 돈다. 언어모델과 GPU를 쓰지 않는다.

- 명세: `docs/specs/` (싱크독 11단계). 읽는 순서는 폴더 번호 순
- 검증 근거: `docs/research/`
- 코드: `backend/` (파이썬 3.12, uv)

## 시작

```bash
cd backend && uv venv && uv pip install -e ".[dev]"
mkdir -p ~/.qbot && cp ../.env.example ~/.qbot/.env   # 값을 채운다. 저장소에는 절대 넣지 않는다
bash ../tools/install-hooks.sh     # 커밋 전 비밀값 검사
.venv/bin/pytest -q
```

## 안전 규칙

- 실전 주문은 모의 운용 관문(QBOT-PRD-001 R8)을 통과하고 운영자가 승인해야만 나간다
- 비밀값은 `~/.qbot/.env`에만 둔다. `.env.example`은 이름만 있다
