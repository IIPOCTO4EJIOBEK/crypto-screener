"""Хранилище живых рыночных данных: sqlite.

Прототип собирал один снимок в JSON, и на нём нельзя было ни сравнивать
моменты, ни считать историю. Здесь появляется база: снимки стакана и свечи
ложатся строками, и по ним уже можно строить временные ряды.

Две таблицы, у каждой первичный ключ покрывает то, что делает запись
уникальной:

  book_snapshots  (ts, symbol, exchange)
  candles         (symbol, exchange, timeframe, open_ts)

Благодаря ключу повторный сбор того же момента не создаёт дубликат:
сборщик пишет `INSERT OR REPLACE`, и второй круг по тем же монетам просто
перезаписывает уже известные строки. Это важно для режима `--loop`: свеча
1m опрашивается каждые 60 секунд, но новая среди них только одна, остальные
уже лежат.

Про полосы. `bid_5bps` — это ноционал, СТОЯЩИЙ ОТ СЕРЕДИНЫ ДО полосы
5 б.п. вниз, а не ноционал самой полосы (5..10 б.п.). Так же считает
кумулятивную глубину архив Binance (см. `src/data/archive.py`) и
`depth_within` из `src/analysis/micro.py`, поэтому числа сопоставимы.
Сторона бида отсекается по `price >= mid - band`, аска — по
`price <= mid + band`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from src.data.market import Candle, OrderBook

# Каталог data/ в проекте уже занят под данные; база ложится туда же.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "screener.db"

# Полосы от середины, в базисных пунктах. Набор фиксирован: под него
# разложены колонки таблицы. 5 б.п. — верх стакана, 50 б.п. — уже
# заметная глубина.
BPS_BANDS = (5.0, 10.0, 25.0, 50.0)

# Полоса, по которой считается дисбаланс снимка.
IMBALANCE_BPS = 10.0

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS book_snapshots (
    ts            INTEGER NOT NULL,   -- момент снимка, мс UTC
    symbol        TEXT    NOT NULL,
    exchange      TEXT    NOT NULL,
    mid           REAL    NOT NULL,   -- середина между лучшими бид и аск
    spread_bps    REAL    NOT NULL,   -- спред в базисных пунктах
    best_bid      REAL    NOT NULL,
    best_ask      REAL    NOT NULL,
    n_bids        INTEGER NOT NULL,   -- сколько уровней пришло в снимке
    n_asks        INTEGER NOT NULL,
    imbalance     REAL    NOT NULL,   -- (бид-аск)/(бид+аск) в полосе {IMBALANCE_BPS:g} б.п.
    bid_5bps      REAL    NOT NULL,
    ask_5bps      REAL    NOT NULL,
    bid_10bps     REAL    NOT NULL,
    ask_10bps     REAL    NOT NULL,
    bid_25bps     REAL    NOT NULL,
    ask_25bps     REAL    NOT NULL,
    bid_50bps     REAL    NOT NULL,
    ask_50bps     REAL    NOT NULL,
    PRIMARY KEY (ts, symbol, exchange)
);

CREATE INDEX IF NOT EXISTS idx_book_symbol_ts ON book_snapshots (symbol, ts);
CREATE INDEX IF NOT EXISTS idx_book_ts        ON book_snapshots (ts);

CREATE TABLE IF NOT EXISTS candles (
    symbol       TEXT    NOT NULL,
    exchange     TEXT    NOT NULL,
    timeframe    TEXT    NOT NULL,   -- "1m", "5m", "15m", "1h", ...
    open_ts      INTEGER NOT NULL,   -- открытие свечи, мс UTC
    open         REAL    NOT NULL,
    high         REAL    NOT NULL,
    low          REAL    NOT NULL,
    close        REAL    NOT NULL,
    volume       REAL    NOT NULL,   -- объём в базовой монете
    quote_volume REAL    NOT NULL,   -- объём в котируемой (обычно USDT)
    trades       INTEGER NOT NULL,   -- число сделок; 0, если биржа не отдаёт
    PRIMARY KEY (symbol, exchange, timeframe, open_ts)
);

CREATE INDEX IF NOT EXISTS idx_candles_symbol_ts ON candles (symbol, open_ts);
CREATE INDEX IF NOT EXISTS idx_candles_ts        ON candles (open_ts);

-- Ожидаемость формаций, измеренная на архиве. Отдельная таблица, потому что
-- измерение считается минутами, а скринеру нужно число сейчас: он ранжирует
-- сегодняшние сигналы по тому, сколько такие формации давали в прошлом.
-- Хранится последнее измерение по каждой формации: история измерений не нужна,
-- а вот дата и охват монет нужны — по ним видно, устарело ли число и на чём
-- оно вообще получено.
CREATE TABLE IF NOT EXISTS formation_stats (
    kind         TEXT    NOT NULL,   -- машинное имя формации
    tf           TEXT    NOT NULL,
    measured_on  TEXT    NOT NULL,   -- сутки измерения, 'YYYY-MM-DD' UTC
    symbol_scope TEXT    NOT NULL,   -- монеты измерения через запятую
    n            INTEGER NOT NULL,   -- сделок в измерении
    win_rate     REAL    NOT NULL,   -- доля дошедших до цели, %
    exp_gross    REAL    NOT NULL,   -- средний R до издержек
    exp_net      REAL    NOT NULL,   -- средний R после издержек
    cost         REAL    NOT NULL,   -- средние издержки в R
    sd           REAL    NOT NULL DEFAULT 0,  -- разброс R по сделкам
    PRIMARY KEY (kind, tf)
);
"""


