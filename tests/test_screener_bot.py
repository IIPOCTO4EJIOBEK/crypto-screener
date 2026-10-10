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

def test_exhausted_capital_does_not_open_dust_position(tmp_path):
    book,broker,ledger,st,data=setup(tmp_path)
    st.cash=1e-10
    result=run(st,broker,ledger,data,[row()],T0)
    assert result['opened']==0 and not st.positions


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


def test_bot_json_для_страницы_скринера(tmp_path):
    import json
    book, broker, ledger, st, data = setup(tmp_path)
    run(st, broker, ledger, data, [row(reasons=["флагшток +3 %", "пробой"], band_usdt=5e5,
                                       spread_bps=1.2)], T0, Config(tp1_r=1.0))
    screener_page.write(ledger, st, tmp_path / "bot.html", policy="all", trend="off",
                        cfg=Config(tp1_r=1.0), now_ms=T0, signals=1)
    d = json.loads((tmp_path / "bot.json").read_text(encoding="utf-8"))
    o = d["open"][0]
    assert o["symbol"] == "AAAUSDT" and o["reasons"] == ["флагшток +3 %", "пробой"]
    assert o["book"]["band_usdt"] == 5e5
    assert o["levels"]["tp1"] == pytest.approx(102.0) and o["levels"]["stop_now"] == 98.0


# --------------------------------------------------------------------------
# внешние сигналы: ручная сделка и вебхук
# --------------------------------------------------------------------------
def test_ручной_сигнал_мимо_фильтра_тренда(tmp_path):
    from src.trade import inbox
    book, broker, ledger, st, data = setup(tmp_path)
    inbox.put(tmp_path, {"symbol": "BINANCE:AAAUSDT.P", "side": "buy", "stop_pct": 2,
                         "note": "пробой"})
    inbox.put(tmp_path, {"symbol": "AAAUSDT", "side": "short"})          # без стопа
    rows, bad = inbox.take(tmp_path, lambda s: 100.0)
    assert len(rows) == 1 and bad and "стоп" in bad[0][1]
    r = rows[0]
    assert r["stop"] == pytest.approx(98.0) and r["target"] == pytest.approx(104.0)
    trend = {"coins": {"AAAUSDT": {"1h": "short", "15m": "short", "overall": "short"}}}
    res = run(st, broker, ledger, data, rows, T0, Config(trend="overall", policy="measured"),
              trend)
    assert res["opened"] == 1 and st.pos()[0].reasons == ["пробой"]
    assert not list((tmp_path / "inbox").glob("*.json"))


def test_вебхук_секрет_и_очередь(tmp_path):
    import io
    import json as _json

    from tools.trade.webhook import make_handler
    bot = tmp_path / "screener-managed"
    bot.mkdir()
    (bot / "state.json").write_text("{}")
    H = make_handler("s" * 20, tmp_path)

    def call(body: dict, token: str | None = None, path="/hook"):
        raw = _json.dumps(body).encode()
        h = H.__new__(H)
        h.path = path
        h.headers = {"Content-Length": str(len(raw)), **({"X-Token": token} if token else {})}
        h.rfile = io.BytesIO(raw)
        h.wfile = io.BytesIO()
        sent = {}
        h.send_response = lambda c: sent.setdefault("code", c)
        h.send_header = lambda *a: None
        h.end_headers = lambda: None
        h.do_POST()
        return sent["code"]

    assert call({"symbol": "BTCUSDT", "side": "long", "stop_pct": 1}, "wrong") == 403
    assert call({"symbol": "BTCUSDT", "side": "long", "stop_pct": 1, "token": "s" * 20}) == 200
    assert call({"symbol": "BTCUSDT"}, "s" * 20, "/hook?bot=nope") == 404
    assert len(list((bot / "inbox").glob("*.json"))) == 1

