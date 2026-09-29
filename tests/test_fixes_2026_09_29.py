"""Тесты на ошибки, найденные и исправленные 29.09.2026.

Каждый тест падает на СТАРОЙ версии кода и проходит на новой. Это не
абстрактные проверки «работает ли функция», а замки на конкретные дефекты,
чтобы они не вернулись при следующей правке.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from src.analysis.density import (MIN_DISTANCE_PCT, Density,
                                  DensityTracker, find_densities)
from src.analysis.formations import (_beyond, detect_bounce,
                                     detect_structure_break)
from src.data.archive import parse_book_depth
from src.data.market import Candle, Level, OrderBook


def candle(ts: int, o: float, h: float, low: float, c: float,
           vol: float = 1.0) -> Candle:
    return Candle(ts, o, h, low, c, vol, vol * c, int(vol))


def make_book(ts: int, big_side: str, big_pct: float, big_size: float,
              base: float = 100.0, depth: int = 48) -> OrderBook:
    """Стакан с ровными уровнями по 1.0 и одной крупной заявкой.

    big_pct — расстояние крупной заявки от середины в процентах; для бида
    со знаком минус. Обязательно не ближе MIN_DISTANCE_PCT: всё, что ближе,
    find_densities отбрасывает как верх стакана, и плотности не будет вовсе.
    Середина у такого стакана ровно base.
    """
    step = 0.0005
    bids = [Level(round(base * (1 - step * k), 6), 1.0)
            for k in range(1, depth + 1)]
    asks = [Level(round(base * (1 + step * k), 6), 1.0)
            for k in range(1, depth + 1)]
    side_levels = bids if big_side == "bid" else asks
    want = base * (1 + big_pct / 100)
    idx = min(range(len(side_levels)),
              key=lambda i: abs(side_levels[i].price - want))
    side_levels[idx] = Level(side_levels[idx].price, big_size)
    return OrderBook("binance", "TESTUSDT", ts, bids, asks)


# --------------------------------------------------------------------------
# levels.horizontal_levels: слияние близких уровней было мёртвой ветвью
# --------------------------------------------------------------------------
def test_близкие_пивоты_сливаются_и_касания_растут():
    """Три близких максимума дают ОДИН уровень с тремя касаниями.

    До фикса тип пивота ("high") сравнивался с типом уровня
    ("resistance"), совпадения не было, и каждый пивот становился
    отдельным уровнем с touches == 1.
    """
    from src.analysis.levels import horizontal_levels

    cs: list[Candle] = []
    # ровный фон, на котором три раза срабатывает один и тот же максимум 100
    base, ts = 90.0, 0
    peaks = (300, 500, 700)
    highs = [100.0, 100.2, 99.9]
    for i in range(900):
        h = highs[peaks.index(i)] if i in peaks else base + 1
        cs.append(candle(ts + i * 300_000, base, h, base - 1, base, 10.0))

    lv = horizontal_levels(cs, "5m", max_levels=12, skip_recent=5,
                           include_broken=True)
    peak_levels = [x for x in lv if 97 < x.price < 103]
    assert len(peak_levels) == 1, f"уровень должен быть один, а их {len(peak_levels)}"
    assert peak_levels[0].touches == 3, (
        f"касаний должно быть 3, а не {peak_levels[0].touches}")


# --------------------------------------------------------------------------
# formations._beyond: цель позади входа — не цель
# --------------------------------------------------------------------------
def test_цели_позади_входа_отсекаются():
    assert _beyond("long", 100.0, [90.0, 110.0, 100.0, 105.0]) == [110.0, 105.0]
    assert _beyond("short", 100.0, [90.0, 110.0, 100.0, 95.0]) == [90.0, 95.0]
    assert _beyond("long", 100.0, [90.0, 99.0]) == []
    assert _beyond("short", 100.0, [101.0, 110.0]) == []


def test_отскок_не_отдаёт_цель_ниже_входа_у_лона():
    """Свеча коснулась уровня, не дойдя до него: проекция ушла бы вниз.

    Допуск уровня считает касанием свечу, чей лоу остался ВЫШЕ уровня,
    поэтому lv.price - last.low отрицательно и «1.5 хвоста» уходят под
    цену. Такой отскок не должен попадать в разборы вообще.
    """
    ts, cs = 0, []
    # падаем к уровню, но не пробиваем его: лоу держится выше 100
    for i in range(120):
        p = 130 - i * 0.25
        cs.append(candle(ts + i * 300_000, p + 0.2, p + 0.3, p - 0.1, p, 5.0))
    # последняя свеча: длинный нижний хвост, закрытие выше уровня
    cs.append(candle(ts + 120 * 300_000, 100.6, 101.0, 100.4, 100.9, 5.0))

    for f in detect_bounce(cs, "5m", "TESTUSDT", "binance"):
        for t in f.targets:
            if f.direction == "long":
                assert t > f.entry, f"цель {t} не выше входа {f.entry}"
            else:
                assert t < f.entry, f"цель {t} не ниже входа {f.entry}"


def test_слом_структуры_не_отдаёт_цель_позади_входа():
    """Подтверждение слома может случиться выше предыдущего максимума.

    Тогда highs[-2] оказывается позади входа, и как цель он бессмыслен.
    """
    ts, cs = 0, []
    # нисходящая структура: максимумы и минимумы падают
    seq = [(110, 100), (108, 98), (106, 96), (104, 94), (102, 92)]
    i = 0
    for hi, lo in seq:
        for _ in range(16):
            p = (hi + lo) / 2
            cs.append(candle(ts + i * 300_000, p, hi, lo, p, 5.0))
            i += 1
    # слом: закрытие выше последнего максимума
    cs.append(candle(ts + i * 300_000, 102.5, 103.5, 102.0, 103.2, 20.0))

    for f in detect_structure_break(cs, "1h", "TESTUSDT", "binance"):
        for t in f.targets:
            if f.direction == "long":
                assert t > f.entry, f"цель {t} не выше входа {f.entry}"
            else:
                assert t < f.entry, f"цель {t} не ниже входа {f.entry}"


# --------------------------------------------------------------------------
# density.Density: eaten и spoof_score
# --------------------------------------------------------------------------
def test_eaten_это_недостача_относительно_пика_а_не_сумма_перепадов():
    """Пик 100 -> 90 -> 80: съедено 20, а не 30.

    Старый код суммировал перепады (10 + 10 + ...), из-за чего при
    монотонном убывании eaten уезжал вверх.
    """
    tr = DensityTracker()
    ob = make_book(10_000, "bid", -0.3, 600.0)
    live = find_densities(ob, top=5)
    assert len(live) == 1, f"должна найтись ровно одна плотность: {live}"
    tracked_price = live[0].price
    tr.update(ob)
    assert tr._live, "плотность должна встать на учёт"

    for ts, size, eaten in ((20_000, 550.0, 50.0),
                            (30_000, 540.0, 60.0),
                            (40_000, 520.0, 80.0)):
        tr.update(make_book(ts, "bid", -0.3, size))
        got = [d for d in tr._live.values()
               if abs(d.price - tracked_price) < 1e-6]
        assert got, "плотность должна отслеживаться на том же уровне"
        assert got[0].eaten == pytest.approx(eaten, abs=1e-6), (
            f"при размере {size} съедено должно быть {eaten}, "
            f"а не {got[0].eaten}")


def test_spoof_score_реагирует_на_близкую_несъедаемую_заявку():
    """Ветка для близкой несъедаемой плотности должна быть достижима.

    Старое условие требовало distance_pct < MIN_DISTANCE_PCT, но всё
    ближе MIN_DISTANCE_PCT отбрасывает сам find_densities — ветка не
    выполнялась никогда. Здесь она единственный источник балла: возраст
    большой, размер не просел, снимков много.
    """
    tr = DensityTracker()
    near = Density("ask", 100.3, 600.0, 60_180.0,
                   MIN_DISTANCE_PCT * 1.5, 0, 20_000, 5, 600.0, 600.0, 0.0)
    far = Density("ask", 101.5, 600.0, 609_000.0,
                  2.0, 0, 20_000, 5, 600.0, 600.0, 0.0)
    assert tr.spoof_score(near) == pytest.approx(0.2), (
        f"близкая несъедаемая заявка должна дать 0.2, а не "
        f"{tr.spoof_score(near)}")
    assert tr.spoof_score(far) == pytest.approx(0.0), (
        "далёкая заявка не должна получать балл за близость")


def test_tracker_не_кладёт_живые_плотности_в_историю():
    """vanished_near должен возвращать ИСЧЕЗНУВШИЕ, а не стоящие сейчас.

    Старый `history.extend(current)` клал в историю живые плотности, и
    поиск исчезнувших возвращал то, что стоит в стакане прямо сейчас.
    """
    tr = DensityTracker()
    tr.update(make_book(10_000, "ask", 0.3, 600.0))
    assert len(tr._live) == 1, "одна плотность должна стоять"
    assert tr.vanished_near(100.3, 1.0) == [], (
        "живая плотность не должна выглядеть исчезнувшей")

    tr.update(make_book(20_000, "ask", 0.3, 1.0))   # крупной заявки больше нет
    assert len(tr._live) == 0, "плотность ушла из стакана"
    gone = tr.vanished_near(100.3, 1.0)
    assert len(gone) == 1, f"исчезнувшая плотность должна найтись, а их {len(gone)}"


# --------------------------------------------------------------------------
# archive.parse_book_depth: обе эпохи формата
# --------------------------------------------------------------------------
HEADER = "timestamp,percentage,depth,notional\n"
OLD_BANDS = (-5.0, -4.0, -3.0, -2.0, -1.0, 1.0, 2.0, 3.0, 4.0, 5.0)
NEW_BANDS = (-5.0, -4.0, -3.0, -2.0, -1.0, -0.2, 0.2, 1.0, 2.0, 3.0, 4.0, 5.0)


def _stamp(i: int) -> str:
    """Отметка времени в том виде, в каком она лежит в архиве."""
    base = datetime(2026, 8, 1, tzinfo=timezone.utc)
    return (base + timedelta(seconds=30 * i)).strftime("%Y-%m-%d %H:%M:%S")


def _depth_csv(bands, snapshots: int = 2, offset: int = 0) -> str:
    rows = [HEADER.rstrip("\n")]
    for i in range(snapshots):
        for k, b in enumerate(bands):
            rows.append(f"{_stamp(offset + i)},{b},{10.0 + k},{1000.0 + k}")
    return "\n".join(rows) + "\n"


def test_book_depth_читает_обе_эпохи_формата():
    """10 целых полос до 2026-01-15 и 12 полос (0.2 плюс целые) после."""
    snaps_old = parse_book_depth(_depth_csv(OLD_BANDS))
    snaps_new = parse_book_depth(_depth_csv(NEW_BANDS))
    assert len(snaps_old) == 2 and len(snaps_new) == 2, (
        f"должно быть по 2 снимка: {len(snaps_old)} и {len(snaps_new)}")
    assert len(snaps_old[0].bands) == 10, (
        f"в старой эпохе 10 полос, распознано {len(snaps_old[0].bands)}")
    assert len(snaps_new[0].bands) == 12, (
        f"в новой эпохе 12 полос, распознано {len(snaps_new[0].bands)}")


def test_book_depth_отбрасывает_обрезанный_снимок():
    """Неполный снимок (обрыв файла) не должен попадать в результат."""
    text = _depth_csv(NEW_BANDS, snapshots=1)
    text += f"{_stamp(1)},-5.0,10.0,1000.0\n"   # одна полоса из 12
    assert len(parse_book_depth(text)) == 1, "обрезанный снимок должен быть отброшен"


def test_book_depth_не_теряет_меньшинство_на_переходном_дне():
    """15.01.2026 — сутки, где соседствуют обе эпохи формата.

    Мода файла там 12 полос, и снимки по 10 отбрасывались как «неполные»:
    на реальном файле это 842 снимка из 2851, 29.5 % суток. Формат снимка
    надо сверять с известными наборами, а не с тем, чего в файле больше.
    """
    text = _depth_csv(OLD_BANDS, snapshots=1)
    text += _depth_csv(NEW_BANDS, snapshots=2, offset=1)[len(HEADER):]
    snaps = parse_book_depth(text)
    assert len(snaps) == 3, f"все три снимка целые, разобрано {len(snaps)}"
    assert sorted(len(s.bands) for s in snaps) == [10, 12, 12]


def test_book_depth_без_заголовка_не_разбирается():
    """Файл без заголовка — это не тот файл; молча разбирать его нельзя."""
    text = "\n".join(f"{_stamp(0)},{b},10.0,1000.0" for b in NEW_BANDS)
    assert parse_book_depth(text) == []


# --------------------------------------------------------------------------
# Сквозная проверка: геометрия любого детектора
# --------------------------------------------------------------------------
def test_ни_один_детектор_не_отдаёт_цель_позади_входа():
    from src.analysis.formations import detect_all

    ts, cs = 0, []
    px = 100.0
    step = (1.7, -0.9, 2.3, -1.4, 0.6, -2.1, 1.1, 0.4, -1.8, 2.6)
    for i in range(400):
        px = max(5.0, px * (1 + step[i % len(step)] / 100))
        hi = px * 1.004
        lo = px * 0.996
        cs.append(candle(ts + i * 300_000, px * 0.999, hi, lo, px,
                         10.0 + (i % 7) * 3))

    for f in detect_all(cs, "5m", "TESTUSDT", "binance", limit=50):
        assert f.entry > 0 and f.stop > 0, f"{f.kind}: вход/стоп не положительны"
        assert abs(f.entry - f.stop) > 0, f"{f.kind}: риск нулевой"
        assert f.targets, f"{f.kind}: нет ни одной цели"
        for t in f.targets:
            if f.direction == "long":
                assert t > f.entry, f"{f.kind}: цель {t} не выше входа {f.entry}"
                assert f.stop < f.entry, f"{f.kind}: стоп {f.stop} не ниже входа"
            else:
                assert t < f.entry, f"{f.kind}: цель {t} не ниже входа {f.entry}"
                assert f.stop > f.entry, f"{f.kind}: стоп {f.stop} не выше входа"


# --------------------------------------------------------------------------
# feed.LiveBook: сборка стакана из потока и сверка со снимком
# --------------------------------------------------------------------------
def _flat_book(ts: int, base: float = 100.0, levels: int = 1000,
               step: float = 0.01, size: float = 1.0) -> OrderBook:
    """Ровный стакан: биды вниз от base, аски вверх, одинаковый размер."""
    bids = [Level(round(base - step * (i + 1), 8), size) for i in range(levels)]
    asks = [Level(round(base + step * (i + 1), 8), size) for i in range(levels)]
    return OrderBook("binance-futures", "TESTUSDT", ts, bids, asks)


def _live_like(ob: OrderBook) -> "LiveBook":
    """Собранный вручную стакан: те же уровни, что в снимке."""
    from src.data.feed import LiveBook

    lb = LiveBook("TESTUSDT")
    lb.bids = {l.price: l.size for l in ob.bids}
    lb.asks = {l.price: l.size for l in ob.asks}
    lb.ts = ob.ts
    return lb


def test_сверка_сравнивает_одинаковую_глубину_обеих_сторон():
    """Собранный стакан сверяется со снимком на одной глубине.

    Снимок приходит на 1000 уровней, а собранный стакан сравнивался по
    верхним 300; сумма объёма считалась у снимка по всем 1000. Отношение
    выходило около 0.2 при ПОЛНОМ совпадении уровней, и проверка браковала
    каждый стакан: за 45 секунд живого сбора — 4 сверки из 4 в расхождение.
    """
    snap = _flat_book(1_000)
    lb = _live_like(snap)
    assert lb._aligned(snap) is True, (
        "совпадающий стакан должен признаваться согласованным")


def test_сверка_видит_пересечённый_стакан():
    """Бид выше аска — расхождение, даже если середина и объём сходятся."""
    snap = _flat_book(1_000)
    lb = _live_like(snap)
    lb.bids[100.9] = 5.0          # бид выше лучшего аска 100.01
    assert lb._aligned(snap) is False, "пересечённый стакан — это расхождение"


def test_сверка_видит_потерянную_глубину():
    """Цена совпала, а объём верхних уровней просел — расхождение.

    Порог по объёму двусторонний (от 0.5 до 2.0), и это намеренно: точного
    равенства не будет никогда, между запросом снимка и сравнением часть
    заявок успевает уйти. Поэтому проверяется не «просело ли», а «просело
    ли настолько, что стакан потерял глубину»: здесь объём верхних уровней
    падает в двадцать раз, и никакой естественный шум этого не объясняет.
    """
    snap = _flat_book(1_000)
    lb = _live_like(snap)
    for side in (lb.bids, lb.asks):
        for price in list(side)[:400]:
            side[price] = 0.05
    assert lb._aligned(snap) is False, (
        "потеря глубины при совпавшей цене должна считаться расхождением")


def test_скользящие_номера_потока_не_мешают_наложению():
    """Разрыв в номерах обновлений — норма, а не признак расхождения.

    U/u — сквозные номера движка биржи, общие для всех монет: между двумя
    событиями одной монеты зияют сотни номеров (замер: u=5249489, следующее
    U=5249763). Требование «событие начинается ровно со следующего номера»
    невыполнимо по построению и отбрасывало первое же событие после снимка.
    """
    from src.data.feed import LiveBook

    lb = LiveBook("TESTUSDT")
    lb._last_u = 100
    lb._apply_diff({"U": 4800, "u": 5000,
                    "b": [["99.0", "5.0"]], "a": [["101.0", "3.0"]]})
    assert lb.bids == {99.0: 5.0}, "событие с дырой в номерах должно наложиться"
    assert lb.asks == {101.0: 3.0}
    assert lb.updates == 1

    lb._apply_diff({"U": 10, "u": 90, "b": [["98.0", "7.0"]], "a": []})
    assert lb.bids == {99.0: 5.0}, "событие старше снимка накладывать нельзя"
    assert lb.updates == 1, "устаревшее событие не считается обновлением"


def test_нулевой_объём_убирает_уровень():
    """qty=0 в потоке означает снятие заявки, а не уровень нулевого размера."""
    from src.data.feed import LiveBook

    lb = LiveBook("TESTUSDT")
    lb._apply_diff({"U": 1, "u": 2, "b": [["99.0", "5.0"]], "a": []})
    assert lb.bids == {99.0: 5.0}
    lb._apply_diff({"U": 3, "u": 4, "b": [["99.0", "0"]], "a": []})
    assert lb.bids == {}, "уровень с нулевым объёмом должен исчезнуть"
