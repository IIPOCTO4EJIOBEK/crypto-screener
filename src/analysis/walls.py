"""Стенки стакана по кумулятивным полосам архива Binance (bookDepth).

Живой путь (`density.py`) видит отдельные цены-уровни: там плотность — это
заявка по конкретной цене. Архив этого не отдаёт. bookDepth — 12
КУМУЛЯТИВНЫХ полос от середины (±0.2, ±1, ±2, ±3, ±4, ±5 %), то есть
ценовое разрешение архива — не уровень, а КОРИДОР: 0.2 % у самой середины
и целый процент дальше. Поэтому стенка на архиве — не заявка по цене, а
коридор, в котором стоит необычно много объёма.

Объём коридора получается разностью соседних кумулятивных полос:
depth(дальняя граница) − depth(ближняя). Готовое разложение уже есть в
`liquidity.marginal_slices` — здесь оно переиспользуется, а не пишется
заново, чтобы определение «объём полосы» в проекте было одно.

Порог крупности задан ПРОЦЕНТИЛЕМ ВНУТРИ СНИМКА, а не кратностью
медианы. Причина измерена: отношение «самая крупная полоса / медиана»
сильно зависит от монеты — у BTCUSDT медиана 2.33, у SOLUSDT 1.82
(2026-09-10…12, 8640 снимков на монету). Фиксированная кратность (скажем,
2.5) на BTC отбирала бы ~0.4 стенки на снимок, а на SOL — 0.01, то есть у
мелкой монеты стенок не было бы вообще. Процентиль внутри снимка
нормирует на сам снимок и потому равно применим к любой монете.

Отсечки по расстоянию. Верх стакана (коридор 0…0.2 %) крупный ВСЕГДА: у
лучшей цены скапливается весь поток, и как уровень отскока он бесполезен —
цена и так стоит на нём. Поэтому коридоры ближе MIN_DISTANCE_PCT не
рассматриваются. MAX_DISTANCE_PCT — самая широкая полоса архива (±5 %),
дальше цене за горизонт наблюдения просто не дойти.

Модуль не ходит в сеть: на вход идёт готовый `BookDepthSnapshot`, на выход
— стенки в процентах от середины. Середины в архиве нет, её надо взять из
свечей; абсолютная цена стенки считается от переданной середины.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median

from src.analysis.liquidity import Slice, marginal_slices
from src.data.archive import BookDepthSnapshot

# Стенка — полоса в верхних 20 % своего снимка. При 10 рассматриваемых
# коридорах (5 на сторону) это примерно две самые крупные полосы: p80 с
# интерполяцией садится между восьмой и девятой из десяти. Порог задан
# процентилем, а не кратностью, по причине из docstring.
WALL_PERCENTILE = 80.0

MIN_DISTANCE_PCT = 0.2   # ближе — верх стакана, не уровень отскока
MAX_DISTANCE_PCT = 5.0   # дальше самой широкой полосы архива цели нет
MIN_BANDS = 6            # меньше коридоров — снимок слишком беден для ранга


@dataclass(frozen=True)
class Wall:
    """Крупная полоса снимка: коридор и объём, который в нём стоит.

    lo_pct/hi_pct — границы коридора в процентах от середины. Сторона
    определяется знаком центра: центр > 0 — это аск (над серединой),
    центр < 0 — бид.
    """
    side: str            # "bid" | "ask"
    lo_pct: float
    hi_pct: float
    notional: float      # объём коридора в котируемой (USDT)
    depth: float         # объём коридора в базовой монете
    ratio_to_median: float   # во сколько раз крупнее медианы того же снимка

    @property
    def center_pct(self) -> float:
        return (self.lo_pct + self.hi_pct) / 2

    @property
    def distance_pct(self) -> float:
        """Расстояние центра коридора от середины, по модулю."""
        return abs(self.center_pct)

    @property
    def near_pct(self) -> float:
        """Граница коридора, ближняя к середине (со знаком)."""
        return self.lo_pct if abs(self.lo_pct) < abs(self.hi_pct) else self.hi_pct

    @property
    def far_pct(self) -> float:
        """Граница коридора, дальняя от середины (со знаком)."""
        return self.hi_pct if self.near_pct == self.lo_pct else self.lo_pct

    @property
    def width_pct(self) -> float:
        return abs(self.hi_pct - self.lo_pct)

    def price_near(self, mid: float) -> float:
        """Цена ближней границы коридора: докуда цене идти до стенки."""
        return mid * (1 + self.near_pct / 100)

    def price_far(self, mid: float) -> float:
        """Цена дальней границы: здесь стенка пройдена целиком."""
        return mid * (1 + self.far_pct / 100)

    def price_center(self, mid: float) -> float:
        return mid * (1 + self.center_pct / 100)


def _percentile(values: list[float], p: float) -> float:
    """Процентиль с линейной интерполяцией (как numpy по умолчанию)."""
    xs = sorted(values)
    if not xs:
        return 0.0
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * p / 100
    lo = int(math.floor(k))
    hi = int(math.ceil(k))
    if lo == hi:
        return xs[lo]
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def candidate_slices(snap: BookDepthSnapshot,
                     min_distance_pct: float = MIN_DISTANCE_PCT,
                     max_distance_pct: float = MAX_DISTANCE_PCT) -> list[Slice]:
    """Коридоры снимка, пригодные для отбора стенки.

    Отбрасывается верх стакана (ближе min_distance_pct) и всё, что дальше
    max_distance_pct. Для 12-полосной эпохи это 10 коридоров (5 на
    сторону) — по одному на каждую границу кроме самой мелкой.
    """
    return [s for s in marginal_slices(snap)
            if min_distance_pct < s.distance_pct <= max_distance_pct]


def find_walls(snap: BookDepthSnapshot, percentile: float = WALL_PERCENTILE,
               min_distance_pct: float = MIN_DISTANCE_PCT,
               max_distance_pct: float = MAX_DISTANCE_PCT) -> list[Wall]:
    """Стенки снимка: коридоры в верхних (100 − percentile) % снимка.

    Крупные — первыми. Пусто, если коридоров меньше MIN_BANDS (снимок
    слишком беден, ранг по нему ничего не значит) или медиана нулевая.
    """
    cand = candidate_slices(snap, min_distance_pct, max_distance_pct)
    if len(cand) < MIN_BANDS:
        return []
    vals = [s.notional for s in cand]
    med = median(vals)
    if med <= 0:
        return []
    thr = _percentile(vals, percentile)
    out = [Wall(s.side, s.lo_pct, s.hi_pct, s.notional, s.depth,
                s.notional / med)
           for s in cand if s.notional >= thr]
    out.sort(key=lambda w: -w.notional)
    return out


def walls_of_side(snap: BookDepthSnapshot, side: str, **kwargs) -> list[Wall]:
    return [w for w in find_walls(snap, **kwargs) if w.side == side]


def nearest_wall(snap: BookDepthSnapshot, side: str,
                 **kwargs) -> Wall | None:
    """Первое препятствие на стороне: стенка с наименьшим расстоянием.

    Для проверки «стенка как барьер» нужна именно ближняя крупная полоса,
    а не самая крупная вообще: цена встретит сначала её.
    """
    ws = walls_of_side(snap, side, **kwargs)
    if not ws:
        return None
    return min(ws, key=lambda w: w.distance_pct)


def largest_wall(snap: BookDepthSnapshot, **kwargs) -> Wall | None:
    ws = find_walls(snap, **kwargs)
    return ws[0] if ws else None


if __name__ == "__main__":
    from datetime import date

    from src.data.archive import load_book_depth
    from src.data.market import ohlcv

    day = date(2026, 9, 25)
    snaps = load_book_depth("BTCUSDT", day, day, workers=1).rows
    if not snaps:
        raise SystemExit("снимков нет")
    snap = snaps[len(snaps) // 2]
    cs = ohlcv("binance", "BTCUSDT", "1m", 60)
    mid = cs[-1].close
    print(f"снимков {len(snaps)}, пример ts={snap.ts}, середина≈{mid:.2f}")
    print("коридоры (side, границы, notional, ×медианы):")
    cand = candidate_slices(snap)
    med = median([s.notional for s in cand])
    walls = find_walls(snap)
    wall_centers = {round(w.center_pct, 6) for w in walls}
    for s in cand:
        mark = " <== стенка" if round(s.center_pct, 6) in wall_centers else ""
        print(f"  {s.side:3} {s.lo_pct:>5}..{s.hi_pct:>5}%  "
              f"${s.notional:>15,.0f}  ×{s.notional / med:4.2f}{mark}")
    for w in walls:
        print(f"стенка {w.side} {w.near_pct:+.2f}..{w.far_pct:+.2f}%  "
              f"×{w.ratio_to_median:.2f}  "
              f"цены {w.price_near(mid):.2f} … {w.price_far(mid):.2f}")
