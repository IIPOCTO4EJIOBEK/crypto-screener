#!/usr/bin/env python
"""Объёмные тезисы на архивах Binance Futures.

Четыре гипотезы, все — на непересекающихся якорях
(`range(warmup, n - h, h)`), с медианой рядом со средним и с контролем по
каждой монете отдельно. Дисциплина та же, что в tools/liquidity_study.py:
перекрывающиеся окна делают наивный t-тест недействительным, поэтому окна
исходов не пересекаются.

  H1  «Растёт объём — идёт движение». RVOL против последующего размаха.
      Q5 против Q1 считается трижды: сырой размах, |доходность| и размах,
      поделённый на ATR якоря. Третье — главное: если эффект есть только в
      сыром размахе и исчезает в нормированном, объём не добавляет ничего
      сверх кластеризации волатильности.
  H2  Объём С движением (подтверждение) против объёма БЕЗ движения
      (поглощение). У обоих — база по всем якорям, иначе меряем не эффект
      объёма, а общий снос рынка.
  H3  Направление даёт агрессор: buy_share из taker_buy_quote_volume.
      Колонки тейкера есть в архиве, но `Candle` их не несёт — читаются
      отдельным ридером, `parse_klines` не трогается.
  H4  Сухой объём (сжатие + низкий RVOL) перед расширением движения.

Запуск:
  .venv/bin/python -m tools.volume_study > /tmp/volume_study.log 2>&1
"""

from __future__ import annotations

import csv
import io
import math
import os
import sys
from datetime import date
from statistics import mean, median, pstdev

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data.archive import _read_zip_csv, load_klines, range_days
from src.data.market import Candle
from tools.liquidity_study import two_mean_z, two_prop_z, wilson

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT"]
START = date(2026, 6, 1)
END = date(2026, 9, 28)

TFS = ("5m", "1h")
HORIZONS = {"5m": (12, 24), "1h": (6, 24)}

RVOL_WIN = 20      # окно среднего объёма, строго ДО якоря
ATR_P = 14
ER_P = 20
WARMUP = max(RVOL_WIN, ATR_P, ER_P) + 1   # 21 свеча прогрева
TREND_BODY_K = 0.5     # тело >= K x ATR% = «трендовая» свеча
ABSORB_BODY_K = 0.25   # тело <= K x ATR% при высоком объёме = «поглощение»
TREND_RVOL = 1.5       # «высокий объём» для трендовой свечи
ABSORB_RVOL = 2.0      # «высокий объём» для поглощения
DRY_RVOL = 0.7         # сухой объём
DRY_ER = 0.5           # сжатие (Кауфман)

_CANDLES: dict[tuple[str, str], list[Candle]] = {}
_TAKER: dict[tuple[str, str], dict[int, float]] = {}


# --------------------------------------------------------------------------
# Данные
# --------------------------------------------------------------------------
def candles(symbol: str, tf: str) -> list[Candle]:
    key = (symbol, tf)
    if key not in _CANDLES:
        _CANDLES[key] = load_klines(symbol, tf, START, END).rows
    return _CANDLES[key]


def taker_share(symbol: str, tf: str) -> dict[int, float]:
    """{ts: buy_share}, buy_share = taker_buy_quote_volume / quote_volume.

    Свой ридер: `Candle` не несёт колонок тейкера, а менять `parse_klines`
    нельзя — он общий для всего проекта. Строки без заголовка в архивах
    фьючерсов не бывает (проверено на 2023–2026), но нечисловой заголовок
    на всякий случай отбрасывается.
    """
    key = (symbol, tf)
    if key in _TAKER:
        return _TAKER[key]
    out: dict[int, float] = {}
    for path in range_days(symbol, tf, START, END):
        try:
            text = _read_zip_csv(path)
        except Exception:
            continue
        for row in csv.DictReader(io.StringIO(text)):
            try:
                ts = int(row["open_time"])
                qv = float(row["quote_volume"])
                tb = float(row["taker_buy_quote_volume"])
            except (KeyError, ValueError, TypeError):
                continue
            if qv > 0:
                out[ts] = tb / qv
    _TAKER[key] = out
    return out


