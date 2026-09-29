"""Стенд: предсказывает ли стенка в стакане что-нибудь — на архиве Binance.

Живой путь видит заявки по ценам, архив — только 12 кумулятивных полос
(см. `src/analysis/walls.py`). Стенд закрывает этот пробел и честно
измеряет, есть ли у крупной полосы предсказательная сила. Возможен
отрицательный ответ, и он здесь полноценный результат.

Данные: bookDepth (снимок ~30 с) и свечи 1m, монеты BTCUSDT, ETHUSDT,
SOLUSDT, DOGEUSDT, окно 2026-08-12…2026-09-26 (45 суток, эпоха 12 полос).

Два вопроса, оба про одно и то же — «важна ли крупность полосы»:

  Магнетизм  доходит ли цена до ближней границы КРУПНОЙ полосы чаще, чем
             до зеркальной точки на том же расстоянии по другую сторону
             середины (там крупной полосы нет).
  Барьер     если цена дошла до стенки, проходит ли она её насквозь реже,
             чем зеркальную полосу той же ширины.

Дисциплина честности, без которой числа ничего не стоят:

1. Стенка определяется ТОЛЬКО по снимку на момент решения. Исход считается
   по свечам строго ПОСЛЕ этого момента. Вход — первая свеча, открывшаяся
   не раньше снимка; ни одна свеча признака не заходит в окно исхода.
2. Основная оценка — НЕПЕРЕСЕКАЮЩИЕСЯ окна: якоря стоят через D минут,
   и окно исхода каждого якоря целиком укладывается в D. Перекрывающиеся
   якоря (каждые 30 с) показаны отдельно и помечены как справка —
   наблюдения в них зависимы, уровни значимости по ним не считаются.
3. Зеркальная полоса — контроль, снимающий главное тривиальное
   объяснение: «до уровня на расстоянии d цена доходит за горизонт с
   вероятностью p(d)». Она стоит на том же расстоянии и той же ширины,
   только по другую сторону середины и без крупного объёма.
4. Контроль сдвигом — ЧЕТЫРЕ фазы (3, 7, 14, 21 суток назад): стенка
   берётся из устаревшего снимка, а исход считается по текущей цене.
   Один сдвиг — это выбор одной фазы; среднее по четырём честнее.
5. Порог крупности — процентиль внутри снимка (см. walls.py), а не
   подгонка под результат.

Запуск: .venv/bin/python -m tools.walls_study [СИМВОЛ ...]
"""

from __future__ import annotations

import math
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from statistics import mean, median

import numpy as np

from src.analysis.walls import (MAX_DISTANCE_PCT, MIN_DISTANCE_PCT,
                                WALL_PERCENTILE, Wall, find_walls, nearest_wall)
from src.data import archive

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT")
END = date(2026, 9, 26)
START = END - timedelta(days=45)          # 2026-08-12 … 2026-09-26
BD_START = START - timedelta(days=25)     # запас под контроль сдвигом
HORIZONS_MIN = (60, 240)                  # минуты: основной и запасной
SHIFT_DAYS = (3, 7, 14, 21)               # фазы контроля сдвигом
MIN_OBS = 60                              # меньше — вывод не делаем
REF_ANCHOR_MIN = 30                       # справка: перекрывающиеся окна
COMPOSITION_STRIDE = 20                   # прореживание описательного раздела

DAY_MS = 86_400_000


def _day_start_ms(d: date) -> int:
    """Полночь UTC суток d в миллисекундах эпохи."""
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


