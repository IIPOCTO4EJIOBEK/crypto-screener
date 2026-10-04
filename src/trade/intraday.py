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
    trend: str = ""                     # тренд монеты по скринеру на входе
    mark: float = 0.0                    # последняя цена — для капитала

    @property
    def risk(self) -> float:
        return abs(self.entry - self.stop)

    def r_of(self, price: float) -> float:
        move = price - self.entry if self.side == "long" else self.entry - price
        return move / self.risk if self.risk > 0 else 0.0


@dataclass(frozen=True)
class Exit:
    reason: str      # stop | target | timeout
    price: float
    ts: int


def signal_key(row: dict) -> str:
    """Один и тот же сигнал скринер показывает несколько кругов подряд."""
    return f"{row['kind']}|{row['tf']}|{row['symbol']}|{row['direction']}|{row['entry']:.10g}"


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


def check_exit(pos: Position, candles: list[Candle], now_ms: int) -> Exit | None:
    """Пройти закрытые минутные свечи после последней проверки.

    Свеча считается, если открылась не раньше открытия позиции. Гэп за стоп
    исполняется по открытию свечи (хуже стопа), а не по стопу.
    """
    for c in candles:
        if c.ts < pos.last_check_ms or c.ts + 60_000 > now_ms or c.ts < pos.opened_ms:
            continue
        if pos.side == "long":
            if c.low <= pos.stop:
                return Exit("stop", min(c.open, pos.stop), c.ts)
            if c.high >= pos.target:
                return Exit("target", pos.target, c.ts)
        else:
            if c.high >= pos.stop:
                return Exit("stop", max(c.open, pos.stop), c.ts)
            if c.low <= pos.target:
                return Exit("target", pos.target, c.ts)
    if now_ms >= pos.expires_ms:
        return Exit("timeout", 0.0, now_ms)      # цена — по стакану в момент выхода
    return None


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
    if Path(path).exists():
        return BotState(**json.loads(Path(path).read_text()))
    return BotState(cash=capital, peak=capital, start_equity=capital, start_ts=now_ms)


def save_state(path: Path, st: BotState) -> None:
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(st), ensure_ascii=False, indent=1))
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
    policy: str = "all"            # all | measured
    trend: str = "off"             # off | tf | overall
    risk_pct: float = 0.01         # риск на сделку от капитала
    max_open: int = 5
    max_age: int = 1               # свечей с момента события формации
    max_drawdown: float = 0.35
    equity_every_ms: int = 15 * 60_000


CLOSE = {"long": "sell", "short": "buy"}
OPEN = {"long": "buy", "short": "sell"}