# --------------------------------------------------------------------------
# Признаки якоря (всё — только по свечам ДО и включая якорь)
# --------------------------------------------------------------------------
class Feats:
    """Предвычисленные ряды: префиксные суммы, O(1) на якорь."""

    def __init__(self, cs: list[Candle]) -> None:
        self.cs = cs
        n = len(cs)
        self.tr = [0.0] * n           # true range по свече
        self.ad = [0.0] * n           # |close[i] - close[i-1]|
        for i, c in enumerate(cs):
            if i == 0:
                self.tr[i] = c.high - c.low
                continue
            pc = cs[i - 1].close
            self.tr[i] = max(c.high - c.low, abs(c.high - pc), abs(c.low - pc))
            self.ad[i] = abs(c.close - pc)
        # prefix[k] = сумма первых k элементов
        self.ptr = [0.0] * (n + 1)
        self.pad = [0.0] * (n + 1)
        self.pqv = [0.0] * (n + 1)
        for i in range(n):
            self.ptr[i + 1] = self.ptr[i] + self.tr[i]
            self.pad[i + 1] = self.pad[i] + self.ad[i]
            self.pqv[i + 1] = self.pqv[i] + cs[i].quote_volume

    def atr_pct(self, i: int) -> float | None:
        if i + 1 < ATR_P or self.cs[i].close <= 0:
            return None
        atr = (self.ptr[i + 1] - self.ptr[i + 1 - ATR_P]) / ATR_P
        return atr / self.cs[i].close * 100

    def er(self, i: int) -> float | None:
        """Коэффициент эффективности Кауфмана за ER_P шагов."""
        if i < ER_P:
            return None
        path = self.pad[i + 1] - self.pad[i + 1 - ER_P]
        if path <= 0:
            return None
        net = abs(self.cs[i].close - self.cs[i - ER_P].close)
        return net / path

    def rvol(self, i: int) -> float | None:
        """Объём якоря ÷ средний объём за RVOL_WIN свечей ДО якоря."""
        if i < RVOL_WIN:
            return None
        base = (self.pqv[i] - self.pqv[i - RVOL_WIN]) / RVOL_WIN
        if base <= 0:
            return None
        return self.cs[i].quote_volume / base


_FEATS: dict[tuple[str, str], "Feats"] = {}


def feats(symbol: str, tf: str) -> "Feats":
    """Признаки строятся один раз на (монета, ТФ): это самый долгий шаг."""
    key = (symbol, tf)
    if key not in _FEATS:
        _FEATS[key] = Feats(candles(symbol, tf))
    return _FEATS[key]


def fwd_stats(cs: list[Candle], i: int, h: int) -> tuple[float, float] | None:
    """(размах в %, доходность в %) за свечи i+1..i+h, от close[i]."""
    if i + h >= len(cs) or cs[i].close <= 0:
        return None
    base = cs[i].close
    hi = max(c.high for c in cs[i + 1:i + h + 1])
    lo = min(c.low for c in cs[i + 1:i + h + 1])
    return ((hi - lo) / base * 100, (cs[i + h].close - base) / base * 100)


def anchors(n: int, h: int) -> range:
    """Непересекающиеся якоря: окна исходов не перекрываются."""
    return range(WARMUP, n - h, h)


def z_vs_half(p: float, n: int) -> float:
    """z доли против 50 %."""
    if n <= 0 or p != p:
        return float("nan")
    return (p - 0.5) / math.sqrt(0.25 / n)


def p_of_z(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2)) if z == z else float("nan")


def verdict(ok: bool, p: float, expect: str = "") -> str:
    """Вердикт требует И направления, И значимости: знак без p ничего не стоит."""
    if p != p:
        return "нет данных"
    if ok and p < 0.05:
        return "ПОДТВЕРЖДЕНА"
    if ok:
        return f"направление есть, но незначимо (p={p:.3f})"
    if p < 0.05:
        return f"ЗНАЧИМО, НО ПРОТИВ ОЖИДАНИЯ (p={p:.4f})"
    return f"отклонена (p={p:.3f})"


def same_sign(g: list[tuple], a: int, b: int) -> tuple[float, int]:
    """Доля записей, где признаки a и b одного знака (нули выброшены)."""
    tot = ok = 0
    for r in g:
        if r[a] == 0 or r[b] == 0:
            continue
        tot += 1
        if r[a] == r[b]:
            ok += 1
    return (ok / tot if tot else float("nan")), tot


