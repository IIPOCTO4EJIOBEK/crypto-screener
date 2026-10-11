"""Страница «Самообучение» и строка в отчёт."""
import json

from tests.test_learning import T0, close, journal
from src.trade import learning
from tools.trade import learning_page


def profiles(root, names):
    (root / "screener-profiles.json").write_text(json.dumps([["--name", n, "--learn"] for n in names]), encoding="utf-8")


def test_изменения_урезано_и_вернулось(tmp_path):
    prev = {"segments": {"bounce|5m|long": {"mult": 1.0, "n": 10, "mean_r": 0.1}, "old|1h|short": {"mult": 0.25, "n": 40, "mean_r": -0.5}},
            "coins": {"METUSDT": {"mult": 0.25, "n": 20, "mean_r": -0.6}}}
    new = {"built_ms": T0, "segments": {"bounce|5m|long": {"mult": 0.25, "n": 40, "mean_r": -1.0}},
           "coins": {"METUSDT": {"mult": 1.0, "n": 25, "mean_r": 0.2}}}
    ev = learning_page.record_changes(prev, new, tmp_path / "h.json")
    got = {(e["name"], e["to"]) for e in ev}
    assert got == {("bounce|5m|long", 0.25), ("METUSDT", 1.0), ("old|1h|short", 1.0)}
    assert len(json.loads((tmp_path / "h.json").read_text(encoding="utf-8"))) == 3
    assert learning_page.record_changes(None, new, tmp_path / "h2.json") == []


def test_пересчёт_пишет_страницу_и_строку_отчёта(tmp_path):
    profiles(tmp_path, ["a"])
    journal(tmp_path, "a", [close(i, -1.0) for i in range(40)])
    m = learning.refresh(tmp_path, now_ms=T0)
    page = (tmp_path / "learning.html").read_text(encoding="utf-8")
    assert "Самообучение ботов" in page and "bounce|5m|long" in page and "Урезанные сетапы (1 из 1)" in page
    line = learning_page.line_for_report(tmp_path)
    assert "урезано 1 сетапов из 1" in line and "learning.html" in line
    assert m["segments"]["bounce|5m|long"]["mult"] == learning.PROBE


def test_до_и_после_включения(tmp_path):
    profiles(tmp_path, ["a"])
    d = tmp_path / "screener-a"; d.mkdir()
    import sqlite3
    c = sqlite3.connect(d / "execution.db")
    c.execute("CREATE TABLE journal(id INTEGER PRIMARY KEY,payload TEXT NOT NULL)")
    rows = [dict(kind="open", key="k1", ts=1000, symbol="A", formation="f", tf="5m", side="long"),
            dict(kind="close", key="k1", ts=2000, opened_ms=1000, pnl=-1, fee=0.1, funding=0, r_net=-1.1),
            dict(kind="open", key="k2", ts=5000, symbol="B", formation="f", tf="5m", side="long", learn_mult=0.25),
            dict(kind="close", key="k2", ts=6000, opened_ms=5000, pnl=-0.25, fee=0.05, funding=0, r_net=-1.2),
            dict(kind="open", key="k3", ts=7000, symbol="C", formation="f", tf="5m", side="long", learn_mult=1.0),
            dict(kind="close", key="k3", ts=8000, opened_ms=7000, pnl=2, fee=0.1, funding=0, r_net=1.9)]
    for r in rows:
        c.execute("INSERT INTO journal(payload) VALUES(?)", (json.dumps(r),))
    c.commit(); c.close()
    s = learning_page.stats(tmp_path)
    assert s["enabled"] == 5000
    assert s["before"]["n"] == 1 and abs(s["before"]["net"] + 1.1) < 1e-9
    assert s["after"]["n"] == 2 and s["cut"]["n"] == 1 and s["full"]["n"] == 1
    assert abs(s["saved"] - 0.9) < 1e-9                      # урезанная −0.30, при полном риске было бы ≈ −1.20
