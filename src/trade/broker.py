"""Исполнение заявок: бумажное и на бирже.

Бумажный брокер ничего не отправляет на биржу. Он берёт **живой стакан**
(публичные данные, ключ не нужен) и считает, по какой средней цене прошла бы
рыночная заявка такого размера, проходя уровни стакана один за другим, плюс
комиссия тейкера. Это и есть то, чего не хватало бэктесту: спред и
проскальзывание там константа (1 б.п.), а здесь — замер на каждой сделке.

Биржевой брокер ставит настоящие рыночные заявки через ccxt — на тестовой
сети (`testnet=True`, отдельные тестовые ключи, деньги ненастоящие) или на
живом счёте. Ключи берутся только из окружения (`.env`), в коде и в журнале
их нет.

Рынок по умолчанию — спот: длинная сторона без плеча, ликвидации нет, фандинг
не платится. Перпетуал (`market="future"`) — с плечом 1 и продажей только на
закрытие (reduceOnly), чтобы бот физически не мог открыть шорт.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.data import market as md
from src.data.market import OrderBook

# комиссия тейкера без скидок: спот 0.1 %, USDT-M 0.05 %
FEES = {"spot": 0.001, "future": 0.0005}


@dataclass(frozen=True)
class Fill:
    symbol: str
    side: str
    qty: float          # сколько монет пришло (buy) или ушло (sell)
    price: float        # средняя цена исполнения
    fee: float          # комиссия в USDT
    mid: float          # середина стакана в момент заявки — для замера проскальзывания
    order_id: str | None = None

    @property
    def cash_delta(self) -> float:
        """Изменение денег бота: покупка тратит, продажа приносит, комиссия всегда минус."""
        gross = self.qty * self.price
        return (-gross if self.side == "buy" else gross) - self.fee

    @property
    def slippage_bp(self) -> float:
        """Цена хуже середины стакана, в б.п. (спред/2 + проход по уровням)."""
        if self.mid <= 0:
            return 0.0
        sign = 1 if self.side == "buy" else -1
        return sign * (self.price / self.mid - 1.0) * 1e4


def walk_book(book: OrderBook, side: str, qty: float) -> float | None:
    """Средняя цена рыночной заявки на qty монет по уровням стакана.

    None — если глубины стакана не хватило: такую заявку бумажный брокер не
    исполняет, вместо того чтобы выдумать цену за пределами стакана.
    """
    levels = book.asks if side == "buy" else book.bids
    left, cost = qty, 0.0
    for lvl in levels:
        take = min(left, lvl.size)
        cost += take * lvl.price
        left -= take
        if left <= 1e-12:
            return cost / qty
    return None


def fetch_book(symbol: str, market: str, depth: int = 100) -> OrderBook:
    if market == "future":
        return md.binance_futures_orderbook(symbol, depth)
    return md.binance_orderbook(symbol, depth)


class PaperBroker:
    """Исполнение по живому стакану без отправки заявок."""

    live = False

    def __init__(self, market: str = "spot", fee: float | None = None,
                 book=fetch_book):
        self.market = market
        self.fee = FEES[market] if fee is None else fee
        self._book = book

    def prepare(self, symbols: list[str]) -> None:
        pass

    def mid(self, symbol: str) -> float:
        return self._book(symbol, self.market).mid

    def execute(self, symbol: str, side: str, qty: float) -> Fill | None:
        book = self._book(symbol, self.market)
        price = walk_book(book, side, qty)
        if price is None:
            return None
        return Fill(symbol, side, qty, price, qty * price * self.fee, book.mid)

    def free_quote(self) -> float | None:
        return None

    def base_balance(self, symbol: str) -> float | None:
        return None


class ExchangeBroker:
    """Настоящие рыночные заявки на Binance через ccxt."""

    live = True

    def __init__(self, *, api_key: str, secret: str, market: str = "spot",
                 testnet: bool = False, quote: str = "USDT"):
        import ccxt  # тяжёлый импорт — только когда брокер нужен

        if not api_key or not secret:
            raise ValueError("нет ключа: задайте его в .env (см. docs/04-торговля.md)")
        self.market = market
        self.quote = quote
        self.testnet = testnet
        self.ex = ccxt.binance({
            "apiKey": api_key, "secret": secret, "enableRateLimit": True,
            "options": {"defaultType": "spot" if market == "spot" else "future"},
        })
        if testnet:
            if market == "spot":
                self.ex.set_sandbox_mode(True)
            else:
                # у USDT-M старой тестовой сети в ccxt больше нет — только demo
                self.ex.enable_demo_trading(True)
        self.ex.load_markets()

    def _sym(self, symbol: str) -> str:
        base = symbol[: -len(self.quote)]
        return (f"{base}/{self.quote}" if self.market == "spot"
                else f"{base}/{self.quote}:{self.quote}")

    def prepare(self, symbols: list[str]) -> None:
        if self.market == "future":
            for s in symbols:
                self.ex.set_leverage(1, self._sym(s))

    def mid(self, symbol: str) -> float:
        t = self.ex.fetch_ticker(self._sym(symbol))
        bid, ask = t.get("bid"), t.get("ask")
        return (bid + ask) / 2 if bid and ask else float(t["last"])

    def free_quote(self) -> float:
        bal = self.ex.fetch_balance()
        return float((bal.get(self.quote) or {}).get("free") or 0.0)

    def base_balance(self, symbol: str) -> float | None:
        if self.market != "spot":
            return None
        bal = self.ex.fetch_balance()
        return float((bal.get(symbol[: -len(self.quote)]) or {}).get("free") or 0.0)

    def execute(self, symbol: str, side: str, qty: float) -> Fill | None:
        s = self._sym(symbol)
        amount = float(self.ex.amount_to_precision(s, qty))
        if amount <= 0:
            return None
        mid = self.mid(symbol)
        params = {"reduceOnly": True} if self.market == "future" and side == "sell" else {}
        o = self.ex.create_order(s, "market", side, amount, None, params)
        if not o.get("average") or o.get("filled") is None:
            o = self.ex.fetch_order(o["id"], s)
        filled = float(o.get("filled") or 0.0)
        price = float(o.get("average") or o.get("price") or mid)
        if filled <= 0:
            return None
        # Комиссия в USDT уменьшает деньги бота; комиссия в самой монете при
        # покупке уменьшает купленное количество; комиссия в BNB деньги бота
        # не трогает (её платит остаток BNB на счёте).
        base = symbol[: -len(self.quote)]
        cash_fee, got = 0.0, filled
        for f in o.get("fees") or ([o["fee"]] if o.get("fee") else []):
            cost, cur = float(f.get("cost") or 0.0), f.get("currency")
            if cur == self.quote:
                cash_fee += cost
            elif cur == base and side == "buy":
                got -= cost
        return Fill(symbol, side, got, price, cash_fee, mid, str(o.get("id")))

    def key_report(self) -> dict:
        """Права ключа (только чтение, без заявок): что разрешено и есть ли вывод."""
        out: dict = {}
        try:
            r = self.ex.sapi_get_account_apirestrictions()
            out = {k: r.get(k) for k in ("enableReading", "enableSpotAndMarginTrading",
                                         "enableFutures", "enableWithdrawals",
                                         "ipRestrict", "permitsUniversalTransfer")}
        except Exception as e:     # у тестовой сети этого метода нет
            out["restrictions_error"] = str(e)[:200]
        try:
            out["free_" + self.quote] = self.free_quote()
        except Exception as e:
            out["balance_error"] = str(e)[:200]
        return out
