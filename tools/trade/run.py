"""Торговый бот тренд-фильтра: бумага → тестовая сеть → живой счёт.

Правило — long-only тренд 28/5 из `docs/research/16` (единственное, что в
проекте пережило издержки). Подробности, порядок запуска и лимиты —
`docs/04-торговля.md`.

    python -m tools.trade.run                      # бумажный шаг (по умолчанию)
    python -m tools.trade.run --plan               # только показать сигнал и заявки
    python -m tools.trade.run --status             # капитал, позиции, проскальзывание
    python -m tools.trade.run --check-key live     # права ключа, без заявок
    python -m tools.trade.run --mode testnet       # заявки на тестовой сети Binance
    python -m tools.trade.run --mode live --i-understand-real-money   # живые деньги

Живой режим требует трёх вещей сразу: `--mode live`, флаг
`--i-understand-real-money` и `TRADE_LIVE=yes` в `.env`. Без любой из них
заявка на живой счёт не уходит.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.data import market as md                      # noqa: E402
from src.trade.broker import ExchangeBroker, PaperBroker  # noqa: E402
from src.trade.engine import step                     # noqa: E402
from src.trade.ledger import Ledger                   # noqa: E402
from src.trade.risk import Limits, plan               # noqa: E402
from src.trade.signal import decide                   # noqa: E402

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT",
           "DOGEUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT"]


def load_env(path: Path = ROOT / ".env") -> None:
    """Прочитать .env в окружение, не перезаписывая уже заданное."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def notify(text: str) -> None:
    """Сообщение в Telegram, если заданы TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID."""
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return
    try:
        import requests
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat, "text": text}, timeout=15)
    except Exception as e:
        print(f"telegram: {e}", file=sys.stderr)


def fetch_series(symbols: list[str], source: str, lookback: int) -> dict:
    fn = md.binance_futures_ohlcv if source == "future" else md.binance_ohlcv
    out = {}
    for s in symbols:
        try:
            out[s] = fn(s, "1d", limit=lookback + 10)
        except Exception as e:
            print(f"  {s}: свечи не получены ({e})", file=sys.stderr)
    return out


def make_broker(mode: str, market: str):
    if mode == "paper":
        return PaperBroker(market)
    if mode == "testnet":
        return ExchangeBroker(api_key=os.environ.get("BINANCE_TESTNET_API_KEY", ""),
                              secret=os.environ.get("BINANCE_TESTNET_API_SECRET", ""),
                              market=market, testnet=True)
    return ExchangeBroker(api_key=os.environ.get("BINANCE_API_KEY", ""),
                          secret=os.environ.get("BINANCE_API_SECRET", ""),
                          market=market, testnet=False)


def publish_page(ledger: Ledger, a) -> None:
    """Пересобрать страницу бота; её сбой не должен ронять торговый шаг."""
    from tools.trade import page
    try:
        kw = dict(mode=a.mode, market=a.market, holding=a.holding, lookback=a.lookback,
                  side=a.side)
        page.write(ledger, ledger.root / "bot.html", **kw)
        if a.page_out:
            page.write(ledger, Path(a.page_out), **kw)
        page.write_all(Path(a.data))      # общая страница всех ботов, по вкладке на бота
    except Exception as e:
        print(f"страница бота: {type(e).__name__}: {e}", file=sys.stderr)


def fmt_day(ts: int) -> str:
    return datetime.fromtimestamp(ts / 1000, timezone.utc).strftime("%Y-%m-%d")


def cmd_status(ledger: Ledger) -> None:
    rows = ledger.journal()
    eq = [r for r in rows if r["kind"] == "equity"]
    fills = [r for r in rows if r["kind"] == "fill"]
    if not eq:
        print("журнала ещё нет: бот не запускался в этом режиме")
        return
    first, last = eq[0], eq[-1]
    print(f"шагов {len(eq)}, сделок {len(fills)}")
    print(f"капитал {last['equity']:.2f}  (старт {first['equity']:.2f}, "
          f"{last['equity'] / first['equity'] - 1:+.2%}), пик {last['peak']:.2f}")
    print(f"деньги {last['cash']:.2f}, позиции: "
          + (", ".join(f"{s} {q:g}" for s, q in last["positions"].items()) or "нет"))
    if fills:
        sl = [f["slippage_bp"] for f in fills]
        fee = sum(f["fee"] for f in fills)
        print(f"проскальзывание от середины стакана: среднее {sum(sl) / len(sl):.1f} б.п., "
              f"худшее {max(sl):.1f} б.п.; комиссий {fee:.2f} USDT")
    if ledger.halted:
        print(f"ОСТАНОВЛЕН: {ledger.halted}  (снять — удалить {ledger.halt_path})")


