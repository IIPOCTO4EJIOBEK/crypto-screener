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


# --------------------------------------------------------------------------
# ведение позиции: тейки, безубыток, трейлинг, стоп по закрытию, лимиты
# --------------------------------------------------------------------------
def test_частичный_тейк_и_безубыток(tmp_path):
    book, broker, ledger, st, data = setup(tmp_path)
    cfg = Config(tp1_r=1.0, tp1_frac=0.5, be_after_tp1=True)
    run(st, broker, ledger, data, [row()], T0, cfg)
    # свеча до +1R (102): половина закрыта, стоп на входе
    data["c"] = [c(T0 + MIN, 100, 102.5, 99.5, 102)]
    run(st, broker, ledger, data, [], T0 + 2 * MIN, cfg)
    p = st.pos()[0]
    assert p.qty == pytest.approx(1.0) and p.stop == pytest.approx(100.0)
    # откат к входу — выход по сдвинутому стопу, сделка в плюсе за счёт тейка
    data["c"] = [c(T0 + 2 * MIN, 101, 101, 99.9, 100)]
    res = run(st, broker, ledger, data, [], T0 + 3 * MIN, cfg)
    assert res["closed"] == 1
    cl = [r for r in ledger.journal() if r["kind"] == "close"][0]
    assert cl["reason"] == "trail" and cl["r_net"] > 0.4


def test_трейлинг_в_r(tmp_path):
    book, broker, ledger, st, data = setup(tmp_path)
    cfg = Config(trail_r=1.0, no_target=True)
    run(st, broker, ledger, data, [row()], T0, cfg)
    assert st.pos()[0].target == 0.0
    data["c"] = [c(T0 + MIN, 100, 106, 100, 105)]          # лучшая 106 → стоп 104
    run(st, broker, ledger, data, [], T0 + 2 * MIN, cfg)
    assert st.pos()[0].stop == pytest.approx(104.0)
    data["c"] = [c(T0 + 2 * MIN, 105, 105, 103, 103.5)]
    run(st, broker, ledger, data, [], T0 + 3 * MIN, cfg)
    cl = [r for r in ledger.journal() if r["kind"] == "close"][0]
    assert cl["reason"] == "trail" and cl["exit"] == pytest.approx(104.0)


def test_стоп_по_закрытию(tmp_path):
    book, broker, ledger, st, data = setup(tmp_path)
    cfg = Config(stop_on_close=True)
    run(st, broker, ledger, data, [row()], T0, cfg)
    data["c"] = [c(T0 + MIN, 100, 100, 97, 99)]             # шпилька ниже стопа, закрытие выше
    assert run(st, broker, ledger, data, [], T0 + 2 * MIN, cfg)["closed"] == 0
    data["c"] = [c(T0 + 2 * MIN, 99, 99, 97, 97.5)]
    run(st, broker, ledger, data, [], T0 + 3 * MIN, cfg)
    cl = [r for r in ledger.journal() if r["kind"] == "close"][0]
    assert cl["reason"] == "stop" and cl["exit"] == 97.5


def test_дневной_лимит_и_пауза_по_монете(tmp_path):
    book, broker, ledger, st, data = setup(tmp_path)
    cfg = Config(daily_loss=0.003, cooldown_min=30)
    run(st, broker, ledger, data, [row()], T0, cfg)
    data["c"] = [c(T0 + MIN, 100, 100, 97, 97)]             # стоп: −2×2 = −4 USDT
    res = run(st, broker, ledger, data, [row(entry=99.5)], T0 + 2 * MIN, cfg)
    assert res["closed"] == 1 and res["opened"] == 0          # −4 > 0.3 % от 996 — лимит дня
    assert any(r["kind"] == "day_stop" for r in ledger.journal())
    assert "AAAUSDT" in st.cooldown


def test_пауза_после_серии_убытков(tmp_path):
    book, broker, ledger, st, data = setup(tmp_path, {"AAAUSDT": 100.0, "BBBUSDT": 100.0})
    cfg = Config(pause_after=1, pause_min=60)
    run(st, broker, ledger, data, [row()], T0, cfg)
    data["c"] = [c(T0 + MIN, 100, 100, 97, 97)]
    res = run(st, broker, ledger, data, [row(symbol="BBBUSDT")], T0 + 2 * MIN, cfg)
    assert res["opened"] == 0 and st.cooldown["__all__"] > T0 + 2 * MIN


def test_лимит_позиций_в_одну_сторону(tmp_path):
    book, broker, ledger, st, data = setup(tmp_path, {"AAAUSDT": 100.0, "BBBUSDT": 100.0})
    res = run(st, broker, ledger, data, [row(), row(symbol="BBBUSDT")], T0, Config(max_side=1))
    assert res["opened"] == 1 and res["skipped"] == 1


def test_уведомления_о_сделках(tmp_path):
    book, broker, ledger, st, data = setup(tmp_path)
    res = run(st, broker, ledger, data, [row()], T0)
    assert res["events"] and res["events"][0].startswith("ВХОД ЛОНГ AAAUSDT")


def test_закрыть_всё_и_пауза_файлами(tmp_path):
    book, broker, ledger, st, data = setup(tmp_path, {"AAAUSDT": 100.0, "BBBUSDT": 100.0})
    run(st, broker, ledger, data, [row()], T0)
    (tmp_path / "PAUSE").write_text("x")
    (tmp_path / "CLOSEALL").write_text("x")
    res = run(st, broker, ledger, data, [row(symbol="BBBUSDT")], T0 + MIN)
    assert res["closed"] == 1 and res["opened"] == 0 and not st.positions
    assert not (tmp_path / "CLOSEALL").exists()


def test_команды_telegram(tmp_path):
    from tools.trade import tg_control
    bot = tmp_path / "screener-all"
    bot.mkdir()
    save_state(bot / "state.json", BotState(cash=1000.0, peak=1000.0, start_equity=1000.0))
    updates = {"result": [
        {"update_id": 5, "message": {"chat": {"id": 42}, "text": "/pause"}},
        {"update_id": 6, "message": {"chat": {"id": 99}, "text": "/closeall"}},  # чужой
        {"update_id": 7, "message": {"chat": {"id": 42}, "text": "/status"}},
    ]}
    cmds = tg_control.poll("t", "42", tmp_path / "off", get=lambda u, p: updates)
    assert cmds == ["/pause", "/status"]
    assert (tmp_path / "off").read_text() == "8"
    out = tg_control.handle(cmds, tmp_path)
    assert (bot / "PAUSE").exists() and not (bot / "CLOSEALL").exists()
    assert "screener-all" in out[1]
    tg_control.handle(["/resume"], tmp_path)
    assert not (bot / "PAUSE").exists()


def test_исследование_правил_выхода():
    """Повтор сделки правилом «как сейчас» совпадает с walk; трейлинг доводит тренд."""
    from types import SimpleNamespace

    from src.backtest.walk import Trade
    from tools.exit_study import RULES, Rule, replay
    bar = 300_000
    cs = [Candle(T0 + i * bar, 100 + i, 101 + i, 99.5 + i, 100.5 + i, 1, 1, 1)
          for i in range(30)]
    f = SimpleNamespace(kind="flag", symbol="A", targets=[104.0], direction="long")
    tr = Trade(f, "target", 2.0, 4, entry=100.0, stop=98.0, side="long",
               entry_ms=cs[0].ts)
    assert replay(tr, cs, "5m", RULES[0]) == pytest.approx(2.0)
    r = replay(tr, cs, "5m", Rule("t", trail_r=1.5, no_target=True), horizon=20)
    assert r > 5                                   # рост до конца окна — трейлинг не выбит
