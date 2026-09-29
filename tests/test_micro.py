"""Тесты микроструктурных мер.

Проверяются не «функция что-то вернула», а направление и смысл: microprice
должен уходить к тонкой стороне, поток заявок — считать поставленное и снятое
с правильным знаком, прокси по архиву — видеть рост глубины у бида как
покупку.
"""

from __future__ import annotations

import pytest

from src.analysis.micro import (depth_within, kyle_lambda, microprice,
                                microprice_tilt_bps, ofi_from_depth,
                                order_flow_imbalance, slope, sweep_cost_bps)
from src.data.archive import BookDepthBand, BookDepthSnapshot
from src.data.market import Level, OrderBook


def book(bids, asks, ts: int = 0) -> OrderBook:
    return OrderBook("test", "TESTUSDT", ts,
                     [Level(p, s) for p, s in bids],
                     [Level(p, s) for p, s in asks])


# --------------------------------------------------------------------------
# microprice
# --------------------------------------------------------------------------
def test_microprice_уходит_к_тонкой_стороне():
    """Мало объёма на биде — следующая сделка скорее ударит по биду.

    Середина остаётся 100.5, а microprice должен сместиться вниз, к биду.
    """
    ob = book([(100.0, 1.0)], [(101.0, 3.0)])
    assert ob.mid == pytest.approx(100.5)
    assert microprice(ob) == pytest.approx(100.25)
    assert microprice_tilt_bps(ob) < 0, "тонкий бид — наклон вниз"


def test_microprice_при_равных_объёмах_совпадает_с_серединой():
    ob = book([(100.0, 2.0)], [(101.0, 2.0)])
    assert microprice(ob) == pytest.approx(ob.mid)
    assert microprice_tilt_bps(ob) == pytest.approx(0.0)


# --------------------------------------------------------------------------
# глубина и стоимость взятия
# --------------------------------------------------------------------------
def test_глубина_в_полосе_считает_только_близкие_уровни():
    ob = book([(99.99, 1.0), (99.0, 10.0)], [(100.01, 1.0), (101.0, 10.0)])
    bid, ask = depth_within(ob, bps=2)   # ±0.02 % — это примерно ±0.02
    assert bid == pytest.approx(1.0), "дальний уровень в полосу не входит"
    assert ask == pytest.approx(1.0)


def test_стоимость_взятия_растёт_с_объёмом_и_не_зависит_от_знака_стороны():
    """Издержка положительна в обе стороны: и купить дороже, и продать дешевле.

    Знак задаётся аргументом стороны, а возвращается величина потери — так
    удобнее сравнивать покупку с продажей (в разборе-источнике обе стороны
    выводятся положительными).
    """
    ob = book([(99.0, 1.0), (98.0, 1.0)], [(101.0, 1.0), (102.0, 1.0)])
    small = sweep_cost_bps(ob, 50.0, "buy")
    big = sweep_cost_bps(ob, 150.0, "buy")
    assert small is not None and big is not None
    assert big > small > 0, "крупный объём проходит глубже и стоит дороже"
    assert sweep_cost_bps(ob, 50.0, "sell") > 0, "продажа тоже стоит денег"


def test_нехватка_стакана_возвращает_ничего():
    """None — это ответ «книги такой толщины нет», а не ошибка."""
    ob = book([(99.0, 1.0)], [(101.0, 1.0)])
    assert sweep_cost_bps(ob, 10_000.0, "buy") is None


def test_наклон_положителен_и_растёт_с_объёмом():
    thin = book([(99.9, 1.0)], [(100.1, 1.0)])
    thick = book([(99.9, 100.0)], [(100.1, 100.0)])
    b1, a1 = slope(thin, depth=1)
    b2, a2 = slope(thick, depth=1)
    assert b1 > 0 and a1 > 0
    assert b2 > b1 * 50, "толстый стакан даёт кратно больший объём на bp"


