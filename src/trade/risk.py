"""Лимиты риска и план заявок: из весов сигнала — список заявок.

Бот считает свой капитал отдельно от счёта: `capital` — бюджет, который ему
выделен, а не баланс биржи. Монеты, купленные руками, бот не видит и не
продаёт: его позиции — только те, что записаны в его журнале.

По умолчанию лимиты не меняют правило, измеренное в бэктесте: веса равные,
верхней границы на монету нет. Меняет его только стоп по просадке — он
останавливает торговлю целиком, а не подрезает позиции. Всё, что подрезает
(`max_coin_weight`, `max_gross`), бэктестом не проверено и включается
осознанно.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Limits:
    capital: float = 1000.0          # бюджет бота в USDT на старте
    max_drawdown: float = 0.35       # стоп: капитал ниже пика на эту долю — всё продать и встать
    max_coin_weight: float = 1.0     # верхняя граница веса одной монеты (1.0 — как в бэктесте)
    max_gross: float = 1.0           # доля капитала в рынке (1.0 — как в бэктесте, плеча нет)
    min_order: float = 10.0          # заявки меньше этой суммы в USDT не ставятся (минимум Binance)


@dataclass(frozen=True)
class Order:
    symbol: str
    side: str          # buy | sell
    qty: float
    notional: float    # оценка суммы в USDT по текущей цене


def breached(equity: float, peak: float, limits: Limits) -> bool:
    """Сработал ли стоп по просадке."""
    return peak > 0 and equity < peak * (1.0 - limits.max_drawdown)


def target_weights(weights: dict[str, float], limits: Limits) -> dict[str, float]:
    """Применить потолки к весам сигнала. Срезанное остаётся в кэше."""
    out = {s: min(w, limits.max_coin_weight) for s, w in weights.items()}
    total = sum(out.values())
    if total > limits.max_gross > 0:
        k = limits.max_gross / total
        out = {s: w * k for s, w in out.items()}
    return out


def plan(weights: dict[str, float], *, equity: float, cash: float,
         positions: dict[str, float], prices: dict[str, float],
         limits: Limits) -> list[Order]:
    """Заявки, переводящие текущие позиции в целевые веса.

    Сначала продажи, потом покупки: покупки идут на деньги от продаж. Если
    денег на все покупки не хватает (комиссия, округление), покупки
    уменьшаются пропорционально — бот не уходит в минус и не берёт плечо.
    """
    target = target_weights(weights, limits)
    sells: list[Order] = []
    buys: list[Order] = []
    for sym in sorted(set(target) | set(positions)):
        px = prices.get(sym)
        if not px or px <= 0:
            continue
        have = positions.get(sym, 0.0)
        want = target.get(sym, 0.0) * equity / px
        delta = want - have
        notional = abs(delta) * px
        if sym not in target and have > 0:
            # выход из монеты — целиком, даже если остаток меньше минимума
            sells.append(Order(sym, "sell", have, have * px))
        elif notional < limits.min_order:
            continue
        elif delta < 0:
            sells.append(Order(sym, "sell", -delta, notional))
        else:
            buys.append(Order(sym, "buy", delta, notional))
    budget = cash + sum(o.notional for o in sells)
    need = sum(o.notional for o in buys)
    if need > budget > 0:
        k = budget / need * 0.995       # запас на комиссию
        buys = [Order(o.symbol, o.side, o.qty * k, o.notional * k) for o in buys
                if o.notional * k >= limits.min_order]
    elif budget <= 0:
        buys = []
    return sells + buys