def cycle(rows: list[dict], st: BotState, *, broker, ledger,
          candles: Callable[[str, int], list[Candle]], now_ms: int,
          cfg: Config, trend: dict | None = None) -> dict:
    """Проверить выходы открытых позиций, затем открыть новые по свежим сигналам.

    rows — строки скринера (как в `tools/live/screen.py --json`), в порядке
    ранжирования. candles(symbol, since_ms) — минутные свечи перпетуала.
    """
    out = {"opened": 0, "closed": 0, "skipped": 0}

    # 1. выходы
    for p in st.pos():
        try:
            cs = candles(p.symbol, max(p.last_check_ms, p.opened_ms))
        except Exception as exc:                            # noqa: BLE001
            ledger.log("error", where="candles", symbol=p.symbol, error=str(exc)[:200])
            continue
        ex = check_exit(p, cs, now_ms)
        closed = [c for c in cs if c.ts + 60_000 <= now_ms]
        if closed:
            p.mark = closed[-1].close
            p.last_check_ms = closed[-1].ts + 60_000
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
        else:
            # стоп и цель — по уровню (стоп при гэпе — по открытию свечи), комиссия тейкера
            price, fee, slip = ex.price, p.qty * ex.price * broker.fee, None
        pnl = _pnl(p, price)
        st.cash += pnl - fee
        st.drop(p.key)
        out["closed"] += 1
        ledger.log("close", key=p.key, symbol=p.symbol, formation=p.kind, title=p.title,
                   tf=p.tf, side=p.side, qty=p.qty, entry=p.entry, exit=price,
                   reason=ex.reason, exit_ts=ex.ts, opened_ms=p.opened_ms,
                   r=p.r_of(price), r_net=(pnl - fee - p.fee_in) / (p.qty * p.risk)
                   if p.risk else 0.0, pnl=pnl, fee=fee + p.fee_in, slippage_bp=slip,
                   measured_r=p.measured_r, measured_n=p.measured_n, trend=p.trend)

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
    if not ledger.halted:
        open_syms = {p.symbol for p in st.pos()}
        for row in rows:
            if not row.get("triggered"):
                continue                       # может сработать позже — не помечать
            key = signal_key(row)
            if key in st.seen:
                continue
            if len(st.positions) >= cfg.max_open:
                break                          # места нет; сигнал ещё свежий — посмотрим в следующий круг
            st.seen[key] = now_ms
            why = eligible(row, policy=cfg.policy, max_age=cfg.max_age)
            if why == "сигнал не свежий":
                continue                       # старые — молча, их много
            side = row["direction"]
            tr = trend_of(trend, row["symbol"], row["tf"], cfg.trend if cfg.trend != "off" else "tf")
            why = why or trend_block(side, tr, cfg.trend)
            if not why and row["symbol"] in open_syms:
                why = "по монете уже есть позиция"
            mid = None
            if not why:
                try:
                    mid = broker.mid(row["symbol"])
                except Exception as exc:                    # noqa: BLE001
                    why = f"стакан не получен: {str(exc)[:80]}"
            why = why or entry_ok(side, mid, row["stop"], row["target"])
            if why:
                out["skipped"] += 1
                ledger.log("skip", key=key, symbol=row["symbol"], formation=row["kind"],
                           tf=row["tf"], side=side, reason=why, trend=tr)
                continue
            qty = size(eq, mid, row["stop"], risk_pct=cfg.risk_pct, max_open=cfg.max_open)
            fill = broker.execute(row["symbol"], OPEN[side], qty) if qty > 0 else None
            if fill is None:
                out["skipped"] += 1
                ledger.log("skip", key=key, symbol=row["symbol"], formation=row["kind"],
                           tf=row["tf"], side=side, reason="глубины стакана не хватило", trend=tr)
                continue
            m = row.get("measured") or {}
            p = Position(key=key, symbol=row["symbol"], kind=row["kind"],
                         title=row.get("title", row["kind"]), tf=row["tf"], side=side,
                         qty=fill.qty, entry=fill.price, stop=row["stop"],
                         target=row["target"], opened_ms=now_ms,
                         expires_ms=expires(now_ms, row["tf"]), fee_in=fill.fee,
                         signal_entry=row["entry"], measured_r=row.get("exp_net"),
                         measured_n=int(m.get("n") or 0), last_check_ms=now_ms,
                         trend=tr, mark=fill.price)
            st.cash -= fill.fee
            st.put(p)
            open_syms.add(p.symbol)
            out["opened"] += 1
            ledger.log("open", key=key, symbol=p.symbol, formation=p.kind, title=p.title,
                       tf=p.tf, side=side, qty=p.qty, entry=p.entry, stop=p.stop,
                       target=p.target, signal_entry=p.signal_entry, fee=fill.fee,
                       slippage_bp=fill.slippage_bp, measured_r=p.measured_r,
                       measured_n=p.measured_n, trend=tr, expires_ms=p.expires_ms)
            if fill.price and entry_ok(side, fill.price, p.stop, p.target):
                # проскальзывание вынесло цену за стоп или цель — выйти сразу
                st.positions[key]["expires_ms"] = now_ms

    # 4. память о виденных сигналах — трое суток
    st.seen = {k: v for k, v in st.seen.items() if now_ms - v < 3 * 86_400_000}
    out["equity"] = st.equity()
    return out
