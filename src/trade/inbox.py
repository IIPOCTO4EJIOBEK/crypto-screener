"""Внешние сигналы: ручная сделка в один клик и вебхук (TradingView и свой JSON).

Сигнал — JSON-файл в каталоге `inbox/` бота. Его кладёт приёмник вебхука
(`tools/trade/webhook.py`) или кнопка на странице скринера. На ближайшем
круге бот превращает файл в строку сигнала и открывает позицию тем же
исполнением и с тем же ведением (тейки, безубыток, трейлинг, лимиты), что и
сигналы скринера. Фильтр тренда и политика «только измеренные» к таким
сигналам не применяются: это решение трейдера.

Формат (обязательны symbol и side; остальное по желанию):

    {"symbol": "BTCUSDT", "side": "long",           # long | short | buy | sell
     "stop": 61000,          # или "stop_pct": 1.5  (% от цены входа)
     "target": 66000,        # или "rr": 2          (цель в R от стопа; по умолчанию 2)
     "tf": "1h",             # от него зависит истечение: 40 свечей; по умолчанию 1h
     "note": "пробой уровня", "source": "tradingview"}

Без стопа сигнал не берётся: размер позиции считается от риска.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

SIDES = {"long": "long", "buy": "long", "short": "short", "sell": "short"}
DEFAULT_STOP_PCT = None          # стоп обязателен — молча не придумываем


def parse(msg: dict, mid: float) -> tuple[dict | None, str | None]:
    """Сигнал → строка в формате скринера (или причина отказа)."""
    sym = str(msg.get("symbol") or msg.get("ticker") or "").upper().replace(".P", "")
    sym = sym.split(":")[-1]                    # BINANCE:BTCUSDT.P → BTCUSDT
    side = SIDES.get(str(msg.get("side") or msg.get("action") or "").lower())
    if not sym or not side:
        return None, "нужны symbol и side (long/short)"
    if mid <= 0:
        return None, "нет цены"
    sign = 1 if side == "long" else -1
    stop = msg.get("stop")
    if stop is None and msg.get("stop_pct") is not None:
        stop = mid * (1 - sign * float(msg["stop_pct"]) / 100)
    if stop is None:
        return None, "нет стопа (stop или stop_pct)"
    stop = float(stop)
    risk = (mid - stop) * sign
    if risk <= 0:
        return None, "стоп не с той стороны от цены"
    target = msg.get("target")
    target = float(target) if target is not None else mid + sign * float(msg.get("rr") or 2) * risk
    tf = msg.get("tf") if msg.get("tf") in ("1m", "5m", "15m", "30m", "1h", "4h") else "1h"
    src = str(msg.get("source") or "manual")
    return {
        "kind": src, "title": "ручная сделка" if src == "manual" else f"сигнал {src}",
        "tf": tf, "symbol": sym, "direction": side, "entry": mid, "stop": stop,
        "target": target, "triggered": True, "age_candles": 0, "manual": True,
        "reasons": [str(msg["note"])] if msg.get("note") else [],
        "measured": None, "exp_net": None, "ts": int(msg.get("ts") or time.time() * 1000),
    }, None


def take(root: Path, mid_of) -> tuple[list[dict], list[tuple[str, str]]]:
    """Забрать файлы из inbox/: (строки сигналов, [(файл, причина отказа)]).

    Файл удаляется сразу после чтения: один сигнал — одна попытка.
    """
    box = Path(root) / "inbox"
    rows, bad = [], []
    if not box.is_dir():
        return rows, bad
    for f in sorted(box.glob("*.json")):
        try:
            msg = json.loads(f.read_text())
            mid = mid_of(str(msg.get("symbol") or msg.get("ticker") or "").upper()
                         .replace(".P", "").split(":")[-1])
            row, why = parse(msg, mid)
        except Exception as exc:                               # noqa: BLE001
            row, why = None, f"не разобран: {str(exc)[:100]}"
        f.unlink(missing_ok=True)
        if row:
            row["key_suffix"] = f.stem
            rows.append(row)
        else:
            bad.append((f.name, why))
    return rows, bad


def put(root: Path, msg: dict) -> Path:
    """Положить сигнал в inbox/ атомарно (так пишет вебхук и кнопка)."""
    box = Path(root) / "inbox"
    box.mkdir(parents=True, exist_ok=True)
    name = f"{int(time.time() * 1000)}-{abs(hash(json.dumps(msg, sort_keys=True))) % 10**6}"
    tmp = box / f"{name}.tmp"
    tmp.write_text(json.dumps(msg, ensure_ascii=False))
    out = box / f"{name}.json"
    tmp.replace(out)
    return out
