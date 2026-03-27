"""관심종목(Watchlist) 관리 v2

스캐너가 발굴한 종목을 등급별로 저장하고, 눌림목 매수 후보로 사용.

v2 변경사항:
  - 등급제: A(핵심)/B(유망)/C(관찰) — 점수+수급 기반 자동 분류
  - 등급별 만료: A=5거래일, B=3거래일, C=2거래일
  - 상태 추적: waiting→approaching→reached→bought/expired
  - 하락장 방어 스캔: 낙폭과대 종목도 C등급으로 저장
  - 기존 종목 재발굴 시 등급 승격 가능

구조:
  {
    "updated": "2026-03-27",
    "candidates": [
      {
        "code": "005930", "name": "삼성전자",
        "close": 72000, "score": 135,
        "grade": "A", "status": "waiting",
        "reasons": ["신고가돌파", "정배열", "수급+35"],
        ...
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

# 등급별 만료 기간 (거래일 기준이지만 달력일로 근사)
GRADE_EXPIRY = {"A": 7, "B": 5, "C": 3}  # 달력일 (주말 포함하면 5거래일≈7일)

# 등급 기준 점수
GRADE_A_THRESHOLD = 100  # 100점 이상 = A등급
GRADE_B_THRESHOLD = 60   # 60점 이상 = B등급


def assign_grade(score: float, foreign_flow: int = 0, both_buying: bool = False) -> str:
    """점수 + 수급으로 등급 결정"""
    # 수급 보너스: 외인+기관 동반매수면 1단계 승격
    upgrade = both_buying or foreign_flow >= 3
    if score >= GRADE_A_THRESHOLD:
        return "A"
    if score >= GRADE_B_THRESHOLD:
        return "A" if upgrade else "B"
    return "B" if upgrade else "C"


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
    grade: str = "C"                 # 등급: A(핵심)/B(유망)/C(관찰)
    status: str = "waiting"          # 상태: waiting/approaching/reached/bought/expired
    scan_type: str = "normal"        # 스캔 유형: normal/defensive/recovery

    @property
    def expiry_days(self) -> int:
        return GRADE_EXPIRY.get(self.grade, 3)

    @property
    def grade_label(self) -> str:
        labels = {"A": "A(핵심)", "B": "B(유망)", "C": "C(관찰)"}
        return labels.get(self.grade, self.grade)

    @property
    def status_label(self) -> str:
        labels = {
            "waiting": "대기",
            "approaching": "접근중",
            "reached": "목표도달",
            "bought": "매수완료",
            "expired": "만료",
        }
        return labels.get(self.status, self.status)

    def update_status(self, current_price: int):
        """현재가 기반 상태 업데이트"""
        if self.status in ("bought", "expired"):
            return
        if self.pullback_target <= 0:
            return

        distance_pct = (current_price - self.pullback_target) / self.pullback_target * 100

        if current_price <= self.pullback_target:
            self.status = "reached"
        elif distance_pct <= 1.5:  # 목표가 대비 1.5% 이내
            self.status = "approaching"
        else:
            self.status = "waiting"


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
                # 기존 데이터 호환: 새 필드 없으면 기본값
                self.candidates.append(WatchItem(**{
                    k: v for k, v in item.items()
                    if k in WatchItem.__dataclass_fields__
                }))
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
        except Exception as e:
            logger.warning("[관심종목] 저장 실패: %s", e)

    def update_candidates(self, items: List[WatchItem], date: str):
        """스캔 결과로 관심종목 갱신.
        기존 후보 중 유효한 것 유지, 새 후보 추가, 기존 종목 재발굴 시 승격."""
        existing = {}
        for c in self.candidates:
            if not c.expired and c.status != "expired":
                # 등급별 만료 체크
                if c.added_date >= _date_minus(date, c.expiry_days):
                    existing[c.code] = c

        added, upgraded = 0, 0
        for item in items:
            item.added_date = item.added_date or date
            if item.code in existing:
                old = existing[item.code]
                # 재발굴: 점수가 더 높으면 등급 승격 + 만료일 갱신
                if item.score > old.score:
                    old_grade = old.grade
                    old.score = item.score
                    old.grade = item.grade
                    old.reasons = item.reasons
                    old.added_date = date  # 만료일 리셋
                    if old_grade > item.grade:  # A < B < C 문자열 비교
                        upgraded += 1
                        logger.info("[관심종목] %s 등급 승격: %s→%s (점수:%.0f)",
                                    old.name, old_grade, item.grade, item.score)
            else:
                existing[item.code] = item
                added += 1

        self.candidates = list(existing.values())
        self.updated = date
        self.save()

        grade_counts = {"A": 0, "B": 0, "C": 0}
        for c in self.candidates:
            grade_counts[c.grade] = grade_counts.get(c.grade, 0) + 1
        logger.info("[관심종목] 갱신: 신규 %d, 승격 %d, 총 %d종목 (A:%d B:%d C:%d)",
                    added, upgraded, len(self.candidates),
                    grade_counts["A"], grade_counts["B"], grade_counts["C"])

    def get_active(self, today: str) -> List[WatchItem]:
        """오늘 매수 가능한 관심종목 (등록일 != 오늘, 만료 안 됨, 등급순 정렬)"""
        result = []
        for c in self.candidates:
            if c.expired or c.status in ("bought", "expired"):
                continue
            if c.added_date == today:
                continue
            if c.added_date < _date_minus(today, c.expiry_days):
                c.expired = True
                c.status = "expired"
                continue
            result.append(c)
        # A등급 → B등급 → C등급 순, 같은 등급 내에서는 점수 높은 순
        result.sort(key=lambda x: (-"CBA".index(x.grade), -x.score))
        return result

    def mark_bought(self, code: str):
        for c in self.candidates:
            if c.code == code:
                c.status = "bought"
        self.save()

    def mark_expired(self, code: str):
        for c in self.candidates:
            if c.code == code:
                c.expired = True
                c.status = "expired"
        self.save()

    def get_summary(self) -> str:
        """현재 관심종목 요약 (로그/텔레그램용)"""
        active = [c for c in self.candidates if not c.expired and c.status != "expired"]
        if not active:
            return "관심종목 없음"
        parts = []
        for c in sorted(active, key=lambda x: (-"CBA".index(x.grade), -x.score)):
            parts.append("[%s] %s %.0f점 %s" % (c.grade, c.name, c.score, c.status_label))
        return "관심종목 %d개: %s" % (len(active), " / ".join(parts))


def _date_minus(date_str: str, days: int) -> str:
    """날짜 문자열에서 N일 빼기"""
    import datetime
    try:
        d = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        return (d - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    except Exception:
        return date_str
