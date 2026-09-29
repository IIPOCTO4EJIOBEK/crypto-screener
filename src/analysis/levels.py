"""Уровни и трендовые линии.

Алгоритм воспроизведён по описанию из документации Digash
(data/corpus/digash-documentation.txt, раздел metrics):

  Горизонтальные уровни — на последних 1000 свечах ищутся локальные
  максимумы и минимумы (период поиска 40 свечей, последние 20 свечей
  исключаются). Экстремум становится уровнем, если не был пробит;
  близкие уровни объединяются с допуском, и у уровня растёт счётчик
  касаний.

  Трендовые линии — из локальных экстремумов перебираются тройки точек;
  линия валидна, если все три касаются её в пределах допуска и цена не
  пробивала её сильнее допуска.

Точные допуски в документации названы только для краёв диапазона
(0.2 % на 1m и 1.25 % на 1d для уровней; 0.05 % на 1m и 0.6 % на 1d для
линий). Промежуточные значения — линейная интерполяция между ними по
логарифму длительности таймфрейма, это наша оценка, а не данные Digash.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from src.data.market import Candle

PIVOT_SPAN = 40       # окно поиска локального экстремума, свечей
SKIP_RECENT = 20      # последние свечи не могут быть экстремумом
PIVOT_GAP = 8         # линия соединяет только близкие по времени экстремумы

_TF_MINUTES = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
               "1h": 60, "4h": 240, "1d": 1440}


def _tolerance(tf: str, at_1m: float, at_1d: float) -> float:
    """Допуск для таймфрейма: интерполяция между значениями на 1m и 1d."""
    m = _TF_MINUTES.get(tf, 1)
    k = math.log(m) / math.log(1440)  # 0 на 1m, 1 на 1d
    return at_1m + (at_1d - at_1m) * k


def adaptive_span(n: int, span: int = PIVOT_SPAN) -> int:
    """Окно поиска экстремума под длину истории.

    Digash ищет экстремумы окном 40 свечей на истории в 1000 свечей. На
    короткой истории то же окно даёт всего несколько точек, и уровней
    находится 4–6 на всю монету — этого мало даже для одного пробоя.
    Поэтому ниже 800 свечей окно сужается пропорционально.
    """
    if n >= 800:
        return span
    return max(10, min(span, n // 25))


def level_tolerance_pct(tf: str) -> float:
    return _tolerance(tf, 0.2, 1.25)


def trend_tolerance_pct(tf: str) -> float:
    return _tolerance(tf, 0.05, 0.6)


@dataclass
class Level:
    price: float
    kind: str          # "support" | "resistance"
    touches: int = 1
    broken: bool = False
    first_ts: int = 0
    last_ts: int = 0

    def distance_pct(self, price: float) -> float:
        if price <= 0:
            return 0.0
        return (self.price - price) / price * 100


def find_pivots(candles: list[Candle], span: int = PIVOT_SPAN,
                skip_recent: int = SKIP_RECENT
                ) -> list[tuple[int, float, str]]:
    """Локальные экстремумы: (индекс, цена, "high"/"low").

    skip_recent нужен детекторам формаций: чтобы поймать момент пробоя,
    свежие экстремумы тоже должны быть видны. Скринеру же они мешают —
    уровень, который прямо сейчас формируется, ещё не уровень.
    """
    pivots: list[tuple[int, float, str]] = []
    # экстремум подтверждается только когда после него прошло span свечей:
    # у конца ряда окно обрезано, и по нему находится «экстремум», которого
    # рынок ещё не подтвердил.
    limit = len(candles) - max(skip_recent, span)
    for i in range(span, limit):
        window = candles[i - span:i + span + 1]
        hi, lo = candles[i].high, candles[i].low
        if hi >= max(c.high for c in window):
            pivots.append((i, hi, "high"))
        elif lo <= min(c.low for c in window):
            pivots.append((i, lo, "low"))
    return pivots


def horizontal_levels(candles: list[Candle], tf: str = "5m",
                      max_levels: int = 12, skip_recent: int = SKIP_RECENT,
                      include_broken: bool = False) -> list[Level]:
    """Уровни поддержки и сопротивления по локальным экстремумам.

    include_broken=True возвращает и пробитые уровни — они нужны детекторам
    пробоя и ретеста: в момент события уровень как раз перестаёт быть
    «непробитым».
    """
    if len(candles) < 26:
        return []
    tol = level_tolerance_pct(tf) / 100
    price_now = candles[-1].close
    span = adaptive_span(len(candles))

    levels: list[Level] = []
    for idx, price, kind in find_pivots(candles, span=span,
                                        skip_recent=skip_recent):
        if price <= 0:
            continue
        # ищем близкий уровень того же типа
        hit = None
        for lv in levels:
            if lv.kind == kind and abs(lv.price - price) / price <= tol:
                hit = lv
                break
        if hit:
            # средневзвешенное по числу касаний
            hit.price = (hit.price * hit.touches + price) / (hit.touches + 1)
            hit.touches += 1
            hit.last_ts = candles[idx].ts
            continue
        after = candles[idx + 1:]
        if kind == "high":
            # уровень пробит, если цена ушла выше больше чем на допуск
            broken = any(c.high > price * (1 + tol) for c in after)
            k = "resistance"
        else:
            broken = any(c.low < price * (1 - tol) for c in after)
            k = "support"
        levels.append(Level(price, k, 1, broken,
                            candles[idx].ts, candles[idx].ts))

    active = levels if include_broken else [lv for lv in levels if not lv.broken]
    active.sort(key=lambda lv: abs(lv.distance_pct(price_now)))
    return active[:max_levels]


def find_trend_lines(candles: list[Candle], tf: str = "5m",
                     max_lines: int = 4,
                     skip_recent: int = SKIP_RECENT
                     ) -> list[tuple[float, float, int, str]]:
    """Трендовые линии: (наклон за свечу, сдвиг, касаний, "up"/"down").

    Перебираются тройки локальных экстремумов; линия валидна, если все три
    лежат на ней в пределах допуска и цена её не пробивала сильнее допуска.
    """
    pivots = find_pivots(candles, span=adaptive_span(len(candles)),
                         skip_recent=skip_recent)
    if len(pivots) < 3:
        return []
    pivots = pivots[-PIVOT_GAP * 8:]   # дальнюю историю держат уровни, не линии
    tol = trend_tolerance_pct(tf) / 100
    lines: list[tuple[float, float, int, str]] = []
    seen: set[tuple[int, int, int]] = set()

    for a in range(len(pivots)):
        for b in range(a + 1, min(a + 1 + PIVOT_GAP, len(pivots))):
            i1, p1, k1 = pivots[a]
            i2, p2, k2 = pivots[b]
            if k1 != k2 or i2 == i1:
                continue
            slope = (p2 - p1) / (i2 - i1)
            if slope == 0:
                continue
            ok, touches = True, 2
            for c in range(b + 1, min(b + 1 + PIVOT_GAP, len(pivots))):
                i3, p3, k3 = pivots[c]
                if k3 != k1:
                    continue
                expect = p1 + slope * (i3 - i1)
                if abs(p3 - expect) / expect <= tol:
                    touches += 1
                    key = (i1, i2, i3)
                    if key in seen:
                        continue
                    seen.add(key)
                    # проверка: цена не пробивала линию сильнее допуска
                    for j in range(i1, len(candles)):
                        line_price = p1 + slope * (j - i1)
                        if line_price <= 0:
                            ok = False
                            break
                        if k1 == "low" and candles[j].low < line_price * (1 - tol):
                            ok = False
                            break
                        if k1 == "high" and candles[j].high > line_price * (1 + tol):
                            ok = False
                            break
                    if ok and touches >= 3:
                        offset = p1 - slope * i1
                        lines.append((slope, offset, touches,
                                      "up" if k1 == "low" else "down"))
    lines.sort(key=lambda l: -l[2])
    return lines[:max_lines]


def nearest_level(levels: list[Level], price: float) -> Level | None:
    if not levels:
        return None
    return min(levels, key=lambda lv: abs(lv.distance_pct(price)))


if __name__ == "__main__":
    from src.data.market import ohlcv

    for tf in ("1m", "5m", "15m", "1h"):
        cs = ohlcv("binance", "BTCUSDT", tf, 1000)
        lv = horizontal_levels(cs, tf)
        tl = find_trend_lines(cs, tf)
        print(f"\n{tf}: свечей {len(cs)}, цена {cs[-1].close:.2f}, "
              f"допуск уровня {level_tolerance_pct(tf):.3f}%")
        for l in lv[:5]:
            print(f"   {l.kind:10} {l.price:10.2f}  касаний {l.touches:2}  "
                  f"до цены {l.distance_pct(cs[-1].close):+.2f}%")
        print(f"   трендовых линий: {len(tl)}")
