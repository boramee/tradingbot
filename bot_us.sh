#!/bin/bash
# 미국 주식 자동매매 봇 관리
# 사용법: ./bot_us.sh {start|stop|restart|status|log}

DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$DIR/venv/bin/python3"
LOG="$DIR/us_bot.log"
PIDFILE="$DIR/.us_bot.pid"

SYMBOLS="${SYMBOLS:-AAPL,NVDA,TSLA}"
STRATEGY="${STRATEGY:-macd}"
MAX_INVEST="${MAX_INVEST:-500}"

start() {
    [ -f "$PIDFILE" ] && kill -0 "$(cat $PIDFILE)" 2>/dev/null && echo "실행 중 (PID: $(cat $PIDFILE))" && return

    VIRTUAL_FLAG="--virtual"
    [ "$REAL" = "1" ] && VIRTUAL_FLAG=""
    TRADE_MODE="모의투자"
    [ "$REAL" = "1" ] && TRADE_MODE="실전"

    echo "============================="
    echo "  미국 주식 자동매매 봇 시작"
    echo "  종목: $SYMBOLS"
    echo "  전략: $STRATEGY"
    echo "  모드: $TRADE_MODE"
    echo "  최대: \$$MAX_INVEST"
    echo "============================="

    source "$DIR/venv/bin/activate" 2>/dev/null
    nohup $VENV run_us.py \
        --symbols "$SYMBOLS" \
        --strategy "$STRATEGY" \
        --max-invest "$MAX_INVEST" \
        $VIRTUAL_FLAG \
        > "$LOG" 2>&1 &
    echo $! > "$PIDFILE"
    echo "시작 (PID: $!)"
}
stop() {
    [ -f "$PIDFILE" ] && kill "$(cat $PIDFILE)" 2>/dev/null && rm -f "$PIDFILE" && echo "종료" || echo "실행 중 아님"
}
case "${1:-start}" in
    start) start ;; stop) stop ;; restart) stop; sleep 1; start ;;
    log) tail -f "$LOG" ;;
    status) [ -f "$PIDFILE" ] && kill -0 "$(cat $PIDFILE)" 2>/dev/null && echo "실행 중 (PID: $(cat $PIDFILE))" && tail -3 "$LOG" || echo "중지됨" ;;
    *) echo "사용법: ./bot_us.sh {start|stop|restart|log|status}"
       echo "  SYMBOLS=AAPL,MSFT ./bot_us.sh start"
       echo "  REAL=1 ./bot_us.sh start  # 실전" ;;
esac
