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
    reserve: float = 0.003           # доля денег под комиссию и спред: иначе кэш после покупок уходит в минус


@dataclass(frozen=True)
class Order:
    symbol: str
    side: str          # buy | sell
    qty: float
    notional: float    # оценка суммы в USDT по текущей цене
    reduce: bool = False   # только уменьшает позицию (закрытие) — на фьючерсах reduceOnly


def breached(equity: float, peak: float, limits: Limits) -> bool:
    """Сработал ли стоп по просадке."""
    return peak > 0 and equity < peak * (1.0 - limits.max_drawdown)


def target_weights(weights: dict[str, float], limits: Limits) -> dict[str, float]:
    """Применить потолки к весам сигнала. Срезанное остаётся в кэше."""
    out = {s: max(min(w, limits.max_coin_weight), -limits.max_coin_weight)
           for s, w in weights.items()}
    total = sum(abs(w) for w in out.values())
    if total > limits.max_gross > 0:
        k = limits.max_gross / total
        out = {s: w * k for s, w in out.items()}
    return out


def plan(weights: dict[str, float], *, equity: float, cash: float,
         positions: dict[str, float], prices: dict[str, float],
         limits: Limits) -> list[Order]:
    """Заявки, переводящие текущие позиции в целевые веса (вес со знаком: − шорт).

    Сначала закрытия и уменьшения позиций, потом открытия. Переворот (лонг в
    шорт и обратно) — это закрытие целиком и открытие заново. Открытия
    уменьшаются пропорционально так, чтобы суммарная позиция по модулю не
    превысила капитал за вычетом запаса под комиссию: плеча бот не берёт.
    Для лонга без шортов это то же, что «покупки на деньги от продаж».
    """
    target = target_weights(weights, limits)
    reduce: list[Order] = []
    opens: list[Order] = []
    for sym in sorted(set(target) | set(positions)):
        px = prices.get(sym)
        if not px or px <= 0:
            continue
        have = positions.get(sym, 0.0)
        want = target.get(sym, 0.0) * equity / px
        close_side = "sell" if have > 0 else "buy"
        if have and (want == 0 or (want > 0) != (have > 0)):
            # выход или переворот — закрытие целиком, даже если остаток меньше минимума
            reduce.append(Order(sym, close_side, abs(have), abs(have) * px, True))
            have = 0.0
        delta = want - have
        notional = abs(delta) * px
        if not delta or notional < limits.min_order:
            continue
        if have and abs(want) < abs(have):
            reduce.append(Order(sym, close_side, abs(delta), notional, True))
        else:
            opens.append(Order(sym, "buy" if delta > 0 else "sell", abs(delta), notional))
    reduced = {o.symbol: o.qty * (1 if o.side == "buy" else -1) for o in reduce}
    held = sum(abs(positions.get(s, 0.0) + reduced.get(s, 0.0)) * prices[s]
               for s in positions if prices.get(s))
    room = equity * (1.0 - limits.reserve) - held
    need = sum(o.notional for o in opens)
    if need > room > 0:
        k = room / need
        opens = [Order(o.symbol, o.side, o.qty * k, o.notional * k) for o in opens
                 if o.notional * k >= limits.min_order]
    elif room <= 0:
        opens = []
    return reduce + opens
