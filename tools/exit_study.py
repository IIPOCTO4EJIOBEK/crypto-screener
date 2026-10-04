"""Исследование: правила ведения позиции на архиве (тейки, безубыток, трейлинг).

Запуск из корня проекта (нужен доступ к data.binance.vision или локальный архив):

    .venv/bin/python -m tools.exit_study
    .venv/bin/python -m tools.exit_study --tfs 5m 15m 1h --days 45 --symbols BTCUSDT ETHUSDT

Сделки берутся те же, что в измерении формаций: `walk()` с честным входом
(`require_fill=True`, лимитный ордер по уровню). Каждая сделка затем
проводится заново разными правилами выхода — теми же функциями, что у живого
бота (`src/trade/intraday.check_exits`), только на свечах таймфрейма сигнала,
а не на минутных. Поэтому сравнение прямое: одна и та же выборка входов,
разные выходы.

Оговорки:

* Внутри свечи стоп проверяется раньше тейков, сдвиг стопа — только по
  закрытой свече. На свечах 1h это грубее, чем на минутных у живого бота:
  безубыток и трейлинг срабатывают позже, чем могли бы.
* Издержки — та же модель, что в измерении (`cost_r`): круговая комиссия,
  проскальзывание и фандинг. Частичный тейк не меняет оборот, поэтому
  издержки сделки одинаковы для всех правил.
* Выборка — те же монеты и окно, что в измерении; правило, выигравшее
  здесь, надо проверить на другом окне, прежде чем ему верить.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass, replace

from src.backtest.walk import HORIZON, Trade
from src.data.market import INTERVALS, Candle
from src.trade.intraday import Position, check_exits

DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT",
                   "DOGEUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT"]


@dataclass(frozen=True)
class Rule:
    name: str
    tp1_r: float = 0.0
    tp1_frac: float = 0.5
    be_after_tp1: bool = False
    breakeven_r: float = 0.0
    trail_r: float = 0.0
    trail_pct: float = 0.0
    no_target: bool = False


RULES = [
    Rule("цель формации (как сейчас)"),
    Rule("безубыток при +1R", breakeven_r=1.0),
    Rule("тейк 50% на 1R + безубыток", tp1_r=1.0, be_after_tp1=True),
    Rule("тейк 50% на 1R + безубыток + трейлинг 1.5R, без цели",
         tp1_r=1.0, be_after_tp1=True, trail_r=1.5, no_target=True),
    Rule("трейлинг 1.5R, без цели", trail_r=1.5, no_target=True),
    Rule("трейлинг 1R, без цели", trail_r=1.0, no_target=True),
    Rule("трейлинг 1R + цель формации", trail_r=1.0),
]


def replay(tr: Trade, candles: list[Candle], tf: str, rule: Rule,
           horizon: int = HORIZON) -> float:
    """Результат сделки в R (без издержек) при правиле выхода rule."""
    bar = INTERVALS[tf] * 1000
    idx = next((i for i, c in enumerate(candles) if c.ts == tr.entry_ms), None)
    if idx is None:
        return tr.r
    f = tr.formation
    risk = abs(tr.entry - tr.stop)
    p = Position(key="", symbol=f.symbol, kind=f.kind, title=f.kind, tf=tf,
                 side=tr.side, qty=1.0, entry=tr.entry, stop=tr.stop,
                 target=0.0 if rule.no_target else f.targets[0],
                 opened_ms=candles[idx].ts, expires_ms=candles[idx].ts + horizon * bar,
                 fee_in=0.0, signal_entry=tr.entry, risk0=risk,
                 breakeven_r=rule.breakeven_r, trail_r=rule.trail_r,
                 trail_pct=rule.trail_pct, tp1_r=rule.tp1_r, tp1_frac=rule.tp1_frac,
                 be_after_tp1=rule.be_after_tp1, best=tr.entry, qty0=1.0,
                 last_check_ms=candles[idx].ts)
    window = candles[idx: idx + horizon]
    end = window[-1].ts + bar
    sign = 1 if tr.side == "long" else -1
    got = 0.0
    for ex in check_exits(p, window, end, bar_ms=bar):
        price = window[-1].close if ex.reason == "timeout" else ex.price
        q = ex.qty or p.qty
        got += sign * (price - tr.entry) * q
        if not ex.qty:
            return got / risk
    return got / risk


def summarize(rows: dict[str, list[float]]) -> list[tuple]:
    out = []
    for name, rs in rows.items():
        if not rs:
            continue
        win = sum(x for x in rs if x > 0)
        loss = -sum(x for x in rs if x < 0)
        out.append((name, len(rs), sum(rs) / len(rs),
                    sum(1 for x in rs if x > 0) / len(rs),
                    win / loss if loss else float("inf")))
    return out


def study(pairs, rules=RULES) -> tuple[dict, dict]:
    """pairs — [(tf, candles, trades)]. Итог: правило → R net; (правило, формация) → R net."""
    total: dict[str, list[float]] = defaultdict(list)
    by_kind: dict[tuple[str, str], list[float]] = defaultdict(list)
    for tf, candles, trades in pairs:
        for tr in trades:
            for rule in rules:
                r = replay(tr, candles, tf, rule) - tr.cost_r
                total[rule.name].append(r)
                by_kind[(rule.name, f"{tr.formation.kind} {tf}")].append(r)
    return total, by_kind


def load_pairs(symbols, tfs, days):
    from datetime import date

    from src.backtest.costs import Costs
    from src.backtest.expectancy import MAX_CANDLES, _months, window
    from src.backtest.walk import walk
    from src.data import archive

    s, e = window(days)
    costs = Costs()
    for tf in tfs:
        btc = archive.load_klines("BTCUSDT", tf, s, e).rows[-MAX_CANDLES:]
        for sym in symbols:
            cs = archive.load_klines(sym, tf, s, e).rows[-MAX_CANDLES:]
            if not cs:
                continue
            first, last = (date.fromtimestamp(cs[0].ts / 1000),
                           date.fromtimestamp(cs[-1].ts / 1000))
            funding = archive.load_funding(sym, *_months(first, last))
            trades = walk(cs, tf, sym, "binance_futures", costs=costs,
                          funding=getattr(funding, "rows", funding),
                          btc=None if sym == "BTCUSDT" else btc, require_fill=True)
            print(f"  {sym} {tf}: свечей {len(cs)}, сделок {len(trades)}", flush=True)
            yield tf, cs, trades


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    ap.add_argument("--tfs", nargs="+", default=["5m", "15m", "1h"])
    ap.add_argument("--days", type=int, default=45)
    ap.add_argument("--min-n", type=int, default=30)
    a = ap.parse_args(argv)

    total, by_kind = study(list(load_pairs(a.symbols, a.tfs, a.days)))
    print("\nПравило выхода | сделок | средний R net | в плюсе | profit factor")
    for name, n, avg, wr, pf in sorted(summarize(total), key=lambda x: -x[2]):
        print(f"{name:55} {n:6} {avg:+.3f} {wr:6.1%} {pf:6.2f}")
    print(f"\nПо формациям (сделок от {a.min_n}), лучшее правило против текущего:")
    kinds = sorted({k for _, k in by_kind})
    base = RULES[0].name
    for k in kinds:
        rows = {r.name: by_kind[(r.name, k)] for r in RULES}
        if len(rows[base]) < a.min_n:
            continue
        stats = {n: avg for n, _, avg, _, _ in summarize(rows)}
        best = max(stats, key=stats.get)
        print(f"{k:28} n={len(rows[base]):4}  сейчас {stats[base]:+.3f}  "
              f"лучшее {stats[best]:+.3f} — {best}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