@dataclass(frozen=True)
class BookSnapshot:
    """Строка таблицы book_snapshots: снимок стакана в один момент."""
    ts: int
    symbol: str
    exchange: str
    mid: float
    spread_bps: float
    best_bid: float
    best_ask: float
    n_bids: int
    n_asks: int
    imbalance: float
    bid_5bps: float
    ask_5bps: float
    bid_10bps: float
    ask_10bps: float
    bid_25bps: float
    ask_25bps: float
    bid_50bps: float
    ask_50bps: float

    @classmethod
    def from_orderbook(cls, ob: OrderBook) -> "BookSnapshot":
        """Считать снимок из стакана, посчитав спред, полосы и дисбаланс."""
        bid, ask = band_notional(ob, IMBALANCE_BPS)
        total = bid + ask
        imbalance = (bid - ask) / total if total > 0 else 0.0
        bands = {bps: band_notional(ob, bps) for bps in BPS_BANDS}
        return cls(
            ts=ob.ts, symbol=ob.symbol, exchange=ob.exchange,
            mid=ob.mid, spread_bps=ob.spread / ob.mid * 10_000 if ob.mid else 0.0,
            best_bid=ob.best_bid, best_ask=ob.best_ask,
            n_bids=len(ob.bids), n_asks=len(ob.asks),
            imbalance=imbalance,
            bid_5bps=bands[5.0][0], ask_5bps=bands[5.0][1],
            bid_10bps=bands[10.0][0], ask_10bps=bands[10.0][1],
            bid_25bps=bands[25.0][0], ask_25bps=bands[25.0][1],
            bid_50bps=bands[50.0][0], ask_50bps=bands[50.0][1],
        )


def band_notional(ob: OrderBook, bps: float) -> tuple[float, float]:
    """Ноционал от середины до полосы ±bps, отдельно по сторонам.

    Полоса в цене: mid × bps / 10 000. Возвращаются (бид, аск).
    """
    band = ob.mid * bps / 10_000
    lo = ob.mid - band
    hi = ob.mid + band
    bid = sum(l.price * l.size for l in ob.bids if l.price >= lo)
    ask = sum(l.price * l.size for l in ob.asks if l.price <= hi)
    return bid, ask


