"""Самообучение: убыточные по своим сделкам сетапы берутся с уменьшенным риском."""
import json
import sqlite3

import pytest

from src.trade import learning
from src.trade.intraday import Config
from tests.test_screener_bot import T0, row, run, setup

DAY = 86_400_000


def journal(root, name, closes):
    d = root / f"screener-{name}"
    d.mkdir(parents=True)
    c = sqlite3.connect(d / "execution.db")
    c.execute("CREATE TABLE journal(id INTEGER PRIMARY KEY,payload TEXT NOT NULL)")
    for r in closes:
        c.execute("INSERT INTO journal(payload) VALUES(?)", (json.dumps(dict(kind="close", **r)),))
    c.commit(); c.close()


def close(i, r_net, kind="bounce", tf="5m", side="long", ts=T0):
    return dict(key=f"{kind}|{tf}|S{i}|{side}|1|attempt1", formation=kind, tf=tf, side=side, r_net=r_net, exit_ts=ts)


def test_убыточный_сегмент_пробный_риск_прибыльный_полный(tmp_path):
    journal(tmp_path, "a", [close(i, -1.0) for i in range(40)] + [close(i, 0.5, kind="retest") for i in range(40)])
    m = learning.build(tmp_path, T0)
    assert m["segments"]["bounce|5m|long"]["mult"] == learning.PROBE
    assert m["segments"]["bounce|5m|long"]["shrunk_r"] == pytest.approx(-40 / 60, abs=1e-4)
    assert learning.multiplier(m, "retest", "5m", "long") == 1.0
    assert learning.multiplier(m, "flag", "1h", "short") == 1.0          # нет данных — полный риск


def test_мало_сделок_не_выключает(tmp_path):
    journal(tmp_path, "a", [close(i, -1.0) for i in range(learning.MIN_N - 1)])
    assert learning.build(tmp_path, T0)["segments"]["bounce|5m|long"]["mult"] == 1.0


def test_один_сигнал_в_нескольких_профилях_считается_один_раз(tmp_path):
    journal(tmp_path, "a", [close(i, -1.0) for i in range(30)])
    journal(tmp_path, "b", [close(i, 1.0) for i in range(30)])
    s = learning.build(tmp_path, T0)["segments"]["bounce|5m|long"]
    assert s["n"] == 30 and s["mean_r"] == 0.0 and s["mult"] == 1.0


def test_старые_сделки_забываются(tmp_path):
    journal(tmp_path, "a", [close(i, -1.0, ts=T0 - 8 * DAY) for i in range(40)])
    assert learning.build(tmp_path, T0)["segments"] == {}


def test_модель_кэшируется_в_файле(tmp_path):
    journal(tmp_path, "a", [close(i, -1.0) for i in range(40)])
    m1 = learning.refresh(tmp_path, now_ms=T0)
    assert (tmp_path / "learning.json").exists()
    journal(tmp_path, "b", [close(100 + i, 5.0) for i in range(40)])
    assert learning.refresh(tmp_path, now_ms=T0 + 60_000) == m1          # свежая — из файла
    m3 = learning.refresh(tmp_path, now_ms=T0 + learning.EVERY_MS + 1)
    assert m3["segments"]["bounce|5m|long"]["mult"] == 1.0


def test_пробный_риск_уменьшает_позицию(tmp_path):
    # риск 1 % = 10 USDT, стоп 2 → 5 монет, потолок доли 2 монеты; с множителем 0.25 — 1.25 монеты
    book, broker, ledger, st, data = setup(tmp_path)
    run(st, broker, ledger, data, [row(_learn_mult=0.25)], T0, Config(learn=True))
    assert st.pos()[0].qty == pytest.approx(1.25)
    op = [r for r in ledger.journal() if r["kind"] == "open"][0]
    assert op["learn_mult"] == 0.25


def test_без_флага_learn_множитель_не_действует(tmp_path):
    book, broker, ledger, st, data = setup(tmp_path)
    run(st, broker, ledger, data, [row(_learn_mult=0.25)], T0, Config())
    assert st.pos()[0].qty == pytest.approx(2.0)
