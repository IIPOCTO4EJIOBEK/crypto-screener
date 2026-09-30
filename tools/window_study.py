"""Исследование: с какой свечи измерение формаций считает сделку.

Запуск из корня проекта:

    .venv/bin/python -m tools.window_study
    .venv/bin/python -m tools.window_study --tfs 1h --days 45

Граница окна устроена так: детекторы зовутся через `_scan`, а тот отбрасывает
последнюю свечу среза (`end = len(candles) - 1 - age`) — она ещё формируется.
Значит для среза `candles[:i]` сигнальная свеча стоит на `i-2-age`, а `walk()`
начинает сделку с `i-1`:

    age 0 — ровно со следующей свечи после сигнала: верно;
    age 1 — через одну свечу: одна пропущена;
    age 2 — через две: пропущено две.

Цена входа при этом — закрытие сигнальной свечи, то есть при age > 0 она
на 1-2 свечи устарела. Направление смещения неочевидно: пропущенные свечи
могли содержать и стоп (тогда текущие числа оптимистичны), и цель (тогда
пессимистичны), а стоп в модели проверяется первым.

Считаются одни и те же найденные формации в трёх моделях:

    как_сейчас    — start = i-1 (то, что делает walk);
    без_пропуска  — start = сигнал + 1 (первая свеча после сигнала);
    вход_по_рынку — как «как_сейчас», но вход переставлен на закрытие свечи
                    i-2: цена в момент решения сегодня, стоп и цель прежние,
                    риск пересчитан от новой цены.

Разрез по возрасту: сколько сделок затронуто и насколько меняется R.
"""

from __future__ import annotations

import argparse
import time
from collections import defaultdict
from datetime import date

from src.analysis.formations import Formation, detect_all
from src.backtest.costs import Costs, cost_r
from src.backtest.walk import HORIZON, STEP, Trade, simulate
from src.data import archive

MODES = ("как_сейчас", "без_пропуска", "вход_по_рынку")


def _reentry(f: Formation, candles, start: int, horizon: int) -> Trade | None:
    """Сделка с цены в момент решения: вход — закрытие свечи start-1.

    Если цена уже за стопом или за целью, входить некуда — сделки нет.
    """
    if start <= 0 or start >= len(candles):
        return None
    entry = candles[start - 1].close
    stop, target = f.stop, (f.targets[0] if f.targets else 0.0)
    if entry <= 0 or stop <= 0 or target <= 0 or entry == stop:
        return None
    long = f.direction == "long"
    if (long and (entry <= stop or entry >= target)) or \
       (not long and (entry >= stop or entry <= target)):
        return None
    again = Formation(f.kind, f.title, f.direction, f.symbol, f.exchange,
                      f.tf, f.ts, entry, entry, stop, list(f.targets),
                      f.triggered, f.confidence, list(f.reasons), f.invalid,
                      f.style, f.age_candles)
    return simulate(again, candles, start, horizon)


