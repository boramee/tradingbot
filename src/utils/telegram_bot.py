"""텔레그램 알림 모듈"""

from __future__ import annotations

import logging
import asyncio
from typing import Optional

logger = logging.getLogger(__name__)

_loop: Optional[asyncio.AbstractEventLoop] = None


def _get_loop() -> asyncio.AbstractEventLoop:
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
    return _loop


class TelegramNotifier:
    """
    텔레그램 봇으로 매매 알림 전송.

    설정 방법:
      1. @BotFather에게 /newbot → 토큰 발급
      2. 봇에게 아무 메시지 보내기
      3. https://api.telegram.org/bot<TOKEN>/getUpdates 에서 chat_id 확인
      4. .env에 TELEGRAM_TOKEN, TELEGRAM_CHAT_ID 설정
    """

    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self._enabled = bool(token and chat_id)
        if self._enabled:
            logger.info("텔레그램 알림 활성화 (chat_id: %s)", chat_id)
        else:
            logger.info("텔레그램 알림 비활성화 (TELEGRAM_TOKEN 또는 TELEGRAM_CHAT_ID 미설정)")

    @property
    def enabled(self) -> bool:
        return self._enabled

    def send(self, message: str):
        """동기 방식으로 메시지 전송 (내부적으로 async 호출)"""
        if not self._enabled:
            return
        try:
            loop = _get_loop()
            loop.run_until_complete(self._send_async(message))
        except Exception as e:
            logger.warning("텔레그램 전송 실패: %s", e)

    async def _send_async(self, message: str):
        from telegram import Bot
        bot = Bot(token=self.token)
        await bot.send_message(
            chat_id=self.chat_id,
            text=message,
            parse_mode="HTML",
        )

    # ── 편의 메서드 ──

    def notify_buy(self, ticker: str, price: float, amount: float, reason: str):
        self.send(
            "<b>🟢 매수 신호</b>\n"
            "코인: <code>%s</code>\n"
            "가격: %s원\n"
            "금액: %s원\n"
            "사유: %s"
            % (ticker, "{:,.0f}".format(price), "{:,.0f}".format(amount), reason)
        )

    def notify_sell(self, ticker: str, price: float, pnl_pct: float, reason: str):
        emoji = "🔴" if pnl_pct < 0 else "🟡"
        self.send(
            "<b>%s 매도 신호</b>\n"
            "코인: <code>%s</code>\n"
            "가격: %s원\n"
            "수익률: <b>%+.2f%%</b>\n"
            "사유: %s"
            % (emoji, ticker, "{:,.0f}".format(price), pnl_pct, reason)
        )

    def notify_stop_loss(self, ticker: str, price: float, loss_pct: float):
        self.send(
            "<b>🚨 손절 실행</b>\n"
            "코인: <code>%s</code>\n"
            "가격: %s원\n"
            "손실: <b>%.1f%%</b>"
            % (ticker, "{:,.0f}".format(price), loss_pct)
        )

    def notify_take_profit(self, ticker: str, price: float, gain_pct: float):
        self.send(
            "<b>💰 익절 실행</b>\n"
            "코인: <code>%s</code>\n"
            "가격: %s원\n"
            "수익: <b>+%.1f%%</b>"
            % (ticker, "{:,.0f}".format(price), gain_pct)
        )

    def notify_start(self, ticker: str, strategy: str, mode: str):
        self.send(
            "<b>🤖 봇 시작</b>\n"
            "대상: <code>%s</code>\n"
            "전략: %s\n"
            "모드: %s"
            % (ticker, strategy, mode)
        )

    def notify_error(self, message: str):
        self.send("<b>⚠️ 오류</b>\n%s" % message)

    def notify_arbitrage(self, symbol: str, buy_ex: str, sell_ex: str,
                         spread_pct: float, net_pct: float):
        self.send(
            "<b>📊 재정거래 기회</b>\n"
            "토큰: <code>%s</code>\n"
            "매수: %s → 매도: %s\n"
            "스프레드: %+.3f%%\n"
            "순수익: <b>%+.3f%%</b>"
            % (symbol, buy_ex.upper(), sell_ex.upper(), spread_pct, net_pct)
        )
