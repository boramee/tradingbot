#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$DIR/venv/bin/python3"
LOG="$DIR/xrp_bot.log"
PIDFILE="$DIR/.xrp_bot.pid"
TICKER="KRW-XRP"
MODE="${MODE:-swing}"
STRATEGY="${STRATEGY:-bollinger}"
INVEST_RATIO="${INVEST_RATIO:-0.3}"
MAX_INVEST="${MAX_INVEST:-100000}"
CANDLE="${CANDLE:-minute15}"
STOP_LOSS="${STOP_LOSS:-1.5}"
TAKE_PROFIT="${TAKE_PROFIT:-2.0}"
TRAILING="${TRAILING:-0.8}"

start() {
    [ -f "$PIDFILE" ] && kill -0 "$(cat $PIDFILE)" 2>/dev/null && echo "XRP 봇 실행 중 (PID: $(cat $PIDFILE))" && return
    source "$DIR/venv/bin/activate" 2>/dev/null
    nohup $VENV run_trader.py --ticker $TICKER --strategy $STRATEGY --invest-ratio $INVEST_RATIO --max-invest $MAX_INVEST --candle $CANDLE --interval 30 --stop-loss $STOP_LOSS --take-profit $TAKE_PROFIT --trailing $TRAILING > "$LOG" 2>&1 &
    echo $! > "$PIDFILE"
    echo "XRP 봇 시작 (PID: $!)"
}
stop() {
    [ -f "$PIDFILE" ] && kill "$(cat $PIDFILE)" 2>/dev/null && rm -f "$PIDFILE" && echo "XRP 봇 종료" || echo "실행 중 아님"
}
case "${1:-start}" in
    start) start ;; stop) stop ;; restart) stop; sleep 1; start ;;
    log) tail -f "$LOG" ;; status) [ -f "$PIDFILE" ] && kill -0 "$(cat $PIDFILE)" 2>/dev/null && echo "실행 중 (PID: $(cat $PIDFILE))" && tail -3 "$LOG" || echo "중지됨" ;;
    *) echo "사용법: ./bot_xrp.sh {start|stop|restart|log|status}" ;;
esac
