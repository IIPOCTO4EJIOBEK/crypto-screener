"""Исследование: ключ дедупликации сделок в измерении формаций.

Запуск из корня проекта:

    .venv/bin/python -m tools.dedupe_study
    .venv/bin/python -m tools.dedupe_study --tfs 5m 15m 1h --days 45

`walk()` помечал найденное как «уже видел» по одному `f.ts`, хотя ключ сделки
в том же модуле — `Trade.key` = (формация, направление, время). Разница видна
на данных: на одной свече срабатывает несколько детекторов сразу, а
`detect_all` отдаёт их отсортированными по уверенности. Дедуп по одному `ts`
оставлял самый уверенный разбор и молча терял остальные — в измерение
попадал отбор по уверенности детектора, а не формация как таковая.

Здесь один и тот же прогон считается двумя правилами и сравнивается:
сколько сделок у каждой формации и какой у неё средний R после издержек.
Ради этого исследования ключ и был исправлен; скрипт остаётся, чтобы правило
можно было перепроверить, а не верить на слово.

Правило `ts` оставлено в файле намеренно: без него сравнение воспроизвести
нечем.
"""

from __future__ import annotations

import argparse
import time
from collections import defaultdict
from datetime import date

from src.analysis.formations import detect_all
from src.backtest.costs import Costs, cost_r
from src.backtest.walk import HORIZON, STEP, Trade, simulate
from src.data import archive


def run_pair(symbol: str, tf: str, *, by_full_key: bool, days: int,
             costs: Costs) -> tuple[list[Trade], dict[tuple[str, str], int]]:
    """Один прогон по паре (монета, ТФ). Возвращает сделки и потери дедупа."""
    from src.backtest.expectancy import MAX_CANDLES, _months, window

    s, e = window(days)
    cs = archive.load_klines(symbol, tf, s, e).rows
    if len(cs) > MAX_CANDLES:
        cs = cs[-MAX_CANDLES:]
    if not cs:
        return [], {}
    btc = None
    if symbol != "BTCUSDT":
        btc = archive.load_klines("BTCUSDT", tf, s, e).rows
        btc = btc[-MAX_CANDLES:] if len(btc) > MAX_CANDLES else btc
    first = date.fromtimestamp(cs[0].ts / 1000)
    last_day = date.fromtimestamp(cs[-1].ts / 1000)
    series = archive.load_funding(symbol, *_months(first, last_day))

    step = {"5m": STEP, "15m": 12, "1h": 3}.get(tf, STEP)
    out: list[Trade] = []
    lost: dict[tuple[str, str], int] = defaultdict(int)
    seen: set = set()
    for i in range(max(300, 40), len(cs) - 1, step):
        try:
            found = detect_all(cs[:i], tf, symbol, "binance", btc=btc, limit=8)
        except Exception:
            continue
        for f in found:
            if not f.triggered:
                continue
            key = (f.kind, f.direction, f.ts) if by_full_key else f.ts
            if key in seen:
                if not by_full_key:
                    lost[(f.kind, f.tf)] += 1
                continue
            seen.add(key)
            tr = simulate(f, cs, i - 1, HORIZON)
            if tr is None:
                continue
            tr.cost_r = cost_r(tr.entry, tr.stop, tr.entry_ms, tr.exit_ms,
                               tr.side, costs, series.rows)
            tr.r_net = tr.r - tr.cost_r
            out.append(tr)
    return out, lost


def summary(trades: list[Trade]) -> dict[tuple[str, str], tuple[int, float]]:
    by: dict[tuple[str, str], list[Trade]] = defaultdict(list)
    for t in trades:
        by[(t.formation.kind, t.formation.tf)].append(t)
    return {k: (len(ts), sum(t.r_net for t in ts) / len(ts))
            for k, ts in by.items()}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols", nargs="+",
                   default=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    p.add_argument("--tfs", nargs="+", default=["1h"])
    p.add_argument("--days", type=int, default=45)
    a = p.parse_args()
    costs = Costs(taker_fee=0.0005, slippage=0.0001, funding=True)

    t0 = time.time()
    a_trades: list[Trade] = []
    b_trades: list[Trade] = []
    lost_all: dict[tuple[str, str], int] = defaultdict(int)
    for sym in a.symbols:
        for tf in a.tfs:
            one, lost = run_pair(sym, tf, by_full_key=False, days=a.days,
                                 costs=costs)
            two, _ = run_pair(sym, tf, by_full_key=True, days=a.days,
                              costs=costs)
            a_trades.extend(one)
            b_trades.extend(two)
            for k, n in lost.items():
                lost_all[k] += n
            print(f"{sym} {tf}: по ts {len(one)} сделок, "
                  f"по (формация, ts) {len(two)}")

    sa, sb = summary(a_trades), summary(b_trades)
    print(f"\nвсего времени {time.time() - t0:.0f} с")
    print(f"\n{'формация':22} {'ТФ':4} {'n по ts':>8} {'R net':>8} "
          f"{'n по ключу':>11} {'R net':>8} {'прибавка':>9}")
    for k in sorted(set(sa) | set(sb), key=lambda x: -sb.get(x, (0, 0))[0]):
        na, ra = sa.get(k, (0, 0.0))
        nb, rb = sb.get(k, (0, 0.0))
        print(f"{k[0]:22} {k[1]:4} {na:8} {ra:+8.3f} {nb:11} {rb:+8.3f} "
              f"{nb - na:+9}")
    na, nb = len(a_trades), len(b_trades)
    ra = sum(t.r_net for t in a_trades) / na if na else 0.0
    rb = sum(t.r_net for t in b_trades) / nb if nb else 0.0
    print(f"\nитого: по ts {na} сделок, средний R net {ra:+.3f}; "
          f"по ключу {nb} сделок, средний R net {rb:+.3f}")
    print("\nпотеряно детекций при дедупликации по ts:")
    for (kind, tf), n in sorted(lost_all.items(), key=lambda x: -x[1])[:12]:
        print(f"  {kind:22} {tf:4} {n:6}")


if __name__ == "__main__":
    main()
