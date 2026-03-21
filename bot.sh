#!/bin/bash
# 자동매매 봇 관리 스크립트
# 사용법: ./bot.sh [start|stop|restart|status|log|config]

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
VENV="$DIR/venv/bin/python3"
LOG="$DIR/bot.log"
PIDFILE="$DIR/.bot.pid"

# 기본 설정 (여기서 수정)
TICKER="${TICKER:-KRW-XRP}"
MODE="${MODE:-scalp}"
STRATEGY="${STRATEGY:-rsi}"
INVEST_RATIO="${INVEST_RATIO:-0.8}"
MAX_INVEST="${MAX_INVEST:-30000}"

start() {
    if is_running; then
        echo "이미 실행 중입니다 (PID: $(cat $PIDFILE))"
        status
        return
    fi

    echo "============================="
    echo "  자동매매 봇 시작"
    echo "  코인: $TICKER"
    echo "  모드: $MODE"
    echo "  전략: $STRATEGY"
    echo "  투자비율: ${INVEST_RATIO}00%"
    echo "  최대투자: ${MAX_INVEST}원"
    echo "============================="

    source "$DIR/venv/bin/activate" 2>/dev/null

    nohup $VENV run_trader.py \
        --ticker "$TICKER" \
        --mode "$MODE" \
        --strategy "$STRATEGY" \
        --invest-ratio "$INVEST_RATIO" \
        --max-invest "$MAX_INVEST" \
        > "$LOG" 2>&1 &

    echo $! > "$PIDFILE"
    echo "시작 완료 (PID: $!)"
    echo "로그: ./bot.sh log"
}

stop() {
    if ! is_running; then
        echo "실행 중인 봇이 없습니다"
        return
    fi

    PID=$(cat "$PIDFILE")
    echo "봇 종료 중... (PID: $PID)"
    kill "$PID" 2>/dev/null
    sleep 2

    if kill -0 "$PID" 2>/dev/null; then
        kill -9 "$PID" 2>/dev/null
    fi

    rm -f "$PIDFILE"
    echo "종료 완료"
}

restart() {
    stop
    sleep 1
    start
}

status() {
    if is_running; then
        PID=$(cat "$PIDFILE")
        echo "상태: 실행 중 (PID: $PID)"
        echo ""
        echo "최근 로그 5줄:"
        tail -5 "$LOG" 2>/dev/null
    else
        echo "상태: 중지됨"
    fi
}

log() {
    if [ ! -f "$LOG" ]; then
        echo "로그 파일 없음"
        return
    fi
    echo "로그 실시간 확인 (Ctrl+C로 나가기)"
    echo "---"
    tail -f "$LOG"
}

config() {
    echo "============================="
    echo "  현재 설정"
    echo "============================="
    echo "  코인:     $TICKER"
    echo "  모드:     $MODE (scalp=단타, swing=스윙)"
    echo "  전략:     $STRATEGY (rsi/macd/bollinger/combined)"
    echo "  투자비율: $INVEST_RATIO"
    echo "  최대투자: ${MAX_INVEST}원"
    echo ""
    echo "변경 방법:"
    echo "  TICKER=KRW-ETH ./bot.sh start"
    echo "  MODE=swing STRATEGY=combined ./bot.sh restart"
    echo ""
    echo "또는 이 파일 상단의 기본 설정을 직접 수정"
}

is_running() {
    [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null
}

case "${1:-help}" in
    start)   start ;;
    stop)    stop ;;
    restart) restart ;;
    status)  status ;;
    log)     log ;;
    config)  config ;;
    *)
        echo "사용법: ./bot.sh {start|stop|restart|status|log|config}"
        echo ""
        echo "  start   - 봇 시작"
        echo "  stop    - 봇 종료"
        echo "  restart - 재시작"
        echo "  status  - 상태 확인"
        echo "  log     - 로그 실시간 보기"
        echo "  config  - 현재 설정 확인"
        echo ""
        echo "설정 변경:"
        echo "  TICKER=KRW-ETH ./bot.sh start"
        echo "  MODE=swing ./bot.sh restart"
        ;;
esac
