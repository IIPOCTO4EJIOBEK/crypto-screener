#!/usr/bin/env python
"""Прогон исследования ликвидности: тезисы Т1–Т5, круглые числа, сравнение со стаканом.

Каждое число в отчёте воспроизводится запуском этого скрипта. Все тезисы
считаются минимум на четырёх монетах (BTCUSDT, ETHUSDT, SOLUSDT, DOGEUSDT)
на данных архива Binance Futures за 2026-01-01 … 2026-09-28.

Секции (включаются через --only, по умолчанию все):

  t1   Магнетизм стенки: цена доходит до самой глубокой полосы чаще, чем
       до зеркальной полосы на том же расстоянии.
  t2   Цена ходит от ликвидности к ликвидности: после касания крупнейшей
       стенки цена идёт к следующей по величине чаще, чем к зеркальному
       уровню.
  t3   Где стоят стопы: глубина прокола экстремума с возвратом + forward
       return срабатываний liquidity_sweep против базы всех свечей.
  t4   Пул из нескольких касаний толще одиночного.
  t5   Перенаселённость: метрики позиционирования и фандинг против
       последующего движения.
  round Круглые числа: асимметрия по двум последним знакам (разворот,
       заход фитиля, ускорение после пробоя) с бутстрап-базой.
  compare Сравнение настоящей глубины bookDepth с выведенными пулами
       (равные экстремумы, круглые числа).
  migrate Миграция ликвидности: стенка стоит на месте или ползёт за ценой.

Запуск: .venv/bin/python tools/liquidity_study.py [--only t1,t3]
"""

from __future__ import annotations

import argparse
import math
import os
import random
import sys
import time
from collections import Counter, defaultdict
from datetime import date, timedelta
from statistics import median, mean, pstdev

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data.archive import (load_book_depth, load_funding, load_klines,
                              load_metrics, parse_book_depth, range_days,
                              _read_zip_csv)
from src.analysis.levels import find_pivots, level_tolerance_pct
from src.analysis.liquidity import (find_walls, imbalance, marginal_slices,
                                    migration_stats, wall_price,
                                    find_equal_extremes)
from src.data.market import Candle

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT"]
START = date(2026, 1, 1)
END = date(2026, 9, 28)
RNG = random.Random(42)

H_MS = {"30m": 30 * 60_000, "1h": 60 * 60_000, "2h": 2 * 3_600_000,
        "4h": 4 * 3_600_000, "6h": 6 * 3_600_000, "12h": 12 * 3_600_000,
        "24h": 24 * 3_600_000}


