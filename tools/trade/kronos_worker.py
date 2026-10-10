"""Прогнозы Kronos для бумажного профиля-эксперимента (`--kronos-filter`).

Бот кладёт в `data/trade/kronos/requests.json` сигналы, по которым ему нужен
прогноз; воркер берёт свежие (новые первыми), качает закрытые свечи ТФ сигнала
с Binance, прогоняет Kronos-small на процессоре и пишет в `forecasts.json`
долю сэмплов, где цена через `HORIZON` свечей выше текущей (`up_share`).

Замер 10.10.2026 (1954 сигнала ботов, research/tsfm-20261010): когда Kronos
уверенно за сделку, сделки были хуже — профиль-эксперимент такие входы
пропускает и сравнивается с обычным managed.

Чтение и запись запросов — без torch, их импортирует сам бот. Сам воркер
запускается из окружения с torch: /opt/kronos/.venv/bin/python -m tools.trade.kronos_worker
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

HORIZON = 8
CONTEXT = 360
SAMPLES = 5
MAX_AGE_MS = 15 * 60_000          # запрос старше — сигнал уже не свежий, не считаем
KEEP_MS = 2 * 86_400_000           # прогнозы храним двое суток
TF_MS = {"5m": 300_000, "15m": 900_000, "1h": 3_600_000}
KRONOS = Path(os.environ.get("KRONOS_HOME", "/opt/kronos"))


@contextmanager
def _locked(folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / ".lock", "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:                                       # noqa: BLE001
        return default


def _write(path: Path, obj) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def read_forecasts(folder: Path) -> dict:
    return _read(Path(folder) / "forecasts.json", {})


def add_requests(folder: Path, items: list[dict], now_ms: int) -> None:
    folder = Path(folder)
    with _locked(folder):
        req = _read(folder / "requests.json", {})
        done = read_forecasts(folder)
        for it in items:
            if it["key"] not in done and it["key"] not in req:
                req[it["key"]] = dict(it, asked_ms=now_ms)
        _write(folder / "requests.json", {k: v for k, v in req.items() if now_ms - v["asked_ms"] < MAX_AGE_MS})


def take_request(folder: Path, now_ms: int) -> dict | None:
    """Самый свежий запрос; протухшие выбрасываются."""
    folder = Path(folder)
    with _locked(folder):
        req = {k: v for k, v in _read(folder / "requests.json", {}).items() if now_ms - v["asked_ms"] < MAX_AGE_MS}
        _write(folder / "requests.json", req)
        if not req:
            return None
        return max(req.values(), key=lambda v: v["asked_ms"])


def save_forecast(folder: Path, key: str, fc: dict, now_ms: int) -> None:
    folder = Path(folder)
    with _locked(folder):
        fcs = {k: v for k, v in read_forecasts(folder).items() if now_ms - v.get("made_ms", 0) < KEEP_MS}
        fcs[key] = dict(fc, made_ms=now_ms)
        _write(folder / "forecasts.json", fcs)
        req = _read(folder / "requests.json", {})
        req.pop(key, None)
        _write(folder / "requests.json", req)


def up_share(last: float, ends: list[float]) -> float:
    return sum(e > last for e in ends) / len(ends)


def _candles(symbol: str, tf: str, now_ms: int) -> list:
    import urllib.parse
    import urllib.request
    end = now_ms // TF_MS[tf] * TF_MS[tf] - 1                # только закрытые свечи
    url = (f"https://fapi.binance.com/fapi/v1/klines?symbol={urllib.parse.quote(symbol)}&interval={tf}"
           f"&limit={CONTEXT}&endTime={end}")
    return json.load(urllib.request.urlopen(url, timeout=15))


def main() -> None:
    import sys
    sys.path.insert(0, str(KRONOS))
    import numpy as np
    import pandas as pd
    import torch
    from model import Kronos, KronosPredictor, KronosTokenizer
    torch.set_num_threads(int(os.environ.get("KRONOS_THREADS", "1")))
    root = Path(__file__).resolve().parents[2] / "data" / "trade" / "kronos"
    tok = KronosTokenizer.from_pretrained(str(KRONOS / "weights/Kronos-Tokenizer-base"))
    model = Kronos.from_pretrained(str(KRONOS / "weights/Kronos-small"))
    pred = KronosPredictor(model, tok, device="cpu", max_context=512)
    print("kronos worker: модель загружена", flush=True)
    while True:
        now = int(time.time() * 1000)
        r = take_request(root, now)
        if not r:
            time.sleep(3)
            continue
        started = time.time()
        try:
            k = _candles(r["symbol"], r["tf"], now)
            if len(k) < 200:
                raise ValueError(f"мало свечей: {len(k)}")
            df = pd.DataFrame([[float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5]), float(x[7])] for x in k],
                              columns=["open", "high", "low", "close", "volume", "amount"])
            ts = pd.Series(pd.to_datetime([x[0] for x in k], unit="ms"))
            step = pd.Timedelta(int(TF_MS[r["tf"]]), unit="ms")
            fut = pd.Series(pd.date_range(ts.iloc[-1] + step, periods=HORIZON, freq=step))
            torch.manual_seed(42); np.random.seed(42)
            res = pred.predict_batch([df] * SAMPLES, [ts] * SAMPLES, [fut] * SAMPLES, pred_len=HORIZON,
                                     T=1.0, top_p=0.9, sample_count=1, verbose=False)
            last = float(df.close.iloc[-1])
            ends = [float(x["close"].iloc[-1]) for x in res]
            fc = {"up_share": up_share(last, ends), "ret_end": sum(ends) / len(ends) / last - 1,
                  "last_candle_ms": int(k[-1][0]), "seconds": round(time.time() - started, 1)}
        except Exception as exc:                            # noqa: BLE001
            print(f"kronos worker: {r['symbol']} {r['tf']}: {type(exc).__name__} {str(exc)[:120]}", flush=True)
            fc = None
        if fc:
            save_forecast(root, r["key"], fc, int(time.time() * 1000))
            print(f"kronos {r['symbol']} {r['tf']} {r['side']}: вверх {fc['up_share']:.0%}, {fc['seconds']} с", flush=True)
        else:
            with _locked(root):
                req = _read(root / "requests.json", {})
                req.pop(r["key"], None)
                _write(root / "requests.json", req)


def pull(folder: Path, now_ms: int, limit: int = 20) -> dict:
    """Для внешнего расчёта (видеокарта на другой машине): свежие запросы со свечами."""
    folder = Path(folder)
    with _locked(folder):
        req = {k: v for k, v in _read(folder / "requests.json", {}).items() if now_ms - v["asked_ms"] < MAX_AGE_MS}
    out = {}
    for r in sorted(req.values(), key=lambda v: -v["asked_ms"])[:limit]:
        try:
            out[r["key"]] = dict(r, candles=_candles(r["symbol"], r["tf"], now_ms))
        except Exception as exc:                            # noqa: BLE001
            print(f"kronos pull: {r['symbol']}: {type(exc).__name__}", file=__import__("sys").stderr)
    return out


def push(folder: Path, forecasts: dict, now_ms: int) -> None:
    for key, fc in forecasts.items():
        save_forecast(folder, key, fc, now_ms)


if __name__ == "__main__":
    import sys
    root = Path(__file__).resolve().parents[2] / "data" / "trade" / "kronos"
    if sys.argv[1:] == ["--pull"]:
        sys.stdout.write(json.dumps(pull(root, int(time.time() * 1000))))
    elif sys.argv[1:] == ["--push"]:
        push(root, json.load(sys.stdin), int(time.time() * 1000))
    else:
        main()
