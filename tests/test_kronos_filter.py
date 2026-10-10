"""Профиль-эксперимент с Kronos: ждёт прогноз, пропускает входы, где Kronos уверенно за."""
from src.trade.intraday import Config, signal_key
from tests.test_screener_bot import T0, row, run, setup
from tools.trade import kronos_worker as kw

CFG = Config(kronos_filter=0.8)


def test_без_прогноза_сигнал_ждёт_и_просит_прогноз(tmp_path):
    book, broker, ledger, st, data = setup(tmp_path)
    res = run(st, broker, ledger, data, [row()], T0, CFG)
    assert res["opened"] == 0 and not st.seen
    assert res["kronos_requests"][0]["symbol"] == "AAAUSDT"
    assert not [r for r in ledger.journal() if r["kind"] == "skip"]       # ожидание не засоряет журнал


def test_kronos_уверенно_за_пропускаем(tmp_path):
    book, broker, ledger, st, data = setup(tmp_path)
    res = run(st, broker, ledger, data, [row(_kronos={"up_share": 1.0})], T0, CFG)
    assert res["opened"] == 0 and res["skipped"] == 1
    assert "Kronos" in [r for r in ledger.journal() if r["kind"] == "skip"][0]["reason"]


def test_kronos_против_или_не_уверен_входим(tmp_path):
    book, broker, ledger, st, data = setup(tmp_path)
    res = run(st, broker, ledger, data, [row(_kronos={"up_share": 0.6})], T0, CFG)
    assert res["opened"] == 1
    assert [r for r in ledger.journal() if r["kind"] == "open"][0]["kronos"] == {"up_share": 0.6}


def test_шорт_считает_согласие_вниз(tmp_path):
    book, broker, ledger, st, data = setup(tmp_path)
    r = row(direction="short", stop=102.0, target=96.0, _kronos={"up_share": 0.0})
    assert run(st, broker, ledger, data, [r], T0, CFG)["opened"] == 0


def test_без_флага_прогноз_не_нужен(tmp_path):
    book, broker, ledger, st, data = setup(tmp_path)
    assert run(st, broker, ledger, data, [row()], T0, Config())["opened"] == 1


def test_очередь_запросов_и_прогнозов(tmp_path):
    key = signal_key(row())
    kw.add_requests(tmp_path, [{"key": key, "symbol": "AAAUSDT", "tf": "5m", "side": "long", "ts": 1}], T0)
    kw.add_requests(tmp_path, [{"key": "old", "symbol": "B", "tf": "5m", "side": "long", "ts": 1}], T0 - kw.MAX_AGE_MS - 1)
    r = kw.take_request(tmp_path, T0 + 1000)
    assert r["key"] == key
    kw.save_forecast(tmp_path, key, {"up_share": 0.4}, T0 + 2000)
    assert kw.read_forecasts(tmp_path)[key]["up_share"] == 0.4
    assert kw.take_request(tmp_path, T0 + 3000) is None                 # обработанный ушёл из очереди
    kw.add_requests(tmp_path, [{"key": key, "symbol": "AAAUSDT", "tf": "5m", "side": "long", "ts": 1}], T0 + 4000)
    assert kw.take_request(tmp_path, T0 + 5000) is None                 # уже посчитанный не просим снова
    # протухший запрос выбрасывается
    kw.add_requests(tmp_path, [{"key": "x", "symbol": "C", "tf": "5m", "side": "long", "ts": 1}], T0)
    assert kw.take_request(tmp_path, T0 + kw.MAX_AGE_MS + 1) is None


def test_доля_прогнозов_вверх():
    assert kw.up_share(100, [101, 99, 102, 100, 103]) == 0.6


def test_внешний_расчёт_pull_push(tmp_path, monkeypatch):
    monkeypatch.setattr(kw, "_candles", lambda s, tf, now: [[1, 1, 1, 1, 1, 1, 1, 1]])
    kw.add_requests(tmp_path, [{"key": "k1", "symbol": "AAAUSDT", "tf": "5m", "side": "long", "ts": 1}], T0)
    got = kw.pull(tmp_path, T0 + 1000)
    assert list(got) == ["k1"] and got["k1"]["candles"]
    kw.push(tmp_path, {"k1": {"up_share": 0.2}}, T0 + 2000)
    assert kw.read_forecasts(tmp_path)["k1"]["up_share"] == 0.2
    assert kw.pull(tmp_path, T0 + 3000) == {}
