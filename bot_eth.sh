#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$DIR/venv/bin/python3"
LOG="$DIR/eth_bot.log"
PIDFILE="$DIR/.eth_bot.pid"
TICKER="KRW-ETH"
MODE="${MODE:-swing}"
STRATEGY="${STRATEGY:-macd}"
INVEST_RATIO="${INVEST_RATIO:-0.3}"
MAX_INVEST="${MAX_INVEST:-100000}"

start() {
    [ -f "$PIDFILE" ] && kill -0 "$(cat $PIDFILE)" 2>/dev/null && echo "ETH 봇 실행 중 (PID: $(cat $PIDFILE))" && return
    source "$DIR/venv/bin/activate" 2>/dev/null
    nohup $VENV run_trader.py --ticker $TICKER --mode $MODE --strategy $STRATEGY --invest-ratio $INVEST_RATIO --max-invest $MAX_INVEST > "$LOG" 2>&1 &
    echo $! > "$PIDFILE"
    echo "ETH 봇 시작 (PID: $!)"
}
stop() {
    [ -f "$PIDFILE" ] && kill "$(cat $PIDFILE)" 2>/dev/null && rm -f "$PIDFILE" && echo "ETH 봇 종료" || echo "실행 중 아님"
}
case "${1:-start}" in
    start) start ;; stop) stop ;; restart) stop; sleep 1; start ;;
    log) tail -f "$LOG" ;; status) [ -f "$PIDFILE" ] && kill -0 "$(cat $PIDFILE)" 2>/dev/null && echo "실행 중 (PID: $(cat $PIDFILE))" && tail -3 "$LOG" || echo "중지됨" ;;
    *) echo "사용법: ./bot_eth.sh {start|stop|restart|log|status}" ;;
esac
