"""Сборщик живых данных: опрашивает публичные стаканы и свечи, пишет в sqlite.

Сеть берётся целиком из `src/data/market.py` — там уже решён маршрут по
биржам (перп Binance идёт напрямую, спот Binance через прокси, Bybit и OKX
напрямую) и есть повтор при обрыве. Здесь только расписание и запись.

По умолчанию собирается USDT-M перпетуал: там исполнение и ликвидность, и
там же считалось измерение. Спот (`--exchange binance`) остаётся отдельным
рядом — он нужен для базиса, а не для сигналов.

Запуск:

    tools/live/collect.py --once
    tools/live/collect.py --loop 60
    tools/live/collect.py --once --exchange bybit --symbols BTCUSDT,ETHUSDT

Ошибка на одной монете не роняет круг: монета пропускается с записью в
счётчик ошибок, остальные опрашиваются дальше. Так падение одного символа
(делистинг, лимит запросов) не оставляет дыру по всем остальным.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# tools/live/collect.py → корень проекта на два уровня выше.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data import market            # noqa: E402
from src.storage import db             # noqa: E402

# Разумный набор ликвидных монет: майоры плюс несколько альтов с плотным
# стаканом. Список нарочно короткий — круг должен укладываться в минуту,
# чтобы зазор между снимками не разрастался.
DEFAULT_SYMBOLS = (
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT",
    "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT",
)

# Таймфреймы, по которым тянем последние свечи. 1m даёт свежесть, старшие —
# контекст. На каждом круге запрашивается история, но новой оказывается лишь
# последняя свеча; остальные перезаписываются по первичному ключу.
DEFAULT_TIMEFRAMES = ("1m", "5m", "15m", "1h")

DEFAULT_CANDLE_LIMIT = 200
DEFAULT_DEPTH = 100
DEFAULT_INTERVAL = 60


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def collect_round(conn, symbols, exchange: str, timeframes, candle_limit: int,
                  depth: int, verbose: bool = True) -> dict:
    """Один круг: снимок стакана и свечи по каждой монете.

    Возвращает счётчики круга. Ничего не поднимает наружу — ошибки копятся
    в `errors`, чтобы круг доходил до конца при сбое отдельной монеты.
    """
    started = time.time()
    books_written = candles_written = 0
    errors: Counter = Counter()

    for symbol in symbols:
        try:
            ob = market.orderbook(exchange, symbol, depth)
            snap = db.BookSnapshot.from_orderbook(ob)
            books_written += db.insert_book_snapshots(conn, [snap])
        except Exception as e:
            errors[("стакан", symbol, type(e).__name__)] += 1
            if verbose:
                print(f"    ошибка стакана {symbol}: {type(e).__name__}: {str(e)[:90]}")
        for tf in timeframes:
            try:
                candles = market.ohlcv(exchange, symbol, tf, candle_limit)
                candles_written += db.insert_candles(conn, symbol, exchange, tf, candles)
            except Exception as e:
                errors[(f"свечи {tf}", symbol, type(e).__name__)] += 1
                if verbose:
                    print(f"    ошибка свечей {symbol} {tf}: "
                          f"{type(e).__name__}: {str(e)[:90]}")

    return {
        "symbols": len(symbols),
        "books": books_written,
        "candles": candles_written,
        "rows": books_written + candles_written,
        "errors": errors,
        "elapsed": time.time() - started,
    }


def _print_round(n: int, st: dict) -> None:
    total_err = sum(st["errors"].values())
    print(f"[{_now_iso()}] круг {n}: монет {st['symbols']}, "
          f"записано строк {st['rows']} "
          f"(снимков {st['books']}, свечей {st['candles']}), "
          f"ошибок {total_err}, {st['elapsed']:.1f} с")
    for (stage, symbol, exc), cnt in st["errors"].most_common():
        print(f"    {stage:10} {symbol:9} {exc} × {cnt}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Сбор живых рыночных данных в sqlite")
    ap.add_argument("--once", action="store_true", help="один круг и выход")
    ap.add_argument("--loop", type=float, nargs="?", const=DEFAULT_INTERVAL,
                    metavar="СЕК",
                    help=f"повторять круг каждые СЕК секунд; без числа — "
                         f"{DEFAULT_INTERVAL}; без флага — один круг")
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS),
                    help="список монет через запятую")
    ap.add_argument("--exchange", default="binance_futures",
                    choices=sorted(market.EXCHANGES),
                    help="биржа (по умолчанию binance_futures — перп)")
    ap.add_argument("--timeframes", default=",".join(DEFAULT_TIMEFRAMES),
                    help="таймфреймы свечей через запятую")
    ap.add_argument("--candle-limit", type=int, default=DEFAULT_CANDLE_LIMIT,
                    help=f"сколько свечей тянуть на таймфрейм (по умолчанию {DEFAULT_CANDLE_LIMIT})")
    ap.add_argument("--depth", type=int, default=DEFAULT_DEPTH,
                    help=f"глубина стакана (по умолчанию {DEFAULT_DEPTH})")
    ap.add_argument("--db", default=str(db.DEFAULT_DB_PATH),
                    help=f"путь к базе (по умолчанию {db.DEFAULT_DB_PATH})")
    ap.add_argument("-q", "--quiet", action="store_true", help="не печатать детали ошибок")
    args = ap.parse_args(argv)

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]
    for tf in timeframes:
        if tf not in market.INTERVALS:
            ap.error(f"неизвестный таймфрейм {tf}; доступны {', '.join(market.INTERVALS)}")

    if args.once and args.loop is not None:
        ap.error("--once и --loop вместе не имеют смысла: выбери одно")
    period = args.loop

    conn = db.connect(args.db)
    print(f"база: {args.db}")
    print(f"биржа: {args.exchange}, монет {len(symbols)}, таймфреймы {timeframes}, "
          f"стакан {args.depth}, свечей {args.candle_limit}")
    print(f"в базе до старта: снимков {db.count_book_snapshots(conn)}, "
          f"свечей {db.count_candles(conn)}")

    try:
        if period is None:
            if not args.once:
                print("флаг не задан — делаю один круг (как --once)")
            st = collect_round(conn, symbols, args.exchange, timeframes,
                               args.candle_limit, args.depth, verbose=not args.quiet)
            _print_round(1, st)
        else:
            n = 0
            while True:
                n += 1
                st = collect_round(conn, symbols, args.exchange, timeframes,
                                   args.candle_limit, args.depth, verbose=not args.quiet)
                _print_round(n, st)
                pause = max(0.0, period - st["elapsed"])
                time.sleep(pause)
    except KeyboardInterrupt:
        print("\nостановлено")
    finally:
        print(f"в базе теперь: снимков {db.count_book_snapshots(conn)}, "
              f"свечей {db.count_candles(conn)}")
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