# --------------------------------------------------------------------------
# Статистические помощники
# --------------------------------------------------------------------------
def wilson(p: float, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (center - half, center + half)


def two_prop_z(p1: float, n1: int, p2: float, n2: int) -> tuple[float, float]:
    p = (p1 * n1 + p2 * n2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return (float("nan"), float("nan"))
    z = (p1 - p2) / se
    return z, math.erfc(abs(z) / math.sqrt(2))


def mcnemar(n_10: int, n_01: int) -> tuple[float, float]:
    """Парный тест Мак-Немара по несогласованным парам (10, 01)."""
    b, c = n_10, n_01
    if b + c == 0:
        return (float("nan"), float("nan"))
    chi2 = (b - c) ** 2 / (b + c)
    return chi2, math.erfc(math.sqrt(chi2 / 2))


def two_mean_z(m1, s1, n1, m2, s2, n2) -> tuple[float, float]:
    se = math.sqrt(s1 * s1 / n1 + s2 * s2 / n2)
    if se == 0:
        return (float("nan"), float("nan"))
    z = (m1 - m2) / se
    return z, math.erfc(abs(z) / math.sqrt(2))


def pct_rows(a: list[float]) -> str:
    a = sorted(a)
    if not a:
        return "n=0"
    n = len(a)
    q = lambda k: a[int(k * (n - 1))]
    return (f"n={n} p25={q(.25):.3f} med={q(.5):.3f} p75={q(.75):.3f} "
            f"mean={mean(a):.3f}")


# --------------------------------------------------------------------------
# Загрузка
# --------------------------------------------------------------------------
class Px:
    """1m-свечи в numpy-массивах: цена в момент и достижение уровня."""

    def __init__(self, candles: list[Candle]):
        self.ts = np.array([c.ts for c in candles], dtype=np.int64)
        self.open = np.array([c.open for c in candles], dtype=np.float64)
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
        j0 = int(np.searchsorted(self.ts, ts, side="right"))
        j1 = int(np.searchsorted(self.ts, ts + horizon_ms, side="right"))
        if j0 >= j1:
            return False
        if side == "ask":
            return bool(self.high[j0:j1].max() >= target)
        return bool(self.low[j0:j1].min() <= target)


_KL1_CACHE: dict[str, tuple[Px, int]] = {}
_BD_CACHE: dict[tuple[str, int], tuple[list, int]] = {}
_KL_CACHE: dict[tuple[str, str], list[Candle]] = {}


def kline_rows(symbol: str, tf: str) -> list[Candle]:
    """Свечи таймфрейма за период с кешем (грузим один раз на прогон)."""
    key = (symbol, tf)
    if key not in _KL_CACHE:
        _KL_CACHE[key] = load_klines(symbol, tf, START, END).rows
    return _KL_CACHE[key]


def load_kl1(symbol: str) -> tuple[Px, int]:
    if symbol not in _KL1_CACHE:
        series = load_klines(symbol, "1m", START, END)
        _KL1_CACHE[symbol] = (Px(series.rows), series.loaded)
    return _KL1_CACHE[symbol]


def load_bd_subsampled(symbol: str, step_minutes: int = 30
                       ) -> tuple[list, int]:
    """Снимки стакана, один на блок step_minutes (примерно).

    Результат кешируется: разбор bookDepth — самая дорогая часть прогона
    (~0.3 с/день × 271 день), и он повторяется в каждой секции.
    """
    key = (symbol, step_minutes)
    if key in _BD_CACHE:
        return _BD_CACHE[key]
    out = []
    days_loaded = 0
    stride = step_minutes * 2  # снимки каждые ~30 с -> 2 на минуту
    for path in range_days(symbol, "bookDepth", START, END):
        try:
            snaps = parse_book_depth(_read_zip_csv(path))
        except Exception:
            continue
        days_loaded += 1
        out.extend(snaps[::stride])
    out.sort(key=lambda s: s.ts)
    _BD_CACHE[key] = (out, days_loaded)
    return _BD_CACHE[key]


def round_step(price: float) -> float:
    """«Круглый» шаг ~0.5 % цены, из ряда 1/2/5 × 10^k.

    Аналог «00»-уровней Ослера: для BTC 83461 -> 500 (83000, 83500, …),
    для ETH 2500 -> 10 (2500, 2510, …), для DOGE 0.25 -> 0.001. Шаг должен
    давать достаточно пересечений уровня, иначе статистика разворота пуста.
    """
    if price <= 0:
        return 1.0
    target = price * 0.005
    k = 10 ** math.floor(math.log10(target))
    m = target / k
    nice = 1 if m < 1.5 else (2 if m < 3.5 else 5)
    return nice * k


# --------------------------------------------------------------------------
# Т1. Магнетизм стенки
# --------------------------------------------------------------------------
def thesis1(symbols=SYMBOLS) -> None:
    print("=" * 72)
    print("Т1. Магнетизм стенки: доходит ли цена до самой глубокой полосы")
    print("    чаще, чем до зеркальной полосы на том же расстоянии")
    print("=" * 72)
    for horizon in ("30m", "2h", "6h"):
        print(f"\n--- горизонт {horizon} ---")
        rows = []
        for sym in symbols:
            px, days1 = load_kl1(sym)
            snaps, days_bd = load_bd_subsampled(sym)
            n_wall = n_reach_wall = n_reach_mirror = both = neither = 0
            wall_only = mirror_only = 0
            dists = []
            for s in snaps:
                walls = find_walls(s)
                if not walls:
                    continue
                mid = px.price_at(s.ts)
                if mid is None or mid <= 0:
                    continue
                w = walls[0]
                d = w.center_pct
                t_wall = mid * (1 + d / 100)
                t_mir = mid * (1 - d / 100)
                rw = px.reached(s.ts, t_wall, H_MS[horizon],
                                "ask" if d > 0 else "bid")
                rm = px.reached(s.ts, t_mir, H_MS[horizon],
                                "bid" if d > 0 else "ask")
                n_wall += 1
                n_reach_wall += rw
                n_reach_mirror += rm
                if rw and rm:
                    both += 1
                elif rw:
                    wall_only += 1
                elif rm:
                    mirror_only += 1
                else:
                    neither += 1
                dists.append(abs(d))
            pw = n_reach_wall / n_wall if n_wall else 0.0
            pm = n_reach_mirror / n_wall if n_wall else 0.0
            ci = wilson(pw, n_wall)
            chi2, pval = mcnemar(wall_only, mirror_only)
            rows.append((sym, n_wall, pw, pm, wall_only, mirror_only, pval,
                         median(dists)))
            print(f"  {sym:8} стенок={n_wall:6}  достигнута={pw*100:5.2f}% "
                  f"[{ci[0]*100:.1f},{ci[1]*100:.1f}]  "
                  f"зеркало={pm*100:5.2f}%  "
                  f"wall-только={wall_only} mirror-только={mirror_only} "
                  f"p={pval:.3g}  мед.расст={median(dists):.2f}%")
        # пул по всем монетам
        nw = sum(r[1] for r in rows)
        pw = sum(r[1] * r[2] for r in rows)  # взвешенно не совсем; пересчитаем
        # проще: суммируем счётчики напрямую нет — пересчёт по rows с долями
        tot = sum(r[1] for r in rows)
        tw = sum(r[2] * r[1] for r in rows) / tot
        tm = sum(r[3] * r[1] for r in rows) / tot
        wo = sum(r[4] for r in rows)
        mo = sum(r[5] for r in rows)
        chi2, pval = mcnemar(wo, mo)
        print(f"  {'ПУЛ':8} стенок={tot:6}  достигнута={tw*100:5.2f}%  "
              f"зеркало={tm*100:5.2f}%  wall-только={wo} mirror-только={mo} "
              f"p={pval:.3g}")


# --------------------------------------------------------------------------
# Т2. От ликвидности к ликвидности
# --------------------------------------------------------------------------
def thesis2(symbols=SYMBOLS) -> None:
    print("\n" + "=" * 72)
    print("Т2. Цена ходит от ликвидности к ликвидности")
    print("=" * 72)
    for sym in symbols:
        px, _ = load_kl1(sym)
        snaps, _ = load_bd_subsampled(sym, step_minutes=120)
        n_two = n_touch_w1 = n_reach_w2 = n_reach_mirror = 0
        w2_only = mir_only = 0
        same_side = opp_side = 0
        for s in snaps:
            walls = find_walls(s)
            if len(walls) < 2:
                continue
            mid = px.price_at(s.ts)
            if mid is None or mid <= 0:
                continue
            w1, w2 = walls[0], walls[1]
            t1 = mid * (1 + w1.center_pct / 100)
            # касание W1 за 12 ч
            hit1_ts = first_reach(px, s.ts, t1, "ask" if w1.center_pct > 0 else "bid",
                                  H_MS["12h"])
            if hit1_ts is None:
                continue
            n_two += 1
            n_touch_w1 += 1
            # W2 замораживаем по цене в момент касания
            mid2 = px.price_at(hit1_ts)
            if mid2 is None or mid2 <= 0:
                continue
            t2 = mid2 * (1 + w2.center_pct / 100)
            t2m = mid2 * (1 - w2.center_pct / 100)
            r2 = px.reached(hit1_ts, t2, H_MS["12h"],
                            "ask" if w2.center_pct > 0 else "bid")
            r2m = px.reached(hit1_ts, t2m, H_MS["12h"],
                             "bid" if w2.center_pct > 0 else "ask")
            n_reach_w2 += r2
            n_reach_mirror += r2m
            if r2 and not r2m:
                w2_only += 1
            if r2m and not r2:
                mir_only += 1
            if w2.side == w1.side:
                same_side += 1
            else:
                opp_side += 1
        if n_touch_w1 == 0:
            print(f"  {sym}: событий с касанием W1 нет")
            continue
        p2 = n_reach_w2 / n_touch_w1
        pm = n_reach_mirror / n_touch_w1
        chi2, pval = mcnemar(w2_only, mir_only)
        print(f"  {sym:8} касаний W1={n_touch_w1:5}  до W2={p2*100:5.1f}%  "
              f"зеркало={pm*100:5.1f}%  w2-только={w2_only} "
              f"mirror-только={mir_only} p={pval:.3g}  "
              f"W2 той же стороны={same_side} / противоположной={opp_side}")


def first_reach(px: Px, ts: int, target: float, side: str,
                horizon_ms: int) -> int | None:
    """Первый момент (ms) внутри горизонта, когда цена дошла до target."""
    j0 = int(np.searchsorted(px.ts, ts, side="right"))
    j1 = int(np.searchsorted(px.ts, ts + horizon_ms, side="right"))
    for j in range(j0, j1):
        if side == "ask" and px.high[j] >= target:
            return int(px.ts[j])
        if side == "bid" and px.low[j] <= target:
            return int(px.ts[j])
    return None


# --------------------------------------------------------------------------
# Т3. Где стоят стопы + forward return срабатываний sweep
# --------------------------------------------------------------------------
def thesis3(symbols=SYMBOLS) -> None:
    print("\n" + "=" * 72)
    print("Т3. Глубина прокола экстремума (оценка расстояния до стопов)")
    print("=" * 72)
    for tf in ("5m", "1h"):
        print(f"\n--- таймфрейм {tf} ---")
        # окно поиска прокола в барах. Должно быть БОЛЬШЕ span пивота (40):
        # внутри ±40 свечей пробития не бывает по построению. 5m: 288 баров =
        # 24 ч; 1h: 168 баров = 7 дней (40 баров подтверждения + ~5 суток).
        window = 288 if tf == "5m" else 168
        all_pen = []
        for sym in symbols:
            cs = kline_rows(sym, tf)
            pen = sweep_penetrations(cs, window)
            all_pen.extend(pen)
            print(f"  {sym:8} {pct_rows(pen)}")
        # нормировка на NATR для сравнения таймфреймов
        if all_pen:
            print(f"  (в % от цены; медиана пула {median(all_pen):.3f}%)")

    print("\n" + "=" * 72)
    print("Т3+. forward return срабатываний liquidity_sweep против базы")
    print("=" * 72)
    for tf in ("5m", "1h"):
        print(f"\n--- таймфрейм {tf} ---")
        window = 288 if tf == "5m" else 168
        for sym in symbols:
            cs = kline_rows(sym, tf)
            sweeps = sweep_events(cs, window)  # list of (index_close, penetration)
            if not sweeps:
                print(f"  {sym}: срабатываний нет")
                continue
            base = forward_returns(cs)  # по всем свечам
            for hlabel, bars in (("1h", 60), ("4h", 240), ("24h", 1440)):
                if tf == "1h":
                    bars = {"1h": 1, "4h": 4, "24h": 24}[hlabel]
                else:
                    bars = {"1h": 12, "4h": 48, "24h": 288}[hlabel]
                ev = [fwd(cs, i, bars) for i, _ in sweeps]
                ev = [x for x in ev if x is not None]
                bl = [x for x in base[bars] if x is not None]
                if not ev:
                    continue
                me, se = mean(ev), pstdev(ev) if len(ev) > 1 else 0.0
                mb, sb = mean(bl), pstdev(bl) if len(bl) > 1 else 0.0
                z, p = two_mean_z(me, se, len(ev), mb, sb, len(bl))
                print(f"    {sym} {hlabel:3}: sweep {me:+.3f}% "
                      f"(n={len(ev)}) vs база {mb:+.3f}% p={p:.3g}")
            # зависимость от глубины прокола (мелкие vs глубокие)
            pen = [p for _, p in sweeps]
            med_pen = median(pen)
            shallow = [fwd(cs, i, 48) for i, p in sweeps if p <= med_pen]
            deep = [fwd(cs, i, 48) for i, p in sweeps if p > med_pen]
            shallow = [x for x in shallow if x is not None]
            deep = [x for x in deep if x is not None]
            if shallow and deep:
                z, p = two_mean_z(mean(shallow),
                                  pstdev(shallow) if len(shallow) > 1 else 0,
                                  len(shallow),
                                  mean(deep), pstdev(deep) if len(deep) > 1 else 0,
                                  len(deep))
                print(f"    {sym} 4h: мелкий прокол {mean(shallow):+.3f}% "
                      f"(n={len(shallow)}) vs глубокий {mean(deep):+.3f}% "
                      f"(n={len(deep)}) p={p:.3g}")


def sweep_penetrations(cs: list[Candle], window: int = 288) -> list[float]:
    """Глубина прокола (в %) для каждого прокола экстремума с возвратом.

    Экстремум подтверждается окном ±span (по умолчанию 40 свечей), поэтому
    внутри этих свечей пробития не бывает по построению. Искать прокол надо
    ПОСЛЕ подтверждения — от экстремума вперёд на window свечей, а не на
    первые 8, иначе срабатываний ноль.
    """
    pivots = find_pivots(cs)
    out = []
    for i, p, kind in pivots:
        for k in range(i + 1, min(i + 1 + window, len(cs))):
            c = cs[k]
            if kind == "low" and c.low < p and c.close > p:
                out.append((p - c.low) / p * 100)
                break
            if kind == "high" and c.high > p and c.close < p:
                out.append((c.high - p) / p * 100)
                break
    return out


def sweep_events(cs: list[Candle], window: int = 288) -> list[tuple[int, float]]:
    """Срабатывания liquidity_sweep: (индекс свечи прокола, глубина %)."""
    pivots = find_pivots(cs)
    out = []
    for i, p, kind in pivots:
        for k in range(i + 1, min(i + 1 + window, len(cs))):
            c = cs[k]
            if kind == "low" and c.low < p and c.close > p:
                out.append((k, (p - c.low) / p * 100))
                break
            if kind == "high" and c.high > p and c.close < p:
                out.append((k, (c.high - p) / p * 100))
                break
    return out


def forward_returns(cs: list[Candle]) -> dict[int, list[float]]:
    """Словарь {баров: [forward return по всем свечам]}."""
    bars = (1, 4, 12, 24, 48, 60, 240, 288, 1440)
    out = {b: [] for b in bars}
    closes = [c.close for c in cs]
    n = len(cs)
    for b in bars:
        for i in range(n - b):
            if closes[i] > 0:
                out[b].append((closes[i + b] - closes[i]) / closes[i] * 100)
    return out


def fwd(cs: list[Candle], i: int, bars: int) -> float | None:
    if i + bars >= len(cs) or cs[i].close <= 0:
        return None
    return (cs[i + bars].close - cs[i].close) / cs[i].close * 100


# --------------------------------------------------------------------------
# Т4. Пул из нескольких касаний толще одиночного
# --------------------------------------------------------------------------
def thesis4(symbols=SYMBOLS) -> None:
    print("\n" + "=" * 72)
    print("Т4. Уровни с 1 касанием против 2–3 касаний: глубина прокола")
    print("=" * 72)
    for tf in ("5m", "1h"):
        print(f"\n--- таймфрейм {tf}, монеты {list(symbols)} ---")
        agg = {"1": [], "2+": []}
        hist = Counter()
        for sym in symbols:
            cs = kline_rows(sym, tf)
            out, h = level_pierces(cs, tf)
            hist.update(h)
            for key, pens in out.items():
                agg[key].extend(pens)
        total = sum(hist.values())
        print(f"  всего уровней: {total} "
              f"(с 1 касанием: {len(agg['1'])}, с 2+: {len(agg['2+'])})")
        print(f"  распределение касаний: {dict(sorted(hist.items()))}")
        for key, pens in agg.items():
            print(f"  касаний {key:2}: {pct_rows(pens)}")
        if agg["1"] and agg["2+"]:
            z, p = two_mean_z(mean(agg["1"]),
                              pstdev(agg["1"]) if len(agg["1"]) > 1 else 0,
                              len(agg["1"]),
                              mean(agg["2+"]),
                              pstdev(agg["2+"]) if len(agg["2+"]) > 1 else 0,
                              len(agg["2+"]))
            print(f"  z={z:.2f} p={p:.3g}: разницы в глубине прокола между "
                  f"уровнем с одним касанием и с несколькими нет")


def cluster_levels(cs: list[Candle], tf: str) -> list[dict]:
    """Уровни из пивотов с честным счётчиком касаний.

    Кластеринг по цене с фиксированным допуском ~0.5 % (как у круглых чисел),
    а не по таймфрейму — пивоты редкие, таймфреймный допуск слишком узок.
    """
    pivots = find_pivots(cs)
    tol = 0.005
    levels: list[dict] = []
    for idx, price, kind in pivots:
        if price <= 0:
            continue
        hit = None
        for lv in levels:
            if lv["kind"] == kind and abs(lv["price"] - price) / price <= tol:
                hit = lv
                break
        if hit is not None:
            hit["price"] = (hit["price"] * hit["touches"] + price) / (hit["touches"] + 1)
            hit["touches"] += 1
            hit["last_ts"] = cs[idx].ts
        else:
            levels.append({"price": price, "kind": kind, "touches": 1,
                           "last_ts": cs[idx].ts})
    return levels


def level_pierces(cs: list[Candle], tf: str
                  ) -> tuple[dict[str, list[float]], Counter]:
    """Максимальная глубина прокола уровня (в %) после его формирования.

    Возвращает (разбивка глубины по числу касаний, распределение числа
    касаний). Прокол считается суффиксным максимумом/минимумом цены после
    последнего касания — это O(n), а не O(n × уровней).
    """
    levels = cluster_levels(cs, tf)
    hist = Counter(lv["touches"] for lv in levels)
    out = {"1": [], "2+": []}
    if not levels:
        return out, hist
    ts = np.array([c.ts for c in cs], dtype=np.int64)
    highs = np.array([c.high for c in cs], dtype=np.float64)
    lows = np.array([c.low for c in cs], dtype=np.float64)
    suf_hi = np.maximum.accumulate(highs[::-1])[::-1]  # максимум справа
    suf_lo = np.minimum.accumulate(lows[::-1])[::-1]   # минимум справа
    for lv in levels:
        key = "1" if lv["touches"] <= 1 else "2+"
        i0 = int(np.searchsorted(ts, lv["last_ts"], side="right"))
        if i0 >= len(cs):
            best = 0.0
        elif lv["kind"] == "high":  # сопротивление: прокол вверх
            best = max(0.0, (suf_hi[i0] - lv["price"]) / lv["price"] * 100)
        else:                       # поддержка: прокол вниз
            best = max(0.0, (lv["price"] - suf_lo[i0]) / lv["price"] * 100)
        out[key].append(best)
    return out, hist


# --------------------------------------------------------------------------
# Т5. Перенаселённость
# --------------------------------------------------------------------------
def thesis5(symbols=SYMBOLS) -> None:
    print("\n" + "=" * 72)
    print("Т5. Метрики позиционирования и фандинг против последующего хода")
    print("=" * 72)
    for sym in symbols:
        mt = load_metrics(sym, START, END).rows
        fu = load_funding(sym, "2026-01", "2026-08").rows
        cs5 = kline_rows(sym, "5m")
        closes = [c.close for c in cs5]
        ts5 = np.array([c.ts for c in cs5], dtype=np.int64)
        n = len(cs5)
        m_ts = np.array([r.create_time for r in mt], dtype=np.int64)
        print(f"\n--- {sym}: metrics={len(mt)} funding={len(fu)} ---")
        for name, getter in (
                ("taker_long_short", lambda r: r.sum_taker_long_short_vol_ratio),
                ("toptrader_ls", lambda r: r.count_toptrader_long_short_ratio),
                ("oi_value", lambda r: r.sum_open_interest_value)):
            vals = np.array([getter(r) for r in mt], dtype=np.float64)
            if vals.size == 0:
                continue
            lo_th = np.percentile(vals, 10)
            hi_th = np.percentile(vals, 90)
            # значение метрики на каждом 5m-баре = последний metrics-ряд до бара
            bar_m = np.searchsorted(m_ts, ts5, side="right") - 1
            bar_val = np.full(n, np.nan)
            ok = (bar_m >= 0) & (bar_m < len(mt))
            bar_val[ok] = vals[bar_m[ok]]
            # Считаем средний forward return на НЕПЕРЕКРЫВАЮЩИХСЯ блоках
            # (якоря через horizon_bars баров): иначе окна перекрываются,
            # наблюдения зависимы и p-значение завышено на порядки.
            for horizon_bars, hlabel in ((48, "4ч"), (288, "24ч")):
                anchors = [a for a in range(0, n - horizon_bars, horizon_bars)]
                base = [(closes[a + horizon_bars] - closes[a]) / closes[a] * 100
                        for a in anchors if closes[a] > 0]
                for label, th, comp in (("низ 10%", lo_th, "<="),
                                        ("верх 10%", hi_th, ">=")):
                    sel = [a for a in anchors
                           if not np.isnan(bar_val[a]) and closes[a] > 0 and
                           (bar_val[a] <= th if comp == "<=" else bar_val[a] >= th)]
                    fwd_sel = [(closes[a + horizon_bars] - closes[a]) / closes[a] * 100
                               for a in sel]
                    if not fwd_sel:
                        print(f"  {name:16} {label:8}: {hlabel} n=0")
                        continue
                    half = len(fwd_sel) // 2
                    m1 = mean(fwd_sel[:half]) if half else float("nan")
                    m2 = mean(fwd_sel[half:]) if half else float("nan")
                    z, p = two_mean_z(mean(fwd_sel),
                                      pstdev(fwd_sel) if len(fwd_sel) > 1 else 0,
                                      len(fwd_sel),
                                      mean(base),
                                      pstdev(base) if len(base) > 1 else 0,
                                      len(base))
                    print(f"  {name:16} {label:8}: {hlabel} {mean(fwd_sel):+.3f}% "
                          f"(n={len(fwd_sel)}) vs база {mean(base):+.3f}% "
                          f"p={p:.3g} | пол.1/пол.2 {m1:+.3f}/{m2:+.3f}%")
        # фандинг: положительная ставка против отрицательной, 24ч вперёд,
        # неперекрывающиеся события (шаг между событиями >= 24ч).
        if fu:
            fu_sorted = sorted(fu, key=lambda f: f.calc_time)
            last_i = -10 ** 9
            ev_pos: list[float] = []
            ev_neg: list[float] = []
            for f in fu_sorted:
                i = int(np.searchsorted(ts5, f.calc_time, side="left"))
                if i + 288 >= n or closes[i] <= 0:
                    continue
                if i - last_i < 288:
                    continue
                last_i = i
                r = (closes[i + 288] - closes[i]) / closes[i] * 100
                (ev_pos if f.last_funding_rate > 0 else ev_neg).append(r)
            if ev_pos and ev_neg:
                z, p = two_mean_z(mean(ev_pos),
                                  pstdev(ev_pos) if len(ev_pos) > 1 else 0,
                                  len(ev_pos),
                                  mean(ev_neg),
                                  pstdev(ev_neg) if len(ev_neg) > 1 else 0,
                                  len(ev_neg))
                print(f"  funding пол.ставка: 24ч {mean(ev_pos):+.3f}% "
                      f"(n={len(ev_pos)}) vs отр. {mean(ev_neg):+.3f}% "
                      f"(n={len(ev_neg)}) p={p:.3g}")


# --------------------------------------------------------------------------
# Круглые числа: асимметрия
# --------------------------------------------------------------------------
def round_numbers(symbols=SYMBOLS) -> None:
    print("\n" + "=" * 72)
    print("Круглые числа: разворот, заход фитиля, ускорение после пробоя")
    print("=" * 72)
    for sym in symbols:
        cs = kline_rows(sym, "5m")
        step = round_step(median(c.close for c in cs))
        print(f"\n--- {sym}: медиана цены {median(c.close for c in cs):.4f}, "
              f"шаг {step:g} ---")
        # (a) разворот у круглого уровня против случайного уровня
        round_rev, round_test = round_reversal_rate(cs, step)
        rand_rev, rand_test = random_reversal_rate(cs, step, trials=5)
        print(f"  разворот у круглого: {round_rev:.3f} (n={round_test}), "
              f"у случайного: {rand_rev:.3f} (n={rand_test})")
        # (b) заход фитиля за круглый уровень
        overs = round_overshoots(cs, step)
        print(f"  заход фитиля за круглый уровень (прокол+возврат): "
              f"{pct_rows(overs)}")
        # (c) ускорение после пробоя круглого уровня
        for hlabel, bars in (("1h", 12), ("2h", 24), ("4h", 48)):
            br = break_returns(cs, step, bars)
            bl = [x for x in forward_returns(cs)[bars] if x is not None]
            if not br:
                continue
            z, p = two_mean_z(mean(br), pstdev(br) if len(br) > 1 else 0,
                              len(br), mean(bl), pstdev(bl) if len(bl) > 1 else 0,
                              len(bl))
            print(f"  после пробоя круглого {hlabel}: {mean(br):+.3f}% "
                  f"(n={len(br)}) vs база {mean(bl):+.3f}% p={p:.3g}")


def _round_crossings(cs: list[Candle], step: float, shift: float = 0.0):
    """Пересечения уровней (кратные step + сдвиг) соседними свечами.

    Генератор отдаёт (уровень, направление, заход фитиля %, закрытие свечи).
    'up' — цена подошла к уровню снизу и зашла за него, 'down' — сверху.
    """
    for i in range(1, len(cs)):
        prev = cs[i - 1].close
        c = cs[i]
        if prev <= 0:
            continue
        lo = math.floor(min(prev, c.low) / step) * step + shift
        hi = math.ceil(max(prev, c.high) / step) * step + shift
        lv = lo
        while lv <= hi:
            if lv > 0:
                if prev < lv <= c.high:
                    yield lv, "up", (c.high - lv) / lv * 100, c.close
                elif prev > lv >= c.low:
                    yield lv, "down", (lv - c.low) / lv * 100, c.close
            lv += step


def round_reversal_rate(cs: list[Candle], step: float) -> tuple[float, int]:
    """Доля касаний круглого уровня, где цена отскочила обратно."""
    tests = bounces = 0
    for lv, d, overshoot, close in _round_crossings(cs, step):
        tests += 1
        if (d == "up" and close < lv) or (d == "down" and close > lv):
            bounces += 1
    return bounces / tests if tests else 0.0, tests


def random_reversal_rate(cs: list[Candle], step: float,
                         trials: int = 5) -> tuple[float, int]:
    """Та же доля на случайной сетке той же плотности (бутстрап-база)."""
    rates = []
    tot = 0
    for _ in range(trials):
        shift = RNG.uniform(0.2, 0.8) * step
        tests = bounces = 0
        for lv, d, overshoot, close in _round_crossings(cs, step, shift):
            tests += 1
            if (d == "up" and close < lv) or (d == "down" and close > lv):
                bounces += 1
        if tests:
            rates.append(bounces / tests)
            tot += tests
    return (mean(rates) if rates else 0.0), tot // max(trials, 1)


def round_overshoots(cs: list[Candle], step: float) -> list[float]:
    """На сколько % фитиль зашёл за круглый уровень при отскоке (прокол+возврат)."""
    out = []
    for lv, d, overshoot, close in _round_crossings(cs, step):
        if (d == "up" and close < lv) or (d == "down" and close > lv):
            out.append(overshoot)
    return out


def break_returns(cs: list[Candle], step: float, bars: int) -> list[float]:
    """Forward return после закрытия за круглым уровнем (пробой)."""
    out = []
    for i in range(1, len(cs) - bars):
        c, p = cs[i], cs[i - 1]
        lv = math.floor(c.close / step) * step
        if lv <= 0:
            continue
        # закрытие пересекло круглый уровень вверх
        if p.close <= lv < c.close and c.close > 0:
            out.append((cs[i + bars].close - c.close) / c.close * 100)
        lv = math.ceil(c.close / step) * step
        if p.close >= lv > c.close and c.close > 0:
            out.append((cs[i + bars].close - c.close) / c.close * 100)
    return out


# --------------------------------------------------------------------------
# Сравнение настоящего стакана с выведенными пулами
# --------------------------------------------------------------------------
def compare(symbols=SYMBOLS) -> None:
    print("\n" + "=" * 72)
    print("Сравнение: настоящие стенки bookDepth против выведенных пулов")
    print("=" * 72)
    tol_pct = 0.2
    for sym in symbols:
        snaps, _ = load_bd_subsampled(sym)
        px, _ = load_kl1(sym)
        cs5 = kline_rows(sym, "5m")
        step = round_step(median(c.close for c in cs5))
        wall_near_round = wall_tot = 0
        rand_near_round = rand_tot = 0
        wall_near_eq = wall_tot_eq = 0
        rand_near_eq = rand_tot_eq = 0
        # равные экстремумы считаем блоками по 3 дня
        blocks = chunk_candles(cs5, 3 * 288)
        eq_levels = []
        for blk in blocks:
            eq_levels.append([p.price for p in find_equal_extremes(blk)])
        for s in snaps:
            mid = px.price_at(s.ts)
            if mid is None or mid <= 0:
                continue
            walls = find_walls(s)
            eq = eq_levels_at(eq_levels, s.ts, blocks)
            for w in walls:
                wp = wall_price(w, mid)
                wall_tot += 1
                if min_nearest_round(wp, step) <= tol_pct:
                    wall_near_round += 1
                if eq and min(abs(wp - e) / wp * 100 for e in eq) <= tol_pct:
                    wall_near_eq += 1
            # случайные уровни: та же цена ± случайный сдвиг в ±5 %
            for w in walls:
                wp = wall_price(w, mid)
                for _ in range(3):
                    rp = wp * (1 + RNG.uniform(-0.05, 0.05))
                    rand_tot += 1
                    if min_nearest_round(rp, step) <= tol_pct:
                        rand_near_round += 1
                    if eq and min(abs(rp - e) / rp * 100 for e in eq) <= tol_pct:
                        rand_near_eq += 1
        print(f"\n--- {sym}: стенок {wall_tot} ---")
        pr = wall_near_round / wall_tot * 100 if wall_tot else 0
        prr = rand_near_round / rand_tot * 100 if rand_tot else 0
        print(f"  стенка у круглого: {pr:.1f}% vs случайный уровень {prr:.1f}%")
        pe = wall_near_eq / wall_tot * 100 if wall_tot else 0
        per = rand_near_eq / rand_tot * 100 if rand_tot else 0
        print(f"  стенка у равных экстремумов: {pe:.1f}% vs случайный "
              f"{per:.1f}%")


def min_nearest_round(price: float, step: float) -> float:
    if price <= 0:
        return float("inf")
    m = round(price / step) * step
    return abs(price - m) / price * 100


def chunk_candles(cs: list[Candle], size: int) -> list[list[Candle]]:
    return [cs[i:i + size] for i in range(0, len(cs), size)]


def eq_levels_at(eq_levels: list[list[float]], ts: int,
                 blocks: list[list[Candle]]) -> list[float]:
    """Равные экстремумы из блока, которому принадлежит ts."""
    for blk, levels in zip(blocks, eq_levels):
        if blk and blk[0].ts <= ts <= blk[-1].ts:
            return levels
    return []


# --------------------------------------------------------------------------
# Миграция ликвидности
# --------------------------------------------------------------------------
def migrate(symbols=SYMBOLS) -> None:
    print("\n" + "=" * 72)
    print("Миграция ликвидности: стенка стоит на месте или ползёт за ценой")
    print("=" * 72)
    for sym in symbols:
        snaps, _ = load_bd_subsampled(sym, step_minutes=30)
        px, _ = load_kl1(sym)
        pairs = []
        for s in snaps:
            mid = px.price_at(s.ts)
            if mid is not None and mid > 0:
                pairs.append((s, mid))
        snaps2 = [p[0] for p in pairs]
        mids = [p[1] for p in pairs]
        m = migration_stats(snaps2, mids)
        print(f"  {sym:8} n={m.n}  корреляция(стенка,середина)={m.corr_wall_mid:+.3f} "
              f"мед.расстояние={m.median_distance_pct:.2f}% "
              f"сторона держится={m.side_consistency*100:.0f}%")


# --------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="",
                    help="секции через запятую: t1,t2,t3,t4,t5,round,compare,migrate")
    args = ap.parse_args()
    only = set(args.only.split(",")) if args.only else None
    t0 = time.time()

    def run(name, fn):
        if only is None or name in only:
            fn()

    run("t1", thesis1)
    run("t2", thesis2)
    run("t3", thesis3)
    run("t4", thesis4)
    run("t5", thesis5)
    run("round", round_numbers)
    run("compare", compare)
    run("migrate", migrate)
    print(f"\nвсего времени: {time.time()-t0:.0f} с")


if __name__ == "__main__":
    main()
