"""Прогон формаций с издержками: сколько остаётся после комиссии и фандинга.

Отличие от прогона в `src/backtest/walk.py` в одном: здесь у каждой сделки
считается результат за вычетом издержек (комиссия, проскальзывание, фандинг),
и в отчёте рядом стоят два числа — «средний R» и «средний R net». Разница
между ними и есть цена торговли в единицах риска.

Окно берётся из архива (`src/backtest/expectancy.py`), а не из живого API:
живой отдаёт максимум 1000 свечей, и на 5m это меньше четырёх суток — окно
целиком попадает в текущий месяц, за который ставок финансирования в архиве
ещё нет. На таком окне «net» не считается, и прогон выглядел бы как прогон с
нулевым фандингом.

Считается один прогон, а не два: результат в R от издержек не зависит, поэтому
и «грязный», и чистый итог берутся из одних и тех же сделок.
"""

from __future__ import annotations

import sys
import time

from src.backtest.costs import Costs
from src.backtest.expectancy import WINDOW_DAYS, table, window
from src.backtest.walk import format_report

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT",
           "DOGEUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT"]
TFS = ("5m", "15m", "1h")


def main() -> None:
    symbols = sys.argv[1:] or SYMBOLS
    costs = Costs(taker_fee=0.0004, slippage=0.0001, funding=True)
    start, end = window(WINDOW_DAYS)

    def progress(sym, tf, n, loaded, missed):
        print(f"{sym:9} {tf:4} сделок {n:5}  месяцев фандинга {loaded}"
              f" (+{missed} нет в архиве)", flush=True)

    t0 = time.time()
    print(f"окно {start} … {end} ({WINDOW_DAYS} суток, архив Binance), "
          f"монет {len(symbols)}, таймфреймов {len(TFS)}\n", flush=True)
    trades = table(symbols, TFS, costs=costs, progress=progress)
    print()
    print(format_report(trades, f"с издержками: {costs}"))
    print(f"\nсделок {len(trades)}, всего времени {time.time() - t0:.1f} с")


if __name__ == "__main__":
    main()
