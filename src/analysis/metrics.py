"""Метрики рынка. Формулы взяты из документации Digash (data/corpus),
чтобы считать так же, как считает скринер, которым пользуется владелец.

Все числа считает код. Языковая модель их не вычисляет.
"""

from __future__ import annotations

import math
from statistics import fmean, pstdev

from src.data.market import Candle


def true_ranges(candles: list[Candle]) -> list[float]:
    """TR = max(high−low, |high−предыдущее закрытие|, |low−предыдущее закрытие|)."""
    out: list[float] = []
    for i, c in enumerate(candles):
        if i == 0:
            out.append(c.high - c.low)
            continue
        pc = candles[i - 1].close
        out.append(max(c.high - c.low, abs(c.high - pc), abs(c.low - pc)))
    return out


def natr(candles: list[Candle], period: int = 14) -> float | None:
    """NATR — волатильность в процентах от цены.

    среднее TR за period свечей ÷ текущая цена × 100.
    Возвращает None, если свечей меньше period.
    """
    if len(candles) < period or candles[-1].close <= 0:
        return None
    tr = true_ranges(candles[-period:])
    return fmean(tr) / candles[-1].close * 100


def volatility_index(candles: list[Candle], period: int = 14) -> float | None:
    """«Нервность» закрытий: стандартное отклонение ln(close/open)."""
    if len(candles) < period:
        return None
    vals = [math.log(c.close / c.open) for c in candles[-period:] if c.open > 0]
    if len(vals) < 2:
        return None
    return pstdev(vals)


def volume_splash(candles: list[Candle], window: int) -> float | None:
    """Всплеск объёма: объём последней свечи ÷ средний объём за window свечей.

    5.0 значит «торгуется в пять раз активнее обычного».
    """
    if len(candles) < window + 1:
        return None
    cur = candles[-1].quote_volume
    avg = fmean(c.quote_volume for c in candles[-window - 1:-1])
    if avg <= 0:
        return None
    return cur / avg


def price_change(candles: list[Candle], minutes: int) -> float | None:
    """Изменение цены за N минут в процентах (от открытия свечи N минут назад)."""
    if not candles:
        return None
    target = candles[-1].ts - minutes * 60_000
    base = next((c for c in candles if c.ts >= target), None)
    if base is None or base.open <= 0:
        return None
    return (candles[-1].close - base.open) / base.open * 100


def efficiency_ratio(candles: list[Candle], period: int = 20) -> float | None:
    """Коэффициент эффективности Кауфмана: направленность движения.

    |закрытие за период − закрытие в начале| ÷ сумма модулей всех шагов.
    1.0 — движение шло строго в одну сторону, 0 — цена топталась на месте.
    Сжатие перед импульсом — это ER около 0.5 и ниже (Crabel, Kaufman;
    порог 0.5–0.7 в docs/research/03-formacii-i-otrabotki.md, раздел 1.6).
    """
    if len(candles) < period + 1:
        return None
    seq = candles[-period - 1:]
    net = abs(seq[-1].close - seq[0].close)
    path = sum(abs(seq[i].close - seq[i - 1].close) for i in range(1, len(seq)))
    if path <= 0:
        return None
    return net / path


def narrow_range(candles: list[Candle], period: int = 7) -> bool:
    """Самый узкий диапазон за последние period свечей (NR7 у Крабела)."""
    if len(candles) < period:
        return False
    last = candles[-1].high - candles[-1].low
    others = [c.high - c.low for c in candles[-period:-1]]
    return last <= min(others)


def log_returns(candles: list[Candle]) -> list[float]:
    out = []
    for i in range(1, len(candles)):
        prev, cur = candles[i - 1].close, candles[i].close
        if prev > 0 and cur > 0:
            out.append(math.log(cur / prev))
    return out


def correlation(a: list[Candle], b: list[Candle]) -> float | None:
    """Корреляция Пирсона между лог-доходностями двух рядов, от −100 до +100."""
    ra, rb = log_returns(a), log_returns(b)
    n = min(len(ra), len(rb))
    if n < 3:
        return None
    ra, rb = ra[-n:], rb[-n:]
    ma, mb = fmean(ra), fmean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    da = math.sqrt(sum((x - ma) ** 2 for x in ra))
    db = math.sqrt(sum((y - mb) ** 2 for y in rb))
    if da == 0 or db == 0:
        return None
    return num / (da * db) * 100


def liquidation_index(liquidations_usd: float, avg_volume_2h_usd: float) -> float | None:
    """Индекс ликвидаций: сумма ликвидаций за 10 мин ÷ средний объём за 2 часа."""
    if avg_volume_2h_usd <= 0:
        return None
    return liquidations_usd / avg_volume_2h_usd


def funding_per_day(rate: float, interval_hours: float) -> float:
    """Нормализация ставки финансирования к «% в день»: rate × (24 ÷ интервал)."""
    if interval_hours <= 0:
        return 0.0
    return rate * (24 / interval_hours)


if __name__ == "__main__":
    from src.data.market import ohlcv

    cs = ohlcv("binance", "BTCUSDT", "5m", 200)
    print(f"свечей {len(cs)}, цена {cs[-1].close}")
    print(f"NATR 5/14 = {natr(cs, 14):.3f}%")
    print(f"индекс волатильности = {volatility_index(cs, 14):.5f}")
    print(f"всплеск объёма (окно 24) = {volume_splash(cs, 24):.2f}")
    print(f"изменение цены за 60 мин = {price_change(cs, 60):+.2f}%")
    btc = ohlcv("binance", "BTCUSDT", "5m", 200)
    eth = ohlcv("binance", "ETHUSDT", "5m", 200)
    print(f"корреляция BTC/ETH = {correlation(btc, eth):+.1f}")
