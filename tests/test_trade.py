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


def test_покупки_оставляют_запас_под_комиссию():
    orders = plan({"A": 0.5, "B": 0.5}, equity=1000, cash=1000, positions={},
                  prices={"A": 100.0, "B": 50.0}, limits=Limits())
    assert sum(o.notional for o in orders) == pytest.approx(997)


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
    assert 0 <= r1.state.cash < 10                       # всё в A, кэш не уходит в минус
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
    b.execute("BTCUSDT", "sell", 1.0, reduce=True)
    assert b.ex.sent[0] == "BTC/USDT:USDT" and b.ex.sent[4] == {"reduceOnly": True}
    b.execute("BTCUSDT", "sell", 1.0)                 # открытие шорта — без reduceOnly
    assert b.ex.sent[4] == {}


def test_страница_бота_собирается_из_журнала(tmp_path):
    from tools.trade import page

    series = {"A": ramp(40, 1.0), "B": ramp(40, -1.0, 200.0)}
    prices = {"A": 140.0, "B": 160.0}
    broker = PaperBroker("spot", fee=0.001, book=book_at(prices, spread=0.02))
    ledger = Ledger(tmp_path)
    now = START + 40 * DAY_MS
    step(series, broker=broker, ledger=ledger, limits=Limits(capital=1000), now_ms=now)
    empty = page.build(Ledger(tmp_path / "empty"))
    assert "сделок ещё нет" in empty
    prices["A"] = 150.0
    step(series, broker=broker, ledger=ledger, limits=Limits(capital=1000), now_ms=now + DAY_MS)
    out = page.write(ledger, tmp_path / "bot.html")
    text = out.read_text(encoding="utf-8")
    assert "<polyline" in text and "покупка" in text and ">A<" in text
    assert page.market_return(ledger.journal() and
                              [r for r in ledger.journal() if r["kind"] == "equity"]) \
        == pytest.approx(150 / 140 - 1)


def test_общая_страница_ботов_по_вкладкам(tmp_path):
    from tools.trade import page

    series = {"A": ramp(40, 1.0)}
    broker = PaperBroker("spot", fee=0.001, book=book_at({"A": 140.0}, spread=0.02))
    step(series, broker=broker, ledger=Ledger(tmp_path / "paper-spot"),
         limits=Limits(capital=1000), now_ms=START + 40 * DAY_MS)
    (tmp_path / "profiles.json").write_text(
        '[[], ["--market", "future", "--holding", "1", "--side", "longshort"]]')
    text = page.write_all(tmp_path).read_text(encoding="utf-8")
    assert text.count('role="tab"') == 2
    assert 'id="paper-spot"' in text and 'id="paper-future-longshort" hidden' in text
    assert "Спот 28/5" in text and "Фьючерсы 28/1 · лонг+шорт" in text
    assert "ещё не запускался" in text and "покупка" in text
    # без profiles.json — один бот по умолчанию
    (tmp_path / "profiles.json").unlink()
    assert page.build_all(tmp_path, page.load_profiles(tmp_path)).count('role="tab"') == 1


def test_общая_страница_показывает_ботов_по_скринеру(tmp_path):
    from tools.trade import page

    (tmp_path / "profiles.json").write_text("[[]]")
    (tmp_path / "screener-profiles.json").write_text(
        '[["--name", "managed", "--trend", "tf"], ["--trend", "overall"]]')
    bot = Ledger(tmp_path / "screener-managed")
    bot.log("equity", equity=1000.0, cash=1000.0, open=0)
    bot.log("equity", equity=1050.0, cash=1050.0, open=0)
    (bot.root / "bot.html").write_text('<h1 class="x">Бот & "managed"</h1>', encoding="utf-8")

    text = page.write_all(tmp_path, now_ms=START).read_text(encoding="utf-8")
    assert text.count('role="tab"') == 3
    assert "По скринеру · managed" in text and "1050.00 USDT · +5.00 %" in text
    # страница бота вшита целиком и экранирована — без ссылок на соседние файлы
    assert 'srcdoc="&lt;h1 class=&quot;x&quot;&gt;Бот &amp; &quot;managed&quot;&lt;/h1&gt;"' in text
    assert "screener-managed/bot.html" not in text
    # бот без запусков: подпись есть, окна нет, каталог не создан
    assert "По скринеру · all-trend-overall" in text
    assert not (tmp_path / "screener-all-trend-overall").exists()
    assert "ботов 3" in text


def test_общая_страница_без_профилей(tmp_path):
    from tools.trade import page

    (tmp_path / "profiles.json").write_text("[]")
    text = page.write_all(tmp_path).read_text(encoding="utf-8")
    assert "профилей нет" in text and 'role="tab"' not in text


