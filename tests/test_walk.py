"""Тесты прогона формаций: какая свеча считается сигнальной и что измеряется.

Здесь закреплены две вещи, которые легко сломать молча.

Первая — граница окна. Детекторы зовутся через `_scan`, а тот отбрасывает
последнюю свечу среза: она ещё формируется, и решение по ней принималось бы
по незаконченным данным. Значит сигнальная свеча — предпоследняя (`i-2` при
возрасте 0), а сделка считается со следующей за ней. Если эту границу
сдвинуть, измерение начнёт считать результат по свече, которая входила в
срез детектора.

Вторая — ключ дедупликации. На одной свече срабатывает несколько детекторов
сразу, и `detect_all` отдаёт их отсортированными по уверенности. Дедуп по
одному `ts` оставлял самый уверенный разбор и терял остальные, то есть в
измерение попадал отбор по уверенности. Ключ обязан совпадать с `Trade.key`:
формация, направление, время.
"""

from __future__ import annotations

from src.analysis.formations import Formation, detect_all
from src.backtest.walk import simulate, walk
from src.data.market import Candle

STEP_MS = 300_000


def candle(ts: int, o: float, h: float, low: float, c: float,
           vol: float = 1.0) -> Candle:
    return Candle(ts, o, h, low, c, vol, vol * c, int(vol))


def series(n: int = 400, splash_at: int = 386, base: float = 100.0
           ) -> list[Candle]:
    """Ряд, на котором срабатывает сразу несколько детекторов.

    До всплеска — ровные свечи с одинаковым объёмом, затем свеча с объёмом
    в шесть раз больше и ходом +1 %: это всплеск объёма, пробой уровня и
    ретест одной и той же свечой.
    """
    out = []
    for k in range(n):
        ts = k * STEP_MS
        if k == splash_at:
            out.append(candle(ts, base, base * 1.011, base * 0.999,
                              base * 1.01, 60.0))
        elif k > splash_at:
            out.append(candle(ts, base * 1.01, base * 1.012, base * 1.008,
                              base * 1.01, 10.0))
        else:
            out.append(candle(ts, base, base * 1.002, base * 0.998, base, 10.0))
    return out


def _formation(entry: float, stop: float, target: float, ts: int = 0
               ) -> Formation:
    return Formation("test", "Тест", "long", "TESTUSDT", "binance", "5m",
                     ts, entry, entry, stop, [target], True, 0.5, [])


def test_ни_одна_формация_не_стоит_на_формирующейся_свече():
    """Последняя свеча списка ещё формируется — формации на ней быть не может."""
    cs = series(splash_at=398)
    found = detect_all(cs, "5m", "TESTUSDT", "binance", limit=50)
    assert found, "ряд обязан давать формации — иначе проверять нечего"
    for f in found:
        assert f.ts <= cs[-2].ts, \
            f"{f.kind}: формация поставлена на формирующуюся свечу"
    assert any(f.ts == cs[-2].ts for f in found), \
        "свежайшая формация обязана стоять ровно на последней закрытой свече"


def test_на_одной_свече_измеряются_все_сработавшие_формации():
    """Дедуп по одному ts терял разборы: на свече сработало трое, мерился один."""
    cs = series(splash_at=386)
    trades = walk(cs, "5m", "TESTUSDT", "binance")
    assert trades, "ряд обязан давать сделки — иначе проверять нечего"
    by_ts: dict[int, set[str]] = {}
    for t in trades:
        by_ts.setdefault(t.formation.ts, set()).add(t.formation.kind)
    most = max(len(v) for v in by_ts.values())
    assert most >= 2, (
        "на одной свече сработало несколько детекторов, а измерен только "
        f"один: {by_ts}")


def test_окно_сделки_начинается_со_свечи_после_сигнала():
    """Вход — свеча, которой детектор не видел, с учётом возраста события.

    Сигнал при возрасте 0 стоит на i-2, при возрасте 1 на i-3. Сделка
    начинается со следующей за сигналом свечи, то есть ровно на «возраст + 1»
    позже него. Если эту связь сдвинуть, результат начнёт считаться по свече
    из среза детектора.
    """
    cs = series(splash_at=386)
    idx = {c.ts: k for k, c in enumerate(cs)}
    trades = walk(cs, "5m", "TESTUSDT", "binance")
    assert trades, "ряд обязан давать сделки — иначе проверять нечего"
    for t in trades:
        sig = idx[t.formation.ts]
        assert idx[t.entry_ms] == sig + t.formation.age_candles + 1, \
            (f"{t.formation.kind}: вход на свече {idx[t.entry_ms]}, сигнал на "
             f"{sig}, возраст {t.formation.age_candles}")
    assert any(t.formation.age_candles > 0 for t in trades), \
        "в ряду обязана быть формация с возрастом — иначе связь не проверена"


def test_симуляция_не_видит_свечи_до_start():
    """Стоп, задень свеча до start, сделкой не считается: её ещё не было."""
    f = _formation(entry=100.0, stop=99.0, target=103.0)
    cs = [candle(0, 100.0, 100.0, 100.0, 100.0),
          candle(STEP_MS, 100.0, 100.5, 98.0, 100.0),      # стоп до окна
          candle(2 * STEP_MS, 100.0, 103.0, 99.5, 102.0),  # цель в окне
          candle(3 * STEP_MS, 102.0, 102.5, 101.0, 102.0)]
    tr = simulate(f, cs, 2, horizon=3)
    assert tr is not None and tr.outcome == "target", \
        "свеча до start не должна попадать в результат"

    early = simulate(f, cs, 1, horizon=3)
    assert early is not None and early.outcome == "stop", \
        "с окном от свечи со стопом результат обязан быть стопом"
