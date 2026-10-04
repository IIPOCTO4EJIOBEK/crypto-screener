"""Тесты торгового контура: сигнал, риск, бумажное исполнение, журнал.

Главное, что здесь проверяется: живой сигнал считает то же правило, что и
бэктест, не смотрит в незакрытый бар, ребаланс не повторяется в один день,
стоп по просадке продаёт всё и останавливает бота, а живой режим без
подтверждения не запускается.
"""

from __future__ import annotations

import pytest

from src.backtest.costs import Costs
from src.backtest.trend import Panel, Params, run
from src.data.market import Candle, Level, OrderBook
from src.trade.broker import PaperBroker, walk_book
from src.trade.engine import step
from src.trade.ledger import Ledger
from src.trade.risk import Limits, breached, plan
from src.trade.signal import DAY_MS, decide, is_decision_day

START = 1_700_006_400_000 - (1_700_006_400_000 % DAY_MS)   # полночь UTC


def bar(day: int, price: float) -> Candle:
    return Candle(START + day * DAY_MS, price, price, price, price, 1.0, price, 1)


def ramp(n: int, step: float, start: float = 100.0) -> list[Candle]:
    return [bar(d, start + step * d) for d in range(n)]


def book_at(prices: dict[str, float], depth: float = 1e9, spread: float = 0.0):
    def book(symbol, market):
        p = prices[symbol]
        return OrderBook("t", symbol, 0, [Level(p - spread / 2, depth)],
                         [Level(p + spread / 2, depth)])
    return book


# --------------------------------------------------------------------------
# сигнал
# --------------------------------------------------------------------------
def test_незакрытый_бар_не_участвует():
    bars = ramp(40, 1.0)
    bars[-1] = bar(39, 1.0)                     # обвал в незакрытом баре
    now = START + 39 * DAY_MS + 3600_000        # час в день 39 — бар 39 открыт
    d = decide({"A": bars}, now_ms=now)
    assert d.day_ts == START + 38 * DAY_MS
    assert d.longs == ["A"]


def test_сигнал_совпадает_с_бэктестом():
    series = {"UP": ramp(60, 1.0), "DOWN": ramp(60, -1.0, 200.0)}
    now = START + 60 * DAY_MS
    d = decide(series, now_ms=now, lookback=28)
    assert d.longs == ["UP"]
    assert d.momentum["UP"] == pytest.approx((100 + 59) / (100 + 31) - 1)
    # тот же ответ даёт бэктест: на росте в позиции, на падении нет
    r = run(Panel.build({"UP": series["UP"]}), Params(lookback=28, holding=5),
            costs=Costs(taker_fee=0, slippage=0, funding=False))
    assert r.exposure == 1.0


def test_монета_без_истории_мимо():
    d = decide({"A": ramp(40, 1.0), "NEW": ramp(10, 1.0)}, now_ms=START + 40 * DAY_MS)
    assert "NEW" in d.skipped and d.longs == ["A"]


def test_день_решения_по_календарю():
    days = [START + i * DAY_MS for i in range(10)]
    assert sum(is_decision_day(t, 5) for t in days) == 2


# --------------------------------------------------------------------------
# риск
# --------------------------------------------------------------------------
def test_план_продажи_раньше_покупок_и_без_плеча():
    orders = plan({"B": 1.0}, equity=1000, cash=0, positions={"A": 10.0},
                  prices={"A": 100.0, "B": 50.0}, limits=Limits())
    assert [o.side for o in orders] == ["sell", "buy"]
    assert orders[1].notional <= 1000


def test_план_мелочь_не_торгуется():
    orders = plan({"A": 1.0}, equity=1000, cash=5, positions={"A": 9.95},
                  prices={"A": 100.0}, limits=Limits(min_order=10))
    assert orders == []


def test_потолок_веса_оставляет_остаток_в_кэше():
    orders = plan({"A": 1.0}, equity=1000, cash=1000, positions={},
                  prices={"A": 100.0}, limits=Limits(max_coin_weight=0.25))
    assert orders[0].notional == pytest.approx(250)


