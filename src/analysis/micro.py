"""Микроструктура стакана: что можно сказать о движении цены по самому стакану.

Зачем это отдельно от формаций. Верх стакана (спред, лучшие цены) почти ничего
не говорит: на BTC спред — сотые доли процента, и он одинаков на спокойном и на
готовом сорваться рынке. Значимую часть короткого горизонта даёт ФОРМА стакана
и, главное, её ИЗМЕНЕНИЕ между снимками: подросший бид и усохший аск означают
одно и то же — давление покупателя.

Набор мер и формулы взяты из открытого разбора микроструктуры
(https://github.com/krabduke/orderbook, MIT): imbalance, microprice, глубина в
полосе, стоимость немедленного взятия, наклон, поток заявок (OFI, Cont, Kukanov
и Stoikov) и лямбда Кайла. Здесь они переписаны под наш OrderBook.

Отдельная функция — прокси OFI по архиву bookDepth: там стакан приходит не
уровнями, а кумулятивной глубиной по полосам каждые 30 секунд, поэтому поток
заявок восстанавливается по приросту глубины на ближней полосе.
"""

from __future__ import annotations

from src.data.archive import BookDepthSnapshot
from src.data.market import OrderBook

# Полоса кумулятивной глубины, по которой архивный снимок даёт ближайший к
# середине объём. На 12-полосных днях это ±0.2 %, на 10-полосных (до
# 15.01.2026) полосы ±0.2 % нет, и ближайшей будет ±1 %; функция берёт ту,
# что есть, поэтому обе эпохи формата считаются одинаково.
NEAR_BAND_PCT = 0.2


# --------------------------------------------------------------------------
# Меры по одному стакану
# --------------------------------------------------------------------------
def microprice(ob: OrderBook) -> float:
    """Взвешенная по объёму середина.

    Сдвинута в сторону ТОНКОЙ стороны: если на биде объёма мало, а на аске
    много, следующая сделка скорее ударит по биду, и цена пойдёт вниз.
    Обычная середина этого не видит.
    """
    bid, ask = ob.bids[0], ob.asks[0]
    total = bid.size + ask.size
    if total <= 0:
        return ob.mid
    return (bid.price * ask.size + ask.price * bid.size) / total


def microprice_tilt_bps(ob: OrderBook) -> float:
    """Насколько microprice ушёл от середины, в базисных пунктах."""
    mid = ob.mid
    if mid <= 0:
        return 0.0
    return (microprice(ob) - mid) / mid * 10_000


def depth_within(ob: OrderBook, bps: float) -> tuple[float, float]:
    """Объём, стоящий в полосе ±bps от середины, отдельно по сторонам.

    Полезно тем, что не зависит от шага цены: у BTC 20 уровней — это десятые
    доли процента, у DOGE — проценты, и сравнение «20 уровней против 20
    уровней» между монетами бессмысленно, а «объём в 10 bp» — осмысленно.
    """
    band = ob.mid * bps / 10_000
    bid = sum(l.size for l in ob.bids if l.price >= ob.mid - band)
    ask = sum(l.size for l in ob.asks if l.price <= ob.mid + band)
    return bid, ask


def sweep_cost_bps(ob: OrderBook, notional: float, side: str) -> float | None:
    """Во что обойдётся немедленно взять notional, в bp от середины.

    Величина всегда положительная: это издержка, а не направление движения.
    Купить дороже середины и продать дешевле — одинаковая потеря, и знак
    стороны уже задан аргументом.

    None означает, что видимого стакана не хватает на такой объём. Это не
    ошибка, а ответ: книги такой толщины рядом с ценой нет.

    Мера отвечает на вопрос, который спред не освещает: спред может быть
    копеечным, а сто тысяч долларов всё равно двинут цену, если стакан тонкий.
    """
    levels = ob.asks if side == "buy" else ob.bids
    remaining = notional
    qty = 0.0
    cost = 0.0
    for level in levels:
        take = min(remaining, level.price * level.size)
        qty += take / level.price
        cost += take
        remaining -= take
        if remaining <= 1e-9:
            break
    else:
        return None
    if qty <= 0:
        return None
    vwap = cost / qty
    sign = 1 if side == "buy" else -1
    return sign * (vwap - ob.mid) / ob.mid * 10_000


