"""Измерение ожидаемости формаций на архиве и запись в базу.

Скринер ранжирует сегодняшние сигналы по тому, сколько такие формации давали
на прошлом окне. Измерение считается минутами, а скринеру число нужно сразу
и при каждом запуске, поэтому считается оно здесь, отдельным шагом, и ложится
в таблицу `formation_stats`. Скринер читает готовое и не считает сам.

Измерение идёт по архиву Binance (`src/backtest/expectancy.py`), а не по тому,
что накопилось в базе: в базе на старте нет ничего, а вывод о формации нужен
сразу. База — это про «сейчас», архив — про «как было».
"""

from __future__ import annotations

import argparse
from datetime import date

from src.backtest import significance
from src.backtest.costs import Costs
from src.backtest.expectancy import MIN_TRADES, WINDOW_DAYS, table, window
from src.backtest.walk import report
from src.storage import db

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT",
           "DOGEUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT"]
TFS = ("5m", "15m", "1h")


def measure(symbols: list[str], tfs: tuple[str, ...], *, days: int,
            costs: Costs, conn, step: int | None = None,
            progress=None, require_fill: bool = False) -> int:
    """Прогнать архив и записать измерение. Возвращает число строк.

    require_fill — правило входа (см. walk.simulate): сделка есть, только
    если цена дошла до входа. По умолчанию старое правило.
    """
    trades = table(list(symbols), tfs, days=days, costs=costs, step=step,
                   progress=progress, require_fill=require_fill)
    # report() даёт разрезы; первый — по (формация, ТФ), это и есть ключ строки
    stats = report(trades)[0]
    scope = ",".join(symbols)
    today = date.today().isoformat()
    rows = [dict(kind=s.kind, tf=s.tf, measured_on=today, symbol_scope=scope,
                 n=s.n, win_rate=round(s.win_rate, 2),
                 exp_gross=round(s.expectancy, 4),
                 exp_net=round(s.expectancy_net, 4),
                 cost=round(s.cost, 4),
                 sd=round(s.sd, 4), se=round(s.se, 6), n_eff=s.n_eff,
                 p_boot=s.p_boot) for s in stats]
    written = db.upsert_formation_stats(conn, rows, tfs)
    return written


def _print_rows(rows: list[dict]) -> None:
    print(f"\n{'формация':24} {'ТФ':4} {'сделок':>7} {'n_eff':>6} {'цель %':>7} "
          f"{'R':>8} {'издержки':>9} {'R net':>8} {'p':>7} {'p бутстр':>8} "
          f"{'значимо':>8}")
    order = sorted(rows, key=lambda r: -r["exp_net"])
    # Один вызов на таблицу: метки строк и итог обязаны считаться по одному
    # набору сравнений, иначе итог говорит «значимых 11», а строки — другое.
    v = significance.judge(order, min_n=MIN_TRADES)
    for r, ok in zip(order, v.flags):
        p = _p(r)
        mark = "—" if ok is None else ("да" if ok else "нет")
        pb = r.get("p_boot")
        print(f"{r['kind']:24} {r['tf']:4} {r['n']:7} {r.get('n_eff') or 0:6} "
              f"{r['win_rate']:7.1f} "
              f"{r['exp_gross']:+8.3f} {r['cost']:9.3f} {r['exp_net']:+8.3f} "
              f"{('—' if p is None else f'{p:7.4f}'):>7} "
              f"{('—' if pb is None else f'{pb:8.4f}'):>8} {mark:>8}")
    n_sig_plus = sum(1 for r, ok in zip(order, v.flags)
                     if ok and r["exp_net"] > 0)
    print(f"\nсравнений {v.n_tested}, из них с плюсом {v.n_positive}; "
          f"после поправки на перебор (FDR {v.alpha:g}) значимых "
          f"{v.n_significant} — из них с плюсом {n_sig_plus}.")
    print(f"«—» в столбце значимости — строка в счёт не вошла: сделок меньше "
          f"{MIN_TRADES} или нет разброса R по сделкам.")
    print("p учитывает перекрытие сделок: ошибка среднего — по кластерам "
          "пересекающихся сделок, степеней свободы n_eff − 1.")
    if v.n_naive:
        print(f"ВНИМАНИЕ: у {v.n_naive} строк нет n_eff (измерены до правки) — "
              f"их p посчитаны как для независимых сделок и занижены.")


def _p(row: dict) -> float | None:
    """p-value среднего R строки с поправкой на перекрытие сделок; None, если
    считать не из чего."""
    return significance.row_p(row)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default=None, help="путь к базе (по умолчанию data/screener.db)")
    p.add_argument("--days", type=int, default=WINDOW_DAYS, help="длина окна, суток")
    p.add_argument("--step", type=int, default=None,
                   help="шаг между срезами в свечах (по умолчанию — из "
                        "STEP_BY_TF, около часа времени между срезами)")
    p.add_argument("--symbols", nargs="+", default=SYMBOLS)
    p.add_argument("--tfs", nargs="+", default=list(TFS))
    p.add_argument("--fee", type=float, default=Costs().taker_fee,
                   help="комиссия за сторону; по умолчанию — из модели издержек")
    p.add_argument("--slippage", type=float, default=Costs().slippage,
                   help="проскальзывание за сторону")
    p.add_argument("--no-funding", action="store_true", help="не учитывать фандинг")
    p.add_argument("--require-fill", action="store_true",
                   help="засчитывать сделку, только если цена дошла до входа "
                        "(docs/research/20)")
    a = p.parse_args()

    costs = Costs(taker_fee=a.fee, slippage=a.slippage,
                  funding=not a.no_funding)
    start, end = window(a.days)
    print(f"окно {start} … {end} ({a.days} суток), монет {len(a.symbols)}, "
          f"таймфреймов {len(a.tfs)}\n")

    def progress(sym, tf, n, loaded, missed):
        print(f"  {sym:9} {tf:4} сделок {n:5}  месяцев фандинга {loaded}"
              f" (+{missed} нет в архиве)", flush=True)

    conn = db.connect(a.db)
    written = measure(a.symbols, tuple(a.tfs), days=a.days, costs=costs,
                      conn=conn, step=a.step, progress=progress,
                      require_fill=a.require_fill)
    _print_rows([dict(r) for r in db.load_formation_stats(conn).values()])
    print(f"\nзаписано строк: {written} → {a.db or db.DEFAULT_DB_PATH}")


if __name__ == "__main__":
    main()
