"""Тесты детектора стенок по кумулятивным полосам архива (walls.py).

Проверяется не «функция что-то вернула», а смысл: объём полосы берётся
РАЗНОСТЬЮ соседних кумулятивных полос, а не сырым накопленным числом; верх
стакана стенкой не считается; сторона и границы коридора читаются верно;
старый 10-полосный формат и бедный снимок не ломают детектор.
"""

from __future__ import annotations

import pytest

from src.analysis.walls import (MAX_DISTANCE_PCT, MIN_DISTANCE_PCT,
                                WALL_PERCENTILE, candidate_slices, find_walls,
                                largest_wall, nearest_wall)
from src.data.archive import BookDepthBand, BookDepthSnapshot

BANDS_12 = (-5.0, -4.0, -3.0, -2.0, -1.0, -0.2, 0.2, 1.0, 2.0, 3.0, 4.0, 5.0)
BANDS_10 = (-5.0, -4.0, -3.0, -2.0, -1.0, 1.0, 2.0, 3.0, 4.0, 5.0)


def snapshot(band_vols: dict[float, float], pcts=BANDS_12,
             ts: int = 0) -> BookDepthSnapshot:
    """Снимок из объёмов КОРИДОРОВ: наружу отдаётся кумулятивная depth.

    band_vols[p] — сколько объёма стоит в коридоре, который ЗАКАНЧИВАЕТСЯ
    на границе p (для аска это коридор от предыдущей границы до p, для бида
    — от p до предыдущей). Кумулятив считается от середины наружу, ровно
    как в архиве.
    """
    depth: dict[float, float] = {}
    run = 0.0
    for p in [x for x in pcts if x > 0]:          # аск: от 0.2 наружу
        run += band_vols.get(p, 0.0)
        depth[p] = run
    run = 0.0
    for p in sorted((x for x in pcts if x < 0), reverse=True):  # бид: от −0.2
        run += band_vols.get(p, 0.0)
        depth[p] = run
    return BookDepthSnapshot(ts, tuple(BookDepthBand(p, depth[p], depth[p])
                                       for p in pcts))


def all_equal(value: float = 10.0) -> dict[float, float]:
    return {p: value for p in BANDS_12}


# --------------------------------------------------------------------------
def test_объём_полосы_берётся_разностью_кумулятивных_полос():
    """Крупный коридор (1…2 %) не должен получить накопленную глубину.

    Кумулятив до 2 % включает и ближние коридоры: 5 + 5 + 500 = 510. Если
    бы стенка считалась по сырому notional, коридор 1…2 % «весил» бы 510,
    хотя в нём самом стоит 500, а самым крупным оказался бы дальний
    коридор со всей накопленной глубиной. Проверяем, что в стенке ровно 500.
    """
    vols = all_equal(10.0)
    vols[2.0] = 500.0
    walls = find_walls(snapshot(vols))
    top = walls[0]
    assert top.lo_pct == pytest.approx(1.0)
    assert top.hi_pct == pytest.approx(2.0)
    assert top.notional == pytest.approx(500.0), (
        "в стенке не должно быть накопленной глубины ближних коридоров")
    assert top.depth == pytest.approx(500.0)


def test_самый_дальний_коридор_не_выигрывает_от_накопления():
    """При равных объёмах коридоров стенками становятся ближние, не дальние.

    У сырого кумулятивного notional дальняя полоса всегда крупнее ближней
    (629 > 584 > …), и детектор «нашёл бы» стенку на краю стакана. После
    разности все коридоры равны — и ни один не выделяется.
    """
    snap = snapshot(all_equal(10.0))
    cand = candidate_slices(snap)
    assert max(s.notional for s in cand) == pytest.approx(10.0)
    assert min(s.notional for s in cand) == pytest.approx(10.0)


def test_верх_стакана_стенкой_не_считается():
    """Коридор 0…0.2 % крупный всегда — как уровень отскока он бесполезен."""
    vols = all_equal(10.0)
    vols[0.2] = 10_000.0        # весь объём у лучшей цены
    walls = find_walls(snapshot(vols))
    assert all(w.distance_pct > MIN_DISTANCE_PCT for w in walls)
    assert all(abs(w.center_pct) != pytest.approx(0.1) for w in walls)
    cand = candidate_slices(snapshot(vols))
    assert all(abs(s.center_pct) > MIN_DISTANCE_PCT for s in cand)


def test_сторона_и_границы_коридора():
    """Бид-стенка лежит ниже середины; ближняя граница ближе к цене."""
    vols = all_equal(10.0)
    vols[-2.0] = 900.0          # коридор −2…−1 % (band_vols[p] — коридор ДО p)
    w = largest_wall(snapshot(vols))
    assert w.side == "bid"
    assert w.near_pct == pytest.approx(-1.0), "ближняя к середине граница — −1 %"
    assert w.far_pct == pytest.approx(-2.0)
    assert w.price_near(100.0) == pytest.approx(99.0)
    assert w.price_far(100.0) == pytest.approx(98.0)
    assert w.price_near(100.0) > w.price_far(100.0), "у бида ближняя цена выше"

    ask = largest_wall(snapshot({**all_equal(10.0), 3.0: 900.0}))
    assert ask.side == "ask"
    assert ask.near_pct == pytest.approx(2.0)
    assert ask.far_pct == pytest.approx(3.0)
    assert ask.price_far(100.0) > ask.price_near(100.0)


def test_ближняя_стенка_это_первое_препятствие():
    vols = all_equal(10.0)
    vols[2.0] = 900.0           # крупная, но далеко
    vols[1.0] = 500.0           # меньше, но ближе
    w = nearest_wall(snapshot(vols), "ask")
    assert w is not None and w.distance_pct == pytest.approx(0.6)


def test_процентиль_управляет_числом_стенок():
    """p90 оставляет верхнюю полосу, p80 — две, p50 — половину коридоров.

    Считается по десяти коридорам (5 на сторону), поэтому «верхние
    20 %» — это две полосы, а не одна: процентиль интерполируется и
    садится между восьмой и девятой из десяти.
    """
    vols = {p: float(i + 1) for i, p in enumerate(BANDS_12)}
    snap = snapshot(vols)
    assert len(candidate_slices(snap)) == 10
    assert len(find_walls(snap, percentile=90.0)) == 1
    assert len(find_walls(snap, percentile=WALL_PERCENTILE)) == 2
    assert len(find_walls(snap, percentile=50.0)) == 5


def test_старый_десятиполосный_формат_читается():
    """До 15.01.2026 полос ±0.2 % не было — коридоров всё равно десять."""
    vols = {p: 10.0 for p in BANDS_10}
    vols[2.0] = 500.0
    snap = snapshot(vols, pcts=BANDS_10)
    cand = candidate_slices(snap)
    assert len(cand) == 10
    w = largest_wall(snap)
    assert w is not None and w.lo_pct == pytest.approx(1.0)


def test_бедный_снимок_стенок_не_даёт():
    """Меньше MIN_BANDS коридоров — ранг по такому снимку ничего не значит."""
    vols = {p: 10.0 for p in BANDS_10}
    for p in (-5.0, -4.0, -3.0, 3.0, 4.0, 5.0):
        vols.pop(p)
    assert find_walls(snapshot(vols, pcts=BANDS_10)) == []


def test_дальний_коридор_за_пределом_не_рассматривается():
    vols = all_equal(10.0)
    wall = find_walls(snapshot(vols))
    assert all(w.distance_pct <= MAX_DISTANCE_PCT for w in wall)
