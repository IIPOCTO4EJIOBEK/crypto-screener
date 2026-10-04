"""Тест на заглядывание в будущее у `trendline_bounce` (docs/research/19, §2).

Внешний разбор назвал главным подозрением look-ahead: трендовая линия могла
строиться по экстремумам, подтверждённым свечами справа, то есть в момент
«касания» детектор знал бы правую часть графика. Предложенная проверка —
прогнать детектор на ряду, усечённом в момент T, и на полном ряду, и сравнить
сигналы до T. Если хоть один изменился, линия перерисовывается будущим.

Проверка здесь сделана жёстче, чем «усечённый против полного»: будущее после
T не отрезается, а подменяется — у второго ряда после T обвал, у которого с
первым нет ничего общего. Прогоны идут через `walk`, как в измерении, поэтому
проверяется вся цепочка: срез, `_scan`, детектор, `find_trend_lines`,
`find_pivots` и симуляция сделки. Сигналы и сделки, закрытые до T, обязаны
совпасть до последнего поля.

Последний тест — проверка самого теста: детектор с утечкой в будущее на
десять свечей обязан быть пойман. Без неё зелёный прогон ничего не значил бы.
"""

from __future__ import annotations

import math
import random

import pytest

from src.analysis import formations
from src.backtest import walk as walk_mod
from src.backtest.walk import walk
from src.data.market import Candle

STEP_MS = 900_000   # 15m
N = 780
CUTS = (520, 700)   # моменты T; у обоих до T десятки сигналов trendline_bounce
SEED = 0
TF = "15m"


def market(n: int, seed: int, start: int = 0, price: float = 100.0,
           drift: float | None = None, vol: float = 0.004) -> list[Candle]:
    """Случайный ряд с медленной волной тренда — на нём строятся наклонки.

    drift=None — синусоида тренда; число — постоянный снос за свечу.
    """
    r = random.Random(seed)
    out: list[Candle] = []
    for k in range(start, start + n):
        d = 0.0006 * math.sin(k / 90) if drift is None else drift
        o = price
        c = o * (1 + d + r.gauss(0, vol))
        h = max(o, c) * (1 + abs(r.gauss(0, vol / 2)))
        low = min(o, c) * (1 - abs(r.gauss(0, vol / 2)))
        v = 10 * (1 + abs(r.gauss(0, 0.5)))
        out.append(Candle(k * STEP_MS, o, h, low, c, v, v * c, 100))
        price = c
    return out


def channel(n: int, seed: int, period: int = 100, amp: float = 0.03,
            slope: float = 0.0004, noise: float = 0.0008) -> list[Candle]:
    """Восходящий канал с шумом: цена раз в period свечей касается линии.

    На случайном ряду через `walk` до T набирается один-два сигнала — мало,
    чтобы сравнение что-то значило. В канале наклонки находятся регулярно и
    в обе стороны: отскоки от нижней линии и от верхней.
    """
    r = random.Random(seed)
    out: list[Candle] = []
    prev = None
    for k in range(n):
        line = 100 * (1 + slope * k)
        mid = line * (1 + amp * (1 - math.cos(2 * math.pi * k / period)) / 2)
        c = mid * (1 + r.gauss(0, noise))
        o = prev if prev is not None else c
        h = max(o, c) * (1 + abs(r.gauss(0, noise / 2)))
        low = max(min(o, c) * (1 - abs(r.gauss(0, noise / 2))),
                  line * (1 - 0.0005))
        v = 10 * (1 + abs(r.gauss(0, 0.5)))
        out.append(Candle(k * STEP_MS, o, h, low, c, v, v * c, 100))
        prev = c
    return out


def forked(cut: int) -> tuple[list[Candle], list[Candle]]:
    """Два ряда с общим прошлым до cut и разным будущим после."""
    base = channel(N, SEED)
    crash = market(N - cut, seed=999, start=cut, price=base[cut - 1].close,
                   drift=-0.003, vol=0.01)
    return base, base[:cut] + crash


def _bounce_signals(trades, before_ms: int):
    """Сигналы trendline_bounce до момента before_ms — все поля разбора."""
    return sorted(
        (t.formation.direction, t.formation.ts, t.formation.age_candles,
         t.formation.entry, t.formation.stop, tuple(t.formation.targets),
         t.formation.confidence, t.entry_ms)
        for t in trades
        if t.formation.kind == "trendline_bounce" and t.entry_ms < before_ms)


