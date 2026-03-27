"""관심종목(Watchlist) 관리

스캐너가 발굴한 종목을 저장하고, 다음 날 눌림목 매수 후보로 사용.

구조:
  {
    "updated": "2026-03-26",
    "candidates": [
      {
        "code": "100790",
        "name": "미래에셋벤처투자",
        "close": 28500,
        "vwap": 27800,
        "change_pct": 5.6,
        "score": 125,
        "reasons": ["거래량전일비264%", "신고가돌파", "정배열"],
        "trade_value": 150000000000,
        "added_date": "2026-03-26",
        "ma5": 27000,
        "ma20": 25500
      }
    ]
  }
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from typing import List, Optional

logger = logging.getLogger(__name__)

WATCHLIST_PATH = "logs/watchlist.json"


@dataclass
class WatchItem:
    """관심종목 항목"""
    code: str
    name: str
    close: int                       # 발굴일 종가
    vwap: float = 0.0                # 발굴일 VWAP
    change_pct: float = 0.0          # 발굴일 등락률
    score: float = 0.0               # 스캐너 점수
    reasons: List[str] = field(default_factory=list)
    trade_value: int = 0             # 거래대금
    added_date: str = ""             # 등록일
    ma5: float = 0.0                 # 5일 이평선
    ma20: float = 0.0                # 20일 이평선
    pullback_target: float = 0.0     # 눌림목 매수 목표가
    foreign_flow: int = 0            # 외국인 연속 순매수 일수 (음수=순매도)
    inst_flow: int = 0               # 기관 연속 순매수 일수
    expired: bool = False            # 만료 여부


class Watchlist:
    """관심종목 저장/로드/관리"""

    def __init__(self, path: str = WATCHLIST_PATH):
        self.path = path
        self.updated: str = ""
        self.candidates: List[WatchItem] = []
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.updated = data.get("updated", "")
            for item in data.get("candidates", []):
                self.candidates.append(WatchItem(**item))
            logger.info("[관심종목] %d종목 로드 (갱신일: %s)", len(self.candidates), self.updated)
        except Exception as e:
            logger.warning("[관심종목] 로드 실패: %s", e)

    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        data = {
            "updated": self.updated,
            "candidates": [asdict(c) for c in self.candidates],
        }
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info("[관심종목] %d종목 저장 완료", len(self.candidates))
        except Exception as e:
            logger.warning("[관심종목] 저장 실패: %s", e)

    def update_candidates(self, items: List[WatchItem], date: str):
        """당일 스캔 결과로 관심종목 갱신.
        기존 후보 중 아직 유효한 것은 유지, 새 후보 추가."""
        # 기존 중 3일 넘은 항목 만료
        existing = {}
        for c in self.candidates:
            if c.added_date >= _date_minus(date, 3) and not c.expired:
                existing[c.code] = c

        # 새 후보 추가 (기존에 없는 것만)
        added = 0
        for item in items:
            if item.code not in existing:
                item.added_date = date
                existing[item.code] = item
                added += 1

        self.candidates = list(existing.values())
        self.updated = date
        self.save()
        logger.info("[관심종목] 갱신: 신규 %d, 유지 %d, 총 %d종목",
                    added, len(self.candidates) - added, len(self.candidates))

    def get_active(self, today: str) -> List[WatchItem]:
        """오늘 매수 가능한 관심종목 (등록일 != 오늘, 만료 안 됨)"""
        result = []
        for c in self.candidates:
            if c.expired:
                continue
            # 등록 당일은 매수 안 함 (내일부터)
            if c.added_date == today:
                continue
            # 3일 넘으면 만료
            if c.added_date < _date_minus(today, 3):
                c.expired = True
                continue
            result.append(c)
        return result

    def mark_expired(self, code: str):
        for c in self.candidates:
            if c.code == code:
                c.expired = True
        self.save()


def _date_minus(date_str: str, days: int) -> str:
    """날짜 문자열에서 N일 빼기"""
    import datetime
    try:
        d = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        return (d - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    except Exception:
        return date_str