def test_entry_fee_belongs_to_entry_day(tmp_path):
    from src.trade.intraday import msk_day
    book, broker, ledger, st, data = setup(tmp_path)
    run(st, broker, ledger, data, [row()], T0)
    assert st.day_pnl == pytest.approx(-0.1)
    tomorrow = (msk_day(T0) + 1) * 86_400_000 - 3 * 3600_000
    # Empty history deliberately exercises the market timeout across midnight.
    book.prices['AAAUSDT'] = 100
    run(st, broker, ledger, data, [], tomorrow + MIN)
    assert not st.positions
    assert st.day_pnl == pytest.approx(-0.1)  # exit fee only, entry paid yesterday


def test_daily_stop_latched_until_midnight(tmp_path):
    from src.trade.intraday import msk_day
    book, broker, ledger, st, data = setup(tmp_path)
    cfg = Config(daily_loss=0.03)
    st.day = msk_day(T0); st.day_pnl = -31
    assert run(st, broker, ledger, data, [row()], T0, cfg)['opened'] == 0
    st.day_pnl = 20  # another existing position realizes a gain
    assert run(st, broker, ledger, data, [row()], T0+MIN, cfg)['opened'] == 0
    tomorrow = (msk_day(T0)+1)*86_400_000-3*3600_000
    assert run(st, broker, ledger, data, [row()], tomorrow+MIN, cfg)['opened'] == 1


def test_funding_uses_original_quantity_until_partial_exit(tmp_path):
    book, broker, ledger, st, data = setup(tmp_path)
    cfg = Config(tp1_r=1, be_after_tp1=True, funding=True)
    run(st, broker, ledger, data, [row()], T0, cfg)
    broker.funding = lambda symbol,start,end: 0.01  # one charge before first partial
    data['c'] = [c(T0+MIN,100,102.5,99.5,102)]
    run(st, broker, ledger, data, [], T0+2*MIN, cfg)
    save_state(ledger.state_path,st); st=load_state(ledger.state_path,1000,T0)
    data['c'] = [c(T0+2*MIN,101,101,99.9,100)]
    run(st, broker, ledger, data, [], T0+3*MIN,cfg)
    cl = [r for r in ledger.journal() if r['kind']=='close'][-1]
    assert cl['funding'] == pytest.approx(2)  # two coins held at the charge
    assert st.cash == pytest.approx(999.799)


def test_funding_failure_replays_partial_and_close_once(tmp_path):
    book, broker, ledger, st, data = setup(tmp_path)
    cfg=Config(tp1_r=1,be_after_tp1=True,funding=True)
    run(st,broker,ledger,data,[row()],T0,cfg)
    data['c']=[c(T0+MIN,100,102.5,99.5,102),c(T0+2*MIN,101,101,99.9,100)]
    def fail(*args):raise RuntimeError('offline')
    broker.funding=fail
    before=st.cash
    run(st,broker,ledger,data,[],T0+3*MIN,cfg)
    assert st.cash==before and st.pos()[0].qty==2
    broker.funding=lambda *args:0.01
    run(st,broker,ledger,data,[],T0+3*MIN,cfg)
    assert len([r for r in ledger.journal() if r['kind']=='partial'])==1
    assert len([r for r in ledger.journal() if r['kind']=='close'])==1


