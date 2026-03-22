#!/bin/bash
# 전체 봇 관리
# 사용법: ./bot_all.sh {start|stop|restart|status}

DIR="$(cd "$(dirname "$0")" && pwd)"

case "${1:-status}" in
    start)
        echo "===== 코인 봇 시작 ====="
        "$DIR/bot_btc.sh" start
        "$DIR/bot_eth.sh" start
        "$DIR/bot_xrp.sh" start
        echo ""
        echo "===== 재정거래 봇 시작 ====="
        "$DIR/bot_arb.sh" start
        echo ""
        echo "===== 미국 주식 봇 시작 ====="
        "$DIR/bot_us.sh" start
        echo ""
        echo "===== 전체 시작 완료 ====="
        echo ""
        echo "  코인: BTC/ETH/XRP (24시간)"
        echo "  재정: 업비트↔바이낸스 (24시간)"
        echo "  미국: AAPL/NVDA/TSLA (밤 22:30~06:00)"
        echo "  국내: ./bot_stock.sh start (별도 실행)"
        echo "  상태: ./bot_all.sh status"
        ;;
    stop)
        "$DIR/bot_btc.sh" stop
        "$DIR/bot_eth.sh" stop
        "$DIR/bot_xrp.sh" stop
        "$DIR/bot_arb.sh" stop
        "$DIR/bot_us.sh" stop
        echo "전체 종료 완료"
        ;;
    restart)
        "$DIR/bot_all.sh" stop
        sleep 2
        "$DIR/bot_all.sh" start
        ;;
    status)
        echo "=== BTC (MACD/스윙) ==="
        "$DIR/bot_btc.sh" status
        echo ""
        echo "=== ETH (MACD/스윙) ==="
        "$DIR/bot_eth.sh" status
        echo ""
        echo "=== XRP (볼린저/스윙) ==="
        "$DIR/bot_xrp.sh" status
        echo ""
        echo "=== 재정거래 (업비트↔바이낸스) ==="
        "$DIR/bot_arb.sh" status
        echo ""
        echo "=== 미국주식 (AAPL/NVDA/TSLA) ==="
        "$DIR/bot_us.sh" status
        ;;
    *)
        echo "사용법: ./bot_all.sh {start|stop|restart|status}"
        ;;
esac
