"""Исторический архив Binance Futures (data.binance.vision) с локальным кешем.

data.binance.vision — публичный статический архив Binance: обычные zip-файлы
по датам, без ключа, без лимитов, без гео-блока.

Архив берётся ради истории, а не потому что живой API закрыт: `fapi.binance.com`
отдаёт не больше 1500 свечей за запрос, а измерению нужны месяцы. Доступен он
при этом напрямую — 10 запросов из 10 ответили 200 (проверено 30.09.2026), а
451 приходит только на маршрут через прокси: тот стоит в США, Binance закрывает
США (см. `src/data/market.py`).

Виды данных (kind):

  bookDepth     снимок глубины стакана каждые ~30 с. CSV с заголовком
                timestamp,percentage,depth,notional. Полосы от середины:
                12 штук (-5,-4,-3,-2,-1,-0.2,0.2,1,2,3,4,5 %) с 2026-01-15
                и 10 штук (те же без ±0.2 %) до этой даты. depth/notional —
                КУМУЛЯТИВНЫЙ объём от середины до этой полосы, а не объём
                самой полосы (проверено: растёт монотонно от 0 к ±5 %).
  metrics       позиционирование каждые 5 минут: открытый интерес и
                соотношения длинных/коротких.
  klines        свечи; kind здесь — интервал ("1m", "5m", "1h", ...).
  fundingRate   ставка финансирования; публикуется только в monthly.

Кеш: /mnt/data/binance-archive/. Уже скачанное повторно не качается.
Битые или ещё не опубликованные файлы пропускаются молча, счётчик
загруженных дней возвращается в Series.

Маршрут как в market.py: сначала через прокси из окружения (trust_env=True),
при неудаче — напрямую. Binance vision через прокси отвечает быстро и
целиком, напрямую — медленно и с обрывом.
"""

from __future__ import annotations

import csv
import io
import os
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import requests

from src.data.market import Candle

BASE_URL = "https://data.binance.vision/data/futures/um"
CACHE_ROOT = "/mnt/data/binance-archive"
TIMEOUT = 120

# Интервалы свечей. Если kind попал сюда — это klines с этим интервалом.
_INTERVALS = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h",
              "12h", "1d", "3d", "1w", "1mo"}

# Заголовки CSV для каждого вида — для проверки, что файл не перепутан.
_EXPECTED_HEADERS = {
    "bookDepth": "timestamp,percentage,depth,notional",
    "metrics": "create_time,symbol,sum_open_interest,sum_open_interest_value,"
               "count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,"
               "count_long_short_ratio,sum_taker_long_short_vol_ratio",
    "fundingRate": "calc_time,funding_interval_hours,last_funding_rate",
}

# Полосы формата bookDepth. До 2026-01-15 полос было 10 — целые ±1..±5 %,
# без ±0.2 %. Переходные сутки — сама дата 15.01.2026: в том файле
# соседствуют снимки обеих эпох (842 по 10 полос и 2009 по 12). Поэтому
# снимок сверяется с ОДНИМ ИЗ наборов, а не с модой файла: мода на
# переходном дне отбрасывает меньшинство (29.5 % суток), а обрыв файла
# посередине снимка не совпадает ни с одним набором и отбрасывается сам.
PERCENTAGES = (-5.0, -4.0, -3.0, -2.0, -1.0, -0.2, 0.2, 1.0, 2.0, 3.0, 4.0, 5.0)
LEGACY_PERCENTAGES = (-5.0, -4.0, -3.0, -2.0, -1.0, 1.0, 2.0, 3.0, 4.0, 5.0)
_BAND_SHAPES = (frozenset(PERCENTAGES), frozenset(LEGACY_PERCENTAGES))


@dataclass(frozen=True)
class BookDepthBand:
    """Одна полоса снимка стакана: кумулятивная глубина до этого % от середины."""
    percentage: float   # смещение от середины, %
    depth: float        # объём в базовой монете
    notional: float     # объём в котируемой (обычно USDT)


@dataclass(frozen=True)
class BookDepthSnapshot:
    """Снимок стакана: полосы одного момента времени (10 или 12 — по эпохе)."""
    ts: int                              # время снимка, миллисекунды UTC
    bands: tuple[BookDepthBand, ...]     # по возрастанию percentage


@dataclass(frozen=True)
class MetricsRow:
    """Строка позиционирования (metrics, каждые 5 минут)."""
    create_time: int
    symbol: str
    sum_open_interest: float
    sum_open_interest_value: float
    count_toptrader_long_short_ratio: float
    sum_toptrader_long_short_ratio: float
    count_long_short_ratio: float
    sum_taker_long_short_vol_ratio: float