def test_стоп_по_просадке():
    assert breached(640, 1000, Limits(max_drawdown=0.35))
    assert not breached(660, 1000, Limits(max_drawdown=0.35))


# --------------------------------------------------------------------------
# исполнение
# --------------------------------------------------------------------------
def test_проход_по_стакану():
    b = OrderBook("t", "A", 0, [Level(99, 1)], [Level(100, 1), Level(110, 1)])
    assert walk_book(b, "buy", 2) == pytest.approx(105)
    assert walk_book(b, "buy", 3) is None


def test_шаг_покупает_и_не_повторяет_ребаланс(tmp_path):
    series = {"A": ramp(40, 1.0), "B": ramp(40, -1.0, 200.0)}
    prices = {"A": 140.0, "B": 160.0}
    broker = PaperBroker("spot", fee=0.001, book=book_at(prices))
    ledger = Ledger(tmp_path)
    now = START + 40 * DAY_MS
    r1 = step(series, broker=broker, ledger=ledger, limits=Limits(capital=1000), now_ms=now)
    assert r1.rebalanced and list(r1.state.positions) == ["A"]
    assert r1.state.cash == pytest.approx(0, abs=10)     # всё в A, минус комиссия
    r2 = step(series, broker=broker, ledger=ledger, limits=Limits(capital=1000), now_ms=now + 60_000)
    assert not r2.rebalanced and r2.fills == []


def test_стоп_продаёт_всё_и_останавливает(tmp_path):
    series = {"A": ramp(40, 1.0)}
    prices = {"A": 140.0}
    broker = PaperBroker("spot", fee=0.0, book=book_at(prices))
    ledger = Ledger(tmp_path)
    now = START + 40 * DAY_MS
    step(series, broker=broker, ledger=ledger, limits=Limits(capital=1000), now_ms=now)
    prices["A"] = 70.0                                   # −50 %
    r = step(series, broker=broker, ledger=ledger, limits=Limits(capital=1000),
             now_ms=now + DAY_MS, force=True)
    assert r.halted and r.state.positions == {}
    assert ledger.halted
    r3 = step(series, broker=broker, ledger=ledger, limits=Limits(capital=1000),
              now_ms=now + 5 * DAY_MS, force=True)
    assert r3.fills == []                                # остановлен — заявок нет


def test_живой_режим_без_подтверждения_заблокирован(monkeypatch, tmp_path):
    from tools.trade import run as cli
    monkeypatch.delenv("TRADE_LIVE", raising=False)
    monkeypatch.setattr(cli, "load_env", lambda *a, **k: None)
    assert cli.main(["--mode", "live", "--data", str(tmp_path)]) == 2
    monkeypatch.setenv("TRADE_LIVE", "yes")
    assert cli.main(["--mode", "live", "--data", str(tmp_path)]) == 2   # нет флага


def test_биржевой_брокер_учитывает_комиссию_в_монете():
    from src.trade.broker import ExchangeBroker

    class FakeEx:
        def amount_to_precision(self, s, q):
            return f"{q:.3f}"

        def fetch_ticker(self, s):
            return {"bid": 99.0, "ask": 101.0, "last": 100.0}

        def create_order(self, s, typ, side, amount, price, params):
            self.sent = (s, typ, side, amount, params)
            return {"id": 7, "filled": amount, "average": 101.0,
                    "fees": [{"cost": 0.001, "currency": "BTC"}]}

    b = ExchangeBroker.__new__(ExchangeBroker)
    b.ex, b.market, b.quote, b.testnet = FakeEx(), "spot", "USDT", True
    f = b.execute("BTCUSDT", "buy", 1.0004)
    assert b.ex.sent == ("BTC/USDT", "market", "buy", 1.0, {})
    assert f.qty == pytest.approx(0.999) and f.fee == 0.0
    assert f.slippage_bp == pytest.approx(100.0)

    b.market = "future"
    b.execute("BTCUSDT", "sell", 1.0)
    assert b.ex.sent[0] == "BTC/USDT:USDT" and b.ex.sent[4] == {"reduceOnly": True}
