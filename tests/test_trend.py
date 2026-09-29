"""Тесты тренд-фильтра на дневных барах.

Проверяется то, что при поломке даёт не ошибку, а правдоподобное число:
заглядывание в будущее, стыковка интервалов, оборот портфеля и учёт
фандинга. Каждое из этого легко теряется при правке и не видно глазом —
доходность просто становится другой.
"""

from __future__ import annotations

import math

from src.backtest.costs import Costs
from src.backtest.trend import DAYS_PER_YEAR, Panel, Params, run
from src.data.archive import FundingRate
from src.data.market import Candle

DAY = 86_400_000
START = 1_600_000_000_000 - (1_600_000_000_000 % DAY)


def bar(day: int, open_: float, close: float) -> Candle:
    return Candle(ts=START + day * DAY, open=open_, high=max(open_, close),
                  low=min(open_, close), close=close, volume=1.0,
                  quote_volume=open_, trades=1)


def straight(n: int, *, step: float = 1.0, start: float = 100.0):
    """Ряд, где open дня d = start + step·d, close = open (без гэпов)."""
    return [bar(d, start + step * d, start + step * d) for d in range(n)]


FREE = Costs(taker_fee=0.0, slippage=0.0, funding=False)


# --------------------------------------------------------------------------
# правило
# --------------------------------------------------------------------------
def test_на_росте_правило_в_позиции_а_на_падении_нет():
    up = Panel.build({"A": straight(60, step=1.0)})
    down = Panel.build({"A": straight(60, step=-1.0, start=200.0)})
    p = Params(lookback=10, holding=5, mode="long")
    r_up = run(up, p, costs=FREE)
    r_down = run(down, p, costs=FREE)
    assert r_up.exposure == 1.0, "на монотонном росте моментум всегда плюс"
    assert r_down.exposure == 0.0
    assert r_down.total == 0.0, "вне рынка капитал не меняется"
    assert r_up.total > 0.0


def test_контр_тренд_теряет_там_где_тренд_зарабатывает():
    """Контр-тренд на трендовом ряде обязан терять — иначе знак не перевёрнут.

    На падающем ряде он набирает лонг (экспозиция 1.0) и едет вниз вместе с
    ценой; положительный результат он даёт только на развороте.
    """
    down = Panel.build({"A": straight(60, step=-1.0, start=200.0)})
    trend = run(down, Params(lookback=10, holding=5, mode="short"), costs=FREE)
    contra = run(down, Params(lookback=10, holding=5, mode="reverse"),
                 costs=FREE)
    assert contra.exposure == 1.0, "контр-тренд в позиции ровно там, где тренд нет"
    assert contra.total < 0.0 < trend.total


def test_long_short_на_монотонном_ряде_равен_лонгу():
    """Обе стороны не встречаются: знак у всех монет один и тот же."""
    up = Panel.build({"A": straight(60, step=1.0)})
    p = dict(lookback=10, holding=5)
    long_ = run(up, Params(mode="long", **p), costs=FREE)
    both = run(up, Params(mode="longshort", **p), costs=FREE)
    assert math.isclose(long_.total, both.total, rel_tol=1e-12)


def test_шорт_на_падении_зарабатывает_без_издержек():
    down = Panel.build({"A": straight(60, step=-1.0, start=200.0)})
    r = run(down, Params(lookback=10, holding=5, mode="short"), costs=FREE)
    assert r.total > 0.0


# --------------------------------------------------------------------------
# честность: будущее, стыковка, оборот
# --------------------------------------------------------------------------
def test_изменение_будущего_не_трогает_прошлое():
    """Ядро честности: сигнал дня i считается только по дням ≤ i.

    Портим последние дни ряда вдвое и смотрим, что капитал на всех более
    ранних концах интервалов не сдвинулся.
    """
    bars = straight(120, step=1.0)
    a = Panel.build({"A": bars})
    spoiled = bars[:-10] + [bar(b.ts // DAY - START // DAY, b.open * 2,
                               b.close * 2) for b in bars[-10:]]
    b = Panel.build({"A": spoiled})
    p = Params(lookback=20, holding=5, mode="long")
    ra, rb = run(a, p, costs=FREE), run(b, p, costs=FREE)
    cut = len(ra.equity) - 3
    assert [v for _, v in ra.equity[:cut]] == [v for _, v in rb.equity[:cut]]
    assert ra.total != rb.total, "правка обязана быть видна хоть где-то"


