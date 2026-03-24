"""포트폴리오 열지수(Heat Index) — 글로벌 노출도 관리 v6

문제:
  BTC, ETH, NVDA 등 여러 봇이 독립 운용되지만,
  시장 폭락 시 상관관계가 1에 수렴하여 모든 자산이 동시 하락.
  개별 봇의 리스크 관리로는 포트폴리오 전체 손실을 방지할 수 없음.

해결:
  전체 계좌의 총 노출도(Total Exposure)를 공유 상태 파일로 관리.
  노출도가 임계치(기본 70%)를 넘으면 신규 매수를 차단하고,
  각 봇은 매수 전 이 글로벌 컷을 확인.

사용법:
  # 각 봇의 매수 로직에서:
  heat = PortfolioHeat()
  heat.register("btc_bot", position_krw=500_000, total_krw=2_000_000)
  if heat.is_overheated():
      # 매수 차단
      pass

  # bot_all.sh 또는 모니터링 스크립트에서:
  heat.summary()  # 전체 노출 현황
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

STATE_FILE = os.path.join("logs", "portfolio_heat.json")


@dataclass
class BotExposure:
    """개별 봇의 노출 정보"""
    bot_name: str
    position_krw: float = 0.0  # 현재 포지션 가치 (KRW)
    total_krw: float = 0.0     # 해당 봇의 총 자산 (KRW)
    updated_at: float = 0.0    # 마지막 업데이트 epoch


class PortfolioHeat:
    """전체 포트폴리오 열지수 관리

    각 봇이 자신의 노출 정보를 등록하면,
    전체 노출도를 계산하여 글로벌 컷 여부를 판단.
    """

    def __init__(
        self,
        max_exposure_pct: float = 70.0,
        state_file: str = STATE_FILE,
        stale_minutes: float = 30,
    ):
        self.max_exposure_pct = max_exposure_pct
        self._state_file = state_file
        self._stale_minutes = stale_minutes  # 이 시간 이상 업데이트 없으면 무시

    def register(self, bot_name: str, position_krw: float, total_krw: float):
        """봇의 현재 노출 정보 등록/갱신"""
        state = self._load_state()
        state[bot_name] = asdict(BotExposure(
            bot_name=bot_name,
            position_krw=position_krw,
            total_krw=total_krw,
            updated_at=time.time(),
        ))
        self._save_state(state)

    def unregister(self, bot_name: str):
        """봇 종료 시 노출 정보 제거"""
        state = self._load_state()
        state.pop(bot_name, None)
        self._save_state(state)

    def get_total_exposure(self) -> Dict[str, float]:
        """전체 포트폴리오 노출 현황 반환

        Returns:
            {"total_position_krw": ..., "total_asset_krw": ...,
             "exposure_pct": ..., "active_bots": N}
        """
        state = self._load_state()
        now = time.time()
        stale_cutoff = now - self._stale_minutes * 60

        total_pos = 0.0
        total_asset = 0.0
        active = 0

        for bot_name, info in state.items():
            if info.get("updated_at", 0) < stale_cutoff:
                continue
            total_pos += info.get("position_krw", 0)
            total_asset += info.get("total_krw", 0)
            active += 1

        exposure_pct = (total_pos / total_asset * 100) if total_asset > 0 else 0.0

        return {
            "total_position_krw": total_pos,
            "total_asset_krw": total_asset,
            "exposure_pct": exposure_pct,
            "active_bots": active,
        }

    def is_overheated(self) -> bool:
        """전체 노출도가 임계치를 초과하는지 확인"""
        info = self.get_total_exposure()
        overheated = info["exposure_pct"] > self.max_exposure_pct
        if overheated:
            logger.warning(
                "[글로벌컷] 포트폴리오 노출도 %.1f%% > %.1f%% → 신규 매수 차단"
                " (포지션: %s원 / 자산: %s원, 활성봇: %d)",
                info["exposure_pct"], self.max_exposure_pct,
                "{:,.0f}".format(info["total_position_krw"]),
                "{:,.0f}".format(info["total_asset_krw"]),
                info["active_bots"],
            )
        return overheated

    def remaining_capacity_pct(self) -> float:
        """매수 가능한 잔여 비중 (%) — 0이면 매수 불가"""
        info = self.get_total_exposure()
        return max(0.0, self.max_exposure_pct - info["exposure_pct"])

    def summary(self) -> str:
        """텔레그램/로그용 포트폴리오 노출 현황 문자열"""
        state = self._load_state()
        now = time.time()
        stale_cutoff = now - self._stale_minutes * 60
        info = self.get_total_exposure()

        lines = [
            "=== 포트폴리오 열지수 ===",
            "총 노출도: %.1f%% / 한도: %.1f%%" % (info["exposure_pct"], self.max_exposure_pct),
            "포지션: %s원 / 자산: %s원" % (
                "{:,.0f}".format(info["total_position_krw"]),
                "{:,.0f}".format(info["total_asset_krw"])),
            "활성 봇: %d개" % info["active_bots"],
            "---",
        ]

        for bot_name, bot_info in state.items():
            stale = bot_info.get("updated_at", 0) < stale_cutoff
            pos = bot_info.get("position_krw", 0)
            total = bot_info.get("total_krw", 0)
            pct = (pos / total * 100) if total > 0 else 0
            tag = " (stale)" if stale else ""
            lines.append("  %s: %s원 / %s원 (%.0f%%)%s" % (
                bot_name,
                "{:,.0f}".format(pos),
                "{:,.0f}".format(total),
                pct, tag))

        return "\n".join(lines)

    # ── 파일 I/O ──

    def _load_state(self) -> Dict:
        try:
            if os.path.exists(self._state_file):
                with open(self._state_file, "r") as f:
                    return json.load(f)
        except Exception as e:
            logger.debug("[PortfolioHeat] 상태 로드 실패: %s", e)
        return {}

    def _save_state(self, state: Dict):
        try:
            Path(self._state_file).parent.mkdir(parents=True, exist_ok=True)
            with open(self._state_file, "w") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.debug("[PortfolioHeat] 상태 저장 실패: %s", e)
