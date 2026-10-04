"""Страница бумажного бота по сигналам скринера.

Главная таблица — живой результат по типам формаций рядом с тем, что
скринер показывает как измеренную ожидаемость: ради этой сверки бот и
запущен. Всё собирается из журнала бота и его состояния.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from src.trade.intraday import HORIZON, BotState, Config
from src.trade.ledger import Ledger
from tools.trade.page import e, max_drawdown, pct, sparkline, t

TREND_TEXT = {
    "off": "без фильтра по тренду (тренд монеты записывается для сверки)",
    "tf": "только по тренду таймфрейма сигнала из скринера (для 5m — тренд 15m)",
    "overall": "только по общему тренду монеты из скринера (1h и 4h совпали)",
}
POLICY_TEXT = {
    "all": "все свежие сработавшие сигналы скринера",
    "measured": "только сигналы, у типа которых измеренная ожидаемость в плюсе (30+ сделок)",
}
REASON = {"stop": "стоп", "target": "цель", "timeout": "время"}


def _r(x) -> str:
    return "—" if x is None else f"{x:+.2f}"


def build(ledger: Ledger, st: BotState, *, policy: str, trend: str, cfg: Config,
          now_ms: int, signals: int = 0, trend_err: str | None = None) -> str:
    j = ledger.journal()
    closed = [r for r in j if r["kind"] == "close"]
    skips = [r for r in j if r["kind"] == "skip"]
    errors = [r for r in j if r["kind"] == "error"][-10:]
    eq_rows = [r for r in j if r["kind"] == "equity"]
    values = [st.start_equity] + [r["equity"] for r in eq_rows] + [st.equity()]
    eq = st.equity()

    rs = [r["r_net"] for r in closed]
    wins = sum(1 for r in rs if r > 0)
    fees = sum(r.get("fee") or 0 for r in closed) + sum(p.fee_in for p in st.pos())
    slips = [r["slippage_bp"] for r in j if r["kind"] in ("open", "close")
             and r.get("slippage_bp") is not None]
    stats = [
        ("капитал, USDT", f"{eq:.2f}"),
        ("результат", pct(eq / st.start_equity - 1 if st.start_equity else None)),
        ("просадка от пика", pct(eq / st.peak - 1 if st.peak else None)),
        ("макс. просадка", pct(max_drawdown(values))),
        ("сделок закрыто", f"{len(closed)}"),
        ("в плюсе", f"{wins}/{len(rs)}" if rs else "—"),
        ("средний R после издержек", _r(sum(rs) / len(rs) if rs else None)),
        ("комиссии, USDT", f"{fees:.2f}"),
        ("проскальзывание входа, б.п.", f"{sum(slips) / len(slips):.1f}" if slips else "—"),
        ("открыто сейчас", f"{len(st.positions)} из {cfg.max_open}"),
        ("сигналов в последнем круге", f"{signals}"),
    ]
    stat_html = "".join(f"<tr><td>{e(k)}</td><td class=num>{e(v)}</td></tr>" for k, v in stats)

    # сверка по типам формаций
    by = defaultdict(list)
    meas = {}
    for r in closed:
        k = (r.get("title") or r["formation"], r["tf"])
        by[k].append(r["r_net"])
        meas[k] = (r.get("measured_r"), r.get("measured_n"))
    for p in st.pos():
        meas.setdefault((p.title, p.tf), (p.measured_r, p.measured_n))
        by.setdefault((p.title, p.tf), [])
    cmp_rows = []
    for k in sorted(by, key=lambda k: -len(by[k])):
        v = by[k]
        mr, mn = meas.get(k, (None, 0))
        live = sum(v) / len(v) if v else None
        cls = "" if live is None else ("long" if live > 0 else "short")
        cmp_rows.append(
            f"<tr><td>{e(k[0])}</td><td>{e(k[1])}</td><td class=num>{len(v)}</td>"
            f"<td class='num {cls}'>{_r(live)}</td><td class=num>{_r(mr)}</td>"
            f"<td class=num>{mn or '—'}</td></tr>")

    pos_rows = []
    for p in sorted(st.pos(), key=lambda p: p.opened_ms):
        now_r = p.r_of(p.mark or p.entry)
        pos_rows.append(
            f"<tr><td class={p.side}>{'ЛОНГ' if p.side == 'long' else 'ШОРТ'}</td>"
            f"<td>{e(p.symbol)}</td><td>{e(p.title)} · {e(p.tf)}</td>"
            f"<td class=num>{p.entry:.6g}</td><td class=num>{p.stop:.6g}</td>"
            f"<td class=num>{p.target:.6g}</td><td class=num>{(p.mark or p.entry):.6g}</td>"
            f"<td class='num {'long' if now_r > 0 else 'short'}'>{now_r:+.2f}</td>"
            f"<td>{e(p.trend or '—')}</td><td>{t(p.opened_ms)}</td><td>{t(p.expires_ms)}</td></tr>")

    tr_rows = []
    for r in reversed(closed[-40:]):
        tr_rows.append(
            f"<tr><td>{t(r['ts'])}</td><td class={r['side']}>{'ЛОНГ' if r['side'] == 'long' else 'ШОРТ'}</td>"
            f"<td>{e(r['symbol'])}</td><td>{e(r.get('title') or r['kind'])} · {e(r['tf'])}</td>"
            f"<td class=num>{r['entry']:.6g}</td><td class=num>{r['exit']:.6g}</td>"
            f"<td>{e(REASON.get(r['reason'], r['reason']))}</td>"
            f"<td class='num {'long' if r['r_net'] > 0 else 'short'}'>{r['r_net']:+.2f}</td>"
            f"<td class=num>{r['pnl'] - r['fee']:+.2f}</td><td>{e(r.get('trend') or '—')}</td></tr>")

    skip_count = defaultdict(int)
    for r in skips:
        skip_count[r["reason"]] += 1
    skip_html = "".join(f"<li>{e(k)}: {v}</li>" for k, v in
                        sorted(skip_count.items(), key=lambda kv: -kv[1]))
    err_html = "".join(f"<li>{t(r['ts'])} {e(r.get('where'))}: {e(r.get('error'))}</li>"
                       for r in errors)
    if trend_err:
        err_html = f"<li>{e(trend_err)}</li>" + err_html
    halted = ledger.halted

    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Бот по скринеру</title>
<style>
  :root {{ --bg:#12141a; --fg:#e6e8ee; --dim:#8b93a7; --line:#252a36;
           --good:#3ddc97; --bad:#ff6b6b; }}
  body {{ margin:0; padding:24px; background:var(--bg); color:var(--fg);
          font:14px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace; max-width:1200px; }}
  h1 {{ font-size:18px; margin:0 0 4px; }}
  h2 {{ font-size:15px; margin:26px 0 8px; }}
  .meta, .sub {{ color:var(--dim); }} .sub {{ font-size:12px; }}
  .box {{ padding:10px 12px; border-left:3px solid var(--line); background:#161922; }}
  .warn {{ border-left-color:var(--bad); }}
  table {{ border-collapse:collapse; width:100%; }}
  th,td {{ text-align:left; padding:6px 9px; border-bottom:1px solid var(--line); }}
  th {{ color:var(--dim); font-weight:400; }}
  td.num {{ text-align:right; white-space:nowrap; }}
  .long {{ color:var(--good); }} .short {{ color:var(--bad); }}
  .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:24px; }}
  @media (max-width:800px) {{ .grid {{ grid-template-columns:1fr; }} body {{ padding:16px; }} }}
  .chart {{ width:100%; height:120px; background:#161922; }}
  .wrap {{ overflow-x:auto; }}
  ul {{ margin:6px 0 0; padding-left:18px; }}
</style></head><body>
<h1>Бот по сигналам скринера · фьючерсы · лонг и шорт</h1>
<div class="meta">режим: бумажный · обновлено {t(now_ms)} МСК</div>
{f'<div class="box warn">ОСТАНОВЛЕН: {e(halted)}</div>' if halted else ''}

<h2>Что делает бот</h2>
<div class="box">
Раз в минуту-две бот берёт сигналы нашего скринера: формации на 5m, 15m и 1h
по фьючерсам Binance. Берёт {e(POLICY_TEXT.get(policy, policy))}, {e(TREND_TEXT.get(trend, trend))}.
Вход — рыночной заявкой по живому стакану, стоп и цель — из разбора формации,
выход по стопу, по первой цели или через {HORIZON} свечей таймфрейма сигнала.
Риск на сделку — {cfg.risk_pct:.0%} капитала, позиций не больше {cfg.max_open},
плеча нет. При просадке {cfg.max_drawdown:.0%} от пика новые входы прекращаются.
<ul>
<li>Заявки на биржу не уходят. Комиссия 0.05 % за сторону; стоп и цель исполняются по уровню
(стоп при гэпе — хуже, по открытию свечи).</li>
<li>Честно: при проверке на архиве с реальным исполнением ни одна формация с нормальной
выборкой не осталась в плюсе после издержек. Бот — живая проверка этого вывода,
а не способ заработать. Смотрите таблицу «Живой результат против измеренного».</li>
</ul></div>

<h2>Статистика</h2>
<div class="grid">
<div><table>{stat_html}</table></div>
<div>{sparkline(values)}</div>
</div>

<h2>Живой результат против измеренного</h2>
<div class="wrap"><table>
<thead><tr><th>формация</th><th>тф</th><th>сделок</th><th>живой R</th>
<th>измеренный R</th><th>сделок в замере</th></tr></thead>
<tbody>{''.join(cmp_rows) or '<tr><td colspan=6 class=sub>сделок ещё нет</td></tr>'}</tbody>
</table></div>
<p class="sub">R — результат в единицах риска (расстояние от входа до стопа) после комиссий.
«Измеренный» — то, что скринер показывает по типу формации на архиве; пусто — замера нет или сделок меньше 30.</p>

<h2>Открытые позиции</h2>
<div class="wrap"><table>
<thead><tr><th></th><th>монета</th><th>формация</th><th>вход</th><th>стоп</th><th>цель</th>
<th>сейчас</th><th>R</th><th>тренд</th><th>открыта</th><th>истекает</th></tr></thead>
<tbody>{''.join(pos_rows) or '<tr><td colspan=11 class=sub>позиций нет</td></tr>'}</tbody>
</table></div>

<h2>Закрытые сделки</h2>
<div class="wrap"><table>
<thead><tr><th>время МСК</th><th></th><th>монета</th><th>формация</th><th>вход</th><th>выход</th>
<th>причина</th><th>R</th><th>USDT</th><th>тренд</th></tr></thead>
<tbody>{''.join(tr_rows) or '<tr><td colspan=10 class=sub>сделок ещё нет</td></tr>'}</tbody>
</table></div>

{f'<h2>Почему сигналы не взяты</h2><div class="box"><ul>{skip_html}</ul></div>' if skip_html else ''}
{f'<h2>Ошибки</h2><div class="box"><ul>{err_html}</ul></div>' if err_html else ''}
<p class="sub">Источник: журнал {e(ledger.journal_path)}. Код: src/trade/intraday.py, tools/trade/screener_bot.py.</p>
</body></html>
"""


def write(ledger: Ledger, st: BotState, out: Path, **kw) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(build(ledger, st, **kw), encoding="utf-8")
    tmp.replace(out)
    return out
