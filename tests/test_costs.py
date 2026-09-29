"""Тесты модели издержек.

Проверяется не «функция вернула число», а то, ради чего модель написана:
издержки в R зависят от расстояния до стопа, фандинг входит со знаком стороны,
а комиссия не может обнулиться фандингом в свою пользу.
"""

from __future__ import annotations

import pytest

from src.backtest.costs import Costs, cost_r, funding_between
from src.data.archive import FundingRate


def test_круговая_ставка_складывается_из_двух_сторон():
    c = Costs(taker_fee=0.0004, slippage=0.0001)
    assert c.per_side == 0.0005
    assert c.round_trip == 0.001


def test_возврат_комиссии_уменьшает_только_комиссию():
    """Кешбэк брокера касается комиссии, но не проскальзывания."""
    c = Costs(taker_fee=0.0004, slippage=0.0001, cashback=0.5)
    assert c.per_side == 0.0004 * 0.5 + 0.0001
    assert c.round_trip == 2 * c.per_side


def test_издержки_в_R_растут_при_близком_стопе():
    """Один и тот же тариф при стопе 0.1 % стоит вдесятеро дороже, чем при 1 %.

    Это главное свойство модели: средний R без издержек несравним между
    формациями с разными стопами. Круговая ставка 0.001 от цены 100 — это
    0.1 в деньгах, и она делится на риск сделки: стоп 0.1 → 1 R, стоп 1 → 0.1 R.
    """
    c = Costs(taker_fee=0.0004, slippage=0.0001)
    near = cost_r(100.0, 99.9, 0, 1, "long", c)      # стоп 0.1 %
    far = cost_r(100.0, 99.0, 0, 1, "long", c)       # стоп 1 %
    assert near == pytest.approx(1.0)
    assert far == pytest.approx(0.1)
    assert near == pytest.approx(10 * far)


def test_фандинг_входит_только_в_своё_окно():
    """Начисление ровно на входе не берётся, на выходе — берётся."""
    s = [FundingRate(100, 8, 0.001), FundingRate(200, 8, 0.002),
         FundingRate(300, 8, 0.003)]
    assert funding_between(s, 100, 200) == 0.002
    assert funding_between(s, 100, 300) == 0.002 + 0.003
    assert funding_between(s, 50, 99) == 0.0


def test_бинарный_поиск_даёт_тот_же_ответ_что_и_перебор():
    """Ускорение не имеет права сдвинуть числа.

    Сверяется с наивным перебором на случайных окнах: границы окна берутся
    равными временам начислений, чтобы проверка попала ровно в те случаи, где
    легко ошибиться на единицу («строго после входа» против «не раньше»).
    """
    import random

    rng = random.Random(4)
    times = sorted(rng.sample(range(0, 100_000), 300))
    s = [FundingRate(t, 8, rng.uniform(-0.001, 0.001)) for t in times]

    def naive(a: int, b: int) -> float:
        return sum(e.last_funding_rate for e in s if a < e.calc_time <= b)

    probes = [0, times[0], times[0] + 1, times[150], times[-1] - 1, times[-1],
              100_000]
    for a in probes:
        for b in probes:
            assert funding_between(s, a, b) == pytest.approx(naive(a, b))


def test_пустое_расписание_и_окно_без_начислений():
    assert funding_between([], 0, 1000) == 0.0
    s = [FundingRate(100, 8, 0.001)]
    assert funding_between(s, 100, 100) == 0.0     # окно нулевой длины
    assert funding_between(s, 500, 900) == 0.0


def test_положительная_ставка_лонгу_в_минус_а_шорту_в_плюс():
    c = Costs(taker_fee=0.0, slippage=0.0, funding=True)
    s = [FundingRate(200, 8, 0.001)]
    long_cost = cost_r(100.0, 99.0, 0, 1000, "long", c, s)
    short_cost = cost_r(100.0, 99.0, 0, 1000, "short", c, s)
    assert long_cost == 0.1        # 0.001 × 100 / 1
    assert short_cost == 0.0       # шорт получает, издержек нет


def test_издержки_не_становятся_отрицательными():
    """Ставка в пользу позиции больше комиссии — издержки всё равно не меньше нуля.

    Положительная ставка означает, что лонг платит шорту. При ставке 1 %
    шорт получает больше, чем отдаёт на комиссии, и без нижней границы его
    сделка выглядела бы дешевле бесплатной.
    """
    c = Costs(taker_fee=0.0004, slippage=0.0001, funding=True)
    s = [FundingRate(200, 8, 0.01)]
    assert cost_r(100.0, 99.0, 0, 1000, "short", c, s) == 0.0
    assert cost_r(100.0, 99.0, 0, 1000, "long", c, s) > 0.0


def test_нулевой_риск_не_делит_на_ноль():
    c = Costs()
    assert cost_r(100.0, 100.0, 0, 1, "long", c) == 0.0
    assert cost_r(0.0, 0.0, 0, 1, "long", c) == 0.0


def test_без_фандинга_расписание_не_читается():
    """Флаг выключает фандинг целиком, даже если расписание передали."""
    c = Costs(taker_fee=0.0004, slippage=0.0001, funding=False)
    s = [FundingRate(200, 8, 0.01)]
    assert cost_r(100.0, 99.0, 0, 1000, "long", c, s) == 0.1
