"""Один шаг бота: сигнал → риск → заявки → журнал.

Шаг запускается раз в сутки, сразу после закрытия дневной свечи (00:05 UTC,
03:05 МСК). Он повторяемый: второй запуск в тот же день ничего не купит
повторно — ребаланс по дню сигнала делается один раз.

Каждый шаг, даже вне дня решения, пересчитывает капитал по текущим ценам и
проверяет стоп по просадке: просадка не ждёт дня ребаланса.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from src.trade.ledger import Ledger, State
from src.trade.risk import Limits, breached, plan
from src.trade.signal import DAY_MS, Decision, decide


@dataclass
class StepResult:
    state: State
    equity: float
    decision: Decision | None = None
    rebalanced: bool = False
    fills: list = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    halted: str | None = None


def _rebalance_due(state: State, decision: Decision, holding: int,
                   force: bool) -> bool:
    last = state.last_rebalance_day
    if last == decision.day_ts:
        return False                      # этот день уже отработан
    if force or last is None:
        return True
    if decision.is_decision_day:
        return True
    # пропущенный день решения (машина была выключена) — догоняем
    return decision.day_ts - last >= holding * DAY_MS


def _execute(orders, broker, state: State, ledger: Ledger, res: StepResult) -> None:
    for o in orders:
        qty = o.qty
        if o.side == "sell":
            have = broker.base_balance(o.symbol)
            if have is not None and have < qty:
                res.problems.append(f"{o.symbol}: на счёте {have:g}, в журнале {qty:g} — продаю, что есть")
                qty = have
        else:
            free = broker.free_quote()
            if free is not None and o.notional > free:
                res.problems.append(f"{o.symbol}: свободных USDT {free:.2f}, заявка уменьшена")
                qty *= max(free / o.notional * 0.99, 0.0)
        if qty <= 0:
            continue
        try:
            fill = broker.execute(o.symbol, o.side, qty)
        except Exception as e:   # заявку отклонили — остальные всё равно пробуем
            res.problems.append(f"{o.symbol} {o.side}: {type(e).__name__}: {str(e)[:200]}")
            ledger.log("error", symbol=o.symbol, side=o.side, qty=qty, error=str(e)[:500])
            continue
        if fill is None:
            res.problems.append(f"{o.symbol} {o.side}: не исполнено (стакан мелкий или объём ниже шага)")
            continue
        state.cash += fill.cash_delta
        q = state.positions.get(o.symbol, 0.0) + (fill.qty if o.side == "buy" else -fill.qty)
        if o.side == "sell" and q < o.qty * 1e-9 + 1e-12:
            q = 0.0
        if q > 0:
            state.positions[o.symbol] = q
        else:
            state.positions.pop(o.symbol, None)
        res.fills.append(fill)
        ledger.log("fill", **asdict(fill), slippage_bp=round(fill.slippage_bp, 2),
                   live=broker.live)


def step(series, *, broker, ledger: Ledger, limits: Limits, now_ms: int,
         lookback: int = 28, holding: int = 5, force: bool = False,
         mode: str = "paper") -> StepResult:
    state = ledger.load(mode, limits.capital)
    halted = ledger.halted

    decision = decide(series, now_ms=now_ms, lookback=lookback, holding=holding)
    symbols = sorted(set(state.positions) | set(decision.longs))
    prices = {}
    res = StepResult(state=state, equity=0.0, decision=decision, halted=halted)
    for s in symbols:
        try:
            prices[s] = broker.mid(s)
        except Exception as e:
            res.problems.append(f"{s}: нет цены ({type(e).__name__})")
    missing = [s for s in state.positions if s not in prices]
    if missing:
        # без цены позиции капитал не посчитать, а значит и ребаланс вести нельзя
        res.problems.append("нет цены по позициям: " + ", ".join(missing) + " — шаг пропущен")
        ledger.log("skip", reason="no_price", symbols=missing)
        return res

    equity = state.equity(prices)
    state.peak = max(state.peak, equity)
    res.equity = equity
    ledger.log("signal", day_ts=decision.day_ts, decision_day=decision.is_decision_day,
               momentum={k: round(v, 5) for k, v in decision.momentum.items()},
               longs=decision.longs, skipped=decision.skipped)

    if not halted and breached(equity, state.peak, limits):
        halted = (f"стоп по просадке: капитал {equity:.2f} ниже пика {state.peak:.2f} "
                  f"больше чем на {limits.max_drawdown:.0%}")
        exit_all = plan({}, equity=equity, cash=state.cash, positions=state.positions,
                        prices=prices, limits=limits)
        _execute(exit_all, broker, state, ledger, res)
        ledger.halt(halted)
        ledger.log("halt", reason=halted)
        res.halted = halted
    elif not halted and _rebalance_due(state, decision, holding, force):
        orders = plan(decision.weights, equity=equity, cash=state.cash,
                      positions=state.positions, prices=prices, limits=limits)
        ledger.log("plan", orders=[asdict(o) for o in orders])
        _execute(orders, broker, state, ledger, res)
        state.last_rebalance_day = decision.day_ts
        res.rebalanced = True

    res.equity = state.equity(prices)
    state.peak = max(state.peak, res.equity)
    ledger.save(state)
    ledger.log("equity", equity=round(res.equity, 4), cash=round(state.cash, 4),
               positions=state.positions, peak=round(state.peak, 4))
    return res
