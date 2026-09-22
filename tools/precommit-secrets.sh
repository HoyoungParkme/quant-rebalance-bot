#!/usr/bin/env bash
# 커밋 전 비밀값 검사 (QBOT-PRD-001 N4). gitleaks가 있으면 그것을, 없으면 내장 패턴 검사를 쓴다.
set -euo pipefail
if command -v gitleaks >/dev/null 2>&1; then
  gitleaks protect --staged --redact --no-banner
  exit $?
fi
# 대체 검사: 스테이징된 파일에서 한투 앱키(PS로 시작하는 36자), 텔레그램 토큰, DART 키(40자 hex), 계좌번호 형태
pat='(PS[A-Za-z0-9]{34}\b|[0-9]{9,10}:[A-Za-z0-9_-]{35}|APP_SECRET=.{20,}|_TOKEN=.{10,}|_KEY=.{10,})'
if git diff --cached --name-only --diff-filter=ACM | grep -v -E '^(\.env\.example|tools/precommit-secrets\.sh)$' | xargs -r git diff --cached -U0 -- | grep -E '^\+' | grep -E -q "$pat"; then
  echo "비밀값으로 보이는 내용이 스테이징에 있습니다. 커밋을 막습니다." >&2
  git diff --cached --name-only --diff-filter=ACM | grep -v -E '^(\.env\.example|tools/precommit-secrets\.sh)$' | xargs -r git diff --cached -U0 -- | grep -E '^\+' | grep -E "$pat" | sed -E 's/(.{0,40}).*/\1…/' >&2
  exit 1
fi