# --------------------------------------------------------------------------
# соединение и схема
# --------------------------------------------------------------------------
def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Открыть базу, создав каталог и схему. Путь по умолчанию — data/screener.db."""
    p = Path(path) if path is not None else DEFAULT_DB_PATH
    if str(p) != ":memory:":
        p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    # WAL: сборщик пишет в фоне, а читатель (разбор) не ждёт его блокировки.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()


# Колонки, дописанные после первого выпуска схемы. `CREATE TABLE IF NOT EXISTS`
# уже существующую таблицу не меняет, поэтому новую колонку надо добавлять
# отдельно: иначе база, созданная раньше, молча останется без неё.
_ADDED_COLUMNS = {
    "formation_stats": (("sd", "REAL NOT NULL DEFAULT 0"),),
}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, columns in _ADDED_COLUMNS.items():
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


# --------------------------------------------------------------------------
# запись
# --------------------------------------------------------------------------
_BOOK_COLS = (
    "ts", "symbol", "exchange", "mid", "spread_bps", "best_bid", "best_ask",
    "n_bids", "n_asks", "imbalance",
    "bid_5bps", "ask_5bps", "bid_10bps", "ask_10bps",
    "bid_25bps", "ask_25bps", "bid_50bps", "ask_50bps",
)
_BOOK_SQL = (
    f"INSERT OR REPLACE INTO book_snapshots ({', '.join(_BOOK_COLS)}) "
    f"VALUES ({', '.join('?' * len(_BOOK_COLS))})"
)

_CANDLE_COLS = (
    "symbol", "exchange", "timeframe", "open_ts",
    "open", "high", "low", "close", "volume", "quote_volume", "trades",
)
_CANDLE_SQL = (
    f"INSERT OR REPLACE INTO candles ({', '.join(_CANDLE_COLS)}) "
    f"VALUES ({', '.join('?' * len(_CANDLE_COLS))})"
)


def insert_book_snapshots(conn: sqlite3.Connection,
                          snaps: list[BookSnapshot]) -> int:
    """Записать пачку снимков стакана. Возвращает число записанных строк."""
    if not snaps:
        return 0
    rows = [tuple(getattr(s, c) for c in _BOOK_COLS) for s in snaps]
    conn.executemany(_BOOK_SQL, rows)
    conn.commit()
    return len(rows)


def insert_candles(conn: sqlite3.Connection, symbol: str, exchange: str,
                   timeframe: str, candles: list[Candle]) -> int:
    """Записать пачку свечей одного потока. Повторы перезаписываются."""
    if not candles:
        return 0
    rows = [
        (symbol.upper(), exchange, timeframe, c.ts, c.open, c.high, c.low,
         c.close, c.volume, c.quote_volume, c.trades)
        for c in candles
    ]
    conn.executemany(_CANDLE_SQL, rows)
    conn.commit()
    return len(rows)


_STATS_COLS = ("kind", "tf", "measured_on", "symbol_scope", "n", "win_rate",
               "exp_gross", "exp_net", "cost", "sd")
_STATS_SQL = (
    f"INSERT OR REPLACE INTO formation_stats ({', '.join(_STATS_COLS)}) "
    f"VALUES ({', '.join('?' * len(_STATS_COLS))})"
)


def upsert_formation_stats(conn: sqlite3.Connection,
                           rows: list[dict],
                           tfs: tuple[str, ...] | None = None) -> int:
    """Записать измеренную ожидаемость формаций, по строке на (формация, ТФ).

    Перезапись, а не накопление: скринеру нужно последнее измерение, а не
    история измерений — она бы только позволила выбрать удобное задним числом.

    `tfs` — таймфреймы прогона; их строки стираются перед вставкой. Без этого
    измерение выходит смешанным: формация, не давшая в прогоне ни одной сделки,
    остаётся строкой от прошлого раза со своей датой, и таблица показывает два
    измерения как одно. Ровно так на странице появилось «измерение от
    2026-09-29» при 24 строках, пересчитанных 30.09.
    """
    with conn:                      # удаление и вставка — одной транзакцией
        if tfs:
            placeholders = ", ".join("?" * len(tfs))
            conn.execute(
                f"DELETE FROM formation_stats WHERE tf IN ({placeholders})",
                tfs)
        if rows:
            conn.executemany(_STATS_SQL,
                             [tuple(r.get(c) for c in _STATS_COLS) for r in rows])
    return len(rows)


def load_formation_stats(conn: sqlite3.Connection) -> dict[tuple[str, str], dict]:
    """Сохранённые ожидаемости: ключ (формация, ТФ) → строка измерения."""
    out: dict[tuple[str, str], dict] = {}
    for r in conn.execute("SELECT * FROM formation_stats"):
        out[(r["kind"], r["tf"])] = dict(r)
    return out


# --------------------------------------------------------------------------
# чтение
# --------------------------------------------------------------------------
def latest_book_snapshots(conn: sqlite3.Connection, limit: int = 20,
                          symbol: str | None = None,
                          exchange: str | None = None) -> list[sqlite3.Row]:
    """Последние N снимков, новыми вперёд."""
    sql = "SELECT * FROM book_snapshots"
    where, params = [], []
    if symbol:
        where.append("symbol = ?")
        params.append(symbol.upper())
    if exchange:
        where.append("exchange = ?")
        params.append(exchange)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts DESC, symbol LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def latest_candles(conn: sqlite3.Connection, limit: int = 20,
                   symbol: str | None = None, timeframe: str | None = None,
                   exchange: str | None = None) -> list[sqlite3.Row]:
    """Последние N свечей, новыми вперёд."""
    sql = "SELECT * FROM candles"
    where, params = [], []
    if symbol:
        where.append("symbol = ?")
        params.append(symbol.upper())
    if timeframe:
        where.append("timeframe = ?")
        params.append(timeframe)
    if exchange:
        where.append("exchange = ?")
        params.append(exchange)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY open_ts DESC, symbol LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def count_book_snapshots(conn: sqlite3.Connection, symbol: str | None = None,
                         exchange: str | None = None) -> int:
    sql = "SELECT COUNT(*) FROM book_snapshots"
    where, params = [], []
    if symbol:
        where.append("symbol = ?")
        params.append(symbol.upper())
    if exchange:
        where.append("exchange = ?")
        params.append(exchange)
    if where:
        sql += " WHERE " + " AND ".join(where)
    return int(conn.execute(sql, params).fetchone()[0])


def count_candles(conn: sqlite3.Connection, symbol: str | None = None,
                  timeframe: str | None = None,
                  exchange: str | None = None) -> int:
    sql = "SELECT COUNT(*) FROM candles"
    where, params = [], []
    if symbol:
        where.append("symbol = ?")
        params.append(symbol.upper())
    if timeframe:
        where.append("timeframe = ?")
        params.append(timeframe)
    if exchange:
        where.append("exchange = ?")
        params.append(exchange)
    if where:
        sql += " WHERE " + " AND ".join(where)
    return int(conn.execute(sql, params).fetchone()[0])


def count_rows(conn: sqlite3.Connection, table: str) -> int:
    """Сколько всего строк в таблице. Имя таблицы — только из белого списка."""
    if table not in ("book_snapshots", "candles", "formation_stats"):
        raise ValueError(f"неизвестная таблица: {table}")
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def symbols_present(conn: sqlite3.Connection,
                    exchange: str | None = None) -> list[str]:
    """Монеты, по которым в базе есть хоть один снимок стакана.

    Биржа задаётся явно: спот и перп одного и того же символа лежат в базе
    рядом, и без фильтра список смешал бы два рынка.
    """
    sql = "SELECT DISTINCT symbol FROM book_snapshots"
    params: list = []
    if exchange:
        sql += " WHERE exchange = ?"
        params.append(exchange)
    sql += " ORDER BY symbol"
    return [r[0] for r in conn.execute(sql, params).fetchall()]


if __name__ == "__main__":
    c = connect()
    print(f"база: {DEFAULT_DB_PATH}")
    print(f"снимков стакана: {count_book_snapshots(c)}, свечей: {count_candles(c)}")
    for row in latest_book_snapshots(c, 3):
        print(f"  {row['symbol']:9} {row['exchange']:8} mid={row['mid']:.2f} "
              f"спред={row['spread_bps']:.2f} б.п. полосы={tuple(row[k] for k in ('bid_5bps','ask_5bps','bid_10bps','ask_10bps'))}")
    for row in latest_candles(c, 3):
        print(f"  {row['symbol']:9} {row['timeframe']:4} open_ts={row['open_ts']} close={row['close']}")
