"""Бумажный бот по сигналам скринера: формация → вход → стоп/цель/истечение.

Каждый круг (раз в минуту-две, из cron):

1. берёт сигналы скринера — те же формации и то же ранжирование, что на
   странице скринера (`tools/live/screen.py`), из его базы, только чтение;
2. проверяет открытые позиции по минутным свечам перпетуала: стоп, первая
   цель, истечение через 40 свечей таймфрейма сигнала;
3. открывает новые позиции по свежим сработавшим сигналам — по живому
   стакану фьючерсов Binance, без отправки заявок;
4. пишет журнал и пересобирает страницу `bot.html`.

Заявки на биржу не уходят: этот бот только бумажный. Цель — живой замер того,
что скринер показывает как ожидаемость формаций.

    python -m tools.trade.screener_bot --db /opt/crypto-screener/data/screener.db
    python -m tools.trade.screener_bot --trend overall      # только по тренду скринера
    python -m tools.trade.screener_bot --status
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.request
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.data import market as md                         # noqa: E402
from src.trade.broker import PaperBroker                   # noqa: E402
from src.trade.intraday import (Config, cycle, load_state,  # noqa: E402
                                save_state)
from src.trade.ledger import Ledger                        # noqa: E402

DEFAULT_DB = "/opt/crypto-screener/data/screener.db"
DEFAULT_TREND = "/opt/crypto-screener/docs/live/trend-now.json"
TREND_MAX_AGE_S = 15 * 60


def open_db(path: str) -> sqlite3.Connection:
    """Только чтение: база принадлежит скринеру, бот в неё не пишет."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def screener_rows(a) -> tuple[list[dict], list[str]]:
    if a.signals_json:
        data = json.loads(Path(a.signals_json).read_text())
        if data.get("built_unix") and time.time()-float(data["built_unix"]) > 900:
            return [], ["файл сигналов устарел"]
        from src.data.market import INTERVALS
        rows=[]
        for r in data.get("signals", []):
            r=dict(r)
            if r.get("page_setups") and r.get("tf") not in a.tfs: continue
            ts=r.get("ts");duration=INTERVALS.get(r.get("tf"),300)*1000
            if isinstance(ts,(int,float)):
                ready=r.get("ready_ms") or ts+duration
                r["age_candles"]=max(0,int((time.time()*1000-ready)//duration))
            rows.append(r)
        return rows, data.get("notes", [])
    from src.backtest.costs import Costs
    from src.storage import db
    from tools.live import screen, universe
    symbols, _ = universe.from_arg(a.universe)
    conn = open_db(a.db)
    stats = db.load_formation_stats(conn)
    found, notes = screen.signals(conn, tuple(a.tfs), a.exchange, symbols=symbols)
    rows = screen.rank(found, stats, conn, Costs(), a.exchange)
    return [{**asdict(r), "exp_net": r.exp_net} for r in rows], notes


def load_trend(src: str, now_s: float) -> tuple[dict | None, str | None]:
    """Файл трендов скринера (путь или URL). Устаревший — не используется."""
    try:
        if src.startswith("http"):
            with urllib.request.urlopen(src, timeout=15) as r:
                data = json.loads(r.read())
        else:
            data = json.loads(Path(src).read_text())
    except Exception as exc:                                   # noqa: BLE001
        return None, f"тренд не прочитан: {str(exc)[:120]}"
    age = now_s - float(data.get("built_unix") or 0)
    if age > TREND_MAX_AGE_S:
        return None, f"тренд устарел: {age / 60:.0f} мин"
    return data, None


def candles_1m(symbol: str, since_ms: int) -> list:
    """Read every minute since the cursor, including an outage longer than 25h."""
    end = int(time.time() * 1000)
    found = {}
    for _ in range(100):
        n = max(3, min(int((end - since_ms) // 60_000) + 3, 1500))
        page = md.binance_futures_ohlcv(symbol, "1m", limit=n, end_ms=end)
        if not page:
            raise RuntimeError("empty minute history during recovery")
        for c in page:
            if c.ts >= since_ms:
                found[c.ts] = c
        oldest = min(c.ts for c in page)
        if oldest <= since_ms:
            result = sorted(found.values(), key=lambda c: c.ts)
            if result and result[0].ts > ((since_ms + 59_999) // 60_000) * 60_000:
                raise RuntimeError("minute history starts after recovery cursor")
            if any(b.ts - a.ts != 60_000 for a, b in zip(result, result[1:])):
                raise RuntimeError("gap in minute history during recovery")
            return result
        if oldest - 1 >= end:
            raise RuntimeError("minute history pagination did not advance")
        end = oldest - 1
    raise RuntimeError("minute history recovery exceeds 100 pages")


def data_dir(a, base: Path = ROOT / "data" / "trade") -> Path:
    if a.data:
        return Path(a.data)
    if a.name:
        return Path(base) / f"screener-{a.name}"
    name = "screener-" + a.policy + ("" if a.trend == "off" else f"-trend-{a.trend}")
    return Path(base) / name


def cmd_status(ledger: Ledger, st) -> None:
    j = ledger.journal()
    closed = [r for r in j if r["kind"] == "close"]
    print(f"капитал {st.equity():.2f} (старт {st.start_equity:.2f}, пик {st.peak:.2f}), "
          f"открыто {len(st.positions)}, закрыто {len(closed)}")
    if closed:
        rs = [r["r_net"] for r in closed]
        wins = sum(1 for r in rs if r > 0)
        print(f"средний R после издержек {sum(rs) / len(rs):+.2f}, "
              f"в плюсе {wins}/{len(rs)}")
    for p in st.pos():
        print(f"  {p.side:5} {p.symbol:12} {p.tf:4} {p.title[:30]:30} "
              f"вход {p.entry:.6g} стоп {p.stop:.6g} цель {p.target:.6g} "
              f"сейчас {p.r_of(p.mark or p.entry):+.2f} R")
    if ledger.halted:
        print(f"ОСТАНОВЛЕН: {ledger.halted}")


PROFILES = ROOT / "data" / "trade" / "screener-profiles.json"
LAST_MONITOR_STATS = {}


def main(argv: list[str] | None = None) -> int:
    """Без аргументов и с файлом профилей — прогнать все профили по очереди.

    Сигналы скринера считаются один раз на все профили (это самая долгая часть круга).
    """
    if argv is None:
        argv = sys.argv[1:]
    exits_only = argv == ["--exits-only"]
    if (argv and not exits_only) or not PROFILES.exists():
        return run_one(argv)
    profiles = json.loads(PROFILES.read_text(encoding="utf-8"))
    try:                                        # команды из Telegram — до круга
        from tools.trade import tg_control
        from tools.trade.run import load_env
        load_env()
        if os.environ.get("TELEGRAM_CONTROL_ENABLED") == "1" and os.environ.get('SCREENER_CONTROL_WORKER')!='1' and not exits_only:
            tg_control.run(ROOT / "data" / "trade")
    except Exception as exc:                               # noqa: BLE001
        print(f"telegram: {exc}", file=sys.stderr)
    cache: dict = {}
    rc = 0
    for prof in profiles:
        if not exits_only: print(f"== {' '.join(prof) or '(по умолчанию)'}")
        try:
            rc |= run_one(prof + (["--exits-only"] if exits_only else []), cache)
        except Exception as exc:                               # noqa: BLE001
            print(f"профиль упал: {exc}", file=sys.stderr)
            rc |= 1
    if exits_only:
        LAST_MONITOR_STATS.clear()
        LAST_MONITOR_STATS.update(busy_profiles=cache.get('_busy', []), market_sources=getattr(cache.get('_minute_cache'), 'sources', {}))
    return rc


def run_one(argv: list[str], cache: dict | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DEFAULT_DB, help="база скринера (только чтение)")
    ap.add_argument("--signals-json", default=None,
                    help="вместо базы — готовый JSON скринера (screen.py --json)")
    ap.add_argument("--universe", default=None)
    ap.add_argument("--tfs", nargs="+", default=["5m", "15m", "1h"])
    ap.add_argument("--exchange", default="binance_futures")
    ap.add_argument("--policy", choices=["all", "measured"], default="all",
                    help="all — все свежие сигналы; measured — только с измеренной "
                         "ожидаемостью в плюсе")
    ap.add_argument("--trend", choices=["off", "tf", "overall"], default="off",
                    help="фильтр по тренду скринера: tf — тренд таймфрейма сигнала, "
                         "overall — общий (1h и 4h совпали)")
    ap.add_argument("--trend-src", default=DEFAULT_TREND)
    ap.add_argument("--no-timeout", action="store_true")
    ap.add_argument("--max-attempts-5m", type=int, default=1)
    ap.add_argument("--capital-fraction", type=float, default=1.0)
    ap.add_argument("--capital", type=float, default=1000.0)
    ap.add_argument("--parallel-timeframes", action="store_true", help="разрешить отдельные позиции одной монеты на разных ТФ")
    ap.add_argument("--min-entry-rr", type=float, default=0.0, help="минимум чистого R:R после исполнения и комиссий")
    ap.add_argument("--exit-on-opposite", action="store_true", help="эксперимент: выход при свежем противоположном пробое/ретесте/сломе")
    ap.add_argument("--risk-pct", type=float, default=0.01)
    ap.add_argument("--max-open", type=int, default=5)
    ap.add_argument("--max-age", type=int, default=1)
    ap.add_argument("--max-drawdown", type=float, default=0.35)
    ap.add_argument("--breakeven", type=float, default=0.0,
                    help="перенести стоп в безубыток при +N R (0 — выкл.)")
    ap.add_argument("--trail", type=float, default=0.0,
                    help="трейлинг-стоп в N R от лучшей цены (0 — выкл.)")
    ap.add_argument("--daily-loss", type=float, default=0.0,
                    help="лимит убытка за сутки МСК, доля капитала, напр. 0.03")
    ap.add_argument("--cooldown", type=int, default=0,
                    help="пауза по монете после стопа, минут")
    ap.add_argument("--max-side", type=int, default=0,
                    help="не больше N позиций в одну сторону")
    ap.add_argument("--trail-pct", type=float, default=0.0,
                    help="трейлинг-стоп в доле цены от лучшей, напр. 0.01")
    ap.add_argument("--stop-on-close", action="store_true",
                    help="стоп по закрытию минутной свечи, а не по касанию")
    ap.add_argument("--tp1", type=float, default=0.0,
                    help="первый тейк при +N R с частичным закрытием (0 — выкл.)")
    ap.add_argument("--tp1-frac", type=float, default=0.5)
    ap.add_argument("--be-after-tp1", action="store_true",
                    help="после первого тейка стоп в безубыток")
    ap.add_argument("--no-target", action="store_true",
                    help="без цели формации: выход стопом, трейлингом или по времени")
    ap.add_argument("--pause-after", type=int, default=0,
                    help="пауза входов после N убыточных сделок подряд")
    ap.add_argument("--pause-min", type=int, default=60)
    ap.add_argument("--funding", action="store_true",
                    help="учитывать фандинг в результате сделки")
    ap.add_argument("--name", default=None,
                    help="имя бота: каталог data/trade/screener-<имя>")
    ap.add_argument("--notify", action="store_true",
                    help="входы и выходы — в Telegram (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID в .env)")
    ap.add_argument("--data", default=None)
    ap.add_argument("--page-out", default=None)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--exits-only", action="store_true", help="проверить выходы и команды без расчёта сигналов и новых входов")
    a = ap.parse_args(argv)

    ledger = Ledger(data_dir(a))
    now_ms = int(time.time() * 1000)
    if a.status:
        st = load_state(ledger.state_path, a.capital, now_ms)
        cmd_status(ledger, st)
        return 0

    if not 0 < a.capital_fraction <= 1: ap.error("capital-fraction must be in (0,1]")
    cfg = Config(no_timeout=a.no_timeout, max_attempts_5m=a.max_attempts_5m, capital_fraction=a.capital_fraction, parallel_timeframes=a.parallel_timeframes, min_entry_rr=a.min_entry_rr, exit_on_opposite=a.exit_on_opposite, policy=a.policy, trend=a.trend, risk_pct=a.risk_pct,
                 max_open=a.max_open, max_age=a.max_age, max_drawdown=a.max_drawdown,
                 breakeven_r=a.breakeven, trail_r=a.trail, daily_loss=a.daily_loss,
                 cooldown_min=a.cooldown, max_side=a.max_side,
                 trail_pct=a.trail_pct, stop_on_close=a.stop_on_close, tp1_r=a.tp1,
                 tp1_frac=a.tp1_frac, be_after_tp1=a.be_after_tp1,
                 no_target=a.no_target, pause_after=a.pause_after,
                 pause_min=a.pause_min, funding=a.funding)
    trend, trend_err = (None, None) if a.exits_only else load_trend(a.trend_src, time.time())
    if trend_err:
        print(trend_err, file=sys.stderr)
    try:
        ck = (a.signals_json, a.db, a.universe, tuple(a.tfs), a.exchange)
        if a.exits_only:
            rows, notes = [], []
        elif cache is not None and ck in cache:
            rows, notes = cache[ck]
        else:
            rows, notes = screener_rows(a)
            if cache is not None:
                cache[ck] = (rows, notes)
    except Exception as exc:                                   # noqa: BLE001
        ledger.log("error", where="signals", error=str(exc)[:300])
        print(f"сигналы не получены: {exc}", file=sys.stderr)
        rows = []
    if a.trend != "off" and trend is None:
        # без свежего тренда фильтр не пропустит ничего; выходы всё равно проверяются
        rows = []
    # The entry worker can recover a long history over REST. Do that before
    # acquiring the writer lock so existing positions keep their fast monitor.
    if not a.exits_only:
        from src.trade.monitor_market import MinuteCache
        cache = cache if cache is not None else {}
        if '_minute_cache' not in cache:
            cache['_minute_cache'] = MinuteCache(Path(a.trend_src).parent/'kl', candles_1m,recovery_root=os.environ.get('SCREENER_RECOVERY_DIR'),local_only=bool(os.environ.get('SCREENER_RECOVERY_DIR')))
        snapshot = load_state(ledger.state_path, a.capital, int(time.time()*1000))
        for p in snapshot.pos():
            try:
                cache['_minute_cache'].candles(p.symbol, max(p.last_check_ms, p.opened_ms))
            except Exception:
                pass  # execute_profile logs a failed recovery; no state is changed here
        from src.trade.entry_snapshot import prepare
        from src.trade.confirmation import annotate
        cache['_entry_broker'] = prepare(rows, snapshot, cfg, cache.setdefault('_entry_books', {}),book_root=os.environ.get('SCREENER_BOOK_DIR'))
        rows=annotate(rows,Path(a.trend_src).parent/'kl')
    from src.trade.atomic_store import transaction, Busy
    try:
        with transaction(ledger) as tx:
            return execute_profile(a, ledger, tx, cfg, trend, trend_err, rows, cache)
    except Busy:
        if cache is not None: cache.setdefault('_busy', []).append(ledger.root.name)
        print(f"{ledger.root.name}: профиль занят, следующий быстрый круг")
        return 0


def execute_profile(a, ledger, tx, cfg, trend, trend_err, rows, cache):
    if not a.exits_only and os.environ.get('SCREENER_TRACE_SLOW') == '1':
        import faulthandler
        faulthandler.dump_traceback_later(20, repeat=True)
    now_ms = int(time.time()*1000)
    st = load_state(ledger.state_path, a.capital, now_ms)
    from src.trade.monitor_market import MinuteCache
    cache = cache if cache is not None else {}
    if '_minute_cache' not in cache:
        cache['_minute_cache'] = MinuteCache(Path(a.trend_src).parent/'kl', candles_1m,recovery_root=os.environ.get('SCREENER_RECOVERY_DIR'),local_only=bool(os.environ.get('SCREENER_RECOVERY_DIR')))
    market_cache = cache['_minute_cache']
    live = market_cache.live([p.symbol for p in st.pos()], now_ms)
    if os.environ.get('SCREENER_BOOK_DIR'):
        from src.trade.entry_snapshot import prepare
        broker=prepare([],st,cfg,book_root=os.environ['SCREENER_BOOK_DIR'])
    else:
        broker = PaperBroker("future") if a.exits_only else cache.get('_entry_broker', PaperBroker("future"))
    broker.defer_funding = os.environ.get('SCREENER_DEFER_FUNDING')=='1'
    # ручные сделки и вебхук: идут первыми, мимо фильтра тренда
    from src.trade import inbox
    manual, bad = ([], []) if a.exits_only else inbox.take(ledger.root, broker.mid, transaction=tx,retry_market=True)
    for name, why in bad:
        ledger.log("skip", key=name, symbol="?", formation="inbox", tf="-", side="-",
                   reason=f"внешний сигнал не принят: {why}", trend="")
    rows = manual + list(rows)
    exit_rows = rows
    if a.exit_on_opposite and a.signals_json and not a.exits_only:
        import copy
        other = copy.copy(a); other.signals_json = None
        ck = (None, other.db, other.universe, tuple(other.tfs), other.exchange)
        try:
            if cache is not None and ck in cache: exit_rows = cache[ck][0]
            else: exit_rows = screener_rows(other)[0]
        except Exception as exc:
            ledger.log("error", where="exit_signals", error=type(exc).__name__)
            exit_rows = []
    from src.trade.position_controls import pending, apply
    commands = pending(ledger.root)
    before = None
    control_events=[]
    consumed_commands=[]
    if commands:
        before = cycle([], st, broker=broker, ledger=ledger, candles=market_cache.candles, now_ms=now_ms, cfg=cfg, trend=trend, live_prices=live)
        control_events=apply(commands, st, broker, ledger, now_ms,consumed=consumed_commands)
    res = cycle(rows, st, broker=broker, ledger=ledger, exit_rows=exit_rows,
                candles=market_cache.candles, now_ms=now_ms, cfg=cfg, trend=trend, live_prices=live)
    res["events"] = control_events + res["events"]
    if before:
        res["events"] = before["events"] + res["events"]
        res["closed"] += before["closed"]
    from src.trade.intraday import signal_key
    tx.inbox_files += [Path(r['_inbox_path']) for r in manual if signal_key(r) in st.seen]
    tx.commit(st, events=res["events"], notify=a.notify, commands=consumed_commands)
    if not a.exits_only and os.environ.get('SCREENER_TRACE_SLOW') == '1':
        import faulthandler
        faulthandler.cancel_dump_traceback_later()
    if not a.exits_only or res['closed'] or control_events:
        print(f"сигналов {len(rows)}, открыто {res['opened']}, закрыто {res['closed']}, "
              f"пропущено {res['skipped']}, капитал {res['equity']:.2f}")

    from tools.trade import screener_page
    kw = dict(policy=a.policy, trend=a.trend, cfg=cfg, now_ms=now_ms,
              signals=len(rows), trend_err=trend_err)
    try:
        screener_page.write(ledger, st, ledger.root / "bot.html", **kw)
        if a.page_out:
            screener_page.write(ledger, st, Path(a.page_out), **kw)
        if not a.data and not a.exits_only: # общая страница всех ботов, по вкладке на бота
            from tools.trade import page
            page.write_all(ledger.root.parent)
    except Exception as exc:                                   # noqa: BLE001
        print(f"страница не собрана: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
