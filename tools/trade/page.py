"""Страница бумажного бота: описание, портфель, сделки, статистика, сверка с бэктестом.

Собирается из журнала бота (`journal.jsonl`) и его состояния — никаких
отдельных расчётов: что бот записал, то и показано. Пересобирается после
каждого шага бота (`tools/trade/run.py`) и вручную:

    python -m tools.trade.page                      # data/trade/paper-spot/bot.html
    python -m tools.trade.page --out /path/bot.html
"""

from __future__ import annotations

import argparse
import html
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.trade.ledger import Ledger            # noqa: E402
from src.trade.signal import DAY_MS             # noqa: E402

MSK = timezone(timedelta(hours=3))

# Числа бэктеста — из docs/research/16 (L28 H5, издержки перпетуала, лаг 1).
BACKTEST = [
    ("Sharpe, состав по дате листинга (честный)", "0.64"),
    ("Sharpe, восемь лидеров 2026 года (завышен отбором)", "1.24"),
    ("превышение над рынком по Sharpe (честный состав)", "+0.13"),
    ("худшая просадка", "−76 … −79 %"),
    ("доля времени в позиции по монете", "≈ 49 %"),
    ("2025 год: правило / рынок", "+15.2 % / −35.8 %"),
    ("2026 год до сентября: правило / рынок", "−17.6 % / −26.9 %"),
    ("издержки в бэктесте за сторону", "0.05 % комиссия + 1 б.п. проскальзывание"),
]


def e(x) -> str:
    return html.escape(str(x))


def t(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, MSK).strftime("%d.%m %H:%M")


def day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%d.%m.%Y")


def pct(x: float | None, digits: int = 2) -> str:
    return "—" if x is None else f"{x * 100:+.{digits}f} %"


def max_drawdown(values: list[float]) -> float:
    peak, worst = float("-inf"), 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            worst = min(worst, v / peak - 1.0)
    return worst


def market_return(equity_rows: list[dict]) -> float | None:
    """Равные веса тех же монет от первого шага с ценами до последнего."""
    rows = [r for r in equity_rows if r.get("prices")]
    if len(rows) < 2:
        return None
    a, b = rows[0]["prices"], rows[-1]["prices"]
    common = [s for s in a if s in b and a[s] > 0]
    if not common:
        return None
    return sum(b[s] / a[s] for s in common) / len(common) - 1.0


def sparkline(values: list[float], w: int = 640, h: int = 120) -> str:
    if len(values) < 2:
        return '<div class="sub">график появится после второго шага бота</div>'
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    pts = " ".join(f"{i * w / (len(values) - 1):.1f},{h - (v - lo) / span * h:.1f}"
                   for i, v in enumerate(values))
    return (f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" class="chart">'
            f'<polyline fill="none" stroke="var(--good)" stroke-width="2" points="{pts}"/>'
            f'</svg><div class="sub">мин {lo:.2f} · макс {hi:.2f} USDT</div>')


