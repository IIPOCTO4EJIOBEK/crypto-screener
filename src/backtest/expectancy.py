"""Историческая ожидаемость формаций — общий кусок отчёта и живого скринера.

Зачем отдельный модуль. Живой скринер ранжирует сегодняшние сигналы по тому,
сколько такие формации давали в прошлом. Меру для ранжирования надо брать из
того же измерения, что и отчёт, иначе ранжирование перестаёт соответствовать
числам, на которые ссылается. Две реализации одного измерения разошлись бы
обязательно — в окне, в издержках, в отсеве.

Окно берётся из архива Binance, а не из живого API: у живого максимум 1000
свечей на запрос, и на 5m это меньше четырёх суток. Ставки финансирования
публикуются только месячными архивами и только за прошедшие месяцы, поэтому
окно короче месяца принципиально не даёт учесть фандинг: текущий месяц в
архиве ещё не выложен. Отсюда и длина окна по умолчанию — 45 суток, чтобы
окно накрывало хотя бы один полный прошедший месяц.
"""

from __future__ import annotations

from datetime import date, timedelta

from src.backtest.costs import Costs
from src.backtest.walk import Trade, walk
from src.data import archive
from src.data.market import Candle

WINDOW_DAYS = 45

# Сколько сделок в измерении нужно, чтобы строка считалась опорой. Число общее
# для отчёта измерения и живого скринера: скринер ранжирует сигналы по этим
# строкам, и разойтись в пороге они не имеют права.
MIN_TRADES = 30

# Шаг между срезами — в свечах, подобран так, чтобы время между срезами было
# примерно одинаковым (около трёх часов) на любом таймфрейме. Иначе 5m
# семплируется каждые 25 минут, а 1h — раз в пять часов, и формации на разных
# ТФ измеряются на несопоставимой плотности.
#
# Плата за крупный шаг обратная и её надо знать: короткие события, вспыхнувшие
# и погасшие между срезами, в измерение не попадают, поэтому редкие и живут
# дольше.
#
# Время прогона. Стоимость одного среза растёт с длиной истории: `walk` отдаёт
# детекторам весь префикс свечей (`candles[:i]`), а не скользящее окно, поэтому
# суммарная работа — порядка n²/шаг. Измерено на BTCUSDT 5m за 45 суток (12 960
# свечей): префикс 1500 свечей — 0.181 с на срез, 4000 — 0.477 с, то есть
# линейно по длине префикса. На полном окне это около пяти минут на пару
# «монета × 5m» и порядка часа на восемь монет по трём таймфреймам. Мелкий шаг
# здесь поэтому стоит не «чуть дороже», а во столько же раз дороже, во сколько
# больше срезов.
STEP_BY_TF = {"1m": 180, "5m": 36, "15m": 12, "30m": 6, "1h": 3, "4h": 1}

# Свечей в окне: 5m — 12 960, 1h — 1080. Ограничение нужно не памяти, а
# времени: стоимость прогона растёт линейно по числу срезов.
MAX_CANDLES = 15_000


def window(days: int = WINDOW_DAYS, end: date | None = None) -> tuple[date, date]:
    """Границы окна [начало, конец]. Конец — вчерашние сутки.

    Архив отстаёт примерно на сутки, а неполные сутки дали бы обрезанный
    последний день и сдвинули бы все срезы у края окна.
    """
    last = (end or date.today()) - timedelta(days=1)
    return last - timedelta(days=days - 1), last


def _months(first: date, last: date) -> tuple[str, str]:
    return f"{first:%Y-%m}", f"{last:%Y-%m}"


def measure(symbol: str, tf: str, *, days: int = WINDOW_DAYS,
            end: date | None = None, costs: Costs | None = None,
            history: int = 300, step: int | None = None,
            horizon: int = 40) -> tuple[list[Trade], int, int]:
    """Сделки по формациям за окно. Возвращает (сделки, дней загружено,
    месяцев фандинга не загружено).

    Число незагруженных месяцев идёт наружу намеренно: без него «net» читается
    как полный результат, хотя фандинг в нём может быть учтён частично.
    """
    if step is None:
        step = STEP_BY_TF.get(tf, 5)
    start, last = window(days, end)
    cs = archive.load_klines(symbol, tf, start, last).rows
    if len(cs) > MAX_CANDLES:
        cs = cs[-MAX_CANDLES:]
    if not cs:
        return [], 0, 0

    btc = None
    if symbol != "BTCUSDT":
        btc = archive.load_klines("BTCUSDT", tf, start, last).rows
        btc = btc[-MAX_CANDLES:] if len(btc) > MAX_CANDLES else btc

    # фандинг — по месяцам окна; месяц, который в архиве ещё не выложен,
    # нормален, но его отсутствие обязано быть видно в отчёте
    first = date.fromtimestamp(cs[0].ts / 1000)
    last_day = date.fromtimestamp(cs[-1].ts / 1000)
    series = archive.load_funding(symbol, *_months(first, last_day))
    trades = walk(cs, tf, symbol, "binance", history=history, step=step,
                  horizon=horizon, btc=btc, costs=costs, funding=series.rows)
    return trades, series.loaded, series.skipped


def table(symbols: list[str], tfs: tuple[str, ...] = ("5m", "15m", "1h"),
          *, days: int = WINDOW_DAYS, costs: Costs | None = None,
          end: date | None = None, step: int | None = None,
          progress=None) -> list[Trade]:
    """Сделки по всем парам «монета × таймфрейм» одним списком."""
    out: list[Trade] = []
    for sym in symbols:
        for tf in tfs:
            trades, loaded, missed = measure(sym, tf, days=days, end=end,
                                             costs=costs, step=step)
            out.extend(trades)
            if progress:
                progress(sym, tf, len(trades), loaded, missed)
    return out