def opp_sign(g: list[tuple], a: int, b: int) -> tuple[float, int]:
    """Доля записей, где признаки a и b разных знаков (нули выброшены)."""
    tot = ok = 0
    for r in g:
        if r[a] == 0 or r[b] == 0:
            continue
        tot += 1
        if r[a] != r[b]:
            ok += 1
    return (ok / tot if tot else float("nan")), tot


def rate_row(name: str, p: float, n: int) -> str:
    if n == 0 or p != p:
        return f"  {name:>18} {n:>6}   нет данных"
    lo, hi = wilson(p, n)
    z = z_vs_half(p, n)
    return (f"  {name:>18} {n:>6} {p:>8.3f} [{lo:.3f},{hi:.3f}] "
            f"{z:>+7.2f} {p_of_z(z):>8.4f}")


# --------------------------------------------------------------------------
# H1. RVOL против последующего движения
# --------------------------------------------------------------------------
def quintile_edges(vals: list[float]) -> list[float]:
    s = sorted(vals)
    n = len(s)
    return [s[int(k * (n - 1))] for k in (0.2, 0.4, 0.6, 0.8)]


def q_index(v: float, edges: list[float]) -> int:
    for k, e in enumerate(edges):
        if v <= e:
            return k
    return len(edges)


def h1(symbols=SYMBOLS) -> None:
    print("\n" + "=" * 80)
    print("H1. Растёт объём — идёт движение?  RVOL против размаха")
    print("=" * 80)
    print(f"RVOL = объём якоря / средний объём за {RVOL_WIN} свечей ДО якоря.")
    for tf in TFS:
        for h in HORIZONS[tf]:
            print(f"\n--- {tf}, горизонт {h} свечей ---")
            per_sym: dict[str, list[tuple[float, float, float, float]]] = {}
            for sym in symbols:
                cs = candles(sym, tf)
                f = feats(sym, tf)
                rows = []
                for i in anchors(len(cs), h):
                    rv, atr = f.rvol(i), f.atr_pct(i)
                    fs = fwd_stats(cs, i, h)
                    if rv is None or atr is None or atr <= 0 or fs is None:
                        continue
                    rows.append((rv, fs[0], abs(fs[1]), atr))
                per_sym[sym] = rows

            pooled = [r for rows in per_sym.values() for r in rows]
            if not pooled:
                print("  данных нет")
                continue
            edges = quintile_edges([r[0] for r in pooled])
            print("  границы квинтилей RVOL: "
                  + ", ".join(f"{e:.2f}" for e in edges))
            print(f"  {'Q':>2} {'n':>6} {'RVOL med':>9} {'размах med':>11} "
                  f"{'размах mean':>12} {'|ret| med':>10} "
                  f"{'размах/ATR med':>15}")
            buckets: dict[int, list[tuple[float, float, float, float]]] = {
                k: [] for k in range(5)}
            for r in pooled:
                buckets[q_index(r[0], edges)].append(r)
            for k in range(5):
                b = buckets[k]
                if not b:
                    continue
                print(f"  {k+1:>2} {len(b):>6} {median(x[0] for x in b):>9.2f} "
                      f"{median(x[1] for x in b):>11.3f} "
                      f"{mean(x[1] for x in b):>12.3f} "
                      f"{median(x[2] for x in b):>10.3f} "
                      f"{median(x[1] / x[3] for x in b):>15.3f}")

            q1, q5 = buckets[0], buckets[4]
            print("\n  Q5 против Q1:")
            cmp_group(q5, q1, norm=False, name="сырой размах")
            cmp_group(q5, q1, norm=True, name="размах/ATR")
            print("  Q5 против Q1 по |доходности|:")
            cmp_abs(q5, q1)

            print("\n  по монетам (медианы, %):")
            for sym in symbols:
                rows = per_sym[sym]
                if len(rows) < 50:
                    print(f"    {sym:9} n={len(rows):>5}  мало данных")
                    continue
                e = quintile_edges([r[0] for r in rows])
                lo = [r for r in rows if q_index(r[0], e) == 0]
                hi = [r for r in rows if q_index(r[0], e) == 4]
                if len(lo) < 20 or len(hi) < 20:
                    print(f"    {sym:9} n={len(rows):>5}  мало в квинтиле")
                    continue
                d_med = median(x[1] for x in hi) - median(x[1] for x in lo)
                d_norm = (median(x[1] / x[3] for x in hi)
                          - median(x[1] / x[3] for x in lo))
                z, p = two_mean_z(mean(x[1] for x in hi),
                                  pstdev(x[1] for x in hi), len(hi),
                                  mean(x[1] for x in lo),
                                  pstdev(x[1] for x in lo), len(lo))
                print(f"    {sym:9} n={len(rows):>5} "
                      f"Q1med={median(x[1] for x in lo):>7.3f} "
                      f"Q5med={median(x[1] for x in hi):>7.3f} "
                      f"Δmed={d_med:>+7.3f} Δ(размах/ATR)={d_norm:>+6.3f} "
                      f"z={z:>+6.2f} p={p:.4f}")


