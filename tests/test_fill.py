"""Тесты правила исполнения в simulate (require_fill).

Старое правило открывало сделку по formation.entry сразу, даже если цена до
этого уровня так и не дошла. У `trendline_bounce` вход стоит на линии, а
сигнал — закрытие выше неё, поэтому старое правило засчитывало цели сделкам,
которых на рынке не было. Новое правило — лимитный ордер: сделка есть, только
если цена коснулась входа. Старое остаётся по умолчанию, чтобы прежние
измерения воспроизводились.
"""

from __future__ import annotations

from src.analysis.formations import Formation
from src.backtest.walk import simulate, walk
from tests.test_lookahead import N, SEED, TF, channel

STEP_MS = 300_000


def candle(k: int, o: float, h: float, low: float, c: float):
    from src.data.market import Candle
    return Candle(k * STEP_MS, o, h, low, c, 1.0, c, 1)


def long_at(entry: float = 100.0, stop: float = 99.0, target: float = 102.0):
    return Formation("test", "Тест", "long", "TESTUSDT", "binance", "5m",
                     0, entry, entry, stop, [target], True, 0.5, [])


def short_at(entry: float = 100.0, stop: float = 101.0, target: float = 98.0):
    return Formation("test", "Тест", "short", "TESTUSDT", "binance", "5m",
                     0, entry, entry, stop, [target], True, 0.5, [])


# цена сразу уходит от входа 100 вверх к цели 102, ко входу не возвращаясь
RUNAWAY = [candle(0, 100.5, 100.8, 100.3, 100.6),
           candle(1, 100.6, 101.2, 100.4, 101.0),
           candle(2, 101.0, 102.3, 100.9, 102.1)]


def test_старое_правило_засчитывает_цель_без_исполнения():
    """Поведение по умолчанию не меняется — прежние числа воспроизводятся."""
    tr = simulate(long_at(), RUNAWAY, 0, horizon=3)
    assert tr is not None and tr.outcome == "target"


def test_цель_без_касания_входа_не_сделка():
    assert simulate(long_at(), RUNAWAY, 0, horizon=3,
                    require_fill=True) is None


def test_неисполненный_ордер_не_сделка():
    flat = [candle(k, 100.5, 100.7, 100.3, 100.5) for k in range(5)]
    assert simulate(long_at(), flat, 0, horizon=5, require_fill=True) is None


def test_сделка_открывается_на_свече_касания():
    cs = [candle(0, 100.5, 100.8, 100.3, 100.6),
          candle(1, 100.6, 100.7, 99.9, 100.2),    # касание входа
          candle(2, 100.2, 102.4, 100.1, 102.2)]   # цель
    tr = simulate(long_at(), cs, 0, horizon=3, require_fill=True)
    assert tr is not None and tr.outcome == "target"
    assert tr.entry_ms == cs[1].ts
    assert tr.bars == 3, "окно считается от start, ожидание входит в него"


def test_на_свече_исполнения_стоп_считается_а_цель_нет():
    stop_bar = [candle(0, 100.5, 100.6, 98.8, 99.0)]
    tr = simulate(long_at(), stop_bar, 0, horizon=1, require_fill=True)
    assert tr is not None and tr.outcome == "stop"

    wide = [candle(0, 100.5, 102.5, 99.5, 101.0),   # вход и цель в одной свече
            candle(1, 101.0, 101.2, 100.8, 101.0)]
    tr = simulate(long_at(), wide, 0, horizon=2, require_fill=True)
    assert tr is not None and tr.outcome == "timeout", \
        "цель на свече исполнения могла пройти до входа — её не засчитываем"


def test_шорт_исполняется_при_касании_сверху():
    cs = [candle(0, 99.5, 99.7, 99.2, 99.4),
          candle(1, 99.4, 100.1, 99.3, 99.8),      # касание входа 100
          candle(2, 99.8, 99.9, 97.8, 97.9)]       # цель 98
    tr = simulate(short_at(), cs, 0, horizon=3, require_fill=True)
    assert tr is not None and tr.outcome == "target"
    assert simulate(short_at(), cs[:1] + cs[2:], 0, horizon=2,
                    require_fill=True) is None


def test_прогон_с_исполнением_не_даёт_неисполненных_целей():
    """На канале старое правило засчитывает trendline_bounce цели без входа,
    новое — только сделки, где цена коснулась линии."""
    cs = channel(N, SEED)
    idx = {c.ts: k for k, c in enumerate(cs)}

    def touched(t) -> bool:
        f = t.formation
        start, end = idx[t.entry_ms], idx[t.exit_ms]
        return any((c.low <= f.entry) if f.direction == "long"
                   else (c.high >= f.entry) for c in cs[start:end + 1])

    old = [t for t in walk(cs, TF, "TESTUSDT", "binance", step=4)
           if t.formation.kind == "trendline_bounce"]
    new = [t for t in walk(cs, TF, "TESTUSDT", "binance", step=4,
                           require_fill=True)
           if t.formation.kind == "trendline_bounce"]
    assert any(t.outcome == "target" and not touched(t) for t in old), \
        "на канале старое правило обязано давать цели без входа"
    assert new, "с исполнением сделки тоже должны находиться"
    assert all(touched(t) for t in new)
