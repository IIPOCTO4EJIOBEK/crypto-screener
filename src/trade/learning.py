"""Самообучение бумажных ботов на собственных сделках.

Каждый сетап — сегмент «формация | ТФ | сторона». По закрытым сделкам всех
профилей за последние `WINDOW_DAYS` дней считается средний чистый результат
в R (после комиссий и фандинга). Один сигнал, взятый несколькими профилями,
считается одной сделкой со средним результатом. Среднее сжимается к нулю
(`PRIOR` воображаемых сделок с результатом 0), чтобы пара случайных стопов не
выключала сетап.

Если у сегмента набралось от `MIN_N` сделок и сжатое среднее хуже `BLOCK_R`,
бот берёт такие сигналы с риском `PROBE` от обычного: убыточный сетап почти
не стоит денег, но продолжает давать данные. Окно скользит, поэтому сетап,
который снова начал зарабатывать, через несколько дней возвращается к полному
риску сам.

Модель пересобирается не чаще раза в `EVERY_MS` и лежит в
`data/trade/learning.json` — её видно и можно проверить руками.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path

WINDOW_DAYS = 7
MIN_N = 30
PRIOR = 20
BLOCK_R = -0.05
PROBE = 0.25
EVERY_MS = 10 * 60_000


def segment(kind: str, tf: str, side: str) -> str:
    return f"{kind}|{tf}|{side}"


def _signal(key: str) -> str:
    """Ключ сигнала без номера попытки: одна сделка на сигнал, сколько бы профилей её ни взяли."""
    return key.split("|attempt")[0]


def build(trade_root: Path, now_ms: int | None = None) -> dict:
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    since = now_ms - WINDOW_DAYS * 86_400_000
    trades: dict[str, dict] = {}
    for db in sorted(Path(trade_root).glob("screener-*/execution.db")):
        try:
            with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)) as c:
                rows = c.execute("SELECT payload FROM journal WHERE payload LIKE '%\"kind\": \"close\"%'").fetchall()
        except sqlite3.Error:
            continue
        for (payload,) in rows:
            r = json.loads(payload)
            if r.get("kind") != "close" or r.get("r_net") is None or (r.get("exit_ts") or r.get("ts") or 0) < since:
                continue
            if not r.get("formation") or not r.get("tf") or not r.get("side"):
                continue
            k = _signal(r.get("key") or "")
            t = trades.setdefault(k, {"seg": segment(r["formation"], r["tf"], r["side"]), "r": []})
            t["r"].append(float(r["r_net"]))
    segs: dict[str, dict] = {}
    for t in trades.values():
        s = segs.setdefault(t["seg"], {"n": 0, "sum": 0.0})
        s["n"] += 1
        s["sum"] += sum(t["r"]) / len(t["r"])
    out = {}
    for name, s in sorted(segs.items()):
        mean = s["sum"] / s["n"]
        shrunk = s["sum"] / (s["n"] + PRIOR)
        out[name] = {"n": s["n"], "mean_r": round(mean, 4), "shrunk_r": round(shrunk, 4),
                     "mult": PROBE if s["n"] >= MIN_N and shrunk < BLOCK_R else 1.0}
    return {"built_ms": now_ms, "window_days": WINDOW_DAYS, "min_n": MIN_N, "prior": PRIOR,
            "block_r": BLOCK_R, "probe": PROBE, "segments": out}


def refresh(trade_root: Path, path: Path | None = None, now_ms: int | None = None) -> dict:
    """Готовая модель: из файла, если свежая, иначе пересобрать и сохранить."""
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    path = Path(path or Path(trade_root) / "learning.json")
    try:
        model = json.loads(path.read_text(encoding="utf-8"))
        if now_ms - int(model.get("built_ms", 0)) < EVERY_MS:
            return model
    except Exception:                                       # noqa: BLE001
        pass
    model = build(trade_root, now_ms)
    try:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(model, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass
    return model


def multiplier(model: dict | None, kind: str, tf: str, side: str) -> float:
    if not model:
        return 1.0
    return float(((model.get("segments") or {}).get(segment(kind, tf, side)) or {}).get("mult", 1.0))