def run_one(argv: list[str] | None = None) -> int:
    load_env()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["paper", "testnet", "live"], default="paper")
    ap.add_argument("--market", choices=["spot", "future"], default="spot",
                    help="где исполнять (спот — без плеча и фандинга)")
    ap.add_argument("--signal-source", choices=["future", "spot"], default="future",
                    help="чьи дневные свечи считать (бэктест мерил перпетуал)")
    ap.add_argument("--symbols", default=",".join(SYMBOLS))
    ap.add_argument("--capital", type=float, default=float(os.environ.get("TRADE_CAPITAL", 1000)))
    ap.add_argument("--max-drawdown", type=float, default=0.35)
    ap.add_argument("--max-coin-weight", type=float, default=1.0)
    ap.add_argument("--max-gross", type=float, default=1.0)
    ap.add_argument("--lookback", type=int, default=28)
    ap.add_argument("--holding", type=int, default=5)
    ap.add_argument("--side", choices=["long", "longshort"], default="long",
                    help="long — только лонг (правило бэктеста); longshort — лонг по росту, шорт по падению (только фьючерсы)")
    ap.add_argument("--force", action="store_true", help="ребаланс сейчас, не дожидаясь дня решения")
    ap.add_argument("--plan", action="store_true", help="показать сигнал и заявки, ничего не исполнять")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--check-key", choices=["live", "testnet"])
    ap.add_argument("--i-understand-real-money", action="store_true")
    ap.add_argument("--data", default=str(ROOT / "data" / "trade"))
    ap.add_argument("--page-out", default=os.environ.get("TRADE_PAGE_OUT"),
                    help="куда ещё положить страницу бота (кроме каталога журнала)")
    a = ap.parse_args(argv)

    if a.side == "longshort" and a.market != "future":
        print("шорт возможен только на фьючерсах: добавьте --market future", file=sys.stderr)
        return 2
    root = Path(a.data) / (f"{a.mode}-{a.market}" + ("" if a.side == "long" else f"-{a.side}"))
    ledger = Ledger(root)
    if a.status:
        cmd_status(ledger)
        return 0
    if a.check_key:
        b = make_broker(a.check_key, a.market)
        for k, v in b.key_report().items():
            print(f"{k:28} {v}")
        return 0
    if a.mode == "live" and not (a.i_understand_real_money
                                 and os.environ.get("TRADE_LIVE") == "yes"):
        print("живой режим заблокирован: нужны --i-understand-real-money и TRADE_LIVE=yes в .env",
              file=sys.stderr)
        return 2

    symbols = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    limits = Limits(capital=a.capital, max_drawdown=a.max_drawdown,
                    max_coin_weight=a.max_coin_weight, max_gross=a.max_gross)
    now = int(time.time() * 1000)
    series = fetch_series(symbols, a.signal_source, a.lookback)

    if a.plan:
        d = decide(series, now_ms=now, lookback=a.lookback, holding=a.holding)
        state = ledger.load(a.mode, a.capital)
        paper = PaperBroker(a.market)
        target = d.signed_weights(a.side)
        prices = {s: paper.mid(s) for s in sorted(set(target) | set(state.positions))}
        eq = state.equity(prices)
        print(f"день сигнала {fmt_day(d.day_ts)}, день решения: {'да' if d.is_decision_day else 'нет'}")
        for s, m in sorted(d.momentum.items(), key=lambda x: -x[1]):
            mark = 'ЛОНГ' if target.get(s, 0) > 0 else ('ШОРТ' if target.get(s, 0) < 0 else '—')
            print(f"  {s:9} моментум {m:+.2%}  {mark}")
        for s, why in d.skipped.items():
            print(f"  {s:9} мимо: {why}")
        for o in plan(target, equity=eq, cash=state.cash, positions=state.positions,
                      prices=prices, limits=limits):
            print(f"  заявка {o.side:4} {o.symbol:9} {o.qty:.6g} ≈ {o.notional:.2f} USDT")
        return 0

    try:
        broker = make_broker(a.mode, a.market)
        broker.prepare(symbols)
        res = step(series, broker=broker, ledger=ledger, limits=limits, now_ms=now,
                   lookback=a.lookback, holding=a.holding, force=a.force, mode=a.mode,
                   side=a.side)
    except Exception as e:   # падение шага не должно пройти молча
        text = f"[{a.mode}/{a.market}] шаг упал: {type(e).__name__}: {str(e)[:300]}"
        print(text, file=sys.stderr)
        ledger.log("error", error=text)
        notify(text)
        publish_page(ledger, a)
        return 1

    d = res.decision
    lines = [f"[{a.mode}/{a.market}] день {fmt_day(d.day_ts)}: "
             f"капитал {res.equity:.2f} USDT ({res.equity / res.state.start_equity - 1:+.2%})",
             "лонги: " + (", ".join(d.longs) or "нет")
             + ("; шорты: " + (", ".join(d.shorts) or "нет") if a.side == "longshort" else "")]
    if res.rebalanced:
        lines.append("ребаланс: " + (", ".join(f"{f.side} {f.symbol} {f.qty:.6g} @ {f.price:g}"
                                               for f in res.fills) or "заявок не понадобилось"))
    lines += [f"! {p}" for p in res.problems]
    if res.halted:
        lines.append(f"ОСТАНОВЛЕН: {res.halted}")
    text = "\n".join(lines)
    print(text)
    if res.rebalanced or res.problems or res.halted:
        notify(text)
    publish_page(ledger, a)
    return 1 if res.problems else 0


PROFILES = ROOT / "data" / "trade" / "profiles.json"


def main(argv: list[str] | None = None) -> int:
    """Без аргументов и при наличии data/trade/profiles.json — все профили по очереди.

    Так один таймер systemd ведёт несколько ботов (спот раз в 5 дней, фьючерсы
    каждый день), не трогая файл сервиса. Файл — список списков аргументов:
    `[[], ["--market", "future", "--holding", "1"]]`. Сбой одного профиля не
    мешает остальным.
    """
    argv = sys.argv[1:] if argv is None else argv
    if argv or not PROFILES.exists():
        return run_one(argv)
    rc = 0
    for args in json.loads(PROFILES.read_text(encoding="utf-8")):
        print(f"--- профиль: {' '.join(args) or 'по умолчанию'}")
        try:
            rc |= run_one(list(args))
        except SystemExit as e:          # argparse на кривом профиле
            rc |= int(e.code or 1)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