def test_фандинг_на_фьючерсах_списывается_с_прошлого_шага(tmp_path):
    series = {"A": ramp(40, 1.0)}
    prices = {"A": 100.0}

    class Fut(PaperBroker):
        def funding(self, symbol, start, end):
            self.asked = (start, end)
            return 0.0003                    # три начисления по 0.01 %

    broker = Fut("future", fee=0.0, book=book_at(prices))
    ledger = Ledger(tmp_path)
    now = START + 40 * DAY_MS
    r1 = step(series, broker=broker, ledger=ledger, limits=Limits(capital=1000), now_ms=now)
    cash = r1.state.cash
    qty = r1.state.positions["A"]
    r2 = step(series, broker=broker, ledger=ledger, limits=Limits(capital=1000), now_ms=now + DAY_MS)
    assert broker.asked == (now, now + DAY_MS)
    assert r2.state.cash == pytest.approx(cash - qty * 100.0 * 0.0003)
    assert [r for r in ledger.journal() if r["kind"] == "funding"]


def test_на_споте_фандинга_нет(tmp_path):
    broker = PaperBroker("spot", fee=0.0, book=book_at({"A": 100.0}))
    ledger = Ledger(tmp_path)
    now = START + 40 * DAY_MS
    step({"A": ramp(40, 1.0)}, broker=broker, ledger=ledger, limits=Limits(), now_ms=now)
    step({"A": ramp(40, 1.0)}, broker=broker, ledger=ledger, limits=Limits(), now_ms=now + DAY_MS)
    assert not [r for r in ledger.journal() if r["kind"] == "funding"]


def test_профили_запускаются_по_очереди(monkeypatch, tmp_path):
    from tools.trade import run as cli
    prof = tmp_path / "profiles.json"
    prof.write_text('[[], ["--market", "future", "--holding", "1"]]')
    seen = []
    monkeypatch.setattr(cli, "PROFILES", prof)
    monkeypatch.setattr(cli, "run_one", lambda a: seen.append(a) or 0)
    assert cli.main([]) == 0
    assert seen == [[], ["--market", "future", "--holding", "1"]]
    seen.clear()
    cli.main(["--status"])                       # с аргументами профили не читаются
    assert seen == [["--status"]]


def test_лонг_шорт_открывает_шорт_по_падению_и_переворачивает(tmp_path):
    series = {"UP": ramp(40, 1.0), "DOWN": ramp(40, -1.0, 200.0)}
    prices = {"UP": 140.0, "DOWN": 160.0}
    broker = PaperBroker("future", fee=0.0, book=book_at(prices))
    broker.funding = lambda *a: 0.0
    ledger = Ledger(tmp_path)
    now = START + 40 * DAY_MS
    r = step(series, broker=broker, ledger=ledger, limits=Limits(capital=1000),
             now_ms=now, side="longshort")
    assert r.state.positions["UP"] > 0 and r.state.positions["DOWN"] < 0
    assert r.equity == pytest.approx(1000)
    gross = sum(abs(q) * prices[s] for s, q in r.state.positions.items())
    assert gross <= 1000                                   # плеча нет
    # шорт зарабатывает на падении
    prices["DOWN"] = 150.0
    r2 = step(series, broker=broker, ledger=ledger, limits=Limits(capital=1000),
              now_ms=now + 60_000, side="longshort")
    assert r2.equity > 1000
    # тренд развернулся — шорт закрывается и становится лонгом
    flipped = {"UP": ramp(45, 1.0), "DOWN": ramp(45, 1.0, 100.0)}
    r3 = step(flipped, broker=broker, ledger=ledger, limits=Limits(capital=1000),
              now_ms=now + 5 * DAY_MS, side="longshort", force=True)
    assert r3.state.positions["DOWN"] > 0
    closes = [f for f in ledger.journal() if f["kind"] == "fill" and f["reduce"]]
    assert closes and closes[-1]["symbol"] == "DOWN" and closes[-1]["side"] == "buy"


def test_стоп_закрывает_и_шорты():
    orders = plan({}, equity=1000, cash=2000, positions={"A": -5.0}, prices={"A": 100.0},
                  limits=Limits())
    assert [(o.side, o.qty, o.reduce) for o in orders] == [("buy", 5.0, True)]


def test_шорт_на_споте_запрещён(tmp_path, monkeypatch):
    from tools.trade import run as cli
    monkeypatch.setattr(cli, "load_env", lambda *a, **k: None)
    assert cli.run_one(["--side", "longshort", "--data", str(tmp_path)]) == 2
