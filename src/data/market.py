"""Публичные рыночные данные с бирж: свечи, стакан, лента сделок.

Ключ не нужен — все запросы к публичным эндпоинтам.

Сессия создаётся с trust_env=False: переменные HTTPS_PROXY из окружения
игнорируются. Это важно, потому что прокси на этом хосте режет часть
биржевых доменов по таймауту (см. docs/00-состояние.md).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import requests

TIMEOUT = 30

# Длительность интервала в секундах — нужна для расчёта окна истории.
INTERVALS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1d": 86400,
}


@dataclass(frozen=True)
class Candle:
    ts: int          # открытие свечи, миллисекунды UTC
    open: float
    high: float
    low: float
    close: float
    volume: float    # объём в базовой монете
    quote_volume: float  # объём в котируемой (обычно USDT)
    trades: int      # число сделок; 0, если биржа не отдаёт


@dataclass(frozen=True)
class Level:
    price: float
    size: float


@dataclass(frozen=True)
class OrderBook:
    exchange: str
    symbol: str
    ts: int
    bids: list[Level]  # по убыванию цены
    asks: list[Level]  # по возрастанию цены

    @property
    def best_bid(self) -> float:
        return self.bids[0].price

    @property
    def best_ask(self) -> float:
        return self.asks[0].price

    @property
    def mid(self) -> float:
        return (self.best_bid + self.best_ask) / 2

    @property
    def spread(self) -> float:
        return self.best_ask - self.best_bid

    def imbalance(self, depth: int = 20) -> float:
        """Дисбаланс объёмов бид/аск в пределах depth уровней.

        От -1 (всё в асках) до +1 (всё в бидах).
        """
        b = sum(l.size for l in self.bids[:depth])
        a = sum(l.size for l in self.asks[:depth])
        if b + a == 0:
            return 0.0
        return (b - a) / (b + a)


def _session(use_env_proxy: bool) -> requests.Session:
    s = requests.Session()
    s.trust_env = use_env_proxy
    s.headers["User-Agent"] = "crypto-screener/0.1"
    return s


# Два маршрута. Прокси из окружения режет одни биржи и ускоряет другие:
# Binance vision напрямую отдаёт ответ за 30 с и обрывает его, через прокси —
# за 2 с целиком; Bybit через прокси получает 403 по стране, а напрямую
# отвечает за 0.4 с. Поэтому маршрут выбирается по бирже.
_DIRECT = _session(False)
_VIA_PROXY = _session(True)

_PREFER_PROXY = {"binance": True, "bybit": False, "okx": False}


def _get(url: str, params: dict | None = None, use_proxy: bool = False) -> object:
    """Запрос с повтором: при неудаче пробуем второй маршрут."""
    routes = [_VIA_PROXY, _DIRECT] if use_proxy else [_DIRECT, _VIA_PROXY]
    last: Exception | None = None
    for s in routes:
        for attempt in range(2):
            try:
                from src.data import binance_limits
                binance_limits.acquire(url, params)
                r = s.get(url, params=params, timeout=TIMEOUT)
                binance_limits.observe(url, r.status_code, r.headers)
                r.raise_for_status()
                return r.json()
            except Exception as e:  # сеть биржи нестабильна
                if isinstance(e, requests.HTTPError) and e.response is not None and e.response.status_code in (418, 429):
                    raise RuntimeError("Binance rate limited; shared cooldown recorded") from e
                last = e
                time.sleep(0.4 * (attempt + 1))
    raise RuntimeError(f"{url}: {last}")


# --------------------------------------------------------------------------
# Binance — два рынка, и это не одно и то же.
#
# Спот: публичный market data отдаётся через data-api.binance.vision. Маршрут
# через прокси: vision отвечает через прокси за 2 с целиком, напрямую —
# медленно и с обрывом.
#
# Перпетуал USDT-M: fapi.binance.com. Прокси здесь не помощник, а препятствие:
# он стоит в США, Binance закрывает США, и 451 «restricted location» приходит
# именно на запрос через прокси. Напрямую тот же адрес отвечает 200 (проверено
# 30.09.2026: 10 запросов из 10). Поэтому у фьючерсных функций use_proxy=False.
# Раньше в этом месте стояло, что домен закрыт из РФ по обоим маршрутам: это
# было неверно дважды — не из РФ и не по обоим.
#
# Что берём откуда: измерение и живые сигналы — из перпа (там исполнение,
# ликвидность и шорт), спот — контрольный ряд для базиса.
# --------------------------------------------------------------------------
_BINANCE = "https://data-api.binance.vision"
_BINANCE_FAPI = "https://fapi.binance.com"

# fapi принимает стакан только этими ступенями, произвольное число — ошибка.
_FAPI_DEPTH = (5, 10, 20, 50, 100, 500, 1000)


def _fapi_depth(depth: int) -> int:
    allowed = [n for n in _FAPI_DEPTH if n <= depth]
    return allowed[-1] if allowed else _FAPI_DEPTH[0]


def binance_ohlcv(symbol: str, interval: str = "1m", limit: int = 1000,
                  end_ms: int | None = None) -> list[Candle]:
    params = {"symbol": symbol.upper(), "interval": interval, "limit": min(limit, 1000)}
    if end_ms:
        params["endTime"] = end_ms
    raw = _get(f"{_BINANCE}/api/v3/klines", params, use_proxy=True)
    return [Candle(int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]),
                   float(c[5]), float(c[7]), int(c[8])) for c in raw]


def binance_orderbook(symbol: str, depth: int = 100) -> OrderBook:
    raw = _get(f"{_BINANCE}/api/v3/depth",
               {"symbol": symbol.upper(), "limit": min(depth, 5000)},
               use_proxy=True)
    return OrderBook("binance", symbol.upper(), int(time.time() * 1000),
                     [Level(float(p), float(q)) for p, q in raw["bids"]],
                     [Level(float(p), float(q)) for p, q in raw["asks"]])


def binance_futures_ohlcv(symbol: str, interval: str = "1m", limit: int = 1000,
                          end_ms: int | None = None) -> list[Candle]:
    """Свечи USDT-M перпетуала. Те же поля, что у спота, лимит до 1500."""
    params = {"symbol": symbol.upper(), "interval": interval, "limit": min(limit, 1500)}
    if end_ms:
        params["endTime"] = end_ms
    raw = _get(f"{_BINANCE_FAPI}/fapi/v1/klines", params, use_proxy=False)
    return [Candle(int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]),
                   float(c[5]), float(c[7]), int(c[8])) for c in raw]


def binance_funding_history(symbol: str, start_ms: int,
                            end_ms: int | None = None) -> list[tuple[int, float]]:
    """Фактические начисления фандинга USDT-M: (время начисления, ставка).

    Ставка положительна — лонг платит шорту. Один запрос отдаёт до 1000
    начислений, этого хватает на месяцы при интервале 4-8 часов.
    """
    params = {"symbol": symbol.upper(), "startTime": start_ms, "limit": 1000}
    if end_ms:
        params["endTime"] = end_ms
    raw = _get(f"{_BINANCE_FAPI}/fapi/v1/fundingRate", params, use_proxy=False)
    return [(int(r["fundingTime"]), float(r["fundingRate"])) for r in raw]


def binance_futures_orderbook(symbol: str, depth: int = 100) -> OrderBook:
    raw = _get(f"{_BINANCE_FAPI}/fapi/v1/depth",
               {"symbol": symbol.upper(), "limit": _fapi_depth(depth)},
               use_proxy=False)
    return OrderBook("binance_futures", symbol.upper(), int(time.time() * 1000),
                     [Level(float(p), float(q)) for p, q in raw["bids"]],
                     [Level(float(p), float(q)) for p, q in raw["asks"]])


# --------------------------------------------------------------------------
# Вселенная: отбор монет по обороту.
#
# Биржа торгует не только монетами. У Binance USDT-M есть токенизированное
# золото, серебро, нефть, акции и ETF, и по обороту они стоят в одном ряду
# с крупнейшими монетами: 30.09.2026 среди 59 инструментов с оборотом свыше
# 100 млн в сутки двадцать оказались не монетами (XAUUSDT — 1.8 млрд,
# CLUSDT, SOXLUSDT, SKHYNIXUSDT и подобные).
#
# Отличаются они парой полей, а не именем: у монет underlyingType = COIN и
# contractType = PERPETUAL, у остальных — EQUITY / COMMODITY / PREMARKET /
# INDEX и TRADIFI_PERPETUAL. Отбор идёт по этим полям, а не по списку
# исключений: список имён пришлось бы править руками при каждом листинге.
# --------------------------------------------------------------------------

def binance_futures_contracts() -> list[dict]:
    """Все контракты USDT-M. Один запрос на весь рынок, а не на монету."""
    return _get(f"{_BINANCE_FAPI}/fapi/v1/exchangeInfo", None,
                use_proxy=False)["symbols"]


def binance_futures_volumes() -> dict[str, float]:
    """Суточный оборот в котируемой валюте по всем контрактам. Один запрос."""
    raw = _get(f"{_BINANCE_FAPI}/fapi/v1/ticker/24hr", None, use_proxy=False)
    return {r["symbol"]: float(r["quoteVolume"]) for r in raw}


def futures_coin_universe(min_quote_volume: float, *, quote: str = "USDT",
                          contracts: list[dict] | None = None,
                          volumes: dict[str, float] | None = None
                          ) -> list[tuple[str, float]]:
    """Монеты-перпетуалы с суточным оборотом не ниже порога.

    Возвращает (символ, оборот за сутки) по убыванию оборота. Оборот — в
    котируемой валюте (для USDT это доллары, но это совпадение, а не
    определение: поле так и называется — quoteVolume).
    """
    if contracts is None:
        contracts = binance_futures_contracts()
    if volumes is None:
        volumes = binance_futures_volumes()
    out: list[tuple[str, float]] = []
    for c in contracts:
        if (c.get("underlyingType") != "COIN"
                or c.get("contractType") != "PERPETUAL"
                or c.get("status") != "TRADING"
                or c.get("quoteAsset") != quote):
            continue
        sym = c.get("symbol") or ""
        v = volumes.get(sym)
        if sym and v is not None and v >= min_quote_volume:
            out.append((sym, v))
    out.sort(key=lambda x: (-x[1], x[0]))
    return out


# --------------------------------------------------------------------------
# Bybit v5 — доступен напрямую.
# --------------------------------------------------------------------------
_BYBIT = "https://api.bybit.com"
_BYBIT_INTERVAL = {"1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
                   "1h": "60", "4h": "240", "1d": "D"}


def bybit_ohlcv(symbol: str, interval: str = "1m", limit: int = 1000,
                end_ms: int | None = None) -> list[Candle]:
    params = {"category": "spot", "symbol": symbol.upper(),
              "interval": _BYBIT_INTERVAL[interval], "limit": min(limit, 1000)}
    if end_ms:
        params["end"] = end_ms
    raw = _get(f"{_BYBIT}/v5/market/kline", params)
    if raw.get("retCode") != 0:
        raise RuntimeError(f"bybit: {raw.get('retMsg')}")
    # Bybit отдаёт свечи от новых к старым
    rows = sorted(raw["result"]["list"], key=lambda r: int(r[0]))
    return [Candle(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]),
                   float(r[5]), float(r[6]), 0) for r in rows]


def bybit_orderbook(symbol: str, depth: int = 50) -> OrderBook:
    raw = _get(f"{_BYBIT}/v5/market/orderbook",
               {"category": "spot", "symbol": symbol.upper(), "limit": min(depth, 200)})
    if raw.get("retCode") != 0:
        raise RuntimeError(f"bybit: {raw.get('retMsg')}")
    res = raw["result"]
    return OrderBook("bybit", symbol.upper(), int(res["ts"]),
                     [Level(float(p), float(q)) for p, q in res["b"]],
                     [Level(float(p), float(q)) for p, q in res["a"]])


# --------------------------------------------------------------------------
# OKX v5 — доступен напрямую. Символ в формате BTC-USDT, свечи от новых.
# --------------------------------------------------------------------------
_OKX = "https://www.okx.com"
_OKX_INTERVAL = {"1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
                 "1h": "1H", "4h": "4H", "1d": "1D"}


def okx_symbol(symbol: str) -> str:
    s = symbol.upper()
    if "-" in s:
        return s
    for quote in ("USDT", "USDC", "USD", "BTC", "ETH"):
        if s.endswith(quote):
            return f"{s[:-len(quote)]}-{quote}"
    return s


def okx_ohlcv(symbol: str, interval: str = "1m", limit: int = 300,
              end_ms: int | None = None) -> list[Candle]:
    params = {"instId": okx_symbol(symbol), "bar": _OKX_INTERVAL[interval],
              "limit": min(limit, 300)}
    if end_ms:
        params["after"] = end_ms
    raw = _get(f"{_OKX}/api/v5/market/candles", params)
    if raw.get("code") != "0":
        raise RuntimeError(f"okx: {raw.get('msg')}")
    rows = sorted(raw["data"], key=lambda r: int(r[0]))
    # OKX: [ts, o, h, l, c, vol(base), volQuote, volQuoteAlt, confirm]
    return [Candle(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]),
                   float(r[5]), float(r[6]), 0) for r in rows]


def okx_orderbook(symbol: str, depth: int = 50) -> OrderBook:
    raw = _get(f"{_OKX}/api/v5/market/books",
               {"instId": okx_symbol(symbol), "sz": min(depth, 400)})
    if raw.get("code") != "0":
        raise RuntimeError(f"okx: {raw.get('msg')}")
    d = raw["data"][0]
    return OrderBook("okx", okx_symbol(symbol), int(d["ts"]),
                     [Level(float(p), float(q)) for p, q, *_ in d["bids"]],
                     [Level(float(p), float(q)) for p, q, *_ in d["asks"]])


EXCHANGES = {
    "binance": (binance_ohlcv, binance_orderbook),
    "binance_futures": (binance_futures_ohlcv, binance_futures_orderbook),
    "bybit": (bybit_ohlcv, bybit_orderbook),
    "okx": (okx_ohlcv, okx_orderbook),
}


def ohlcv(exchange: str, symbol: str, interval: str = "1m",
          limit: int = 300, end_ms: int | None = None) -> list[Candle]:
    return EXCHANGES[exchange][0](symbol, interval, limit, end_ms)


def orderbook(exchange: str, symbol: str, depth: int = 50) -> OrderBook:
    return EXCHANGES[exchange][1](symbol, depth)


if __name__ == "__main__":
    for name in EXCHANGES:
        try:
            c = ohlcv(name, "BTCUSDT", "1m", 3)
            ob = orderbook(name, "BTCUSDT", 5)
            print(f"{name:8} свечей {len(c)}, последняя цена {c[-1].close}, "
                  f"стакан bid {ob.best_bid} / ask {ob.best_ask}, "
                  f"спред {ob.spread:.2f}, дисбаланс {ob.imbalance(5):+.3f}")
        except Exception as e:
            print(f"{name:8} ошибка: {type(e).__name__}: {str(e)[:90]}")