def build(ledger: Ledger, *, mode: str = "paper", market: str = "spot",
          holding: int = 5, lookback: int = 28, now_ms: int | None = None) -> str:
    rows = ledger.journal()
    eq = [r for r in rows if r["kind"] == "equity"]
    fills = [r for r in rows if r["kind"] == "fill"]
    signals = [r for r in rows if r["kind"] == "signal"]
    errors = [r for r in rows if r["kind"] in ("error", "skip", "halt")]
    now_ms = now_ms or (rows[-1]["ts"] if rows else 0)

    values = [r["equity"] for r in eq]
    last = eq[-1] if eq else None
    start = eq[0]["equity"] if eq else None
    ret = (last["equity"] / start - 1.0) if last and start else None
    mkt = market_return(eq)
    dd = max_drawdown(values) if values else None
    fee = sum(f["fee"] for f in fills)
    sl = [f["slippage_bp"] for f in fills]
    days = (now_ms - eq[0]["ts"]) / DAY_MS if eq else 0
    sig = signals[-1] if signals else None

    # следующий день решения: ближайший календарный день с номером, кратным holding,
    # после дня последнего сигнала; бот отработает его в 00:05 UTC следующих суток
    next_dec = None
    if sig:
        d = sig["day_ts"] // DAY_MS + 1
        while d % holding:
            d += 1
        next_dec = (d + 1) * DAY_MS + 5 * 60_000

    stats = [
        ("капитал", f"{last['equity']:.2f} USDT" if last else "—"),
        ("старт", f"{start:.2f} USDT" if start else "—"),
        ("доходность бота", pct(ret)),
        ("рынок: те же монеты, равные веса", pct(mkt)),
        ("разница с рынком", pct(None if ret is None or mkt is None else ret - mkt)),
        ("худшая просадка", pct(dd)),
        ("пик", f"{last['peak']:.2f} USDT" if last else "—"),
        ("деньги вне рынка", f"{last['cash']:.2f} USDT" if last else "—"),
        ("сделок", str(len(fills))),
        ("комиссий уплачено", f"{fee:.2f} USDT"),
        ("проскальзывание от середины стакана",
         f"среднее {sum(sl) / len(sl):.1f} б.п., худшее {max(sl):.1f} б.п." if sl else "—"),
        ("дней работы", f"{days:.1f}"),
        ("следующий ребаланс", t(next_dec) + " МСК" if next_dec else "—"),
    ]
    halted = ledger.halted

    # портфель по последним ценам
    pos_rows = []
    if last:
        prices = last.get("prices") or {}
        for s, q in sorted(last["positions"].items()):
            px = prices.get(s)
            val = q * px if px else None
            w = val / last["equity"] if val and last["equity"] else None
            pos_rows.append(f"<tr><td>{e(s)}</td><td class=num>{q:.6g}</td>"
                            f"<td class=num>{'—' if px is None else f'{px:g}'}</td>"
                            f"<td class=num>{'—' if val is None else f'{val:.2f}'}</td>"
                            f"<td class=num>{'—' if w is None else f'{w * 100:.1f} %'}</td></tr>")

    sig_rows = []
    if sig:
        for s, m in sorted(sig["momentum"].items(), key=lambda x: -x[1]):
            cls = "long" if s in sig["longs"] else "dim"
            sig_rows.append(f"<tr class={cls}><td>{e(s)}</td><td class=num>{m * 100:+.2f} %</td>"
                            f"<td>{'в портфеле' if s in sig['longs'] else 'вне портфеля'}</td></tr>")
        for s, why in (sig.get("skipped") or {}).items():
            sig_rows.append(f"<tr class=dim><td>{e(s)}</td><td class=num>—</td><td>{e(why)}</td></tr>")

    fill_rows = [
        f"<tr><td>{t(f['ts'])}</td><td class={'long' if f['side'] == 'buy' else 'short'}>"
        f"{'покупка' if f['side'] == 'buy' else 'продажа'}</td><td>{e(f['symbol'])}</td>"
        f"<td class=num>{f['qty']:.6g}</td><td class=num>{f['price']:g}</td>"
        f"<td class=num>{f['qty'] * f['price']:.2f}</td><td class=num>{f['fee']:.3f}</td>"
        f"<td class=num>{f['slippage_bp']:.1f}</td></tr>"
        for f in reversed(fills[-200:])]

    err_rows = [f"<li>{t(r['ts'])}: {e(r.get('error') or r.get('reason') or r)}</li>"
                for r in errors[-20:]]

    stat_html = "".join(f"<tr><td>{e(k)}</td><td class=num>{e(v)}</td></tr>" for k, v in stats)
    bt_html = "".join(f"<tr><td>{e(k)}</td><td class=num>{e(v)}</td></tr>" for k, v in BACKTEST)
    mode_name = {"paper": "бумажный", "testnet": "тестовая сеть", "live": "ЖИВОЙ СЧЁТ"}.get(mode, mode)

    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Торговый бот</title>
