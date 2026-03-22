#!/bin/bash
# 거래소 간 재정거래 봇 관리
# 사용법: ./bot_arb.sh {start|stop|restart|status|log}

DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$DIR/venv/bin/python3"
LOG="$DIR/arb_bot.log"
PIDFILE="$DIR/.arb_bot.pid"

COINS="${COINS:-BTC,ETH,XRP}"
MIN_PROFIT="${MIN_PROFIT:-0.3}"
MAX_TRADE="${MAX_TRADE:-100000}"

start() {
    [ -f "$PIDFILE" ] && kill -0 "$(cat $PIDFILE)" 2>/dev/null && echo "실행 중 (PID: $(cat $PIDFILE))" && return
    echo "============================="
    echo "  재정거래 봇 시작"
    echo "  코인: $COINS"
    echo "  최소순수익: ${MIN_PROFIT}%"
    echo "  1회최대: ${MAX_TRADE}원"
    echo "============================="
    source "$DIR/venv/bin/activate" 2>/dev/null
    nohup $VENV run_arb.py --coins "$COINS" --min-profit "$MIN_PROFIT" --max-trade "$MAX_TRADE" > "$LOG" 2>&1 &
    echo $! > "$PIDFILE"
    echo "시작 (PID: $!)"
}
stop() {
    [ -f "$PIDFILE" ] && kill "$(cat $PIDFILE)" 2>/dev/null && rm -f "$PIDFILE" && echo "종료" || echo "실행 중 아님"
}
case "${1:-start}" in
    start) start ;; stop) stop ;; restart) stop; sleep 1; start ;;
    log) tail -f "$LOG" ;; status) [ -f "$PIDFILE" ] && kill -0 "$(cat $PIDFILE)" 2>/dev/null && echo "실행 중 (PID: $(cat $PIDFILE))" && tail -5 "$LOG" || echo "중지됨" ;;
    *) echo "사용법: ./bot_arb.sh {start|stop|restart|log|status}" ;;
esac
