# 에이전트용 안내

- 구조의 확정본은 `docs/specs/06-DOM/QBOT-DOM-002.md` 1장이다. 폴더를 새로 만들려면 도메인 모델부터 고친다
- 함수 동작은 `docs/specs/10-MS/QBOT-MS-001.md`, 구현 순서는 `docs/specs/11-CODE/QBOT-CODE-001.md`
- 호출 방향은 `entry → service → crud` 한 방향. 다른 도메인의 crud를 부르지 않는다
- 시장 데이터 조회는 `PointInTime`만 받는다. `date`를 받는 조회 함수를 만들지 않는다
- 주문 전송 호출은 재시도하지 않는다. 상태는 `planned → unknown → sent` 순서로 바꾼다
- 비밀값을 코드·테스트·문서에 쓰지 않는다. 커밋 훅이 막는다
- 브랜치는 `feat/<슬라이스>`, main에 squash 머지
