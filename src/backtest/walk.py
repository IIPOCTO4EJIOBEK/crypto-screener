"""Прогон формаций по истории: проверка, что детекторы вообще работают.

Это не проверка доходности стратегии. Смысл другой: детектор, который за
сотню исторических срезов не нашёл ни одной формации, — сломан, а не
«рынок спокойный». Поэтому первый вопрос к прогону: сколько раз каждая
формация сработала. Второй — чем заканчивался вход: дошла ли цена до цели
раньше стопа.

Честные оговорки, без которых числа ниже читаются неверно:

  Заглядывания в будущее нет: формация ищется на срезе candles[:i], а
  результат считается по свечам после i. Детектор не видит того, что
  случится после момента принятия решения.

  Комиссии, фандинг и проскальзывание не учтены. На 1m-15m они съедают
  заметную часть движения — реальный результат хуже показанного.

  Если свеча задела и стоп, и цель, засчитывается стоп. Без тиковых
  данных порядок внутри свечи неизвестен, и в такую неопределённость
  честнее записать убыток.

  Пороги детекторов подобраны не на этом же отрезке: они заданы априори и
  взяты из источников (docs/research/03-formacii-i-otrabotki.md), а не
  подогнаны под результат.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from src.analysis.formations import Formation, detect_all
from src.data.market import Candle

HISTORY = 300     # сколько свечей видит детектор в каждом срезе
STEP = 5          # через сколько свечей делается следующий срез
HORIZON = 40      # сколько свечей даётся сделке до истечения


@dataclass
class Trade:
    formation: Formation
    outcome: str    # "target" | "stop" | "timeout"
    r: float        # результат в единицах риска: −1 это стоп
    bars: int       # через сколько свечей закрылась

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.formation.kind, self.formation.direction,
                self.formation.ts)


def simulate(formation: Formation, candles: list[Candle], start: int,
             horizon: int = HORIZON) -> Trade | None:
    """Просчитать одну сделку по свечам после момента входа.

    start — индекс первой свечи, которая ещё не была известна детектору.
    """
    entry, stop = formation.entry, formation.stop
    if entry <= 0 or stop <= 0 or entry == stop:
        return None
    if not formation.targets:
        return None
    target = formation.targets[0]
    if target <= 0:
        return None
    long = formation.direction == "long"

    risk = abs(entry - stop)
    for k, c in enumerate(candles[start:start + horizon], start=1):
        if long:
            hit_stop = c.low <= stop
            hit_target = c.high >= target
        else:
            hit_stop = c.high >= stop
            hit_target = c.low <= target
        if hit_stop:      # стоп проверяется первым: см. оговорку выше
            return Trade(formation, "stop", -1.0, k)
        if hit_target:
            return Trade(formation, "target", abs(target - entry) / risk, k)

    last = candles[min(start + horizon, len(candles)) - 1]
    move = (last.close - entry) if long else (entry - last.close)
    return Trade(formation, "timeout", move / risk, horizon)


def walk(candles: list[Candle], tf: str, symbol: str, exchange: str,
         *, history: int = HISTORY, step: int = STEP,
         horizon: int = HORIZON, btc: list[Candle] | None = None,
         limit: int = 8) -> list[Trade]:
    """Пройти историю срезами и собрать сделки по найденным формациям."""
    trades: list[Trade] = []
    seen: set[int] = set()
    for i in range(max(history, 40), len(candles) - 1, step):
        sub = candles[:i]
        try:
            found = detect_all(sub, tf, symbol, exchange, btc=btc, limit=limit)
        except Exception:
            continue
        for f in found:
            if not f.triggered or f.ts in seen:
                continue
            # событие произошло на предпоследней свече среза: последнюю
            # детектор считает ещё формирующейся, торговать по ней нельзя
            tr = simulate(f, candles, i - 1, horizon)
            if tr is None:
                continue
            seen.add(f.ts)
            trades.append(tr)
    return trades


@dataclass
class Stats:
    kind: str
    tf: str
    n: int
    target: int
    stop: int
    timeout: int
    total_r: float

    @property
    def win_rate(self) -> float:
        return self.target / self.n * 100 if self.n else 0.0

    @property
    def expectancy(self) -> float:
        return self.total_r / self.n if self.n else 0.0


def rr_bucket(rr: float) -> str:
    if rr < 1.0:
        return "меньше 1"
    if rr < 1.5:
        return "1.0-1.4"
    if rr < 2.0:
        return "1.5-1.9"
    return "2.0 и выше"


def _build(groups: dict) -> list[Stats]:
    out = []
    for key, ts in groups.items():
        kind, tf = key if isinstance(key, tuple) else (key, "")
        out.append(Stats(kind, tf, len(ts),
                         sum(1 for t in ts if t.outcome == "target"),
                         sum(1 for t in ts if t.outcome == "stop"),
                         sum(1 for t in ts if t.outcome == "timeout"),
                         sum(t.r for t in ts)))
    out.sort(key=lambda s: -s.n)
    return out


def report(trades: list[Trade]):
    """Разрезы: по формациям, по таймфреймам, по направлению, по R:R."""
    by_kind: dict[tuple[str, str], list[Trade]] = defaultdict(list)
    by_tf: dict[str, list[Trade]] = defaultdict(list)
    by_dir: dict[str, list[Trade]] = defaultdict(list)
    by_rr: dict[str, list[Trade]] = defaultdict(list)
    for t in trades:
        by_kind[(t.formation.kind, t.formation.tf)].append(t)
        by_tf[t.formation.tf].append(t)
        by_dir[t.formation.direction].append(t)
        by_rr[rr_bucket(t.formation.rr)].append(t)
    return (_build(by_kind), _build(by_tf), _build(by_dir), _build(by_rr))


def format_report(trades: list[Trade], title: str = "") -> str:
    kinds, tfs, dirs, rrs = report(trades)
    lines = []
    if title:
        lines.append(f"=== {title} ===")
    lines.append(f"сделок всего: {len(trades)}")
    lines.append("")
    lines.append("формация                 ТФ     сделок  цель  стоп  таймаут  "
                 "доля цели  средний R  сумма R")
    for s in kinds:
        lines.append(f"{s.kind:23} {s.tf:5} {s.n:7} {s.target:6} "
                     f"{s.stop:5} {s.timeout:8} {s.win_rate:9.1f}% "
                     f"{s.expectancy:+9.3f} {s.total_r:+8.2f}")
    for head, group in (("таймфрейм", tfs), ("направление", dirs),
                        ("соотношение прибыль/риск", rrs)):
        lines.append("")
        lines.append(f"{head:26} сделок  цель  стоп  таймаут  доля цели  "
                     f"средний R  сумма R")
        for s in group:
            lines.append(f"{s.kind:26} {s.n:7} {s.target:6} {s.stop:5} "
                         f"{s.timeout:8} {s.win_rate:9.1f}% "
                         f"{s.expectancy:+9.3f} {s.total_r:+8.2f}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    import time

    from src.data.market import ohlcv

    symbols = sys.argv[1:] or ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
    tfs = ("5m", "15m", "1h")
    all_trades: list[Trade] = []
    t0 = time.time()

    for sym in symbols:
        for tf in tfs:
            try:
                cs = ohlcv("binance", sym, tf, 1000)
                btc = ohlcv("binance", "BTCUSDT", tf, 1000)
            except Exception as e:
                print(f"{sym} {tf}: ошибка загрузки {type(e).__name__}")
                continue
            t = time.time()
            trades = walk(cs, tf, sym, "binance", btc=btc)
            all_trades.extend(trades)
            print(f"{sym} {tf}: {len(trades)} сделок за {time.time()-t:.1f} с")
    print()
    print(format_report(all_trades, f"прогон по {len(symbols)} монетам"))
    print(f"\nвсего времени: {time.time()-t0:.1f} с")