@dataclass(frozen=True)
class FundingRate:
    """Одна ставка финансирования (fundingRate) вместе с её интервалом.

    Интервал у символов разный и меняется со временем: у BTCUSDT это 8 часов
    (93 начисления за август 2026), у WIFUSDT — 4 (186 за тот же месяц).
    Поэтому поле хранится рядом со ставкой, а не подразумевается: суммы
    считаются по фактическим начислениям, и константы «8 часов» в расчёте нет.
    """
    calc_time: int
    funding_interval_hours: int
    last_funding_rate: float


@dataclass
class Series:
    """Загруженный ряд плюс счётчик реально загруженных дневных файлов."""
    rows: list
    loaded: int = 0      # сколько файлов распарсено успешно
    skipped: int = 0     # сколько файлов отсутствует или битых

    def __bool__(self) -> bool:
        return bool(self.rows)


def _ts_ms(text: str) -> int:
    """'YYYY-MM-DD HH:MM:SS' (UTC) -> миллисекунды эпохи."""
    dt = datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _session(use_env_proxy: bool) -> requests.Session:
    s = requests.Session()
    s.trust_env = use_env_proxy
    s.headers["User-Agent"] = "crypto-screener/0.1"
    return s


_DIRECT = _session(False)
_VIA_PROXY = _session(True)


def _sub(symbol: str, kind: str) -> str:
    """Путь внутри daily/monthly для пары (символ, вид)."""
    symbol = symbol.upper()
    if kind in _INTERVALS:
        return f"klines/{symbol}/{kind}/{symbol}-{kind}"
    return f"{kind}/{symbol}/{symbol}-{kind}"


def _local_path(symbol: str, kind: str, period: str, monthly: bool) -> str:
    root = os.path.join(CACHE_ROOT, "monthly" if monthly else "daily")
    return os.path.join(root, _sub(symbol, kind)) + f"-{period}.zip"


def _remote_url(symbol: str, kind: str, period: str, monthly: bool) -> str:
    return f"{BASE_URL}/{'monthly' if monthly else 'daily'}/{_sub(symbol, kind)}" \
           f"-{period}.zip"


