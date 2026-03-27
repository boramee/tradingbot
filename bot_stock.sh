#!/bin/bash
# 주식 자동매매 봇 관리 스크립트
# 사용법: ./bot_stock.sh [start|stop|restart|status|log]

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
# .env 파일 로드
set -a
[ -f "$DIR/.env" ] && source "$DIR/.env"
set +a
VENV="$DIR/venv/bin/python3"
LOG="$DIR/stock_bot.log"
PIDFILE="$DIR/.stock_bot.pid"

# 기본 설정
CODE="${CODE:-005930}"           # 종목코드 (삼성전자)
STRATEGY="${STRATEGY:-combined}"
INVEST_RATIO="${INVEST_RATIO:-0.2}"
MAX_INVEST="${MAX_INVEST:-300000}"
SCAN="${SCAN:-1}"                 # 기본: 자동스캔. 끄려면 SCAN=0
REAL="${REAL:-1}"                  # 기본: 실전. 모의투자는 REAL=0

start() {
    if is_running; then
        echo "이미 실행 중 (PID: $(cat $PIDFILE))"
        status
        return
    fi

    if [ "$SCAN" = "1" ]; then
        SCAN_LABEL="자동 스캔 (스캐너가 종목 선택)"
    else
        SCAN_LABEL="고정 종목: $CODE"
    fi

    TRADE_MODE="모의투자"
    [ "$REAL" = "1" ] && TRADE_MODE="실전"

    echo "============================="
    echo "  주식 스윙 봇 v6 시작"
    echo "  종목: $SCAN_LABEL"
    echo "  전략: Swing (눌림목 매수)"
    echo "  모드: $TRADE_MODE"
    echo "  투자비율: ${INVEST_RATIO}"
    echo "  최대투자: ${MAX_INVEST}원"
    echo "  손절: -3% | 익절: +5% | 트레일링: -2%"
    echo "============================="

    source "$DIR/venv/bin/activate" 2>/dev/null

    SCAN_FLAG=""
    [ "$SCAN" = "1" ] && SCAN_FLAG="--auto-scan"

    VIRTUAL_FLAG="--virtual"
    [ "$REAL" = "1" ] && VIRTUAL_FLAG=""

    CODE_FLAG="--code $CODE"
    [ "$SCAN" = "1" ] && CODE_FLAG=""

    nohup $VENV run_stock.py \
        $CODE_FLAG \
        --strategy "$STRATEGY" \
        --invest-ratio "$INVEST_RATIO" \
        --max-invest "$MAX_INVEST" \
        $SCAN_FLAG \
        $VIRTUAL_FLAG \
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

config() {
    TRADE_MODE="모의투자"
    [ "$REAL" = "1" ] && TRADE_MODE="🔴 실전"

    if [ "$SCAN" = "1" ]; then
        SCAN_LABEL="자동 스캔"
    else
        SCAN_LABEL="고정: $CODE"
    fi

    echo "============================="
    echo "  주식봇 현재 설정"
    echo "============================="
    echo "  종목: $SCAN_LABEL"
    echo "  전략: $STRATEGY"
    echo "  모드: $TRADE_MODE"
    echo "  투자비율: ${INVEST_RATIO} (${MAX_INVEST}원 제한)"
    echo ""
    echo "  KIS_APP_KEY: $([ -n "$KIS_APP_KEY" ] && echo '설정됨' || echo '❌ 미설정')"
    echo "  KIS_ACCOUNT_NO: $([ -n "$KIS_ACCOUNT_NO" ] && echo "$KIS_ACCOUNT_NO" || echo '❌ 미설정')"
    echo "  TELEGRAM: $([ -n "$TELEGRAM_TOKEN" ] && echo '설정됨' || echo '❌ 미설정')"
    echo "============================="
}

check() {
    echo "사전점검 실행 중..."
    source "$DIR/venv/bin/activate" 2>/dev/null

    VIRTUAL_FLAG="--virtual"
    [ "$REAL" = "1" ] && VIRTUAL_FLAG=""

    $VENV run_stock.py --code "$CODE" --strategy "$STRATEGY" $VIRTUAL_FLAG --preflight
    if [ $? -eq 0 ]; then
        echo ""
        echo "✅ 사전점검 통과 — 시작 준비 완료"
    else
        echo ""
        echo "❌ 사전점검 실패 — 위 항목을 확인하세요"
    fi
}

is_running() { [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }

case "${1:-help}" in
    start)   start ;;
    stop)    stop ;;
    restart) restart ;;
    status)  status ;;
    log)     log ;;
    config)  config ;;
    check)   check ;;
    *)
        echo "사용법: ./bot_stock.sh {start|stop|restart|status|log|config|check}"
        echo ""
        echo "  ./bot_stock.sh config                    # 현재 설정 확인"
        echo "  ./bot_stock.sh check                     # 사전점검 (인증/잔고/시세)"
        echo "  ./bot_stock.sh start                     # 자동 스캔 (모의투자)"
        echo "  REAL=1 ./bot_stock.sh check              # 실전 모드 사전점검"
        echo "  REAL=1 ./bot_stock.sh start              # 실전 투입"
        echo "  SCAN=0 CODE=005930 ./bot_stock.sh start  # 고정 종목"
        echo "  STRATEGY=rsi ./bot_stock.sh restart"
        ;;

esac