def slope(ob: OrderBook, depth: int = 10) -> tuple[float, float]:
    """Объём на базисный пункт расстояния от середины, по сторонам.

    Крутой стакан поглощает поток (цена вязнет), пологий — проваливается.
    """
    def one_side(levels) -> float:
        total = distance = 0.0
        for level in levels[:depth]:
            total += level.size
            distance += abs(level.price - ob.mid) / ob.mid * 10_000 * level.size
        if total <= 0 or distance <= 0:
            return 0.0
        return total / (distance / total)

    return one_side(ob.bids), one_side(ob.asks)


# --------------------------------------------------------------------------
# Меры по последовательности стаканов
# --------------------------------------------------------------------------
def order_flow_imbalance(prev: OrderBook, curr: OrderBook,
                         depth: int = 5) -> float:
    """Поток заявок между двумя снимками (Cont, Kukanov, Stoikov), в единицах объёма.

    Сравниваются уровни по порядку от середины. Выросший бид и усохший аск
    складываются в плюс; усохший бид и выросший аск — в минус; ушедший уровень
    считается целиком, потому что его объём покинул стакан.

    Смысл меры в том, что статичная форма стакана обманывает (заявку можно
    выставить для вида), а изменение — нет: чтобы изменить стакан, надо
    совершить действие.
    """
    ofi = 0.0
    n = min(depth, len(prev.bids), len(curr.bids), len(prev.asks), len(curr.asks))
    for i in range(n):
        pb, qb = curr.bids[i].price, curr.bids[i].size
        pb0, qb0 = prev.bids[i].price, prev.bids[i].size
        pa, qa = curr.asks[i].price, curr.asks[i].size
        pa0, qa0 = prev.asks[i].price, prev.asks[i].size
        if pb > pb0:
            ofi += qb
        elif pb == pb0:
            ofi += qb - qb0
        else:
            ofi -= qb0
        if pa < pa0:
            ofi -= qa
        elif pa == pa0:
            ofi -= qa - qa0
        else:
            ofi += qa0
    return ofi


def kyle_lambda(books: list[OrderBook], depth: int = 5) -> float:
    """Цена движения на единицу потока заявок, в bp на единицу объёма.

    Наклон прямой, проведённой по точкам «поток заявок → сдвиг середины» между
    соседними снимками. Большая лямбда — стакан легко толкнуть; малая — цена
    стоит, сколько её ни толкай. Это замена лямбды Кайла, у которой вместо
    подписанного объёма сделок стоит поток заявок: снимки стакана сделок не
    содержат.
    """
    if len(books) < 3:
        raise ValueError("для оценки нужны хотя бы три снимка")
    xs = [order_flow_imbalance(a, b, depth) for a, b in zip(books, books[1:])]
    ys = [(b.mid - a.mid) / a.mid * 10_000
          for a, b in zip(books, books[1:]) if a.mid > 0]
    if len(ys) != len(xs):
        return 0.0
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom


# --------------------------------------------------------------------------
# Поток заявок по архиву bookDepth
# --------------------------------------------------------------------------
def _near_depth(snap: BookDepthSnapshot, side: str) -> float:
    """Кумулятивная глубина на ближайшей к середине полосе этой стороны."""
    want = -NEAR_BAND_PCT if side == "bid" else NEAR_BAND_PCT
    exact = {b.percentage: b.depth for b in snap.bands}
    if want in exact:
        return exact[want]
    # полосы ±0.2 % нет (формат до 15.01.2026) — берём ближайшую по модулю
    candidates = [(abs(b.percentage - want), b.depth) for b in snap.bands
                  if (b.percentage < 0) == (side == "bid")]
    if not candidates:
        return 0.0
    return min(candidates)[1]


def ofi_from_depth(prev: BookDepthSnapshot, curr: BookDepthSnapshot) -> float:
    """Прокси потока заявок по соседним снимкам архива.

    Архив bookDepth отдаёт не уровни, а кумулятивную глубину по полосам, снятую
    каждые ~30 секунд. Точный OFI по уровням из него не собрать, но знак потока
    виден: выросла глубина у бида и упала у аска — покупатель ставил заявки,
    наоборот — продавец.

    Мельче полосы ±0.2 % архив не различает, поэтому величина здесь —
    МИНИМАЛЬНЫЙ поток: заявки, поставленные и снятые между снимками, в неё не
    попадают вовсе. Для проверки знака этого достаточно, для оценки размера —
    нет.

    Возвращается в единицах базовой монеты: суммарный сдвиг ближних полос.
    """
    return (_near_depth(curr, "bid") - _near_depth(prev, "bid")) \
        - (_near_depth(curr, "ask") - _near_depth(prev, "ask"))
