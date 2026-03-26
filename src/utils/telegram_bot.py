"""텔레그램 알림 모듈 v2

개선사항:
  - Bot 인스턴스 재사용 (매 전송마다 생성하지 않음)
  - 백그라운드 스레드로 메시지 큐 처리 (매매 루프 블로킹 방지)
  - 전송 실패 시 최대 3회 재시도 (지수 백오프)
  - 긴 메시지 자동 분할 (4096자 제한)
  - HTML 특수문자 이스케이프
"""

from __future__ import annotations

import html
import logging
import queue
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 4096
MAX_RETRY = 3
RETRY_DELAYS = [1, 3, 7]  # 재시도 대기 (초)


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
        self._bot = None
        self._queue: queue.Queue = queue.Queue(maxsize=100)
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        if self._enabled:
            self._init_bot()
            self._start_sender()
            logger.info("텔레그램 알림 활성화 (chat_id: %s)", chat_id)
        else:
            logger.info("텔레그램 알림 비활성화 (TELEGRAM_TOKEN 또는 TELEGRAM_CHAT_ID 미설정)")

    def _init_bot(self):
        """Bot 인스턴스를 한 번만 생성"""
        try:
            from telegram import Bot
            self._bot = Bot(token=self.token)
        except Exception as e:
            logger.warning("텔레그램 Bot 초기화 실패: %s", e)
            self._enabled = False

    def _start_sender(self):
        """백그라운드 전송 스레드 시작"""
        self._thread = threading.Thread(target=self._sender_loop, daemon=True)
        self._thread.start()

    def _sender_loop(self):
        """큐에서 메시지를 꺼내 순차 전송"""
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        while not self._stop_event.is_set():
            try:
                msg = self._queue.get(timeout=1)
            except queue.Empty:
                continue

            self._send_with_retry(loop, msg)
            self._queue.task_done()

        loop.close()

    def _send_with_retry(self, loop, message: str):
        """최대 3회 재시도"""
        for attempt in range(MAX_RETRY):
            try:
                loop.run_until_complete(self._send_async(message))
                return
            except Exception as e:
                if attempt < MAX_RETRY - 1:
                    delay = RETRY_DELAYS[attempt]
                    logger.debug("텔레그램 전송 실패 (시도 %d/%d): %s → %d초 후 재시도",
                                 attempt + 1, MAX_RETRY, e, delay)
                    time.sleep(delay)
                else:
                    logger.warning("텔레그램 전송 최종 실패: %s", e)

    async def _send_async(self, message: str):
        """실제 전송 (Bot 인스턴스 재사용)"""
        if not self._bot:
            return
        await self._bot.send_message(
            chat_id=self.chat_id,
            text=message,
            parse_mode="HTML",
        )

    @property
    def enabled(self) -> bool:
        return self._enabled

    def send(self, message: str):
        """메시지를 큐에 추가 (논블로킹). 긴 메시지는 자동 분할."""
        if not self._enabled:
            return

        # 긴 메시지 분할
        for chunk in self._split_message(message):
            try:
                self._queue.put_nowait(chunk)
            except queue.Full:
                logger.warning("텔레그램 큐 가득 참 — 메시지 드롭")

    def stop(self):
        """종료 시 큐 비우고 스레드 정리"""
        if self._thread and self._thread.is_alive():
            self._stop_event.set()
            self._thread.join(timeout=5)

    @staticmethod
    def _split_message(message: str) -> list:
        """4096자 초과 메시지를 줄 단위로 분할"""
        if len(message) <= MAX_MESSAGE_LENGTH:
            return [message]

        chunks = []
        current = ""
        for line in message.split("\n"):
            if len(current) + len(line) + 1 > MAX_MESSAGE_LENGTH:
                if current:
                    chunks.append(current)
                current = line
            else:
                current = current + "\n" + line if current else line
        if current:
            chunks.append(current)
        return chunks

    @staticmethod
    def escape(text: str) -> str:
        """HTML 특수문자 이스케이프"""
        return html.escape(str(text))

    # ── 편의 메서드 ──

    def notify_buy(self, ticker: str, price: float, amount: float, reason: str):
        self.send(
            "<b>🟢 매수</b>\n"
            "코인: <code>%s</code>\n"
            "가격: %s원\n"
            "금액: %s원\n"
            "사유: %s"
            % (self.escape(ticker), "{:,.0f}".format(price),
               "{:,.0f}".format(amount), self.escape(reason))
        )

    def notify_sell(self, ticker: str, price: float, pnl_pct: float, reason: str):
        if pnl_pct >= 0:
            emoji = "💰" if pnl_pct >= 1.0 else "🟡"
        else:
            emoji = "🔴"
        self.send(
            "<b>%s 매도</b>\n"
            "코인: <code>%s</code>\n"
            "가격: %s원\n"
            "수익률: <b>%+.2f%%</b>\n"
            "사유: %s"
            % (emoji, self.escape(ticker), "{:,.0f}".format(price),
               pnl_pct, self.escape(reason))
        )

    def notify_stop_loss(self, ticker: str, price: float, loss_pct: float):
        self.send(
            "<b>🚨 손절</b>\n"
            "코인: <code>%s</code>\n"
            "가격: %s원\n"
            "손실: <b>-%.1f%%</b>"
            % (self.escape(ticker), "{:,.0f}".format(price), loss_pct)
        )

    def notify_take_profit(self, ticker: str, price: float, gain_pct: float):
        self.send(
            "<b>💰 익절</b>\n"
            "코인: <code>%s</code>\n"
            "가격: %s원\n"
            "수익: <b>+%.1f%%</b>"
            % (self.escape(ticker), "{:,.0f}".format(price), gain_pct)
        )

    def notify_start(self, ticker: str, strategy: str, mode: str):
        self.send(
            "<b>🤖 봇 시작</b>\n"
            "대상: <code>%s</code>\n"
            "전략: %s\n"
            "모드: %s"
            % (self.escape(ticker), self.escape(strategy), self.escape(mode))
        )

    def notify_error(self, message: str):
        self.send("<b>⚠️ 오류</b>\n%s" % self.escape(message))

    def notify_heartbeat(self, ticker: str, hold_info: str, daily_trades: int, daily_pnl: float):
        emoji = "📈" if daily_pnl >= 0 else "📉"
        self.send(
            "<b>%s 정기보고</b>\n"
            "대상: <code>%s</code>\n"
            "%s\n"
            "금일 거래: %d건\n"
            "금일 PnL: <b>%s원</b>"
            % (emoji, self.escape(ticker), self.escape(hold_info),
               daily_trades, "{:+,.0f}".format(daily_pnl))
        )

    def notify_arbitrage(self, symbol: str, buy_ex: str, sell_ex: str,
                         spread_pct: float, net_pct: float):
        self.send(
            "<b>📊 재정거래 기회</b>\n"
            "토큰: <code>%s</code>\n"
            "매수: %s → 매도: %s\n"
            "스프레드: %+.3f%%\n"
            "순수익: <b>%+.3f%%</b>"
            % (self.escape(symbol), self.escape(buy_ex.upper()),
               self.escape(sell_ex.upper()), spread_pct, net_pct)
        )

    def notify_cooldown(self, losses: int, minutes: int):
        self.send(
            "<b>⏸ 쿨다운 발동</b>\n"
            "%d연속 손실 → %d분 매매 중지"
            % (losses, minutes)
        )

    def notify_kill_switch(self, loss_pct: float, daily_pnl: float):
        self.send(
            "<b>🛑 킬스위치 발동</b>\n"
            "일일 손실: <b>-%.1f%%</b>\n"
            "PnL: %s원\n"
            "당일 매매 중단"
            % (loss_pct, "{:+,.0f}".format(daily_pnl))
        )