def cmp_group(a: list[tuple], b: list[tuple], norm: bool, name: str) -> None:
    if not a or not b:
        print(f"    {name}: пусто")
        return
    va = [(x[1] / x[3]) if norm else x[1] for x in a]
    vb = [(x[1] / x[3]) if norm else x[1] for x in b]
    z, p = two_mean_z(mean(va), pstdev(va), len(va),
                      mean(vb), pstdev(vb), len(vb))
    up = median(va) > median(vb)
    print(f"    {name:14}: Q5 med={median(va):.3f} mean={mean(va):.3f} "
          f"(n={len(va)}) | Q1 med={median(vb):.3f} mean={mean(vb):.3f} "
          f"(n={len(vb)}) | z={z:+.2f} p={p:.4f} -> {verdict(up, p)}")


def cmp_abs(a: list[tuple], b: list[tuple]) -> None:
    va = [x[2] for x in a]
    vb = [x[2] for x in b]
    z, p = two_mean_z(mean(va), pstdev(va), len(va),
                      mean(vb), pstdev(vb), len(vb))
    up = median(va) > median(vb)
    print(f"    |доходность| : Q5 med={median(va):.3f} (n={len(va)}) | "
          f"Q1 med={median(vb):.3f} (n={len(vb)}) | z={z:+.2f} p={p:.4f} -> "
          f"{verdict(up, p)}")