def _download(url: str, dest: str) -> None:
    """Скачать url в dest через временный файл, чтобы битое не осталось в кеше."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    last: Exception | None = None
    for s in (_VIA_PROXY, _DIRECT):  # сначала прокси, потом напрямую
        try:
            with s.get(url, timeout=TIMEOUT, stream=True) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 16):
                        f.write(chunk)
            os.replace(tmp, dest)
            return
        except Exception as e:  # сеть нестабильна, пробуем второй маршрут
            last = e
    if os.path.exists(tmp):
        os.remove(tmp)
    raise RuntimeError(f"{url}: {last}")


def _as_date(d) -> date:
    if isinstance(d, date):
        return d
    return date.fromisoformat(str(d))


def daily(symbol: str, kind: str, day) -> str:
    """Скачать (или взять из кеша) один дневной файл; вернуть путь к zip."""
    d = _as_date(day)
    path = _local_path(symbol, kind, d.isoformat(), monthly=False)
    if os.path.exists(path):
        return path
    _download(_remote_url(symbol, kind, d.isoformat(), monthly=False), path)
    return path


def monthly(symbol: str, kind: str, month: str) -> str:
    """Скачать (или взять из кеша) один месячный файл; вернуть путь к zip.

    month — строка 'YYYY-MM'. Нужен для fundingRate, который только в monthly.
    """
    path = _local_path(symbol, kind, month, monthly=True)
    if os.path.exists(path):
        return path
    _download(_remote_url(symbol, kind, month, monthly=True), path)
    return path


def range_days(symbol: str, kind: str, start, end):
    """Итератор по путям дневных файлов; отсутствующие пропускаются молча."""
    d = _as_date(start)
    stop = _as_date(end)
    while d <= stop:
        try:
            yield daily(symbol, kind, d)
        except Exception:
            pass
        d += timedelta(days=1)


def _fetch_many(symbol: str, kind: str, days: list[date],
                workers: int) -> list[tuple[date, str]]:
    """Параллельно скачать (или взять из кеша) файлы; вернуть (дата, путь)."""
    out: list[tuple[date, str]] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(daily, symbol, kind, d): d for d in days}
        for fut in as_completed(futs):
            d = futs[fut]
            try:
                out.append((d, fut.result()))
            except Exception:
                pass
    out.sort(key=lambda p: p[0])
    return out


def _read_zip_csv(path: str) -> str:
    """Достать текст единственного CSV из zip-файла."""
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.endswith(".csv")]
        if not names:
            return ""
        return z.read(names[0]).decode("utf-8")


def _check_header(text: str, kind: str) -> bool:
    if kind not in _EXPECTED_HEADERS:
        return True
    return text.startswith(_EXPECTED_HEADERS[kind])


# --------------------------------------------------------------------------
# Разбор CSV в dataclass'ы
# --------------------------------------------------------------------------
def parse_book_depth(text: str) -> list[BookDepthSnapshot]:
    """Разобрать bookDepth: сгруппировать полосы по timestamp.

    Формат менялся: до 2026-01-15 полос было 10 (целые -5..-1, 1..5, без
    ±0.2 %), с 2026-01-15 — 12 (с ±0.2 %). Переходные сутки — сама дата
    15.01.2026, в её файле лежат снимки обеих эпох.

    Снимок целый, только если набор его процентов совпадает с одним из
    известных форматов. Так отбрасываются обрывы файла, но не теряются
    снимки меньшинства на переходном дне. Если не совпал НИ ОДИН снимок —
    формат изменился снова; тогда берётся самый частый набор полос, чтобы
    файл не пропал целиком молча.
    """
    if not _check_header(text, "bookDepth"):
        return []
    buckets: dict[int, list[BookDepthBand]] = {}
    for row in csv.DictReader(io.StringIO(text)):
        ts = _ts_ms(row["timestamp"])
        band = BookDepthBand(float(row["percentage"]), float(row["depth"]),
                             float(row["notional"]))
        buckets.setdefault(ts, []).append(band)
    if not buckets:
        return []

    shapes = {ts: frozenset(round(b.percentage, 2) for b in bands)
              for ts, bands in buckets.items()}
    known = {ts for ts, shape in shapes.items() if shape in _BAND_SHAPES}
    if not known:
        common = Counter(shapes.values()).most_common(1)[0][0]
        known = {ts for ts, shape in shapes.items() if shape == common}

    out: list[BookDepthSnapshot] = []
    for ts in sorted(known):
        bands = tuple(sorted(buckets[ts], key=lambda b: b.percentage))
        out.append(BookDepthSnapshot(ts, bands))
    return out


def parse_metrics(text: str) -> list[MetricsRow]:
    if not _check_header(text, "metrics"):
        return []
    out: list[MetricsRow] = []
    for row in csv.DictReader(io.StringIO(text)):
        out.append(MetricsRow(
            _ts_ms(row["create_time"]), row["symbol"],
            float(row["sum_open_interest"]), float(row["sum_open_interest_value"]),
            float(row["count_toptrader_long_short_ratio"]),
            float(row["sum_toptrader_long_short_ratio"]),
            float(row["count_long_short_ratio"]),
            float(row["sum_taker_long_short_vol_ratio"])))
    return out


def parse_klines(text: str) -> list[Candle]:
    """Свечи из CSV. Ранние файлы Binance идут без строки заголовка.

    Заголовок распознаётся по первому полю: время — целое число, а имя
    колонки («open_time») — нет. Разбор через DictReader здесь не годится:
    на файле без заголовка он принимает первую свечу за шапку и молча её
    теряет.
    """
    rows = list(csv.reader(io.StringIO(text)))
    if rows and not rows[0][0].strip().lstrip("-").isdigit():
        rows = rows[1:]
    return [Candle(int(r[0]), float(r[1]), float(r[2]), float(r[3]),
                   float(r[4]), float(r[5]), float(r[7]), int(r[8]))
            for r in rows if r and r[0].strip()]


def parse_funding(text: str) -> list[FundingRate]:
    if not _check_header(text, "fundingRate"):
        return []
    out: list[FundingRate] = []
    for row in csv.DictReader(io.StringIO(text)):
        out.append(FundingRate(int(row["calc_time"]),
                               int(row["funding_interval_hours"]),
                               float(row["last_funding_rate"])))
    return out


# --------------------------------------------------------------------------
# Загрузка рядов
# --------------------------------------------------------------------------
def load_book_depth(symbol: str, start, end, workers: int = 8) -> Series:
    """Снимки стакана за период [start, end] включительно."""
    days = [_as_date(start) + timedelta(days=i) for i in
            range((_as_date(end) - _as_date(start)).days + 1)]
    rows: list[BookDepthSnapshot] = []
    loaded = 0
    for _, path in _fetch_many(symbol, "bookDepth", days, workers):
        try:
            rows.extend(parse_book_depth(_read_zip_csv(path)))
        except Exception:
            continue
        loaded += 1
    rows.sort(key=lambda s: s.ts)
    return Series(rows, loaded, len(days) - loaded)


def load_metrics(symbol: str, start, end, workers: int = 8) -> Series:
    days = [_as_date(start) + timedelta(days=i) for i in
            range((_as_date(end) - _as_date(start)).days + 1)]
    rows: list[MetricsRow] = []
    loaded = 0
    for _, path in _fetch_many(symbol, "metrics", days, workers):
        try:
            rows.extend(parse_metrics(_read_zip_csv(path)))
        except Exception:
            continue
        loaded += 1
    rows.sort(key=lambda r: r.create_time)
    return Series(rows, loaded, len(days) - loaded)


def load_klines(symbol: str, tf: str, start, end, workers: int = 8) -> Series:
    """Свечи таймфрейма tf (например '1m', '5m') за период [start, end]."""
    days = [_as_date(start) + timedelta(days=i) for i in
            range((_as_date(end) - _as_date(start)).days + 1)]
    rows: list[Candle] = []
    loaded = 0
    for _, path in _fetch_many(symbol, tf, days, workers):
        try:
            rows.extend(parse_klines(_read_zip_csv(path)))
        except Exception:
            continue
        loaded += 1
    rows.sort(key=lambda c: c.ts)
    return Series(rows, loaded, len(days) - loaded)


def _month_list(start_month: str, end_month: str) -> list[str]:
    """Месяцы от start_month до end_month включительно, строки 'YYYY-MM'."""
    y0, m0 = map(int, start_month.split("-"))
    y1, m1 = map(int, end_month.split("-"))
    months: list[str] = []
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        months.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return months


def load_monthly_klines(symbol: str, tf: str, start_month: str, end_month: str,
                        workers: int = 4) -> Series:
    """Свечи по месячным файлам архива ('YYYY-MM').

    Отличие от `load_klines` только в цене запроса: год дневных баров — это
    12 файлов вместо 365, потому что в месячном файле лежат все свечи месяца.
    Для дневного таймфрейма на длинной истории разница решающая.

    Текущий месяц в monthly ещё не выложен — его надо исключать из диапазона
    и добирать дневными файлами, если он нужен.
    """
    months = _month_list(start_month, end_month)
    rows: list[Candle] = []
    loaded = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(monthly, symbol, tf, mm): mm for mm in months}
        for fut in as_completed(futs):
            try:
                rows.extend(parse_klines(_read_zip_csv(fut.result())))
                loaded += 1
            except Exception:
                pass
    rows.sort(key=lambda c: c.ts)
    return Series(rows, loaded, len(months) - loaded)


def load_funding(symbol: str, start_month: str, end_month: str,
                 workers: int = 4) -> Series:
    """Ставки финансирования по месяцам ('YYYY-MM').

    fundingRate публикуется только в monthly, причём текущий месяц ещё не
    выложен — его надо исключать из диапазона.
    """
    months = _month_list(start_month, end_month)
    rows: list[FundingRate] = []
    loaded = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(monthly, symbol, "fundingRate", mm): mm for mm in months}
        for fut in as_completed(futs):
            try:
                path = fut.result()
                rows.extend(parse_funding(_read_zip_csv(path)))
                loaded += 1
            except Exception:
                pass
    rows.sort(key=lambda r: r.calc_time)
    return Series(rows, loaded, len(months) - loaded)


if __name__ == "__main__":
    import sys

    sym = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    d = date(2026, 9, 28)

    bd = load_book_depth(sym, d, d, workers=1)
    print(f"bookDepth {sym} {d}: {len(bd.rows)} снимков, "
          f"дней загружено {bd.loaded}/{bd.loaded + bd.skipped}")
    if bd.rows:
        s0 = bd.rows[0]
        print(f"  первый снимок ts={s0.ts}, полос {len(s0.bands)}")
        print(f"  полосы: {[b.percentage for b in s0.bands]}")

    mt = load_metrics(sym, d, d, workers=1)
    print(f"metrics {sym} {d}: {len(mt.rows)} строк")

    kl = load_klines(sym, "5m", d, d, workers=1)
    print(f"klines 5m {sym} {d}: {len(kl.rows)} свечей")
    if kl.rows:
        print(f"  последняя: ts={kl.rows[-1].ts} close={kl.rows[-1].close}")

    fu = load_funding(sym, "2026-08", "2026-08", workers=1)
    print(f"fundingRate {sym} 2026-08: {len(fu.rows)} строк")
