"""Тесты измерения ожидаемости и ранжирования сигналов.

Проверяется то, что легко сломать незаметно и что при поломке даёт не ошибку,
а правдоподобное число: границы окна, порог по числу сделок и порядок
ранжирования. Скринер, поставивший наверх сигнал без измерения, выглядит
работающим и врёт при этом молча.
"""

from __future__ import annotations

from datetime import date

from src.analysis.formations import Formation
from src.backtest.costs import Costs
from src.backtest.expectancy import STEP_BY_TF, window
from src.storage import db
from tools.live.screen import MIN_TRADES, rank


def formation(kind: str = "breakout", tf: str = "5m", symbol: str = "BTCUSDT",
              direction: str = "long", entry: float = 100.0,
              stop: float = 99.0, target: float = 102.0,
              confidence: float = 0.5) -> Formation:
    return Formation(kind=kind, title="Проба", direction=direction,
                     symbol=symbol, exchange="binance", tf=tf, ts=1, price=entry,
                     entry=entry, stop=stop, targets=[target],
                     triggered=True, confidence=confidence)


def stats(kind: str, tf: str, n: int, exp_net: float) -> dict:
    return dict(kind=kind, tf=tf, measured_on="2026-09-29", symbol_scope="BTCUSDT",
                n=n, win_rate=50.0, exp_gross=exp_net + 0.1, exp_net=exp_net,
                cost=0.1)


def empty_db():
    return db.connect(":memory:")


# --------------------------------------------------------------------------
# окно
# --------------------------------------------------------------------------
def test_окно_кончается_вчерашними_сутками():
    """Архив отстаёт на сутки: последние сутки в окно попасть не должны."""
    start, end = window(45, date(2026, 9, 29))
    assert end == date(2026, 9, 28)
    assert start == date(2026, 8, 15)
    assert (end - start).days == 44        # 45 суток включая обе границы


def test_окно_в_один_день_не_разваливается():
    start, end = window(1, date(2026, 9, 29))
    assert start == end == date(2026, 9, 28)


def test_шаг_среза_задан_для_всех_рабочих_таймфреймов():
    for tf in ("5m", "15m", "1h"):
        assert STEP_BY_TF[tf] >= 1


def test_шаг_среза_примерно_одинаков_по_времени():
    """Три часа между срезами на каждом ТФ — иначе ТФ несопоставимы."""
    minutes = {"5m": 5, "15m": 15, "1h": 60}
    for tf, m in minutes.items():
        assert STEP_BY_TF[tf] * m == 180


# --------------------------------------------------------------------------
# ранжирование
# --------------------------------------------------------------------------
def test_наверху_сигнал_с_лучшей_измеренной_ожидаемостью():
    conn = empty_db()
    found = [formation(kind="breakout", confidence=0.9),
             formation(kind="retest", confidence=0.1)]
    st = {("breakout", "5m"): stats("breakout", "5m", 100, -0.20),
          ("retest", "5m"): stats("retest", "5m", 100, +0.05)}
    rows = rank(found, st, conn, Costs())
    assert [r.kind for r in rows] == ["retest", "breakout"]
    assert rows[0].exp_net == 0.05


def test_неизмеренный_сигнал_ниже_измеренного_даже_с_высокой_уверенностью():
    conn = empty_db()
    found = [formation(kind="breakout", confidence=0.95),
             formation(kind="flow", confidence=0.10)]
    st = {("breakout", "5m"): stats("breakout", "5m", 100, -0.30)}
    rows = rank(found, st, conn, Costs())
    assert [r.kind for r in rows] == ["breakout", "flow"]
    assert rows[1].exp_net is None


def test_малое_число_сделок_не_считается_измерением():
    """Иначе десяток сделок решал бы, что брать, — то же, что монетка."""
    conn = empty_db()
    found = [formation(kind="breakout")]
    st = {("breakout", "5m"): stats("breakout", "5m", MIN_TRADES - 1, +0.9)}
    rows = rank(found, st, conn, Costs())
    assert rows[0].exp_net is None
    assert rows[0].measured is not None      # измерение есть, но оно не опора


def test_издержки_сделки_считаются_от_её_собственного_стопа():
    """Круговые 0.1 % при стопе 1 % — это 0.1 R, а не 0.001."""
    conn = empty_db()
    rows = rank([formation(entry=100.0, stop=99.0)],
                {}, conn, Costs(taker_fee=0.0004, slippage=0.0001))
    assert rows[0].cost_now == 0.1


def test_нулевой_риск_в_список_не_попадает():
    conn = empty_db()
    rows = rank([formation(entry=100.0, stop=100.0)], {}, conn, Costs())
    assert rows == []


def test_снимок_стакана_берётся_со_стороны_входа():
    """Лонг смотрит на объём у бида, шорт — у аска."""
    conn = empty_db()
    ob = db.BookSnapshot(ts=1, symbol="BTCUSDT", exchange="binance", mid=100.0,
                         spread_bps=0.5, best_bid=99.99, best_ask=100.01,
                         n_bids=10, n_asks=10, imbalance=0.1,
                         bid_5bps=1000.0, ask_5bps=2000.0, bid_10bps=3000.0,
                         ask_10bps=4000.0, bid_25bps=5000.0, ask_25bps=6000.0,
                         bid_50bps=7000.0, ask_50bps=8000.0)
    db.insert_book_snapshots(conn, [ob])
    rows = rank([formation(direction="long"), formation(direction="short")],
                {}, conn, Costs())
    by_dir = {r.direction: r for r in rows}
    assert by_dir["long"].band_usdt == 3000.0
    assert by_dir["short"].band_usdt == 4000.0
    assert by_dir["long"].mid == 100.0