# --------------------------------------------------------------------------
# H2. Объём с движением против объёма без движения
# --------------------------------------------------------------------------
def h2(symbols=SYMBOLS) -> None:
    print("\n" + "=" * 80)
    print("H2. Объём С движением (подтверждение) против БЕЗ движения (поглощение)")
    print("=" * 80)
    print(f"трендовая : тело >= {TREND_BODY_K} x ATR% и RVOL >= {TREND_RVOL}")
    print(f"поглощение: тело <= {ABSORB_BODY_K} x ATR% и RVOL >= {ABSORB_RVOL}")
    print("У поглощения тело почти ноль, поэтому его знак — шум: разворот")
    print("меряется против ПРЕДЫДУЩЕГО хода (close[i] - close[i-h]), не тела.")
    for tf in TFS:
        for h in HORIZONS[tf]:
            print(f"\n--- {tf}, горизонт {h} свечей ---")
            # запись: (размах, знак исхода, знак тела, знак прошлого хода)
            Rec = tuple[float, int, int, int]
            groups: dict[str, list[Rec]] = {
                "база": [], "тренд. RVOL>=1.5": [], "тренд. RVOL<1.5": [],
                "поглощение": []}
            # поимённо: на BTC знак уже переворачивался (Т1), поэтому pooled
            # доля сама по себе ничего не доказывает
            by_sym: dict[str, dict[str, list[Rec]]] = {}
            for sym in symbols:
                g: dict[str, list[Rec]] = {k: [] for k in groups}
                by_sym[sym] = g
                cs = candles(sym, tf)
                f = feats(sym, tf)
                for i in anchors(len(cs), h):
                    rv, atr = f.rvol(i), f.atr_pct(i)
                    fs = fwd_stats(cs, i, h)
                    if rv is None or atr is None or atr <= 0 or fs is None:
                        continue
                    c = cs[i]
                    body = (abs(c.close - c.open) / c.open * 100
                            if c.open > 0 else 0.0)
                    bsign = 1 if c.close > c.open else (-1 if c.close < c.open
                                                        else 0)
                    psign = 0
                    if i >= h:
                        d = c.close - cs[i - h].close
                        psign = 1 if d > 0 else (-1 if d < 0 else 0)
                    rsign = 1 if fs[1] > 0 else (-1 if fs[1] < 0 else 0)
                    rec = (fs[0], rsign, bsign, psign)
                    groups["база"].append(rec)
                    g["база"].append(rec)
                    if body >= TREND_BODY_K * atr:
                        key = ("тренд. RVOL>=1.5" if rv >= TREND_RVOL
                               else "тренд. RVOL<1.5")
                        groups[key].append(rec)
                        g[key].append(rec)
                    if rv >= ABSORB_RVOL and body <= ABSORB_BODY_K * atr:
                        groups["поглощение"].append(rec)
                        g["поглощение"].append(rec)

            print(f"  якорей в базе: {len(groups['база'])}")
            print("\n  (а) знак тела совпал со знаком последующего хода:")
            print(f"  {'группа':>18} {'n':>6} {'доля':>8} {'95% ДИ':>16} "
                  f"{'z':>7} {'p':>8}")
            for name in ("тренд. RVOL>=1.5", "тренд. RVOL<1.5", "база"):
                p, n = same_sign(groups[name], 1, 2)
                print(rate_row(name, p, n))
            pa = same_sign(groups["тренд. RVOL>=1.5"], 1, 2)
            pb = same_sign(groups["база"], 1, 2)
            if pa[1] and pb[1]:
                z, p = two_prop_z(pa[0], pa[1], pb[0], pb[1])
                print(f"    трендовая-на-объёме против базы: Δ={pa[0]-pb[0]:+.4f} "
                      f"z={z:+.2f} p={p:.4f} -> {verdict(pa[0] > pb[0], p)}")

            print("\n  (б) поглощение: исход ПРОТИВ предыдущего хода (разворот):")
            print(f"  {'группа':>18} {'n':>6} {'доля':>8} {'95% ДИ':>16} "
                  f"{'z':>7} {'p':>8}")
            for name in ("поглощение", "база"):
                p, n = opp_sign(groups[name], 1, 3)
                print(rate_row(name, p, n))
            pa = opp_sign(groups["поглощение"], 1, 3)
            pb = opp_sign(groups["база"], 1, 3)
            if pa[1] and pb[1]:
                z, p = two_prop_z(pa[0], pa[1], pb[0], pb[1])
                print(f"    поглощение против базы: Δ={pa[0]-pb[0]:+.4f} "
                      f"z={z:+.2f} p={p:.4f} -> {verdict(pa[0] > pb[0], p)} "
                      f"(ожидание: разворачивает чаще базы)")

            print("\n  (в) размах в группах (%):")
            for name, g in groups.items():
                if g:
                    print(f"    {name:>18}: n={len(g):>6} "
                          f"med={median(x[0] for x in g):.3f} "
                          f"mean={mean(x[0] for x in g):.3f}")

            # (г) поимённый контроль: pooled-доля может держаться на одной
            # монете, а на другой знак переворачивается — тогда правила нет
            print("\n  (г) по монетам (тренд. RVOL>=1.5 против своей базы; "
                  "поглощение против своей базы):")
            print(f"  {'символ':>9} {'n тренд':>8} {'Δ знак':>8} {'p':>8} "
                  f"{'n погл':>7} {'Δ разворот':>11} {'p':>8}")
            flips = 0
            for sym in symbols:
                g = by_sym[sym]
                a = same_sign(g["тренд. RVOL>=1.5"], 1, 2)
                b = same_sign(g["база"], 1, 2)
                if a[1] >= 20 and b[1] >= 20:
                    _, dp = two_prop_z(a[0], a[1], b[0], b[1])
                    d_sign = a[0] - b[0]
                    flips += 1 if d_sign < 0 else 0
                else:
                    dp, d_sign = float("nan"), float("nan")
                e = opp_sign(g["поглощение"], 1, 3)
                b2 = opp_sign(g["база"], 1, 3)
                if e[1] >= 20 and b2[1] >= 20:
                    _, ep = two_prop_z(e[0], e[1], b2[0], b2[1])
                    d_abs = e[0] - b2[0]
                else:
                    ep, d_abs = float("nan"), float("nan")
                print(f"  {sym:>9} {a[1]:>8} {d_sign:>+8.4f} {dp:>8.4f} "
                      f"{e[1]:>7} {d_abs:>+11.4f} {ep:>8.4f}")
            print(f"    монет с отрицательным знаком трендового эффекта: "
                  f"{flips} из {len(symbols)}")


