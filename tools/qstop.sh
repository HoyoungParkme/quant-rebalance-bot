#!/usr/bin/env bash
# 봇을 확실히 멈춘다. 감시 스크립트를 먼저 죽여야 다시 안 뜬다.
pkill -f "run-bot.sh" 2>/dev/null
for i in 1 2 3 4 5; do
  pid=$(pgrep -f "qbot run$" | head -1)
  [ -z "$pid" ] && break
  kill "$pid" 2>/dev/null
  sleep 2
done
pid=$(pgrep -f "qbot run$" | head -1)
if [ -n "$pid" ]; then kill -9 "$pid" 2>/dev/null; sleep 1; fi
pgrep -f "qbot run$" >/dev/null && echo "아직 살아 있다: $(pgrep -f 'qbot run$')" || echo "봇 멈춤"