def test_minute_history_recovers_beyond_1500(monkeypatch):
    from tools.trade import screener_bot as cli
    now=T0+1600*MIN
    monkeypatch.setattr(cli.time,'time',lambda:now/1000)
    def fetch(symbol, interval, limit, end_ms):
        end=(end_ms//MIN)*MIN
        return [c(ts,100,101,99,100) for ts in range(end-(limit-1)*MIN,end+1,MIN)]
    monkeypatch.setattr(cli.md,'binance_futures_ohlcv',fetch)
    cs=cli.candles_1m('AAAUSDT',T0)
    assert len(cs)==1601 and cs[0].ts==T0 and cs[-1].ts==now


def test_audit_reconciles_partial_cash_and_detects_damage(tmp_path):
    from tools.trade.audit import profile
    book,broker,ledger,st,data=setup(tmp_path)
    cfg=Config(tp1_r=1,be_after_tp1=True)
    run(st,broker,ledger,data,[row()],T0,cfg)
    data['c']=[c(T0+MIN,100,102.5,99.5,102),c(T0+2*MIN,101,101,99.9,100)]
    run(st,broker,ledger,data,[],T0+3*MIN,cfg)
    save_state(ledger.state_path,st)
    # Name identifies intraday schema; copy into a realistic profile directory.
    import shutil
    d=tmp_path/'screener-audit';d.mkdir()
    for name in ['state.json','journal.jsonl']:shutil.copy(tmp_path/name,d/name)
    r=profile(d)
    assert r['reconciled'] and r['fees']==pytest.approx(.201)
    st.cash+=1;save_state(d/'state.json',st)
    assert not profile(d)['reconciled']


def test_capacity_records_reason_without_consuming_signal(tmp_path):
    book,broker,ledger,st,data=setup(tmp_path)
    run(st,broker,ledger,data,[row()],T0,Config(max_open=1))
    result=run(st,broker,ledger,data,[row(symbol='API3USDT')],T0+MIN,Config(max_open=1))
    events=[r for r in ledger.journal() if r['kind']=='capacity']
    assert events[-1]['symbols']==['API3USDT']
    assert not any('API3USDT' in key for key in st.seen)
    assert result['events']


def test_actual_paper_fill_beyond_target_is_rejected(tmp_path):
    book,broker,ledger,st,data=setup(tmp_path)
    class MovingBook:
        calls=0
        def __call__(self,symbol,market):
            self.calls+=1
            price=100 if self.calls==1 else 105
            return OrderBook('t',symbol,0,[Level(price,1e9)],[Level(price,1e9)])
    broker=PaperBroker('future',book=MovingBook())
    res=run(st,broker,ledger,data,[row()],T0)
    assert res['opened']==0 and st.cash==1000 and not st.positions
    assert any('цена исполнения' in r.get('reason','') for r in ledger.journal())


def test_minimum_net_rr_rejects_late_entry(tmp_path):
    book,broker,ledger,st,data=setup(tmp_path)
    result=run(st,broker,ledger,data,[row()],T0,Config(min_entry_rr=3))
    assert not result['opened'] and st.cash==1000
    assert any(r.get('rr_net',99)<3 for r in ledger.journal() if r['kind']=='skip')


def test_opposite_structural_signal_closes_old_position(tmp_path):
    book,broker,ledger,st,data=setup(tmp_path)
    run(st,broker,ledger,data,[row()],T0)
    opposite=row(direction='short',kind='breakout',entry=100,stop=102,target=94,ts=T0-4*MIN,trigger_level=101,_latest_closed=100)
    result=run(st,broker,ledger,data,[opposite],T0+MIN,Config(exit_on_opposite=True))
    closes=[r for r in ledger.journal() if r['kind']=='close']
    assert result['closed']==1 and closes[-1]['reason']=='invalidation'


def test_parallel_timeframes_keep_distinct_positions(tmp_path):
    book,broker,ledger,st,data=setup(tmp_path)
    result=run(st,broker,ledger,data,[row(tf='5m'),row(tf='15m')],T0,Config(parallel_timeframes=True))
    assert result['opened']==2
    assert all(p.entry_rules['parallel_timeframes'] for p in st.pos())


def test_more_slots_do_not_create_extra_capital(tmp_path):
    book,broker,ledger,st,data=setup(tmp_path,prices={'AAAUSDT':100,'BBB USDT':100,'BBBUSDT':100})
    run(st,broker,ledger,data,[row()],T0,Config(max_open=1,risk_pct=1))
    result=run(st,broker,ledger,data,[row(symbol='BBBUSDT')],T0+MIN,Config(max_open=20))
    assert result['opened']==0 and not any('BBBUSDT' in key for key in st.seen)
    assert any('капитал' in r.get('reason','') for r in ledger.journal())


def test_eighty_percent_capital_limit(tmp_path):
    book,broker,ledger,st,data=setup(tmp_path)
    run(st,broker,ledger,data,[row()],T0,Config(max_open=1,risk_pct=1,capital_fraction=.8))
    assert sum(p.qty*p.entry for p in st.pos()) <= 800+1e-7
    assert len(st.positions)==1


def test_three_attempts_have_distinct_receipts_and_wait_for_next_event(tmp_path):
    book,broker,ledger,st,data=setup(tmp_path)
    cfg=Config(max_attempts_5m=3,no_timeout=True)
    signal=row(ts=T0)
    run(st,broker,ledger,data,[signal],T0,cfg)
    for n in range(3):
        data['c']=[c(T0+(2*n+1)*MIN,100,101,97,99)]
        run(st,broker,ledger,data,[signal],T0+(2*n+2)*MIN,cfg)
    assert not st.positions
    opened=[r for r in ledger.journal() if r['kind']=='open']
    assert len(opened)==3 and len({r['key'] for r in opened})==3
    assert any(r['kind']=='attempt_review' for r in ledger.journal())
    data['c']=[]
    assert run(st,broker,ledger,data,[row(ts=T0+5*MIN)],T0+8*MIN,cfg)['opened']==1


def test_no_timeout_preserves_position_after_old_horizon(tmp_path):
    book,broker,ledger,st,data=setup(tmp_path)
    cfg=Config(no_timeout=True)
    run(st,broker,ledger,data,[row()],T0,cfg)
    assert run(st,broker,ledger,data,[],T0+3*86400000,cfg)['closed']==0


def test_manual_stop_edit_and_close_preserve_reason(tmp_path):
    from src.trade.position_controls import enqueue,pending,apply
    book,broker,ledger,st,data=setup(tmp_path)
    run(st,broker,ledger,data,[row()],T0,Config(no_timeout=True))
    p=st.pos()[0]
    enqueue(tmp_path,dict(op='edit',key=p.key,reason='уровень поддержки',stop=99,target=107))
    commands=pending(tmp_path);apply(commands,st,broker,ledger,T0+MIN)
    assert st.pos()[0].stop==99 and st.pos()[0].target==107
    apply([(tmp_path/'close.json',dict(op='close',key=p.key,reason='слом идеи'))],st,broker,ledger,T0+MIN)
    result=run(st,broker,ledger,data,[],T0+2*MIN,Config(no_timeout=True))
    assert result['closed']==1
    closed=[r for r in ledger.journal() if r['kind']=='close'][-1]
    assert closed['reason']=='manual' and closed['manual_reason']=='слом идеи'


def test_близкий_стоп_дорогая_комиссия_не_входим(tmp_path):
    # стоп 0.2 при цене 100: комиссия 0.0005*(100+99.8)≈0.1 = 0.5R > 0.25R
    book, broker, ledger, st, data = setup(tmp_path)
    res = run(st, broker, ledger, data, [row(stop=99.8, target=101.0)], T0, Config(max_fee_r=0.25))
    assert res["opened"] == 0 and res["skipped"] == 1
    sk = [r for r in ledger.journal() if r["kind"] == "skip"][-1]
    assert "комиссия" in sk["reason"] and sk["fee_r"] == pytest.approx(0.499, abs=1e-3)
    # обычный стоп 2.0 (комиссия 0.05R) проходит
    assert run(st, broker, ledger, data, [row()], T0, Config(max_fee_r=0.25))["opened"] == 1


def test_исключённые_формации_не_берём(tmp_path):
    book, broker, ledger, st, data = setup(tmp_path)
    cfg = Config(skip_kinds=("absorption", "bounce"))
    res = run(st, broker, ledger, data, [row(kind="absorption")], T0, cfg)
    assert res["opened"] == 0 and res["skipped"] == 1
    assert run(st, broker, ledger, data, [row(kind="retest", entry=100.2)], T0, cfg)["opened"] == 1


def test_ручной_сигнал_фильтры_не_режут(tmp_path):
    book, broker, ledger, st, data = setup(tmp_path)
    res = run(st, broker, ledger, data, [row(kind="absorption", stop=99.8, target=101.0, manual=True)], T0,
              Config(max_fee_r=0.25, skip_kinds=("absorption",)))
    assert res["opened"] == 1