# --------------------------------------------------------------------------
# H3. Направление даёт агрессор
# --------------------------------------------------------------------------
def h3(symbols=SYMBOLS) -> None:
    print("\n" + "=" * 80)
    print("H3. buy_share (тейкер) предсказывает направление")
    print("=" * 80)
    print("buy_share = taker_buy_quote_volume / quote_volume на якоре.")
    for tf in TFS:
        for h in HORIZONS[tf]:
            print(f"\n--- {tf}, горизонт {h} свечей ---")
            print(f"  {'символ':>9} {'якорей':>7} {'с ts':>6} "
                  f"{'>0.5 вверх':>11} {'95% ДИ':>16} {'z':>7} "
                  f"{'>=.65 вверх':>12} {'<=.35 вниз':>11}")
            pool_up: list[float] = []
            pool_dn: list[float] = []
            pool_base: list[float] = []
            pool_xu: list[float] = []
            pool_xd: list[float] = []
            for sym in symbols:
                cs = candles(sym, tf)
                ts = taker_share(sym, tf)
                tot = matched = up = 0
                n_xu = n_xd = 0
                hit_xu = hit_xd = 0
                for i in anchors(len(cs), h):
                    fs = fwd_stats(cs, i, h)
                    if fs is None:
                        continue
                    tot += 1
                    bs = ts.get(cs[i].ts)
                    if bs is None:
                        continue
                    matched += 1
                    rose = fs[1] > 0
                    pool_base.append(1.0 if rose else 0.0)
                    if bs > 0.5:
                        up += 1
                        pool_up.append(1.0 if rose else 0.0)
                    else:
                        pool_dn.append(0.0 if rose else 1.0)
                    if bs >= 0.65:
                        n_xu += 1
                        if rose:
                            hit_xu += 1
                        pool_xu.append(1.0 if rose else 0.0)
                    if bs <= 0.35:
                        n_xd += 1
                        if not rose:
                            hit_xd += 1
                        pool_xd.append(0.0 if rose else 1.0)
                if matched == 0:
                    print(f"  {sym:>9} {tot:>7} {matched:>6}   нет данных")
                    continue
                p_up = up / matched
                lo, hi = wilson(p_up, matched)
                z = z_vs_half(p_up, matched)
                xu = hit_xu / n_xu if n_xu else float("nan")
                xd = hit_xd / n_xd if n_xd else float("nan")
                flag = "" if matched >= 30 else "  ^ мало"
                print(f"  {sym:>9} {tot:>7} {matched:>6} {p_up:>11.3f} "
                      f"[{lo:.3f},{hi:.3f}] {z:>+7.2f} "
                      f"{xu:>12.3f} {xd:>11.3f}{flag}")
            print()
            for nm, arr in (("buy_share>0.5 -> вверх", pool_up),
                            ("buy_share<0.5 -> вниз", pool_dn),
                            ("база: доля роста", pool_base),
                            ("край >=0.65 -> вверх", pool_xu),
                            ("край <=0.35 -> вниз", pool_xd)):
                if not arr:
                    continue
                p = mean(arr)
                lo, hi = wilson(p, len(arr))
                z = z_vs_half(p, len(arr))
                print(f"  ПУЛ {nm:22} n={len(arr):>6} доля={p:.3f} "
                      f"[{lo:.3f},{hi:.3f}] z={z:+.2f} p={p_of_z(z):.4f}")


