"""Прогон формаций с издержками: сколько остаётся после комиссии и фандинга.

Отличие от `src/backtest/walk.py` в одном: здесь у каждой сделки считается
результат за вычетом издержек (комиссия, проскальзывание, фандинг), и в
отчёте рядом стоят два числа — «средний R» и «средний R net». Разница между
ними и есть цена торговли в единицах риска.

Порядок важен: сначала прогон без издержек, потом с ними, на тех же свечах
и тех же формациях. Иначе видно только итог и непонятно, откуда он взялся.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone

from src.backtest.costs import Costs
from src.backtest.walk import format_report, walk
from src.data import archive
from src.data.market import ohlcv

TFS = ("5m", "15m", "1h")


def _months(first_ms: int, last_ms: int) -> tuple[str, str]:
    a = datetime.fromtimestamp(first_ms / 1000, timezone.utc)
    b = datetime.fromtimestamp(last_ms / 1000, timezone.utc)
    return f"{a:%Y-%m}", f"{b:%Y-%m}"


def main() -> None:
    symbols = sys.argv[1:] or ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
    costs = Costs(taker_fee=0.0004, slippage=0.0001, funding=True)

    gross_all, net_all, gross_free = [], [], []
    t0 = time.time()
    for sym in symbols:
        for tf in TFS:
            try:
                cs = ohlcv("binance", sym, tf, 1000)
                btc = ohlcv("binance", "BTCUSDT", tf, 1000)
            except Exception as exc:                       # noqa: BLE001
                print(f"{sym} {tf}: свечи не загрузились — {type(exc).__name__}")
                continue

            # фандинг тянем по месяцам, покрывающим окно свечей; текущий
            # месяц в архиве ещё не выложен, поэтому его отсутствие — норма
            months = _months(cs[0].ts, cs[-1].ts)
            try:
                series = archive.load_funding(sym, *months)
                funding = series.rows
                missed = series.skipped
            except Exception as exc:                       # noqa: BLE001
                print(f"{sym} {tf}: фандинг не загрузился — {type(exc).__name__}")
                funding, missed = [], len(months)

            t = time.time()
            plain = walk(cs, tf, sym, "binance", btc=btc)
            paid = walk(cs, tf, sym, "binance", btc=btc, costs=costs,
                        funding=funding)
            gross_all.extend(plain)
            net_all.extend(paid)
            gross_free.extend(plain)
            # пропущенные месяцы — не мелочь: без них фандинг в net-R занижен,
            # поэтому их число идёт в тот же отчёт, что и результат
            print(f"{sym} {tf}: сделок {len(paid)}, "
                  f"фандинг-начислений {len(funding)}, "
                  f"месяцев не загружено {missed}, {time.time() - t:.1f} с",
                  flush=True)

    print()
    print(format_report(net_all, f"с издержками: {costs}"))
    print()
    print(format_report(gross_all, "без издержек (для сравнения)"))
    print(f"\nсделок {len(net_all)}, всего времени {time.time() - t0:.1f} с")


if __name__ == "__main__":
    main()