# --------------------------------------------------------------------------
# Статистика
# --------------------------------------------------------------------------
def wilson(p: float, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (center - half, center + half)


def mcnemar(n_10: int, n_01: int) -> tuple[float, float]:
    """Парный тест Мак-Немара по несогласованным парам (10, 01)."""
    b, c = n_10, n_01
    if b + c == 0:
        return (float("nan"), float("nan"))
    chi2 = (b - c) ** 2 / (b + c)
    return chi2, math.erfc(math.sqrt(chi2 / 2))


def two_prop_z(p1: float, n1: int, p2: float, n2: int) -> tuple[float, float]:
    if n1 == 0 or n2 == 0:
        return (float("nan"), float("nan"))
    p = (p1 * n1 + p2 * n2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return (float("nan"), float("nan"))
    z = (p1 - p2) / se
    return z, math.erfc(abs(z) / math.sqrt(2))


# --------------------------------------------------------------------------
# Цена и снимки
# --------------------------------------------------------------------------
class Px:
    """1m-свечи в массивах: цена в момент и достижение уровня за горизонт."""

    def __init__(self, candles):
        self.ts = np.array([c.ts for c in candles], dtype=np.int64)
        self.high = np.array([c.high for c in candles], dtype=np.float64)
        self.low = np.array([c.low for c in candles], dtype=np.float64)
        self.close = np.array([c.close for c in candles], dtype=np.float64)

    def price_at(self, ts: int) -> float | None:
        i = int(np.searchsorted(self.ts, ts, side="right")) - 1
        if i < 0:
            return None
        return float(self.close[i])

    def reached(self, ts: int, target: float, horizon_ms: int,
                side: str) -> bool:
        """Дошла ли цена до target внутри (ts, ts+horizon_ms].

        j0 — первая свеча, открывшаяся НЕ РАНЬШЕ снимка: свеча, содержащая
        момент решения, в исход не берётся, иначе признак подглядывал бы.
        """
        j0 = int(np.searchsorted(self.ts, ts, side="right"))
        j1 = int(np.searchsorted(self.ts, ts + horizon_ms, side="right"))
        if j0 >= j1:
            return False
        if side == "ask":
            return bool(self.high[j0:j1].max() >= target)
        return bool(self.low[j0:j1].min() <= target)


class SnapIndex:
    """Снимки стакана с поиском «последний не позже ts»."""

    def __init__(self, snaps):
        self.snaps = snaps
        self.ts = np.array([s.ts for s in snaps], dtype=np.int64)

    def at_or_before(self, ts: int):
        i = int(np.searchsorted(self.ts, ts, side="right")) - 1
        if i < 0:
            return None
        return self.snaps[i]


@dataclass
class Counts:
    """Счётчики одной группы событий (стенки или их зеркала)."""
    events: int = 0          # сколько якорей дали стенку на этой стороне
    touch: int = 0           # цена дошла до ближней границы
    far: int = 0             # цена дошла до дальней границы
    ask: int = 0             # из events: сколько на аск-стороне (баланс)
    touch_up: int = 0        # цели ВЫШЕ середины: дошло
    far_up: int = 0          # цели выше середины: прошло насквозь

    @property
    def touch_rate(self) -> float:
        return self.touch / self.events if self.events else 0.0

    @property
    def far_given_touch(self) -> float:
        return self.far / self.touch if self.touch else 0.0

    @property
    def far_given_touch_up(self) -> float:
        return self.far_up / self.touch_up if self.touch_up else 0.0

    @property
    def far_given_touch_down(self) -> float:
        d = self.touch - self.touch_up
        return (self.far - self.far_up) / d if d else 0.0


@dataclass
class Result:
    """Итог одного среза: стенки против зеркал, плюс парные счётчики."""
    horizon_min: int
    wall: Counts
    mirror: Counts
    # парные счётчики по якорям, где стенка вообще нашлась
    wall_touch_only: int = 0     # стенка задета, зеркало нет
    mirror_touch_only: int = 0
    both_touch: int = 0
    neither_touch: int = 0
    # парные счётчики по якорям, где задеты ОБА (честный тест барьера)
    wall_far_only: int = 0
    mirror_far_only: int = 0
    both_far: int = 0
    neither_far: int = 0
    ratios: list = None
    dists: list = None

    def __post_init__(self):
        if self.ratios is None:
            self.ratios = []
        if self.dists is None:
            self.dists = []


def run_slice(px: Px, idx: SnapIndex, horizon_min: int,
              shift_ms: int = 0) -> Result:
    """Один срез: якоря через horizon_min, стенка из снимка (со сдвигом).

    Момент решения — текущий якорь: середина и окно исхода берутся из
    него. shift_ms > 0 подменяет только СТЕНКУ — она берётся из снимка на
    shift_ms раньше. Так контроль проверяет именно то, что нужно: несёт ли
    стенка, увиденная N суток назад, информацию о сегодняшнем движении.
    Считать исход от устаревшего снимка нельзя — это была бы та же самая
    выборка, сдвинутая по времени, и она ничего не контролирует.
    """
    h_ms = horizon_min * 60_000
    res = Result(horizon_min, Counts(), Counts())
    grid = range(int(px.ts[0]) + h_ms, int(px.ts[-1]) - h_ms, h_ms)
    for anchor in grid:
        cur = idx.at_or_before(anchor)                # момент решения
        wsnap = idx.at_or_before(anchor - shift_ms)   # откуда берётся стенка
        if cur is None or wsnap is None:
            continue
        mid = px.price_at(cur.ts)
        if not mid or mid <= 0:
            continue
        for side in ("ask", "bid"):
            w = nearest_wall(wsnap, side)
            if w is None:
                continue
            res.wall.events += 1
            res.wall.ask += 1 if side == "ask" else 0
            res.ratios.append(w.ratio_to_median)
            res.dists.append(w.distance_pct)
            near = mid * (1 + w.near_pct / 100)
            far = mid * (1 + w.far_pct / 100)
            # зеркало: отражение через середину — то же расстояние, та же
            # ширина, но полоса не крупная
            mir_side = "bid" if w.near_pct > 0 else "ask"
            mir_near = mid * (1 - w.near_pct / 100)
            mir_far = mid * (1 - w.far_pct / 100)

            wt = px.reached(cur.ts, near, h_ms, side)
            wf = px.reached(cur.ts, far, h_ms, side) if wt else False
            mt = px.reached(cur.ts, mir_near, h_ms, mir_side)
            mf = px.reached(cur.ts, mir_far, h_ms, mir_side) if mt else False

            res.wall.touch += wt
            res.wall.far += wf
            if w.near_pct > 0:      # цель стенки выше середины
                res.wall.touch_up += wt
                res.wall.far_up += wf
            res.mirror.events += 1
            res.mirror.touch += mt
            res.mirror.far += mf
            if w.near_pct < 0:      # цель зеркала выше середины
                res.mirror.touch_up += mt
                res.mirror.far_up += mf

            if wt and not mt:
                res.wall_touch_only += 1
            elif mt and not wt:
                res.mirror_touch_only += 1
            elif wt and mt:
                res.both_touch += 1
            else:
                res.neither_touch += 1
            if wt and mt:
                if wf and not mf:
                    res.wall_far_only += 1
                elif mf and not wf:
                    res.mirror_far_only += 1
                elif wf and mf:
                    res.both_far += 1
                else:
                    res.neither_far += 1
    return res


def fmt_counts(label: str, c: Counts) -> str:
    lo, hi = wilson(c.touch_rate, c.events)
    return (f"  {label:9} n={c.events:>5}  дошло до границы {c.touch_rate*100:5.1f}% "
            f"[{lo*100:.1f},{hi*100:.1f}]  насквозь|дошло "
            f"{c.far_given_touch*100:5.1f}% (n={c.touch})")


def magnet_line(res: Result, prefix: str = "") -> str:
    """Магнетизм: доходит ли до стенки чаще, чем до зеркала (парно)."""
    chi2, p = mcnemar(res.wall_touch_only, res.mirror_touch_only)
    return (f"  {prefix}магнетизм: стенка-только={res.wall_touch_only} "
            f"зеркало-только={res.mirror_touch_only} "
            f"оба={res.both_touch} ни одного={res.neither_touch}  "
            f"p={p:.3g}")


def barrier_line(res: Result, prefix: str = "") -> str:
    """Барьер: среди якорей, где задеты ОБА, кто чаще прошёл насквозь."""
    chi2, p = mcnemar(res.wall_far_only, res.mirror_far_only)
    z2, p2 = two_prop_z(res.wall.far_given_touch, res.wall.touch,
                        res.mirror.far_given_touch, res.mirror.touch)
    return (f"  {prefix}барьер (парно, задеты оба): стенка-только={res.wall_far_only} "
            f"зеркало-только={res.mirror_far_only} оба={res.both_far} "
            f"ни одного={res.neither_far}  p={p:.3g} | "
            f"непарно z={z2:+.2f} p={p2:.3g}")


# --------------------------------------------------------------------------
# Загрузка (с кешем: разбор bookDepth — самая дорогая часть прогона)
# --------------------------------------------------------------------------
_BD_CACHE: dict[str, object] = {}
_KL_CACHE: dict[str, object] = {}
_IDX_CACHE: dict[str, tuple[Px, "SnapIndex"]] = {}


def book_depth(sym: str):
    if sym not in _BD_CACHE:
        _BD_CACHE[sym] = archive.load_book_depth(sym, BD_START, END)
    return _BD_CACHE[sym]


def klines(sym: str):
    if sym not in _KL_CACHE:
        _KL_CACHE[sym] = archive.load_klines(sym, "1m", START, END)
    return _KL_CACHE[sym]


def px_idx(sym: str) -> tuple[Px, "SnapIndex"]:
    if sym not in _IDX_CACHE:
        _IDX_CACHE[sym] = (Px(klines(sym).rows), SnapIndex(book_depth(sym).rows))
    return _IDX_CACHE[sym]


# --------------------------------------------------------------------------
# Секции
# --------------------------------------------------------------------------
def section_composition(symbols) -> None:
    print("\n" + "=" * 74)
    print("A. Состав: что вообще отбирает детектор")
    print("=" * 74)
    print(f"  порог крупности: {WALL_PERCENTILE:g}-й процентиль внутри снимка; "
          f"расстояние {MIN_DISTANCE_PCT}…{MAX_DISTANCE_PCT}%")
    print("  «×медианы» — во сколько раз полоса крупнее медианы своего снимка")
    print(f"  (описательный раздел — каждый {COMPOSITION_STRIDE}-й снимок: "
          f"это распределение, а не проверка гипотезы)")
    lo_ms = _day_start_ms(START)
    for sym in symbols:
        bd = book_depth(sym)
        rx, dist = [], []
        no_wall = total = 0
        for s in bd.rows[::COMPOSITION_STRIDE]:
            if s.ts < lo_ms:
                continue
            total += 1
            ws = find_walls(s)
            if not ws:
                no_wall += 1
                continue
            rx.append(max(w.ratio_to_median for w in ws))
            dist.append(min(w.distance_pct for w in ws))
        if not rx:
            print(f"  {sym}: стенок нет вовсе")
            continue
        print(f"  {sym:9} снимков={total:>6}  без стенки "
              f"{no_wall/total*100:4.1f}%  ×медианы мед={median(rx):.2f} "
              f"p90={np.percentile(rx, 90):.2f}  ближняя стенка мед={median(dist):.2f}%")


def add_result(acc: Result, r: Result) -> None:
    """Сложить срезы нескольких монет в один пул (счётчики аддитивны)."""
    for src, dst in ((r.wall, acc.wall), (r.mirror, acc.mirror)):
        dst.events += src.events
        dst.touch += src.touch
        dst.far += src.far
        dst.ask += src.ask
        dst.touch_up += src.touch_up
        dst.far_up += src.far_up
    acc.wall_touch_only += r.wall_touch_only
    acc.mirror_touch_only += r.mirror_touch_only
    acc.both_touch += r.both_touch
    acc.neither_touch += r.neither_touch
    acc.wall_far_only += r.wall_far_only
    acc.mirror_far_only += r.mirror_far_only
    acc.both_far += r.both_far
    acc.neither_far += r.neither_far
    acc.ratios.extend(r.ratios)
    acc.dists.extend(r.dists)


def section_primary(symbols) -> dict:
    print("\n" + "=" * 74)
    print("B. Основные оценки (непересекающиеся окна)")
    print("=" * 74)
    out = {}
    pooled = {h: Result(h, Counts(), Counts()) for h in HORIZONS_MIN}
    for sym in symbols:
        bd, kl = book_depth(sym), klines(sym)
        px, idx = px_idx(sym)
        print(f"\n--- {sym}: снимков {len(bd.rows)}, свечей {len(kl.rows)} ---")
        for h in HORIZONS_MIN:
            res = run_slice(px, idx, h)
            print(f"  горизонт {h} мин (якоря через {h} мин, окна не пересекаются)")
            print(fmt_counts("стенки", res.wall))
            print(fmt_counts("зеркала", res.mirror))
            print(f"  баланс сторон: аск {res.wall.ask} / бид "
                  f"{res.wall.events - res.wall.ask}   "
                  f"расстояние мед {median(res.dists):.2f}%")
            print(magnet_line(res))
            print(barrier_line(res))
            out[(sym, h)] = res
            add_result(pooled[h], res)
        # справка: перекрывающиеся окна
        h = HORIZONS_MIN[0]
        res_ref = run_slice_overlap(px, idx, h)
        print(f"  справка (перекрытие, якоря через {REF_ANCHOR_MIN} мин — "
              f"наблюдения зависимы, p не считается):")
        print(f"    стенки: дошло {res_ref.wall.touch_rate*100:.1f}% "
              f"насквозь|дошло {res_ref.wall.far_given_touch*100:.1f}% | "
              f"зеркала: дошло {res_ref.mirror.touch_rate*100:.1f}% "
              f"насквозь|дошло {res_ref.mirror.far_given_touch*100:.1f}%")
    if len(symbols) > 1:
        print(f"\n=== ПУЛ по {len(symbols)} монетам ===")
        for h in HORIZONS_MIN:
            r = pooled[h]
            print(f"  горизонт {h} мин (якоря через {h} мин, окна не пересекаются)")
            print(fmt_counts("стенки", r.wall))
            print(fmt_counts("зеркала", r.mirror))
            print(magnet_line(r))
            print(barrier_line(r))
            print(direction_line(r))
    return out


def direction_line(r: Result) -> str:
    """Насквозь|дошло отдельно для целей выше и ниже середины.

    Если эти два числа сильно расходятся, зеркальное сравнение несёт в
    себе тренд рынка, и разницу стенка/зеркало надо читать с оговоркой.
    """
    wu, wd = r.wall.far_given_touch_up, r.wall.far_given_touch_down
    mu, md = r.mirror.far_given_touch_up, r.mirror.far_given_touch_down
    return (f"  направление: стенки вверх {wu*100:5.1f}% (n={r.wall.touch_up}) "
            f"вниз {wd*100:5.1f}% (n={r.wall.touch - r.wall.touch_up}) | "
            f"зеркала вверх {mu*100:5.1f}% (n={r.mirror.touch_up}) "
            f"вниз {md*100:5.1f}% "
            f"(n={r.mirror.touch - r.mirror.touch_up})")


def run_slice_overlap(px: Px, idx: SnapIndex, horizon_min: int) -> Result:
    """Тот же расчёт, но якоря каждые REF_ANCHOR_MIN минут (для справки)."""
    step = REF_ANCHOR_MIN * 60_000
    h_ms = horizon_min * 60_000
    res = Result(horizon_min, Counts(), Counts())
    for anchor in range(int(px.ts[0]) + h_ms, int(px.ts[-1]) - h_ms, step):
        snap = idx.at_or_before(anchor)
        if snap is None:
            continue
        mid = px.price_at(snap.ts)
        if not mid or mid <= 0:
            continue
        for side in ("ask", "bid"):
            w = nearest_wall(snap, side)
            if w is None:
                continue
            res.wall.events += 1
            near = mid * (1 + w.near_pct / 100)
            far = mid * (1 + w.far_pct / 100)
            mir_side = "bid" if w.near_pct > 0 else "ask"
            mir_near = mid * (1 - w.near_pct / 100)
            mir_far = mid * (1 - w.far_pct / 100)
            wt = px.reached(snap.ts, near, h_ms, side)
            mt = px.reached(snap.ts, mir_near, h_ms, mir_side)
            res.wall.touch += wt
            res.wall.far += (px.reached(snap.ts, far, h_ms, side) if wt else False)
            res.mirror.events += 1
            res.mirror.touch += mt
            res.mirror.far += (px.reached(snap.ts, mir_far, h_ms, mir_side) if mt else False)
    return res


def section_control(symbols) -> None:
    print("\n" + "=" * 74)
    print("C. Контроль сдвигом: стенка из устаревшего снимка (4 фазы)")
    print("=" * 74)
    print("  Подменяется ТОЛЬКО стенка: она берётся из снимка на N суток")
    print("  раньше, а середина и окно исхода — текущие. Если эффект держится")
    print("  и на устаревшей стенке, он не про объём в этой полосе.")
    pooled = {h: {d: Result(h, Counts(), Counts()) for d in SHIFT_DAYS}
              for h in HORIZONS_MIN}
    for sym in symbols:
        px, idx = px_idx(sym)
        for h in HORIZONS_MIN:
            print(f"\n--- {sym}, горизонт {h} мин ---")
            acc_mag, acc_bar = [], []
            for d in SHIFT_DAYS:
                res = run_slice(px, idx, h, shift_ms=d * DAY_MS)
                add_result(pooled[h][d], res)
                _, pm = mcnemar(res.wall_touch_only, res.mirror_touch_only)
                _, pb = mcnemar(res.wall_far_only, res.mirror_far_only)
                acc_mag.append(pm)
                acc_bar.append(pb)
                print(f"  −{d:>2} дн: стенка-дошло {res.wall.touch_rate*100:5.1f}% "
                      f"зеркало-дошло {res.mirror.touch_rate*100:5.1f}% "
                      f"p={pm:.3g} | насквозь стенка "
                      f"{res.wall.far_given_touch*100:5.1f}% зеркало "
                      f"{res.mirror.far_given_touch*100:5.1f}% p={pb:.3g}")
            print(f"  среднее p по 4 фазам: магнетизм {mean(acc_mag):.3g}, "
                  f"барьер {mean(acc_bar):.3g}")
    if len(symbols) > 1:
        print(f"\n=== ПУЛ контроля по {len(symbols)} монетам ===")
        for h in HORIZONS_MIN:
            print(f"  горизонт {h} мин")
            for d in SHIFT_DAYS:
                r = pooled[h][d]
                print(f"  −{d:>2} дн: стенка-дошло {r.wall.touch_rate*100:5.1f}% "
                      f"зеркало-дошло {r.mirror.touch_rate*100:5.1f}% | "
                      f"насквозь стенка {r.wall.far_given_touch*100:5.1f}% "
                      f"зеркало {r.mirror.far_given_touch*100:5.1f}%")
                print(f"    {magnet_line(r).strip()}")
                print(f"    {barrier_line(r).strip()}")


def section_strength(symbols) -> None:
    print("\n" + "=" * 74)
    print("D. Сила стенки: слабая против сильной (по ×медианы)")
    print("=" * 74)
    print("  Если крупность важна, сильная стенка должна вести себя иначе.")
    for sym in symbols:
        px, idx = px_idx(sym)
        for h in HORIZONS_MIN:
            strong = Counts()
            weak = Counts()
            h_ms = h * 60_000
            rows = []
            for anchor in range(int(px.ts[0]) + h_ms, int(px.ts[-1]) - h_ms, h_ms):
                snap = idx.at_or_before(anchor)
                if snap is None:
                    continue
                mid = px.price_at(snap.ts)
                if not mid or mid <= 0:
                    continue
                for side in ("ask", "bid"):
                    w = nearest_wall(snap, side)
                    if w is None:
                        continue
                    rows.append((w, side, mid, snap.ts))
            if len(rows) < MIN_OBS:
                print(f"  {sym} {h} мин: событий {len(rows)} — тест не состоялся")
                continue
            med_ratio = median(r.ratio_to_median for r, _, _, _ in rows)
            strong_d, weak_d = [], []
            for w, side, mid, ts0 in rows:
                c = strong if w.ratio_to_median >= med_ratio else weak
                (strong_d if c is strong else weak_d).append(w.distance_pct)
                c.events += 1
                near = mid * (1 + w.near_pct / 100)
                far = mid * (1 + w.far_pct / 100)
                if px.reached(ts0, near, h_ms, side):
                    c.touch += 1
                    if px.reached(ts0, far, h_ms, side):
                        c.far += 1
            z, p = two_prop_z(strong.far_given_touch, strong.touch,
                              weak.far_given_touch, weak.touch)
            uf_s = strong.far / strong.events * 100
            uf_w = weak.far / weak.events * 100
            print(f"  {sym:9} {h:>3} мин (медиана ×{med_ratio:.2f}): "
                  f"сильная n={strong.events} дошло {strong.touch_rate*100:5.1f}% "
                  f"насквозь|дошло {strong.far_given_touch*100:5.1f}% "
                  f"(безусловно {uf_s:.2f}%) расст.мед {median(strong_d):.2f}% | "
                  f"слабая n={weak.events} дошло {weak.touch_rate*100:5.1f}% "
                  f"насквозь|дошло {weak.far_given_touch*100:5.1f}% "
                  f"(безусловно {uf_w:.2f}%) расст.мед {median(weak_d):.2f}% | "
                  f"z={z:+.2f} p={p:.3g}")


# --------------------------------------------------------------------------
def main() -> None:
    syms = tuple(sys.argv[1:]) or SYMBOLS
    t0 = time.time()
    print(f"Период {START} … {END} (45 суток), монеты {list(syms)}")
    print(f"Снимки bookDepth: {BD_START} … {END} (запас под контроль сдвигом)")
    section_composition(syms)
    print(f"\n[состав посчитан за {time.time()-t0:.0f} с]", flush=True)
    section_primary(syms)
    print(f"\n[основные оценки за {time.time()-t0:.0f} с]", flush=True)
    section_control(syms)
    print(f"\n[контроль за {time.time()-t0:.0f} с]", flush=True)
    section_strength(syms)
    print(f"\nвсего времени: {time.time()-t0:.0f} с")


if __name__ == "__main__":
    main()