def test_интервалы_стыкуются_без_разрывов_и_перекрытий():
    """Выход одного интервала — вход следующего: капитал покрыт целиком."""
    bars = straight(100, step=1.0)
    p = Params(lookback=10, holding=7, mode="all")
    r = run(Panel.build({"A": bars}), p, costs=FREE)
    # первая точка — стартовый капитал в первый день календаря, до всякой
    # торговли; разрывы считаются между концами интервалов
    ts = [t for t, _ in r.equity][1:]
    gaps = {ts[i + 1] - ts[i] for i in range(len(ts) - 1)}
    assert gaps == {p.holding * DAY}, f"разрывы между интервалами: {gaps}"


def test_вход_по_открытию_а_не_по_закрытию_сигнального_дня():
    """Разница двух режимов исполнения обязана быть, и она не нулевая.

    Ряд с гэпом: закрытие дня сигнала и открытие следующего дня не совпадают,
    поэтому режимы дают разный результат. Если бы вход брался по закрытию
    всегда, тест бы этого не заметил.
    """
    bars = [bar(d, 100.0 + d, 100.0 + d + (5.0 if d % 3 == 0 else 0.0))
            for d in range(80)]
    pnl = Panel.build({"A": bars})
    a = run(pnl, Params(lookback=10, holding=5, mode="all", use_open=True),
            costs=FREE)
    b = run(pnl, Params(lookback=10, holding=5, mode="all", use_open=False),
            costs=FREE)
    assert not math.isclose(a.total, b.total, rel_tol=1e-9)


def test_задержка_исполнения_сдвигает_вход_на_lag_дней():
    """lag — сколько дней от сигнала до входа; проверяется ценами входа.

    Ряд растущий и без гэпов: open дня d = 100 + d, поэтому доходность каждого
    интервала видна прямо из цен. Если бы вход всегда брался по открытию
    следующего дня, произведение ниже не сошлось бы.
    """
    n, lb, h, lag = 60, 10, 5, 3
    pnl = Panel.build({"A": straight(n, step=1.0)})
    assert len(pnl.ts) == n, "панель не должна терять дни"
    r = run(pnl, Params(lookback=lb, holding=h, mode="all", lag=lag), costs=FREE)
    expected = 1.0
    for i in range(lb, n - h - lag, h):
        expected *= (100.0 + i + lag + h) / (100.0 + i + lag)
    assert math.isclose(r.total, expected - 1.0, rel_tol=1e-12)


def test_задержка_по_умолчанию_совпадает_с_единицей():
    """lag=1 — прежнее поведение; иначе все старые числа поехали бы молча."""
    bars = [bar(d, 100.0 + d, 100.0 + d + (5.0 if d % 3 == 0 else 0.0))
            for d in range(80)]
    pnl = Panel.build({"A": bars})
    a = run(pnl, Params(lookback=10, holding=5, mode="long"), costs=FREE)
    b = run(pnl, Params(lookback=10, holding=5, mode="long", lag=1), costs=FREE)
    assert a.total == b.total
    assert [x[1] for x in a.equity] == [x[1] for x in b.equity]


def test_на_росте_задержка_снижает_доходность():
    """Растущий ряд: чем позже вход, тем дороже цена и тем меньше доходность."""
    pnl = Panel.build({"A": straight(80, step=1.0)})
    quick = run(pnl, Params(lookback=10, holding=5, mode="all", lag=1), costs=FREE)
    slow = run(pnl, Params(lookback=10, holding=5, mode="all", lag=10), costs=FREE)
    assert slow.total < quick.total
    assert slow.in_market == quick.in_market == 1.0


def test_оборот_считает_и_вход_и_перекладывание():
    """Портфель из двух монет: оборот не меньше одного полного входа."""
    up = straight(60, step=1.0)
    dn = straight(60, step=-1.0, start=200.0)
    pnl = Panel.build({"UP": up, "DN": dn})
    r = run(pnl, Params(lookback=10, holding=5, mode="long"), costs=FREE)
    assert r.exposure == 0.5, "в позиции только растущая монета"
    assert r.in_market == 1.0
    assert 0 < r.turnover_year


def test_издержки_уменьшают_результат_пропорционально_обороту():
    bars = straight(120, step=1.0)
    pnl = Panel.build({"A": bars})
    p = Params(lookback=20, holding=5, mode="all")
    free = run(pnl, p, costs=FREE)
    paid = run(pnl, p, costs=Costs(taker_fee=0.0005, slippage=0.0001,
                                   funding=False))
    assert paid.total < free.total
    assert paid.turnover_year > 0


