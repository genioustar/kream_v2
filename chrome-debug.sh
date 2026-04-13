#!/bin/bash
# 아디다스 크롤러용 Chrome CDP 실행 스크립트

CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
DEBUG_PORT=9222
USER_DATA_DIR="/tmp/chrome-debug-profile"

# 이미 실행 중인지 확인
if curl -s "http://127.0.0.1:${DEBUG_PORT}/json/version" &>/dev/null; then
    echo "Chrome CDP가 이미 실행 중입니다 (포트 ${DEBUG_PORT})"
    exit 0
fi

# 기존 Chrome 프로세스 종료
pkill -f "Google Chrome" 2>/dev/null
sleep 1

# Chrome 실행
"$CHROME" \
    --remote-debugging-port=$DEBUG_PORT \
    --user-data-dir="$USER_DATA_DIR" \
    --no-first-run \
    > /tmp/chrome_debug.log 2>&1 &

# 포트 열릴 때까지 대기 (최대 10초)
for i in $(seq 1 10); do
    sleep 1
    if curl -s "http://127.0.0.1:${DEBUG_PORT}/json/version" &>/dev/null; then
        echo "Chrome CDP 준비 완료 (포트 ${DEBUG_PORT})"
        exit 0
    fi
done

echo "오류: Chrome CDP 연결 실패. /tmp/chrome_debug.log 확인:"
cat /tmp/chrome_debug.log
exit 1