def _closed_trades(trades, before_ms: int):
    """Сделки, закрытые до before_ms: их исход будущее менять не может."""
    return sorted(
        (t.formation.kind, t.formation.direction, t.formation.ts, t.outcome,
         t.r, t.bars)
        for t in trades if t.exit_ms < before_ms)


def _walk(cs: list[Candle]):
    return walk(cs, TF, "TESTUSDT", "binance", step=4)


@pytest.fixture(scope="module")
def runs():
    """Прогоны по каждому T: (реальное будущее, подменённое будущее, усечённый)."""
    real = channel(N, SEED)
    real_trades = _walk(real)
    out = {}
    for cut in CUTS:
        _, fake = forked(cut)
        out[cut] = (real_trades, _walk(fake), _walk(real[:cut]), real)
    return out


@pytest.mark.parametrize("cut", CUTS)
def test_сигналы_до_T_не_зависят_от_будущего(runs, cut):
    real_trades, fake_trades, cut_trades, real = runs[cut]
    t_ms = real[cut].ts
    real_sig = _bounce_signals(real_trades, t_ms)
    assert len(real_sig) >= 10, \
        "ряд обязан давать trendline_bounce до T — иначе проверять нечего"
    assert _bounce_signals(fake_trades, t_ms) == real_sig, \
        "сигнал до T изменился, когда изменилось будущее после T"


@pytest.mark.parametrize("cut", CUTS)
def test_усечённый_ряд_даёт_те_же_сигналы(runs, cut):
    """Буквальная проверка из разбора: ряд до T против полного ряда."""
    real_trades, _, cut_trades, real = runs[cut]
    t_ms = real[cut].ts
    # Усечённый прогон не делает срез на последней свече ряда, поэтому
    # сравниваем сигналы с входом раньше предпоследней свечи до T.
    edge = real[cut - 2].ts
    assert _bounce_signals(cut_trades, edge) == \
        _bounce_signals(real_trades, edge)
    assert _bounce_signals(real_trades, t_ms), "до T нет сигналов"


@pytest.mark.parametrize("cut", CUTS)
def test_исход_сделок_до_T_не_зависит_от_будущего(runs, cut):
    real_trades, fake_trades, _, real = runs[cut]
    t_ms = real[cut].ts
    closed = _closed_trades(real_trades, t_ms)
    assert closed, "до T нет закрытых сделок — проверять нечего"
    assert _closed_trades(fake_trades, t_ms) == closed


def test_проверка_ловит_перерисовку_линий(monkeypatch):
    """Если линии строятся по всему ряду, тест обязан это поймать.

    Утечка смоделирована так, как её описывал разбор: экстремумы для линии
    берутся с правой части графика, которой в момент касания ещё не было, —
    линия «перерисовывается» новыми экстремумами.
    """
    cut = CUTS[0]
    real, fake = forked(cut)
    honest = formations.find_trend_lines

    def run(cs):
        def repainting(candles, tf, **kw):
            return honest(cs, tf, **kw)
        monkeypatch.setattr(formations, "find_trend_lines", repainting)
        return _walk(cs)

    t_ms = real[cut].ts
    assert _bounce_signals(run(real), t_ms) != \
        _bounce_signals(run(fake), t_ms), \
        "перерисовка линий будущими экстремумами прошла незамеченной"


def test_walk_отдаёт_детектору_только_прошлое(monkeypatch):
    """Ни один срез не содержит свечей позже момента сделки."""
    cs = market(400, SEED)
    seen_len: list[int] = []
    real_detect_all = walk_mod.detect_all

    def spy(sub, *a, **kw):
        seen_len.append(len(sub))
        return real_detect_all(sub, *a, **kw)

    monkeypatch.setattr(walk_mod, "detect_all", spy)
    trades = walk(cs, TF, "TESTUSDT", "binance", step=3)
    idx = {c.ts: k for k, c in enumerate(cs)}
    assert seen_len and max(seen_len) < len(cs)
    for t in trades:
        # детектор видел candles[:i], сделка стартует на i-1 и позже
        assert idx[t.entry_ms] >= idx[t.formation.ts] + 1