# --------------------------------------------------------------------------
# поток заявок
# --------------------------------------------------------------------------
def test_поток_заявок_складывает_поставленное_и_снятое():
    """Бид подрос, аск усох — покупатель; обратное — продавец."""
    prev = book([(100.0, 5.0)], [(101.0, 5.0)])
    buy_pressure = book([(100.0, 8.0)], [(101.0, 2.0)])
    sell_pressure = book([(100.0, 2.0)], [(101.0, 8.0)])
    assert order_flow_imbalance(prev, buy_pressure, depth=1) == pytest.approx(6.0)
    assert order_flow_imbalance(prev, sell_pressure, depth=1) == pytest.approx(-6.0)
    assert order_flow_imbalance(prev, prev, depth=1) == pytest.approx(0.0)


def test_ушедший_уровень_считается_целиком():
    """Заявка ушла с бида, её объём покинул стакан — это минус целиком.

    Сравнивать с нулём нельзя: без уровня вторая цена встаёт на его место, и
    разница «было 5, стало 3» занизила бы уход вдвое.
    """
    prev = book([(100.0, 5.0), (99.0, 3.0)], [(101.0, 5.0)])
    curr = book([(99.0, 3.0)], [(101.0, 5.0)])
    assert order_flow_imbalance(prev, curr, depth=1) == pytest.approx(-5.0)


def test_лямбда_кайла_отличает_толкаемый_стакан_от_стоящего():
    """При одном и том же потоке цена сдвигается — лямбда большая.

    И наоборот: поток есть, а середина стоит — лямбда около нуля.
    """
    pushed = [book([(100.0 - 0.1 * i, 1.0)], [(100.1 - 0.1 * i, 1.0)], ts=i)
              for i in range(4)]
    for i, ob in enumerate(pushed):
        pushed[i] = book([(100.0 - 0.1 * i, 1.0 + i)], [(100.1 - 0.1 * i, 1.0)],
                         ts=i)
    lam = kyle_lambda(pushed, depth=1)
    assert lam > 0, "растущий бид при растущей середине — положительная лямбда"

    flat = [book([(100.0, 1.0 + i)], [(100.1, 1.0)], ts=i) for i in range(4)]
    assert kyle_lambda(flat, depth=1) == pytest.approx(0.0), (
        "середина стоит — лямбда нулевая")


def test_для_лямбды_нужны_три_снимка():
    with pytest.raises(ValueError):
        kyle_lambda([book([(100.0, 1.0)], [(101.0, 1.0)])])


# --------------------------------------------------------------------------
# поток заявок по архиву bookDepth
# --------------------------------------------------------------------------
def _snap(ts: int, bid_depth: float, ask_depth: float) -> BookDepthSnapshot:
    """Снимок в новом формате: 12 полос, ближние ±0.2 %."""
    bands = tuple(
        BookDepthBand(p, (bid_depth if p < 0 else ask_depth) * abs(p) / 0.2, 0.0)
        for p in (-5.0, -4.0, -3.0, -2.0, -1.0, -0.2, 0.2, 1.0, 2.0, 3.0, 4.0, 5.0))
    return BookDepthSnapshot(ts, bands)


def test_прокси_потока_видит_поставленное_у_бида_как_покупку():
    prev = _snap(0, 10.0, 10.0)
    grew = _snap(30_000, 14.0, 10.0)
    shrank = _snap(30_000, 10.0, 15.0)
    assert ofi_from_depth(prev, grew) == pytest.approx(4.0)
    assert ofi_from_depth(prev, shrank) == pytest.approx(-5.0)
    assert ofi_from_depth(prev, prev) == pytest.approx(0.0)


def test_прокси_потока_читает_старый_формат_без_полосы_ноль_два():
    """До 15.01.2026 полосы ±0.2 % не было — берётся ближайшая ±1 %."""
    bands = tuple(BookDepthBand(p, (12.0 if p < 0 else 10.0), 0.0)
                  for p in (-5.0, -4.0, -3.0, -2.0, -1.0, 1.0, 2.0, 3.0, 4.0, 5.0))
    old = BookDepthSnapshot(0, bands)
    newer = BookDepthSnapshot(30_000, tuple(
        BookDepthBand(b.percentage, (10.0 if b.percentage < 0 else 10.0), 0.0)
        for b in bands))
    assert ofi_from_depth(old, newer) == pytest.approx(-2.0), (
        "падение глубины бида с 12 до 10 — поток вниз")