def run_pair(symbol: str, tf: str, *, days: int, costs: Costs):
    from src.backtest.expectancy import MAX_CANDLES, _months, window

    s, e = window(days)
    cs = archive.load_klines(symbol, tf, s, e).rows
    if len(cs) > MAX_CANDLES:
        cs = cs[-MAX_CANDLES:]
    if not cs:
        return {}, {}
    btc = None
    if symbol != "BTCUSDT":
        btc = archive.load_klines("BTCUSDT", tf, s, e).rows
        btc = btc[-MAX_CANDLES:] if len(btc) > MAX_CANDLES else btc
    first = date.fromtimestamp(cs[0].ts / 1000)
    last_day = date.fromtimestamp(cs[-1].ts / 1000)
    series = archive.load_funding(symbol, *_months(first, last_day))

    step = {"5m": STEP, "15m": 12, "1h": 3}.get(tf, STEP)
    out: dict[str, list[Trade]] = {m: [] for m in MODES}
    ages: dict[str, list[int]] = {m: [] for m in MODES}
    seen: set[tuple[str, str, int]] = set()
    for i in range(max(300, 40), len(cs) - 1, step):
        try:
            found = detect_all(cs[:i], tf, symbol, "binance", btc=btc, limit=8)
        except Exception:
            continue
        for f in found:
            key = (f.kind, f.direction, f.ts)
            if not f.triggered or key in seen:
                continue
            seen.add(key)
            sig = i - 2 - f.age_candles          # свеча сигнала у детектора
            starts = {"как_сейчас": i - 1,
                      "без_пропуска": sig + 1,
                      "вход_по_рынку": i - 1}
            for m in MODES:
                st = starts[m]
                tr = (_reentry(f, cs, st, HORIZON) if m == "вход_по_рынку"
                      else simulate(f, cs, st, HORIZON))
                if tr is None:
                    continue
                tr.cost_r = cost_r(tr.entry, tr.stop, tr.entry_ms, tr.exit_ms,
                                   tr.side, costs, series.rows)
                tr.r_net = tr.r - tr.cost_r
                out[m].append(tr)
                ages[m].append(f.age_candles)
    return out, ages


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols", nargs="+",
                   default=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    p.add_argument("--tfs", nargs="+", default=["5m", "15m", "1h"])
    p.add_argument("--days", type=int, default=45)
    a = p.parse_args()
    costs = Costs(taker_fee=0.0005, slippage=0.0001, funding=True)

    allm: dict[str, list[Trade]] = {m: [] for m in MODES}
    allages: dict[str, list[int]] = {m: [] for m in MODES}
    t0 = time.time()
    for sym in a.symbols:
        for tf in a.tfs:
            out, ages = run_pair(sym, tf, days=a.days, costs=costs)
            if not out:
                print(f"{sym} {tf}: свечей нет, пропуск")
                continue
            for m in MODES:
                allm[m].extend(out[m])
                allages[m].extend(ages[m])
            print(f"{sym} {tf}: " +
                  ", ".join(f"{m} {len(out[m])}" for m in MODES))
    print(f"\nвсего времени {time.time() - t0:.0f} с\n")

    for m in MODES:
        trades, ages = allm[m], allages[m]
        if not trades:
            continue
        print(f"=== модель {m} ===  сделок {len(trades)}")
        print(f"{'возр':>4} {'доля %':>7} {'R':>8} {'R net':>8} {'стоп %':>7}")
        for age in (0, 1, 2):
            sub = [t for t, x in zip(trades, ages) if x == age]
            if not sub:
                continue
            gross = sum(t.r for t in sub) / len(sub)
            net = sum(t.r_net for t in sub) / len(sub)
            stop = sum(1 for t in sub if t.outcome == "stop") / len(sub) * 100
            print(f"{age:4} {len(sub) / len(trades) * 100:7.1f} {gross:+8.3f} "
                  f"{net:+8.3f} {stop:7.1f}")
        gross = sum(t.r for t in trades) / len(trades)
        net = sum(t.r_net for t in trades) / len(trades)
        print(f"  итого: R {gross:+.3f}, R net {net:+.3f}\n")

    print("=== разница «без_пропуска» минус «как_сейчас», по возрасту ===")
    print(f"{'возр':>4} {'сделок':>7} {'Δ R':>8} {'Δ R net':>9} "
          f"{'Δ стоп, п.п.':>13}")
    for age in (0, 1, 2):
        cur = [t for t, x in zip(allm["как_сейчас"], allages["как_сейчас"])
               if x == age]
        new = [t for t, x in zip(allm["без_пропуска"], allages["без_пропуска"])
               if x == age]
        if not cur or not new:
            continue
        d_r = (sum(t.r for t in new) / len(new)
               - sum(t.r for t in cur) / len(cur))
        d_net = (sum(t.r_net for t in new) / len(new)
                 - sum(t.r_net for t in cur) / len(cur))
        d_stop = (sum(1 for t in new if t.outcome == "stop") / len(new)
                  - sum(1 for t in cur if t.outcome == "stop") / len(cur)) * 100
        print(f"{age:4} {len(cur):7} {d_r:+8.3f} {d_net:+9.3f} {d_stop:+13.1f}")


if __name__ == "__main__":
    main()