# --------------------------------------------------------------------------
# метрики
# --------------------------------------------------------------------------
def test_sharpe_и_просадка_на_известных_рядах():
    up = Panel.build({"A": straight(200, step=1.0)})
    r = run(up, Params(lookback=10, holding=5, mode="all"), costs=FREE)
    assert r.max_drawdown == 0.0, "на монотонном росте просадки нет"
    assert r.sharpe is not None and r.sharpe > 0
    down = Panel.build({"A": straight(200, step=-1.0, start=400.0)})
    rd = run(down, Params(lookback=10, holding=5, mode="all"), costs=FREE)
    assert rd.sharpe is not None and rd.sharpe < 0
    # цена проходит путь 400 → 201, то есть вдвое вниз; просадка покупателя
    # обязана быть около половины (а не −99 %: она меряется от максимума)
    assert -0.6 < rd.max_drawdown < -0.4


def test_годовой_множитель_sharpe_учитывает_длину_удержания():
    """Sharpe на интервалах: годовой множитель — корень из интервалов в году."""
    up = Panel.build({"A": straight(300, step=1.0)})
    p = Params(lookback=20, holding=5, mode="all")
    r = run(up, p, costs=FREE)
    per_year = DAYS_PER_YEAR / p.holding
    mean = sum(r.returns) / len(r.returns)
    sd = (sum((x - mean) ** 2 for x in r.returns)
          / (len(r.returns) - 1)) ** 0.5
    assert math.isclose(r.sharpe, mean / sd * math.sqrt(per_year), rel_tol=1e-9)


def test_капитал_начинается_с_единицы_и_равен_произведению_интервалов():
    bars = straight(80, step=1.0)
    p = Params(lookback=10, holding=5, mode="all")
    r = run(Panel.build({"A": bars}), p, costs=FREE)
    assert r.equity[0][1] == 1.0
    acc = 1.0
    for x in r.returns:
        acc *= (1.0 + x)
    assert math.isclose(r.equity[-1][1], acc, rel_tol=1e-12)


# --------------------------------------------------------------------------
# фандинг и панель
# --------------------------------------------------------------------------
def test_фандинг_съедает_доходность_лонга():
    """Положительная ставка: лонг платит, и ровно за свои начисления."""
    bars = straight(80, step=1.0)
    pnl = Panel.build({"A": bars})
    p = Params(lookback=10, holding=5, mode="all")
    # ставка каждый час = 0.0001; за сутки начисляется 24 раза
    sched = {"A": [FundingRate(calc_time=START + h * 3_600_000,
                               funding_interval_hours=1,
                               last_funding_rate=0.0001)
                   for h in range(1, 80 * 24)]}
    free = run(pnl, p, costs=Costs(taker_fee=0.0, slippage=0.0, funding=False))
    paid = run(pnl, p, costs=Costs(taker_fee=0.0, slippage=0.0, funding=True),
               funding=sched)
    assert paid.total < free.total
    # за 5 суток удержания 120 начислений по 0.0001 = 1.2 % за интервал
    first_free = free.returns[0]
    first_paid = paid.returns[0]
    assert math.isclose(first_paid - first_free, -0.012, rel_tol=0.05)


def test_панель_ставит_пропуск_монете_без_бара():
    a = straight(10)
    b = [bar(d, 50.0, 50.0) for d in range(5, 10)]    # листится позже
    pnl = Panel.build({"A": a, "B": b})
    assert len(pnl.ts) == 10
    assert pnl.bars["B"][0] is None
    assert pnl.bars["B"][5] is not None
    assert sum(1 for c in pnl.bars["B"] if c is not None) == 5


def test_монета_без_истории_не_ломает_прогон():
    a = straight(60)
    b = []                                            # данных нет вовсе
    pnl = Panel.build({"A": a, "B": b})
    r = run(pnl, Params(lookback=10, holding=5, mode="all"), costs=FREE)
    assert r.total > 0.0
    assert r.exposure == 1.0, "пустая монета не должна считаться вне рынка"


def test_случайный_вход_с_нулевой_плотностью_не_торгует():
    pnl = Panel.build({"A": straight(60)})
    r = run(pnl, Params(lookback=10, holding=5, mode="random", density=0.0,
                        seed=1), costs=FREE)
    assert r.total == 0.0
    assert r.in_market == 0.0
