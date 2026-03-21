#!/bin/bash
# 주식 자동매매 봇 관리 스크립트
# 사용법: ./bot_stock.sh [start|stop|restart|status|log]

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
VENV="$DIR/venv/bin/python3"
LOG="$DIR/stock_bot.log"
PIDFILE="$DIR/.stock_bot.pid"

# 기본 설정
CODE="${CODE:-005930}"           # 종목코드 (삼성전자)
STRATEGY="${STRATEGY:-combined}"
INVEST_RATIO="${INVEST_RATIO:-0.1}"
MAX_INVEST="${MAX_INVEST:-500000}"
SCAN="${SCAN:-}"                 # 자동스캔: SCAN=1 ./bot_stock.sh start

start() {
    if is_running; then
        echo "이미 실행 중 (PID: $(cat $PIDFILE))"
        status
        return
    fi

    echo "============================="
    echo "  주식 자동매매 봇 시작"
    echo "  종목: $CODE"
    echo "  전략: $STRATEGY"
    echo "  투자비율: ${INVEST_RATIO}"
    echo "  최대투자: ${MAX_INVEST}원"
    echo "============================="

    source "$DIR/venv/bin/activate" 2>/dev/null

    SCAN_FLAG=""
    [ -n "$SCAN" ] && SCAN_FLAG="--auto-scan"

    nohup $VENV run_stock.py \
        --code "$CODE" \
        --strategy "$STRATEGY" \
        --invest-ratio "$INVEST_RATIO" \
        --max-invest "$MAX_INVEST" \
        $SCAN_FLAG \
        > "$LOG" 2>&1 &

    echo $! > "$PIDFILE"
    echo "시작 완료 (PID: $!)"
    echo "로그: ./bot_stock.sh log"
}

stop() {
    if ! is_running; then
        echo "실행 중인 봇이 없습니다"
        return
    fi
    PID=$(cat "$PIDFILE")
    echo "종료 중... (PID: $PID)"
    kill "$PID" 2>/dev/null
    sleep 2
    kill -0 "$PID" 2>/dev/null && kill -9 "$PID" 2>/dev/null
    rm -f "$PIDFILE"
    echo "종료 완료"
}

restart() { stop; sleep 1; start; }

status() {
    if is_running; then
        echo "상태: 실행 중 (PID: $(cat $PIDFILE))"
        echo ""; tail -5 "$LOG" 2>/dev/null
    else
        echo "상태: 중지됨"
    fi
}

log() { echo "Ctrl+C로 나가기"; echo "---"; tail -f "$LOG"; }

is_running() { [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }

case "${1:-help}" in
    start)   start ;;
    stop)    stop ;;
    restart) restart ;;
    status)  status ;;
    log)     log ;;
    *)
        echo "사용법: ./bot_stock.sh {start|stop|restart|status|log}"
        echo ""
        echo "  CODE=035720 ./bot_stock.sh start     # 카카오"
        echo "  CODE=005380 ./bot_stock.sh start     # 현대차"
        echo "  STRATEGY=rsi ./bot_stock.sh restart"
        ;;
esac
