#!/usr/bin/env bash
# git 훅 설치. 저장소를 받은 뒤 한 번 실행한다.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
git config core.hooksPath .githooks
echo "훅 설치됨: .githooks/"
