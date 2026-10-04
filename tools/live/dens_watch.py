"""Жизненный цикл плотностей: стоит ли ещё заявка, которую нашёл круг.

Круг пересобирает `densities.html` раз в несколько минут, а заявку могут
съесть или снять за секунды. Поэтому между кругами процесс свечей раз в
полминуты сверяет каждую показанную плотность со свежим стаканом:

* `live` — на этой цене всё ещё стоит не меньше трети исходного объёма;
* `eaten` — объём ушёл, и цена с момента снимка доходила до этой цены
  (заявку разобрали сделками);
* `pulled` — объём ушёл, а цена до неё не доходила (заявку сняли);
* `unknown` — цена за пределами отданного биржей стакана, проверить нечем.

Итог — `kl/dens_state.json`, ключ `SYMBOL|side|price`; страница по нему
убирает съеденные и снятые плотности из таблицы, вкладки и графика.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from tools.live.market_stats import Banned, _get, banned

API = "https://fapi.binance.com/fapi/v1/depth"
NEAR = 1.5                  # сверяем только плотности ближе 1,5% — их и могут съесть
LIMIT = 500                 # вес 10; 1000 уровней стоили бы 20
KEEP = 0.33                 # меньше трети исходного объёма — заявки больше нет


def snapshot(path: Path) -> tuple[float, dict[str, list[dict]]]:
    """(время файла, {symbol: [плотности]}) из densities.html."""
    s = path.read_text(encoding="utf-8")
    m = re.search(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', s, re.S)
    d = json.loads(m.group(1)) if m else {}
    return path.stat().st_mtime, {c["symbol"]: c.get("densities") or [] for c in d.get("coins", [])}


def key(sym: str, d: dict) -> str:
    return f"{sym}|{d['side']}|{round(d['price'] * 1e8)}"


def _book(sym: str) -> dict | None:
    try:
        return _get("/fapi/v1/depth", symbol=sym, limit=LIMIT)
    except Exception:                               # noqa: BLE001
        return None


def classify(d: dict, book: dict, touched: bool) -> dict:
    side = d["side"]
    levels = book.get("asks" if side == "ask" else "bids") or []
    if not levels:
        return {"s": "unknown"}
    far = float(levels[-1][0])
    if (side == "ask" and d["price"] > far) or (side == "bid" and d["price"] < far):
        return {"s": "unknown"}
    qty = 0.0
    for p, q in levels:
        if abs(float(p) - d["price"]) <= d["price"] * 1e-9:
            qty = float(q)
            break
    left = qty * d["price"]
    if left >= KEEP * (d.get("notional") or 0):
        return {"s": "live", "left": round(left)}
    return {"s": "eaten" if touched else "pulled", "left": round(left)}


def check(path: Path, touched_since: Callable[[str, str, float, float], bool],
          workers: int = 6) -> dict:
    """Сверить все плотности снимка со стаканом. touched_since(sym, side, price, t)."""
    if banned():
        raise Banned("ждём снятия ограничения Binance")
    mtime, by_sym = snapshot(path)
    since = mtime - 150                             # стакан снимают в начале страницы
    by_sym = {s: [d for d in ds if abs(d.get("distance_pct") or 99) <= NEAR] for s, ds in by_sym.items()}
    syms = [s for s, ds in by_sym.items() if ds]
    with ThreadPoolExecutor(3) as ex:
        books = dict(zip(syms, ex.map(_book, syms)))
    items: dict[str, dict] = {}
    for sym in syms:
        book = books.get(sym)
        for d in by_sym[sym]:
            if book is None:
                items[key(sym, d)] = {"s": "unknown"}
                continue
            items[key(sym, d)] = classify(d, book, touched_since(sym, d["side"], d["price"], since))
    counts: dict[str, int] = {}
    for v in items.values():
        counts[v["s"]] = counts.get(v["s"], 0) + 1
    return {"snapshot": int(mtime * 1000), "counts": counts, "items": items}