<style>
  :root {{ --bg:#12141a; --fg:#e6e8ee; --dim:#8b93a7; --line:#252a36;
           --good:#3ddc97; --bad:#ff6b6b; }}
  body {{ margin:0; padding:24px; background:var(--bg); color:var(--fg);
          font:14px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace; max-width:1100px; }}
  h1 {{ font-size:18px; margin:0 0 4px; }}
  h2 {{ font-size:15px; margin:26px 0 8px; color:var(--fg); }}
  .meta, .sub, .dim td {{ color:var(--dim); }}
  .sub {{ font-size:12px; }}
  .box {{ padding:10px 12px; border-left:3px solid var(--line); background:#161922; }}
  .warn {{ border-left-color:var(--bad); }}
  table {{ border-collapse:collapse; width:100%; }}
  th,td {{ text-align:left; padding:6px 9px; border-bottom:1px solid var(--line); }}
  th {{ color:var(--dim); font-weight:400; }}
  td.num {{ text-align:right; white-space:nowrap; }}
  .long, tr.long td:first-child {{ color:var(--good); }} .short {{ color:var(--bad); }}
  .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:24px; }}
  @media (max-width:800px) {{ .grid {{ grid-template-columns:1fr; }} body {{ padding:16px; }} }}
  .chart {{ width:100%; height:120px; background:#161922; }}
  .wrap {{ overflow-x:auto; }}
  ul {{ margin:6px 0 0; padding-left:18px; }}
</style></head><body>
<h1>Торговый бот: тренд-фильтр 28/5</h1>
<div class="meta">режим: {e(mode_name)} · рынок: {e(market)} · обновлено {t(now_ms) if now_ms else '—'} МСК</div>
{f'<div class="box warn">ОСТАНОВЛЕН: {e(halted)}</div>' if halted else ''}

<h2>Что делает бот</h2>
<div class="box">
Каждый день в 03:05 МСК, после закрытия дневной свечи, бот берёт дневные свечи
восьми монет (BTC, ETH, SOL, XRP, DOGE, ADA, LINK, AVAX) и считает моментум:
насколько цена выросла за {lookback} дней. Монеты с ростом — в портфель равными
долями, остальные — нет, деньги ждут в USDT. Шорта и плеча нет.
Портфель пересобирается раз в {holding} дней. Если капитал упадёт ниже пика
больше чем на 35 %, бот продаёт всё и останавливается до ручного решения.
<ul>
<li>Режим «бумажный»: заявки на биржу не уходят. Цена каждой сделки считается
по живому стакану Binance — так, как прошла бы настоящая рыночная заявка, — плюс
комиссия спота 0.1 %.</li>
<li>Это единственное правило в проекте, которое пережило издержки в бэктесте.
Преимущество скромное: над рынком около +0.13 по Sharpe, а в 2026 году правило
в минусе вместе с рынком. Бумага нужна, чтобы проверить перенос на живой рынок,
а не чтобы подтвердить прибыль.</li>
</ul></div>

<h2>Статистика</h2>
<div class="grid">
<div><table>{stat_html}</table></div>
<div>{sparkline(values)}</div>
</div>

<h2>Портфель</h2>
<div class="wrap"><table>
<thead><tr><th>монета</th><th>количество</th><th>цена</th><th>стоимость, USDT</th><th>доля</th></tr></thead>
<tbody>{''.join(pos_rows) or '<tr><td colspan=5 class=sub>позиций нет</td></tr>'}</tbody>
</table></div>

<h2>Последний сигнал{f' — день {day(sig["day_ts"])}' if sig else ''}</h2>
<div class="wrap"><table>
<thead><tr><th>монета</th><th>моментум {lookback} дн.</th><th>решение</th></tr></thead>
<tbody>{''.join(sig_rows) or '<tr><td colspan=3 class=sub>сигналов ещё нет</td></tr>'}</tbody>
</table></div>

<h2>Сверка с бэктестом</h2>
<div class="grid">
<div><table><thead><tr><th>бэктест 2020–2026 (docs/research/16)</th><th></th></tr></thead>{bt_html}</table></div>
<div class="box">
Что сравнивать по мере накопления журнала:
<ul>
<li><b>Издержки.</b> В бэктесте — 0.05 % + 1 б.п. за сторону. Здесь — комиссия спота
0.1 % и проскальзывание, замеренное на каждой сделке (в статистике слева).</li>
<li><b>Разница с рынком.</b> Бэктест обещает преимущество над рынком в основном на
падениях: правило уходит в кэш. На росте оно идёт почти вровень с рынком.</li>
<li><b>Просадка.</b> Стоп 35 % в бэктесте не проверялся; исторически правило
проходило −76…−79 %, то есть стоп сработал бы.</li>
<li>За месяц бот принимает около 6 решений — выводы имеют смысл через несколько
месяцев, не дней.</li>
</ul></div>
</div>

<h2>Сделки</h2>
<div class="wrap"><table>
<thead><tr><th>время МСК</th><th>сторона</th><th>монета</th><th>количество</th><th>цена</th>
<th>сумма, USDT</th><th>комиссия</th><th>проскальз., б.п.</th></tr></thead>
<tbody>{''.join(fill_rows) or '<tr><td colspan=8 class=sub>сделок ещё нет</td></tr>'}</tbody>
</table></div>
{f'<h2>Ошибки и пропуски</h2><div class="box"><ul>{"".join(err_rows)}</ul></div>' if err_rows else ''}
<p class="sub">Источник: журнал бота {e(ledger.journal_path)}. Код: src/trade, tools/trade, описание — docs/04-торговля.md.</p>
</body></html>
"""


def write(ledger: Ledger, out: Path, **kw) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(build(ledger, **kw), encoding="utf-8")
    tmp.replace(out)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="paper")
    ap.add_argument("--market", default="spot")
    ap.add_argument("--data", default=str(ROOT / "data" / "trade"))
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    ledger = Ledger(Path(a.data) / f"{a.mode}-{a.market}")
    out = Path(a.out) if a.out else ledger.root / "bot.html"
    print(write(ledger, out, mode=a.mode, market=a.market))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
