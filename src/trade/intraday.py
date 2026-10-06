"""Бумажная торговля по сигналам скринера: вход, стоп, цель, истечение.

Правила сделки — те же, что в измерении формаций (`src/backtest/walk.py`),
чтобы живой результат можно было положить рядом с измеренным числом:

* вход — по сигналу, у которого условие уже выполнено (`triggered`), и только
  свежему (событие на последней закрытой свече или предыдущей);
* стоп и первая цель — из разбора формации, без подгонки;
* внутри свечи стоп проверяется раньше цели (как в измерении: худший случай);
* истечение — через HORIZON свечей таймфрейма сигнала, выход по рынку.

В отличие от измерения, цена входа здесь — не уровень формации, а реальная
цена рыночной заявки по живому стакану в момент, когда бот увидел сигнал.
Именно эта разница и проверялась в документе 20: в архиве вход «по уровню»
завышал результат. Поэтому R считается от фактического входа, а сигнал, у
которого цена уже ушла за стоп или за цель, не берётся вовсе.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from src.data.market import INTERVALS, Candle

HORIZON = 40           # свечей таймфрейма на сделку — как в src/backtest/walk.py


@dataclass
class Position:
    key: str
    symbol: str
    kind: str
    title: str
    tf: str
    side: str                # long | short
    qty: float
    entry: float             # фактическая цена входа
    stop: float
    target: float
    opened_ms: int
    expires_ms: int
    fee_in: float            # комиссия входа, USDT
    signal_entry: float      # цена входа по разбору формации — для сверки
    measured_r: float | None = None     # измеренная ожидаемость типа, R net
    measured_n: int = 0
    last_check_ms: int = 0
    last_tick_ms: int = 0
    trend: str = ""                     # тренд монеты по скринеру на входе
    mark: float = 0.0                    # последняя цена — для капитала
    risk0: float = 0.0                   # начальный риск: R считается от него, даже когда стоп сдвинут
    breakeven_r: float = 0.0             # при +N R стоп переносится на вход (0 — выкл.)
    trail_r: float = 0.0                 # трейлинг: стоп в N R от лучшей цены (0 — выкл.)
    best: float = 0.0                    # лучшая цена с момента входа (по закрытым свечам)
    trail_pct: float = 0.0               # трейлинг: стоп в доле цены от лучшей (0 — выкл.)
    stop_on_close: bool = False          # стоп по закрытию минутной свечи, а не по касанию
    tp1_r: float = 0.0                   # первый тейк при +N R (0 — выкл.)
    tp1_frac: float = 0.5                # какая доля позиции закрывается на первом тейке
    tp1_done: bool = False
    be_after_tp1: bool = False           # после первого тейка стоп — в безубыток
    realized: float = 0.0                # P&L частичных выходов за вычетом их комиссий
    funding_legs: list = field(default_factory=list)  # quantity and end time of partial exits
    qty0: float = 0.0                    # начальный объём
    reasons: list = field(default_factory=list)   # почему скринер нашёл формацию
    book: dict = field(default_factory=dict)      # стакан на входе: спред, перекос, плотность
    entry_rules: dict = field(default_factory=dict)
    pending_exit_reason: str = ""
    manual_reason: str = ""
    attempt_key: str = ""
    source_key: str = ""
    market_context: list[str] = field(default_factory=list)
    signal_ts: int = 0                   # время сигнальной свечи формации

    @property
    def risk(self) -> float:
        return self.risk0 or abs(self.entry - self.stop)

    def r_of(self, price: float) -> float:
        move = price - self.entry if self.side == "long" else self.entry - price
        return move / self.risk if self.risk > 0 else 0.0


@dataclass(frozen=True)
class Exit:
    reason: str      # stop | trail | tp1 | target | timeout
    price: float
    ts: int
    qty: float = 0.0  # 0 — вся позиция; иначе частичный выход на qty монет


def signal_key(row: dict) -> str:
    """Один и тот же сигнал скринер показывает несколько кругов подряд."""
    key = f"{row['kind']}|{row['tf']}|{row['symbol']}|{row['direction']}|{row['entry']:.10g}"
    return key + (f"|{row['key_suffix']}" if row.get("key_suffix") else f"|event{row['ts']}" if row.get("ts") else "")


def eligible(row: dict, *, policy: str, max_age: int = 1) -> str | None:
    """Почему сигнал не берётся (None — берётся)."""
    if not row.get("triggered"):
        return "условие входа ещё не выполнено"
    if row.get("age_candles", 0) > max_age:
        return "сигнал не свежий"
    if not row.get("target") or not row.get("stop") or row["entry"] == row["stop"]:
        return "нет стопа или цели"
    if policy == "measured":
        if row.get("exp_net") is None:
            return "тип формации не измерен (или сделок меньше порога)"
        if row["exp_net"] <= 0:
            return f"измеренная ожидаемость {row['exp_net']:+.2f} R — не в плюсе"
    return None


def size(equity: float, entry: float, stop: float, *, risk_pct: float,
         max_open: int) -> float:
    """Количество монет: риск на сделку от капитала, но не больше доли капитала.

    Доля — капитал / max_open: сумма всех открытых позиций по модулю не
    превышает капитал, плеча нет. При узком стопе ограничивает именно она.
    """
    risk = abs(entry - stop)
    if risk <= 0 or entry <= 0:
        return 0.0
    by_risk = equity * risk_pct / risk
    by_cap = equity / max_open / entry
    return min(by_risk, by_cap)


def entry_ok(side: str, fill: float, stop: float, target: float) -> str | None:
    """Фактическая цена входа должна лежать между стопом и целью."""
    if side == "long" and not (stop < fill < target):
        return "цена уже за стопом или за целью"
    if side == "short" and not (target < fill < stop):
        return "цена уже за стопом или за целью"
    return None


def move_stop(pos: Position, c: Candle) -> None:
    """Сдвинуть стоп после закрытия свечи: безубыток и трейлинг.

    Сдвиг — только по закрытой свече и только в сторону уменьшения риска;
    новый стоп действует со следующей свечи. Внутри той же свечи порядок
    максимума и минимума неизвестен, поэтому сдвигать стоп по ней и тут же
    проверять его было бы заглядыванием.
    """
    if not (pos.breakeven_r or pos.trail_r or pos.trail_pct):
        return
    long = pos.side == "long"
    pos.best = max(pos.best or pos.entry, c.high) if long else min(pos.best or pos.entry, c.low)
    gain = (pos.best - pos.entry) if long else (pos.entry - pos.best)
    cands = []
    if pos.breakeven_r and gain >= pos.breakeven_r * pos.risk:
        cands.append(pos.entry)
    if pos.trail_r and gain >= pos.trail_r * pos.risk:
        d = pos.trail_r * pos.risk
        cands.append(pos.best - d if long else pos.best + d)
    if pos.trail_pct and gain > 0:
        d = pos.best * pos.trail_pct
        if gain >= d:                 # трейлинг включается, когда он уже не хуже входа
            cands.append(pos.best - d if long else pos.best + d)
    for s in cands:
        if long and s > pos.stop:
            pos.stop = s
        elif not long and s < pos.stop:
            pos.stop = s


def _stop_hit(pos: Position, c: Candle) -> float | None:
    """Цена выхода по стопу на этой свече или None.

    По касанию: гэп за стоп — по открытию (хуже стопа). По закрытию
    (`stop_on_close`): только если свеча закрылась за стопом, выход по закрытию.
    """
    long = pos.side == "long"
    if pos.stop_on_close:
        beyond = c.close <= pos.stop if long else c.close >= pos.stop
        return c.close if beyond else None
    if long and c.low <= pos.stop:
        return min(c.open, pos.stop)
    if not long and c.high >= pos.stop:
        return max(c.open, pos.stop)
    return None


def _reached(pos: Position, c: Candle, level: float) -> bool:
    return c.high >= level if pos.side == "long" else c.low <= level


def check_exits(pos: Position, candles: list[Candle], now_ms: int,
                bar_ms: int = 60_000, update_trailing: bool = True) -> list[Exit]:
    """Пройти закрытые минутные свечи после последней проверки; выходы по порядку.

    Частичный выход (первый тейк) — Exit с qty > 0, позиция уменьшается на месте;
    полный — qty = 0, после него проверка заканчивается. Внутри свечи стоп
    проверяется раньше тейков (худший случай). Стоп, сдвинутый безубытком или
    трейлингом, даёт причину «trail».
    """
    out: list[Exit] = []
    long = pos.side == "long"
    for c in candles:
        if c.ts < pos.last_check_ms or c.ts + bar_ms > now_ms or c.ts < pos.opened_ms:
            continue
        moved = pos.risk0 and abs(abs(pos.stop - pos.entry) - pos.risk0) > 1e-12
        px = _stop_hit(pos, c)
        if px is not None:
            out.append(Exit("trail" if moved else "stop", px, c.ts))
            return out
        if pos.tp1_r and not pos.tp1_done:
            lvl = pos.entry + pos.tp1_r * pos.risk if long else pos.entry - pos.tp1_r * pos.risk
            if _reached(pos, c, lvl):
                q = pos.qty * pos.tp1_frac
                out.append(Exit("tp1", lvl, c.ts, q))
                pos.qty -= q
                pos.tp1_done = True
                if pos.be_after_tp1:
                    pos.stop = max(pos.stop, pos.entry) if long else min(pos.stop, pos.entry)
        if pos.target and _reached(pos, c, pos.target):
            out.append(Exit("target", pos.target, c.ts))
            return out
        if update_trailing:
            move_stop(pos, c)
    if now_ms >= pos.expires_ms:
        out.append(Exit("timeout", 0.0, now_ms))      # цена — по стакану в момент выхода
    return out


def check_exit(pos: Position, candles: list[Candle], now_ms: int) -> Exit | None:
    """Полный выход, если он есть (старый вид — для простых позиций без частичных тейков)."""
    ex = [e for e in check_exits(pos, candles, now_ms) if not e.qty]
    return ex[0] if ex else None


def expires(opened_ms: int, tf: str) -> int:
    return opened_ms + HORIZON * INTERVALS[tf] * 1000


@dataclass
class BotState:
    cash: float
    peak: float
    start_equity: float
    positions: dict[str, dict] = field(default_factory=dict)   # key → asdict(Position)
    seen: dict[str, int] = field(default_factory=dict)         # key → когда видели
    start_ts: int = 0
    last_equity_ms: int = 0
    day: int = 0                     # сутки МСК, к которым относится day_pnl
    day_pnl: float = 0.0             # реализованный результат за эти сутки
    cooldown: dict[str, int] = field(default_factory=dict)   # монета → до какого момента пауза
    attempts: dict[str, int] = field(default_factory=dict)
    streak: int = 0                  # убыточных сделок подряд

    def pos(self) -> list[Position]:
        return [Position(**p) for p in self.positions.values()]

    def put(self, p: Position) -> None:
        self.positions[p.key] = asdict(p)

    def drop(self, key: str) -> None:
        self.positions.pop(key, None)

    def equity(self) -> float:
        """Деньги плюс нереализованный результат по последней цене (без плеча:
        фьючерсный учёт — в деньгах лежит весь капитал, позиция даёт только P&L)."""
        return self.cash + sum(_pnl(p, p.mark or p.entry) for p in self.pos())


def _pnl(p: Position, price: float) -> float:
    sign = 1 if p.side == "long" else -1
    return sign * p.qty * (price - p.entry)


def load_state(path: Path, capital: float, now_ms: int) -> BotState:
    from src.trade.atomic_store import read_state
    authoritative = read_state(Path(path).parent)
    if authoritative is not None:
        return BotState(**authoritative)
    if Path(path).exists():
        return BotState(**json.loads(Path(path).read_text(encoding="utf-8")))
    return BotState(cash=capital, peak=capital, start_equity=capital, start_ts=now_ms)


def save_state(path: Path, st: BotState) -> None:
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(st), ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# тренд скринера
# --------------------------------------------------------------------------
# Сигнал 5m сверяется с трендом 15m: своего тренда на 5m скринер не пишет.
TREND_TF = {"5m": "15m", "15m": "15m", "1h": "1h", "4h": "4h"}


def trend_of(trend: dict | None, symbol: str, tf: str, how: str) -> str:
    """Сторона тренда монеты по файлу скринера: long | short | flat | "" (нет данных)."""
    if not trend:
        return ""
    coin = (trend.get("coins") or {}).get(symbol) or {}
    key = "overall" if how == "overall" else TREND_TF.get(tf, "1h")
    return coin.get(key) or ""


def trend_block(side: str, tr: str, how: str) -> str | None:
    if how == "off":
        return None
    if not tr:
        return "нет тренда монеты в файле скринера"
    if tr != side:
        return f"против тренда ({tr})"
    return None


# --------------------------------------------------------------------------
# один круг бота
# --------------------------------------------------------------------------
@dataclass
class Config:
    no_timeout: bool = False
    max_attempts_5m: int = 1
    parallel_timeframes: bool = False
    min_entry_rr: float = 0.0      # net reward / risk after simulated fill and fees
    exit_on_opposite: bool = False # experimental: fresh opposite structural signal
    policy: str = "all"            # all | measured
    trend: str = "off"             # off | tf | overall
    risk_pct: float = 0.01         # риск на сделку от капитала
    capital_fraction: float = 1.0
    max_open: int = 5
    max_age: int = 1               # свечей с момента события формации
    max_drawdown: float = 0.35
    equity_every_ms: int = 15 * 60_000
    breakeven_r: float = 0.0       # перенос стопа в безубыток при +N R (0 — выкл.)
    trail_r: float = 0.0           # трейлинг-стоп в N R от лучшей цены (0 — выкл.)
    daily_loss: float = 0.0        # лимит убытка за сутки МСК, доля капитала (0 — выкл.)
    cooldown_min: int = 0          # пауза по монете после стопа, минут (0 — выкл.)
    max_side: int = 0              # не больше N позиций в одну сторону (0 — без лимита)
    trail_pct: float = 0.0         # трейлинг в доле цены от лучшей (0 — выкл.)
    stop_on_close: bool = False    # стоп по закрытию минутной свечи
    tp1_r: float = 0.0             # первый тейк при +N R (0 — выкл.)
    tp1_frac: float = 0.5          # доля позиции на первом тейке
    be_after_tp1: bool = False     # после первого тейка стоп в безубыток
    no_target: bool = False        # без цели формации: выход только стопом/трейлингом/временем
    pause_after: int = 0           # пауза входов после N убыточных сделок подряд (0 — выкл.)
    pause_min: int = 60
    funding: bool = False          # учитывать фандинг в P&L при закрытии


SIDE_RU = {"long": "ЛОНГ", "short": "ШОРТ"}
REASON_RU = {"stop": "стоп", "trail": "сдвинутый стоп", "target": "цель", "timeout": "время", "invalidation": "смена структуры", "manual": "ручной выход"}
MSK_SHIFT_MS = 3 * 3600_000


def msk_day(ms: int) -> int:
    return (ms + MSK_SHIFT_MS) // 86_400_000


CLOSE = {"long": "sell", "short": "buy"}
OPEN = {"long": "buy", "short": "sell"}


def cycle(rows: list[dict], st: BotState, *, broker, ledger,
          candles: Callable[[str, int], list[Candle]], now_ms: int,
          cfg: Config, trend: dict | None = None, exit_rows: list[dict] | None = None,
          live_prices: dict | None = None) -> dict:
    """Проверить выходы открытых позиций, затем открыть новые по свежим сигналам.

    rows — строки скринера (как в `tools/live/screen.py --json`), в порядке
    ранжирования. candles(symbol, since_ms) — минутные свечи перпетуала.
    """
    if getattr(broker, "live", False):
        raise ValueError("Intraday cycle supports paper execution only")
    out = {"opened": 0, "closed": 0, "skipped": 0, "events": []}
    today = msk_day(now_ms)
    if st.day != today:
        st.day, st.day_pnl = today, 0.0

    # 0. команда «закрыть всё» (файл CLOSEALL — из Telegram или руками):
    # выход по рынку по стакану в этом же круге
    closeall = ledger.root / "CLOSEALL"
    if closeall.exists():
        for p in st.pos():
            p.expires_ms = 0
            p.pending_exit_reason = "manual"
            p.manual_reason = "команда закрыть все позиции"
            st.put(p)
        tx = getattr(ledger, '_transaction', None)
        if tx: tx.consumed.append(closeall)
        else: closeall.unlink()
        ledger.log("closeall", positions=len(st.positions))

    # 1. выходы
    for p in st.pos():
        if cfg.no_timeout and not p.pending_exit_reason: p.expires_ms = 2**62
        if cfg.exit_on_opposite and not p.pending_exit_reason:
            opposite = next((r for r in (exit_rows if exit_rows is not None else rows)
                if r.get("symbol") == p.symbol and r.get("direction") in ("long", "short") and r.get("direction") != p.side
                and r.get("triggered") and r.get("age_candles", 99) == 0
                and r.get("kind") in ("breakout", "retest", "structure_break")
                and INTERVALS.get(r.get("tf"), 10**9) <= INTERVALS[p.tf]), None)
            if opposite:
                p.pending_exit_reason = "invalidation"
                p.expires_ms = 0
                st.put(p)
                ledger.log("invalidation", key=p.key, symbol=p.symbol,
                           opposite_kind=opposite["kind"], opposite_tf=opposite["tf"])
        try:
            cs = candles(p.symbol, max(p.last_check_ms, p.opened_ms))
        except Exception as exc:                            # noqa: BLE001
            ledger.log("error", where="candles", symbol=p.symbol, error=str(exc)[:200])
            continue
        exits = check_exits(p, cs, now_ms)
        closed = [c for c in cs if c.ts + 60_000 <= now_ms]
        if closed:
            p.mark = closed[-1].close
            accepted = [c for c in closed if c.ts >= max(p.last_check_ms, p.opened_ms)]
            if accepted:
                p.last_check_ms = max(p.last_check_ms, accepted[-1].ts + 60_000)
        tick = (live_prices or {}).get(p.symbol)
        if tick and not any(not e.qty for e in exits) and not p.stop_on_close:
            price, stamp = tick[:2]
            if p.opened_ms <= stamp <= now_ms and now_ms-stamp <= 10_000:
                points = list(tick[2]) if len(tick) > 2 else []
                points.append((stamp, price))
                for observed_ms, observed_price in points:
                    if observed_ms <= p.last_tick_ms or observed_ms < max(p.opened_ms, p.last_check_ms) or observed_ms > now_ms:
                        continue
                    point = Candle(observed_ms, observed_price, observed_price, observed_price, observed_price, 0, 0, 0)
                    legs = check_exits(p, [point], now_ms, bar_ms=0, update_trailing=False)
                    p.last_tick_ms = observed_ms
                    exits += legs
                    # A partial take changes the stop now, never retroactively
                    # against the earlier low/high of this unfinished minute.
                    if any(e.qty for e in legs):
                        p.last_check_ms = max(p.last_check_ms, observed_ms)
                    if any(not e.qty for e in legs):
                        break
        if closed:
            p.last_check_ms = max(p.last_check_ms, closed[-1].ts + 60_000)
        if tick and 0 <= now_ms-tick[1] <= 10_000:
            p.mark = tick[0]
        funding = 0.0
        full_exit = next((e for e in exits if not e.qty), None)
        if cfg.funding and full_exit is not None:
            try:
                rate = broker.funding(p.symbol, p.opened_ms, full_exit.ts)
                weighted = p.qty * rate
                legs = p.funding_legs + [{"qty": e.qty, "end_ms": e.ts} for e in exits if e.qty]
                for leg in legs:
                    weighted += leg["qty"] * broker.funding(p.symbol, p.opened_ms, leg["end_ms"])
                funding = (1 if p.side == "long" else -1) * p.entry * weighted
            except Exception as exc:
                ledger.log("error", where="funding", symbol=p.symbol, error=str(exc)[:200])
                continue  # no cash/cursor changes: replay these candles on the next cycle
        ex = None
        for e in exits:
            if e.qty:                                   # частичный тейк — по уровню
                part = (e.price - p.entry) * e.qty * (1 if p.side == "long" else -1)
                fee = e.qty * e.price * broker.fee
                st.cash += part - fee
                st.day_pnl += part - fee
                p.realized += part - fee
                p.funding_legs.append({"qty": e.qty, "end_ms": e.ts})
                ledger.log("partial", key=p.key, symbol=p.symbol, side=p.side, qty=e.qty,
                           price=e.price, reason=e.reason, pnl=part, fee=fee,
                           stop=p.stop, left=p.qty)
                out["events"].append(
                    f"ТЕЙК {SIDE_RU[p.side]} {p.symbol}: {e.qty:.6g} по {e.price:.6g}, "
                    f"{part - fee:+.2f} USDT" + (", стоп в безубыток" if p.be_after_tp1 else ""))
            else:
                ex = e
        if ex is None:
            st.put(p)
            continue
        if ex.reason == "timeout":
            fill = broker.execute(p.symbol, CLOSE[p.side], p.qty, reduce=True)
            if fill is None:
                ledger.log("error", where="close", symbol=p.symbol,
                           error="глубины стакана не хватило — выход в следующий круг")
                st.put(p)
                continue
            price, fee, slip = fill.price, fill.fee, fill.slippage_bp
            if p.pending_exit_reason:
                ex = Exit(p.pending_exit_reason, price, now_ms)
        else:
            # стоп и цель — по уровню (стоп при гэпе — по открытию свечи), комиссия тейкера
            price, fee, slip = ex.price, p.qty * ex.price * broker.fee, None
        pnl = _pnl(p, price)
        st.cash += pnl - fee - funding
        total = p.realized + pnl - fee - funding - p.fee_in
        st.day_pnl += pnl - fee - funding
        st.drop(p.key)
        if p.tf == "5m" and p.attempt_key and ex.reason == "stop":
            count = st.attempts.get(p.attempt_key, 1)
            if count < cfg.max_attempts_5m:
                st.seen.pop(p.source_key or p.key, None)
                out["events"].append(f"{p.symbol} 5m: попытка {count}/{cfg.max_attempts_5m} закрыта стопом; повтор только по свежему сигналу и допустимой цене")
            else:
                out["events"].append(f"{p.symbol} 5m: {count} попытки исчерпаны; новые входы до следующего сигнала 5m заблокированы, старшие ТФ продолжаются. Проверить комиссию, движение и причины стопов в журнале.")
                previous=[r for r in ledger.journal() if r['kind']=='close' and r.get('attempt_key')==p.attempt_key]
                net=total+sum(r['pnl']-r['fee']-r.get('funding',0) for r in previous)
                fees=fee+p.fee_in+sum(r['fee'] for r in previous)
                out['events'].append(f"Разбор {p.symbol} 5m: {count} попытки, итог {net:+.2f} USDT, комиссии {fees:.2f}. Три стопа по одному событию: сигнал исчерпан; проверить шум и расстояние стопа относительно ATR. Причины и часы входов сохранены.")
                ledger.log("attempt_review", symbol=p.symbol, attempt_key=p.attempt_key, attempts=count, net=net, fees=fees)
        out["closed"] += 1
        if ex.reason == "stop" and cfg.cooldown_min:
            st.cooldown[p.symbol] = now_ms + cfg.cooldown_min * 60_000
        st.streak = st.streak + 1 if total < 0 else 0
        if cfg.pause_after and st.streak >= cfg.pause_after:
            st.cooldown["__all__"] = now_ms + cfg.pause_min * 60_000
            st.streak = 0
            ledger.log("pause", until=st.cooldown["__all__"], reason="серия убыточных сделок")
            out["events"].append(f"Пауза входов на {cfg.pause_min} мин: {cfg.pause_after} убыточных сделок подряд")
        q0 = p.qty0 or p.qty
        r_net = total / (q0 * p.risk) if p.risk and q0 else 0.0
        out["events"].append(
            f"ВЫХОД {SIDE_RU[p.side]} {p.symbol} {p.title} {p.tf}: {REASON_RU.get(ex.reason, ex.reason)}, "
            f"{price:.6g}, {r_net:+.2f} R, {total:+.2f} USDT" + (f"; причина: {p.manual_reason}" if p.manual_reason else ""))
        ledger.log("close", key=p.key, symbol=p.symbol, formation=p.kind, title=p.title,
                   tf=p.tf, side=p.side, qty=q0, entry=p.entry, exit=price,
                   reason=ex.reason, exit_ts=ex.ts, opened_ms=p.opened_ms,
                   r=p.r_of(price), r_net=r_net, pnl=total + fee + funding + p.fee_in,
                   fee=fee + p.fee_in, funding=funding, partial=p.realized,
                   slippage_bp=slip, measured_r=p.measured_r, measured_n=p.measured_n,
                   trend=p.trend, stop_final=p.stop, target_final=p.target, rules=p.entry_rules, market_context=p.market_context, manual_reason=p.manual_reason, attempt_key=p.attempt_key)

    # 2. капитал и стоп по просадке
    eq = st.equity()
    st.peak = max(st.peak, eq)
    if st.peak > 0 and eq < st.peak * (1 - cfg.max_drawdown) and not ledger.halted:
        ledger.halt(f"просадка {eq / st.peak - 1:+.1%} от пика {st.peak:.2f}")
        ledger.log("halt", equity=eq, peak=st.peak)
    if now_ms - st.last_equity_ms >= cfg.equity_every_ms:
        st.last_equity_ms = now_ms
        ledger.log("equity", equity=eq, cash=st.cash, open=len(st.positions))

    # 3. входы
    day_stop = bool(cfg.daily_loss) and (st.cooldown.get("__day__") == today
                                      or st.day_pnl <= -cfg.daily_loss * max(eq, 0.0))
    if day_stop and st.cooldown.get("__day__") != today:
        st.cooldown["__day__"] = today
        ledger.log("day_stop", day_pnl=st.day_pnl, equity=eq)
        out["events"].append(f"Дневной лимит убытка: {st.day_pnl:+.2f} USDT, входы до конца суток МСК остановлены")
    st.cooldown = {k: v for k, v in st.cooldown.items() if k == "__day__" or v > now_ms}
    open_cool = {k for k in st.cooldown if not k.startswith("__")}
    paused = st.cooldown.get("__all__", 0) > now_ms or (ledger.root / "PAUSE").exists()
    if not ledger.halted and not day_stop and not paused:
        open_syms = {p.symbol for p in st.pos()}
        open_tfs = {(p.symbol, p.tf) for p in st.pos()}
        remaining_notional = max(0.0, eq * cfg.capital_fraction - sum(p.qty * (p.mark or p.entry) for p in st.pos()))
        for row in rows:
            if not row.get("triggered"):
                continue                       # может сработать позже — не помечать
            key = signal_key(row)
            attempt_key = f"{row['symbol']}|{row['tf']}|{row.get('ts') or row.get('key_suffix') or key}"
            if row['tf'] == '5m' and st.attempts.get(attempt_key, 0) >= cfg.max_attempts_5m:
                continue
            if key in st.seen:
                continue
            if len(st.positions) >= cfg.max_open:
                pending = [r for r in rows if r.get("triggered")
                           and signal_key(r) not in st.seen
                           and (r.get("age_candles") or 0) <= cfg.max_age]
                ledger.log("capacity", reason="достигнут лимит открытых позиций",
                           max_open=cfg.max_open, pending=len(pending),
                           symbols=sorted({r["symbol"] for r in pending}))
                out["events"].append(f"Лимит {cfg.max_open} позиций: {len(pending)} свежих сигналов ждут места")
                break                          # don't consume signals; retry if a slot becomes free
            st.seen[key] = now_ms
            manual = bool(row.get("manual"))     # ручной / вебхук: решение трейдера
            why = eligible(row, policy="all" if manual else cfg.policy, max_age=cfg.max_age)
            if why == "сигнал не свежий":
                continue                       # старые — молча, их много
            side = row["direction"]
            tr = trend_of(trend, row["symbol"], row["tf"], cfg.trend if cfg.trend != "off" else "tf")
            why = why or (None if manual else trend_block(side, tr, cfg.trend))
            if not why and ((row["symbol"], row["tf"]) in open_tfs if cfg.parallel_timeframes else row["symbol"] in open_syms):
                why = "по монете уже есть позиция"
            if not why and row["symbol"] in open_cool:
                why = "пауза по монете после стопа"
            if not why and cfg.max_side and sum(
                    1 for q in st.pos() if q.side == side) >= cfg.max_side:
                why = f"уже {cfg.max_side} позиций в эту сторону"
            mid = None
            if not why:
                try:
                    mid = broker.mid(row["symbol"])
                except Exception as exc:                    # noqa: BLE001
                    why = f"стакан не получен: {str(exc)[:80]}"
            why = why or entry_ok(side, mid, row["stop"], row["target"])
            if why:
                st.seen.pop(key, None)
                out["skipped"] += 1
                ledger.log("skip", key=key, symbol=row["symbol"], formation=row["kind"],
                           tf=row["tf"], side=side, reason=why, trend=tr)
                continue
            if remaining_notional <= 0:
                st.seen.pop(key, None)
                out["skipped"] += 1
                ledger.log("skip", key=key, symbol=row["symbol"], formation=row["kind"], tf=row["tf"], side=side,
                           reason="выделенный капитал уже занят открытыми позициями", trend=tr)
                continue
            qty = min(size(eq, mid, row["stop"], risk_pct=cfg.risk_pct, max_open=cfg.max_open), remaining_notional / mid)
            fill = broker.execute(row["symbol"], OPEN[side], qty) if qty > 0 else None
            if fill is None:
                st.seen.pop(key, None)
                out["skipped"] += 1
                ledger.log("skip", key=key, symbol=row["symbol"], formation=row["kind"],
                           tf=row["tf"], side=side, reason="глубины стакана не хватило", trend=tr)
                continue
            # Paper execute simulates a fill without moving cash or sending an order.
            why = entry_ok(side, fill.price, row["stop"], row["target"])
            if why:
                st.seen.pop(key, None)
                out["skipped"] += 1
                ledger.log("skip", key=key, symbol=row["symbol"], formation=row["kind"],
                           tf=row["tf"], side=side, reason="цена исполнения: " + why,
                           trend=tr, fill_price=fill.price)
                continue
            # Requote a smaller paper quantity if the actual price changes capital/risk limits.
            for _ in range(3):
                allowed = min(size(eq, fill.price, row["stop"], risk_pct=cfg.risk_pct, max_open=cfg.max_open),
                              remaining_notional / fill.price)
                if fill.qty <= allowed + 1e-10:
                    break
                fill = broker.execute(row["symbol"], OPEN[side], allowed) if allowed > 0 else None
                if fill is None: break
            if fill is None or entry_ok(side, fill.price, row["stop"], row["target"]) or fill.qty*fill.price > remaining_notional + 1e-7 or fill.qty > size(eq, fill.price, row["stop"], risk_pct=cfg.risk_pct, max_open=cfg.max_open) + 1e-10:
                st.seen.pop(key, None)
                out["skipped"] += 1
                ledger.log("skip", key=key, symbol=row["symbol"], formation=row["kind"], tf=row["tf"], side=side,
                           reason="исполнение не укладывается в лимит капитала или уровни", trend=tr)
                continue
            risk_net = abs(fill.price - row["stop"]) + broker.fee * (fill.price + row["stop"])
            reward_net = (1 if side == "long" else -1) * (row["target"] - fill.price) - broker.fee * (fill.price + row["target"])
            rr_net = reward_net / risk_net if risk_net else 0
            if reward_net <= 0 or rr_net + 1e-8 < cfg.min_entry_rr:
                st.seen.pop(key, None)
                out["skipped"] += 1
                ledger.log("skip", key=key, symbol=row["symbol"], formation=row["kind"],
                           tf=row["tf"], side=side, reason="цель не покрывает расходы или R:R ниже порога",
                           rr_net=rr_net, min_entry_rr=cfg.min_entry_rr, trend=tr)
                continue
            from src.data.market_clock import context
            m = row.get("measured") or {}
            p = Position(key=(key+f"|attempt{st.attempts.get(attempt_key,0)+1}" if row["tf"]=="5m" and cfg.max_attempts_5m>1 else key), source_key=key, symbol=row["symbol"], kind=row["kind"],
                         title=row.get("title", row["kind"]), tf=row["tf"], side=side,
                         qty=fill.qty, entry=fill.price, stop=row["stop"],
                         target=row["target"], opened_ms=now_ms,
                         expires_ms=(2**62 if cfg.no_timeout else expires(now_ms, row["tf"])), fee_in=fill.fee,
                         signal_entry=row["entry"], measured_r=row.get("exp_net"),
                         measured_n=int(m.get("n") or 0), last_check_ms=now_ms,
                         trend=tr, mark=fill.price, risk0=abs(fill.price - row["stop"]),
                         breakeven_r=cfg.breakeven_r, trail_r=cfg.trail_r, best=fill.price,
                         trail_pct=cfg.trail_pct, stop_on_close=cfg.stop_on_close,
                         tp1_r=cfg.tp1_r, tp1_frac=cfg.tp1_frac,
                         be_after_tp1=cfg.be_after_tp1, qty0=fill.qty,
                         reasons=list(row.get("reasons") or []), market_context=context(now_ms),
                         book={k: row.get(k) for k in ("spread_bps", "imbalance", "band_usdt", "mid")
                               if row.get(k) is not None},
                         signal_ts=int(row.get("ts") or 0), entry_rules=asdict(cfg), attempt_key=attempt_key)
            if cfg.no_target:
                p.target = 0.0
            st.attempts[attempt_key] = st.attempts.get(attempt_key, 0) + 1
            remaining_notional -= fill.qty * fill.price
            st.cash -= fill.fee
            st.day_pnl -= fill.fee
            st.put(p)
            open_syms.add(p.symbol)
            open_tfs.add((p.symbol, p.tf))
            out["opened"] += 1
            ledger.log("open", key=p.key, symbol=p.symbol, formation=p.kind, title=p.title,
                       tf=p.tf, side=side, qty=p.qty, entry=p.entry, stop=p.stop,
                       target=p.target, signal_entry=p.signal_entry, fee=fill.fee,
                       slippage_bp=fill.slippage_bp, measured_r=p.measured_r,
                       measured_n=p.measured_n, trend=tr, expires_ms=p.expires_ms,
                       reasons=p.reasons, book=p.book, risk0=p.risk0,
                       tp1=(p.entry + (1 if side == "long" else -1) * p.tp1_r * p.risk0)
                       if p.tp1_r else None, rules=p.entry_rules, market_context=p.market_context, manual_reason=p.manual_reason, attempt_key=p.attempt_key, rr_net_entry=rr_net, signal_ts=p.signal_ts)
            out["events"].append(
                f"ВХОД {SIDE_RU[side]} {p.symbol} {p.title} {p.tf}: {p.entry:.6g}, "
                f"стоп {p.stop:.6g}, цель {p.target:.6g}"
                + (f", измерено {p.measured_r:+.2f} R" if p.measured_r is not None else ""))

    # 4. память о виденных сигналах — трое суток
    st.seen = {k: v for k, v in st.seen.items() if now_ms - v < 3 * 86_400_000}
    out["equity"] = st.equity()
    return out
