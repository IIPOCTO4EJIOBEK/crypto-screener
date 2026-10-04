"""Бумажный бот по сигналам скринера: вход, стоп раньше цели, гэп, истечение,
повтор сигнала, фильтр тренда, стоп по просадке, страница."""

from __future__ import annotations

import pytest

from src.data.market import Candle, Level, OrderBook
from src.trade.broker import PaperBroker
from src.trade.intraday import (HORIZON, BotState, Config, check_exit, cycle,
                                load_state, save_state)
from src.trade.ledger import Ledger
from tools.trade import screener_page

T0 = 1_760_000_000_000 - (1_760_000_000_000 % 60_000)
MIN = 60_000


def row(**kw):
    r = dict(kind="flag", title="Флаг", tf="5m", symbol="AAAUSDT", direction="long",
             entry=100.0, stop=98.0, target=104.0, triggered=True, age_candles=0,
             measured={"n": 50}, exp_net=0.1)
    r.update(kw)
    return r


class Book:
    def __init__(self, prices):
        self.prices = prices

    def __call__(self, symbol, market):
        p = self.prices[symbol]
        return OrderBook("t", symbol, 0, [Level(p, 1e9)], [Level(p, 1e9)])


def c(ts, o, h, l, cl):
    return Candle(ts, o, h, l, cl, 1.0, cl, 1)


def setup(tmp_path, prices=None, candles=None):
    book = Book(prices or {"AAAUSDT": 100.0})
    broker = PaperBroker("future", book=book)
    ledger = Ledger(tmp_path)
    st = BotState(cash=1000.0, peak=1000.0, start_equity=1000.0)
    data = {"c": candles or []}
    return book, broker, ledger, st, data


def run(st, broker, ledger, data, rows, now, cfg=None, trend=None):
    return cycle(rows, st, broker=broker, ledger=ledger,
                 candles=lambda s, since: data["c"], now_ms=now,
                 cfg=cfg or Config(), trend=trend)


def test_вход_размер_и_цель(tmp_path):
    book, broker, ledger, st, data = setup(tmp_path)
    res = run(st, broker, ledger, data, [row()], T0)
    assert res["opened"] == 1
    p = st.pos()[0]
    # риск 1 % = 10 USDT при стопе 2 → 5 монет, но доля 1000/5/100 = 2 монеты
    assert p.qty == pytest.approx(2.0)
    assert st.cash == pytest.approx(1000 - 2 * 100 * 0.0005)
    # тот же сигнал на следующем круге не берётся второй раз
    assert run(st, broker, ledger, data, [row()], T0 + MIN)["opened"] == 0
    data["c"] = [c(T0 + MIN, 100, 105, 99, 104)]
    res = run(st, broker, ledger, data, [], T0 + 3 * MIN)
    assert res["closed"] == 1 and not st.positions
    cl = [r for r in ledger.journal() if r["kind"] == "close"][0]
    assert cl["reason"] == "target" and cl["r"] == pytest.approx(2.0)
    assert st.cash == pytest.approx(1000 + 2 * 4 - 0.1 - 2 * 104 * 0.0005)


def test_стоп_раньше_цели_и_гэп(tmp_path):
    p_book, broker, ledger, st, data = setup(tmp_path)
    run(st, broker, ledger, data, [row()], T0)
    pos = st.pos()[0]
    # свеча задела и стоп, и цель — считается стоп (худший случай)
    ex = check_exit(pos, [c(T0 + MIN, 100, 105, 97, 101)], T0 + 5 * MIN)
    assert ex.reason == "stop" and ex.price == 98.0
    # гэп ниже стопа — исполнение по открытию, хуже стопа
    ex = check_exit(pos, [c(T0 + MIN, 96, 97, 95, 96)], T0 + 5 * MIN)
    assert ex.price == 96.0
    # незакрытая свеча не считается
    assert check_exit(pos, [c(T0 + MIN, 100, 105, 97, 101)], T0 + MIN + 30_000) is None


def test_шорт_и_истечение(tmp_path):
    book, broker, ledger, st, data = setup(tmp_path)
    r = row(direction="short", stop=102.0, target=96.0, tf="5m")
    run(st, broker, ledger, data, [r], T0)
    assert st.pos()[0].side == "short"
    book.prices["AAAUSDT"] = 99.0
    data["c"] = [c(T0 + MIN, 100, 100.5, 99, 99)]
    end = T0 + HORIZON * 300_000
    res = run(st, broker, ledger, data, [], end)
    assert res["closed"] == 1
    cl = [x for x in ledger.journal() if x["kind"] == "close"][0]
    assert cl["reason"] == "timeout" and cl["exit"] == 99.0 and cl["r"] == pytest.approx(0.5)


def test_цена_ушла_за_стоп_не_входим(tmp_path):
    book, broker, ledger, st, data = setup(tmp_path, {"AAAUSDT": 97.0})
    res = run(st, broker, ledger, data, [row()], T0)
    assert res["opened"] == 0 and res["skipped"] == 1


def test_фильтр_тренда(tmp_path):
    book, broker, ledger, st, data = setup(tmp_path)
    trend = {"coins": {"AAAUSDT": {"15m": "short", "1h": "long", "overall": "flat"}}}
    res = run(st, broker, ledger, data, [row()], T0, Config(trend="tf"), trend)
    assert res["opened"] == 0          # 5m сверяется с 15m: short против лонга
    res = run(st, broker, ledger, data, [row(tf="1h", entry=100.5)], T0, Config(trend="tf"), trend)
    assert res["opened"] == 1
    assert st.pos()[0].trend == "long"


def test_политика_measured(tmp_path):
    book, broker, ledger, st, data = setup(tmp_path)
    res = run(st, broker, ledger, data, [row(exp_net=None), row(entry=101, exp_net=-0.2)],
              T0, Config(policy="measured"))
    assert res["opened"] == 0 and res["skipped"] == 2


def test_просадка_останавливает_входы(tmp_path):
    book, broker, ledger, st, data = setup(tmp_path)
    st.cash, st.peak = 600.0, 1000.0
    res = run(st, broker, ledger, data, [row()], T0)
    assert ledger.halted and res["opened"] == 0


def test_состояние_и_страница(tmp_path):
    book, broker, ledger, st, data = setup(tmp_path)
    run(st, broker, ledger, data, [row()], T0)
    save_state(ledger.state_path, st)
    st2 = load_state(ledger.state_path, 1000.0, T0)
    assert st2.positions == st.positions
    html = screener_page.build(ledger, st2, policy="all", trend="off", cfg=Config(),
                               now_ms=T0, signals=1)
    assert "AAAUSDT" in html and "Живой результат" in html
