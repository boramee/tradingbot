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
    """텔레그램 봇으로 매매 알림 전송"""

    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self._enabled = bool(token and chat_id)
        if self._enabled:
            logger.info("텔레그램 알림 활성화 (chat_id: %s)", chat_id)
        else:
            logger.info("텔레그램 알림 비활성화 (토큰 미설정)")

    @property
    def enabled(self) -> bool:
        return self._enabled

    def send(self, message: str):
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

    def notify_buy(self, stock: str, price: float, amount: float, reason: str):
        self.send(
            "<b>🟢 매수 신호</b>\n"
            "종목: <code>%s</code>\n"
            "가격: %s원\n"
            "금액: %s원\n"
            "사유: %s"
            % (stock, f"{price:,.0f}", f"{amount:,.0f}", reason)
        )

    def notify_sell(self, stock: str, price: float, pnl_pct: float, reason: str):
        emoji = "🔴" if pnl_pct < 0 else "🟡"
        self.send(
            "<b>%s 매도 신호</b>\n"
            "종목: <code>%s</code>\n"
            "가격: %s원\n"
            "수익률: <b>%+.2f%%</b>\n"
            "사유: %s"
            % (emoji, stock, f"{price:,.0f}", pnl_pct, reason)
        )

    def notify_stop_loss(self, stock: str, price: float, loss_pct: float):
        self.send(
            "<b>🚨 손절 실행</b>\n"
            "종목: <code>%s</code>\n"
            "가격: %s원\n"
            "손실: <b>%.1f%%</b>"
            % (stock, f"{price:,.0f}", loss_pct)
        )

    def notify_take_profit(self, stock: str, price: float, gain_pct: float):
        self.send(
            "<b>💰 익절 실행</b>\n"
            "종목: <code>%s</code>\n"
            "가격: %s원\n"
            "수익: <b>+%.1f%%</b>"
            % (stock, f"{price:,.0f}", gain_pct)
        )

    def notify_start(self, stock: str, strategy: str, mode: str):
        self.send(
            "<b>🤖 삼성전자 자동매매 시작</b>\n"
            "종목: <code>%s</code>\n"
            "전략: %s\n"
            "모드: %s"
            % (stock, strategy, mode)
        )

    def notify_error(self, message: str):
        self.send("<b>⚠️ 오류</b>\n%s" % message)
