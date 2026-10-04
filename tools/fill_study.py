"""Исследование: засчитывать ли сделку, до входа которой цена не дошла.

Запуск из корня проекта:

    .venv/bin/python -m tools.fill_study
    .venv/bin/python -m tools.fill_study --tfs 15m 1h --days 90

`trendline_bounce` ставит вход по цене линии, а сигнал требует, чтобы свеча
закрылась выше линии: к моменту сигнала цена от входа уже ушла. Старое
правило `simulate` открывало сделку по этой цене сразу, не проверяя, вернулась
ли к ней цена, и засчитывало цель сделке, которой на рынке не было. Тот же
вопрос касается любой формации, у которой вход не равен закрытию сигнальной
свечи.

Здесь один и тот же прогон считается двумя правилами и сравнивается:

  старое — вход сразу по formation.entry (require_fill=False);
  новое  — вход лимитным ордером, только если цена до него дошла
           (require_fill=True).

Издержки в обоих прогонах одинаковые — тейкерские. Лимитный вход на деле
платит мейкерскую комиссию, но сравнение должно показывать только разницу
в правиле исполнения, а не в двух параметрах сразу.

Старое правило оставлено в коде намеренно: без него сравнение воспроизвести
нечем, а прежние измерения не пересчитать.
"""

from __future__ import annotations

import argparse
import time
from collections import defaultdict

from src.backtest.costs import Costs
from src.backtest.expectancy import WINDOW_DAYS, measure
from src.backtest.walk import Trade

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT",
           "LINKUSDT", "AVAXUSDT"]


def summary(trades: list[Trade]
            ) -> dict[tuple[str, str], tuple[int, float, float]]:
    """(формация, ТФ) → (сделок, доля целей в %, средний R net)."""
    by: dict[tuple[str, str], list[Trade]] = defaultdict(list)
    for t in trades:
        by[(t.formation.kind, t.formation.tf)].append(t)
    return {k: (len(ts),
                100 * sum(t.outcome == "target" for t in ts) / len(ts),
                sum(t.r_net for t in ts) / len(ts))
            for k, ts in by.items()}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols", nargs="+", default=SYMBOLS)
    p.add_argument("--tfs", nargs="+", default=["15m", "1h"])
    p.add_argument("--days", type=int, default=WINDOW_DAYS)
    a = p.parse_args()
    costs = Costs()

    t0 = time.time()
    old: list[Trade] = []
    new: list[Trade] = []
    for sym in a.symbols:
        for tf in a.tfs:
            one, _, _ = measure(sym, tf, days=a.days, costs=costs)
            two, _, _ = measure(sym, tf, days=a.days, costs=costs,
                                require_fill=True)
            old.extend(one)
            new.extend(two)
            print(f"{sym} {tf}: старое правило {len(one)} сделок, "
                  f"новое {len(two)}", flush=True)

    so, sn = summary(old), summary(new)
    print(f"\nвсего времени {time.time() - t0:.0f} с")
    print(f"\n{'формация':22} {'ТФ':4} | {'n':>5} {'цель':>6} {'R net':>7} "
          f"| {'n':>5} {'цель':>6} {'R net':>7} | {'ΔR net':>7}")
    print(f"{'':27} | {'старое правило':^20} | {'с исполнением':^20} |")
    for k in sorted(set(so) | set(sn), key=lambda x: -so.get(x, (0,))[0]):
        no, wo, ro = so.get(k, (0, 0.0, 0.0))
        nn, wn, rn = sn.get(k, (0, 0.0, 0.0))
        print(f"{k[0]:22} {k[1]:4} | {no:5} {wo:5.1f}% {ro:+7.3f} "
              f"| {nn:5} {wn:5.1f}% {rn:+7.3f} | {rn - ro:+7.3f}")


if __name__ == "__main__":
    main()
