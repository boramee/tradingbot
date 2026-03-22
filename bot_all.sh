#!/bin/bash
# 코인 봇 전체 관리
# 사용법: ./bot_all.sh {start|stop|restart|status}

DIR="$(cd "$(dirname "$0")" && pwd)"

case "${1:-status}" in
    start)
        "$DIR/bot_btc.sh" start
        "$DIR/bot_eth.sh" start
        "$DIR/bot_xrp.sh" start
        echo "전체 시작 완료"
        ;;
    stop)
        "$DIR/bot_btc.sh" stop
        "$DIR/bot_eth.sh" stop
        "$DIR/bot_xrp.sh" stop
        echo "전체 종료 완료"
        ;;
    restart)
        "$DIR/bot_all.sh" stop
        sleep 2
        "$DIR/bot_all.sh" start
        ;;
    status)
        echo "=== BTC ===" && "$DIR/bot_btc.sh" status
        echo ""
        echo "=== ETH ===" && "$DIR/bot_eth.sh" status
        echo ""
        echo "=== XRP ===" && "$DIR/bot_xrp.sh" status
        ;;
    *)
        echo "사용법: ./bot_all.sh {start|stop|restart|status}"
        ;;
esac