# --------------------------------------------------------------------------
# H4. Сухой объём перед расширением
# --------------------------------------------------------------------------
def h4(symbols=SYMBOLS) -> None:
    print("\n" + "=" * 80)
    print(f"H4. Сухой объём (ER<={DRY_ER} и RVOL<={DRY_RVOL}) перед расширением")
    print("=" * 80)
    print("Тезис: у сухого объёма последующий размах ВЫШЕ базы.")
    for tf in TFS:
        for h in HORIZONS[tf]:
            print(f"\n--- {tf}, горизонт {h} свечей ---")
            dry: list[tuple[float, float]] = []
            base: list[tuple[float, float]] = []
            by_sym: dict[str, tuple[list[tuple[float, float]],
                                    list[tuple[float, float]]]] = {}
            for sym in symbols:
                cs = candles(sym, tf)
                f = feats(sym, tf)
                d_s: list[tuple[float, float]] = []
                b_s: list[tuple[float, float]] = []
                by_sym[sym] = (d_s, b_s)
                for i in anchors(len(cs), h):
                    rv, er, atr = f.rvol(i), f.er(i), f.atr_pct(i)
                    fs = fwd_stats(cs, i, h)
                    if rv is None or er is None or atr is None or fs is None:
                        continue
                    if atr <= 0:
                        continue
                    base.append((fs[0], atr))
                    b_s.append((fs[0], atr))
                    if er <= DRY_ER and rv <= DRY_RVOL:
                        dry.append((fs[0], atr))
                        d_s.append((fs[0], atr))
            if not dry or not base:
                print("  сухих якорей нет")
                continue
            print(f"  сухих: n={len(dry)} из {len(base)} "
                  f"({len(dry) / len(base) * 100:.1f} % якорей)")
            print(f"  размах: сухие med={median(x[0] for x in dry):.3f} "
                  f"mean={mean(x[0] for x in dry):.3f}")
            print(f"          база  med={median(x[0] for x in base):.3f} "
                  f"mean={mean(x[0] for x in base):.3f}")
            z, p = two_mean_z(mean(x[0] for x in dry),
                              pstdev(x[0] for x in dry), len(dry),
                              mean(x[0] for x in base),
                              pstdev(x[0] for x in base), len(base))
            ok = median(x[0] for x in dry) > median(x[0] for x in base)
            print(f"  z={z:+.2f} p={p:.4f} -> {verdict(ok, p)} "
                  f"(ожидание: сухие выше базы)")
            print(f"  размах/ATR: сухие med="
                  f"{median(x[0] / x[1] for x in dry):.3f} база med="
                  f"{median(x[0] / x[1] for x in base):.3f}")
            # контроль: сухие не должны быть просто низковолатильными
            print(f"  ATR якоря: сухие med="
                  f"{median(x[1] for x in dry):.3f}% база med="
                  f"{median(x[1] for x in base):.3f}%")
            # поимённо: pooled-эффект может держаться на одной монете
            print(f"  {'символ':>9} {'сухих':>6} {'мед.сух':>9} "
                  f"{'мед.база':>9} {'Δ':>8} {'сух/ATR':>8} {'база/ATR':>9}")
            plus = 0
            tot_sym = 0
            for sym in symbols:
                d_s, b_s = by_sym[sym]
                if len(d_s) < 20 or not b_s:
                    print(f"  {sym:>9} {len(d_s):>6}   мало данных")
                    continue
                md = median(x[0] for x in d_s)
                mb = median(x[0] for x in b_s)
                tot_sym += 1
                plus += 1 if md > mb else 0
                print(f"  {sym:>9} {len(d_s):>6} {md:>9.3f} {mb:>9.3f} "
                      f"{md - mb:>+8.3f} "
                      f"{median(x[0] / x[1] for x in d_s):>8.3f} "
                      f"{median(x[0] / x[1] for x in b_s):>9.3f}")
            print(f"    монет, где сухие дали размах выше своей базы: "
                  f"{plus} из {tot_sym}")


def main() -> None:
    print("Объёмные тезисы на архивах Binance Futures (data.binance.vision)")
    print(f"монеты: {', '.join(SYMBOLS)}")
    print(f"период: {START} … {END}; ТФ {', '.join(TFS)}; "
          f"горизонты {HORIZONS}")
    print(f"прогрев {WARMUP} свечей; якоря непересекающиеся (шаг = горизонт)")
    h1()
    h2()
    h3()
    h4()


if __name__ == "__main__":
    main()
