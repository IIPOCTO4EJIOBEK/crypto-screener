"""Рыночные метрики для таблицы главной: то, чего нет в свечах базы.

С Binance USDT-M за один проход по составу:

* `ticker/24hr` (один запрос на все монеты) — сделок за сутки;
* `premiumIndex` (один запрос) — текущий funding и время следующего;
* по каждой монете `klines 1m` за час — сделки в минуту сейчас и CVD за час
  (покупки тейкером минус продажи, в USDT);
* `klines 1d` за 8 дней — дневной и недельный high/low;
* `openInterestHist` 5m за сутки — изменение открытого интереса за 1ч и 24ч;
* `globalLongShortAccountRatio` — доля счетов в лонге к доле в шорте.

Ошибка любого запроса даёт пустое поле, а не падение страницы.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import requests

API = "https://fapi.binance.com"
TIMEOUT = 10
# Binance за превышение веса сначала отвечает 429, потом банит IP (418) на всё
# время, пока запросы продолжаются. Поймали — молчим до снятия бана: от этого
# IP живёт и круг скринера.
BANNED_UNTIL = 0.0


class Banned(RuntimeError):
    pass


def banned() -> bool:
    return time.time() < BANNED_UNTIL


def _get(path: str, **params):
    global BANNED_UNTIL
    if banned():
        raise Banned("ждём снятия ограничения Binance")
    from src.data import binance_limits
    binance_limits.acquire(API + path, params)
    r = requests.get(API + path, params=params, timeout=TIMEOUT)
    binance_limits.observe(API + path, r.status_code, r.headers)
    if r.status_code in (418, 429):
        wait = float(r.headers.get("Retry-After") or 0) or 600.0
        BANNED_UNTIL = time.time() + max(wait, 120.0)
        raise Banned(f"Binance {r.status_code}, пауза {wait:g} с")
    r.raise_for_status()
    return r.json()


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception:                               # noqa: BLE001
        return None


def _one(sym: str) -> dict:
    out: dict = {}
    m1 = _safe(lambda: _get("/fapi/v1/klines", symbol=sym, interval="1m", limit=61))
    if m1 and len(m1) > 6:
        closed = m1[:-1]
        out["tpm"] = round(sum(int(k[8]) for k in closed[-5:]) / 5)
        # CVD: покупки тейкером (k[10]) минус продажи (k[7] - k[10])
        out["cvd1h"] = round(sum(2 * float(k[10]) - float(k[7]) for k in closed[-60:]))
    d1 = _safe(lambda: _get("/fapi/v1/klines", symbol=sym, interval="1d", limit=8))
    if d1:
        out["day_hi"], out["day_lo"] = float(d1[-1][2]), float(d1[-1][3])
        wk = d1[-7:]
        out["week_hi"] = max(float(k[2]) for k in wk)
        out["week_lo"] = min(float(k[3]) for k in wk)
    oi = _safe(lambda: _get("/futures/data/openInterestHist", symbol=sym, period="5m", limit=289))
    if oi and len(oi) > 12:
        v = [float(x["sumOpenInterestValue"]) for x in oi]
        out["oi"] = round(v[-1])
        out["oi1h"] = round((v[-1] - v[-13]) / v[-13] * 100, 2) if v[-13] else None
        out["oi24h"] = round((v[-1] - v[0]) / v[0] * 100, 2) if v[0] and len(v) > 280 else None
    ls = _safe(lambda: _get("/futures/data/globalLongShortAccountRatio", symbol=sym, period="5m", limit=1))
    if ls:
        out["ls"] = round(float(ls[-1]["longShortRatio"]), 2)
    return out


def fetch(symbols: list[str], workers: int = 8) -> dict[str, dict]:
    """{symbol: {tpm, tpm_avg, cvd1h, day_hi, ..., funding, next_funding}}."""
    started = time.time()
    res: dict[str, dict] = {s: {} for s in symbols}
    if banned():
        raise Banned("ждём снятия ограничения Binance")
    t24 = _safe(lambda: _get("/fapi/v1/ticker/24hr")) or []
    for t in t24:
        if t.get("symbol") in res:
            res[t["symbol"]]["tpm_avg"] = round(int(t.get("count") or 0) / 1440)
            res[t["symbol"]]["price"] = float(t.get("lastPrice") or 0) or None
            res[t["symbol"]]["ch24h"] = round(float(t.get("priceChangePercent") or 0), 2)
            res[t["symbol"]]["qv24"] = round(float(t.get("quoteVolume") or 0))
            res[t["symbol"]]["trades24"] = int(t.get("count") or 0)
    prem = _safe(lambda: _get("/fapi/v1/premiumIndex")) or []
    for p in prem:
        if p.get("symbol") in res:
            res[p["symbol"]]["funding"] = round(float(p.get("lastFundingRate") or 0) * 100, 4)
            res[p["symbol"]]["next_funding"] = int(p.get("nextFundingTime") or 0)
    with ThreadPoolExecutor(workers) as ex:
        for sym, one in zip(symbols, ex.map(_one, symbols)):
            res[sym].update(one)
    res["_elapsed"] = round(time.time() - started, 1)   # type: ignore[assignment]
    return res
