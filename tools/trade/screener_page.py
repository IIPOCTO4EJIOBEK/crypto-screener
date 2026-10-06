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
from src.trade import trend_display

TREND_TEXT = {
    "off": "без фильтра по тренду (тренд монеты записывается для сверки)",
    "tf": "только по тренду таймфрейма сигнала из скринера (для 5m — тренд 15m)",
    "overall": "только по общему тренду монеты из скринера (1h и 4h совпали)",
}
POLICY_TEXT = {
    "all": "все свежие сработавшие сигналы скринера",
    "measured": "только сигналы, у типа которых измеренная ожидаемость в плюсе (30+ сделок)",
}
REASON = {"stop": "стоп", "trail": "сдвинутый стоп", "target": "цель", "timeout": "время", "invalidation": "смена структуры", "manual": "ручной выход"}


def _net(r: dict) -> float:
    """Чистый результат сделки в USDT: с частичными тейками, комиссиями и фандингом."""
    return r["pnl"] - r["fee"] - (r.get("funding") or 0.0)


def _group(closed: list[dict], key) -> list[str]:
    g = defaultdict(list)
    for r in closed:
        g[key(r)].append(r)
    out = []
    for k, v in sorted(g.items(), key=lambda kv: -sum(_net(r) for r in kv[1])):
        rs = [r["r_net"] for r in v]
        net = sum(_net(r) for r in v)
        out.append(f"<tr><td>{e(k)}</td><td class=num>{len(v)}</td>"
                   f"<td class=num>{sum(1 for x in rs if x > 0)}/{len(rs)}</td>"
                   f"<td class=num>{sum(rs) / len(rs):+.2f}</td>"
                   f"<td class='num {'long' if net > 0 else 'short'}'>{net:+.2f}</td></tr>")
    return out


def settings(cfg: Config) -> list[str]:
    """Включённые правила ведения позиции — словами, для блока «Что делает бот»."""
    out = [f"До {cfg.max_attempts_5m} попыток входа 5m на одно событие; затем ожидание следующего сигнала 5m", "Выход по времени выключен" if cfg.no_timeout else "Выход по времени включён"]
    out += [f"до {cfg.max_open} одновременно открытых позиций", f"капитал в позициях не выше {cfg.capital_fraction:.0%}", f"остановка входов при просадке от пика {cfg.max_drawdown:.0%}"]
    if cfg.parallel_timeframes:
        out.append("отдельные позиции одной монеты на разных таймфреймах")
    if cfg.exit_on_opposite:
        out.append("выход при подтверждённом развороте: свежий противоположный пробой, ретест или слом на том же/старшем ТФ; закрытая свеча и текущая цена за уровнем")
    if cfg.min_entry_rr:
        out.append(f"чистый R:R фактического входа не ниже {cfg.min_entry_rr:g}, включая комиссии")
    if cfg.tp1_r:
        out.append(f"первый тейк: {cfg.tp1_frac:.0%} позиции на +{cfg.tp1_r:g} R"
                   + (", после него стоп в безубыток" if cfg.be_after_tp1 else ""))
    if cfg.breakeven_r:
        out.append(f"безубыток: стоп на вход при +{cfg.breakeven_r:g} R")
    if cfg.trail_r:
        out.append(f"трейлинг: стоп в {cfg.trail_r:g} R от лучшей цены")
    if cfg.trail_pct:
        out.append(f"трейлинг: стоп в {cfg.trail_pct:.2%} от лучшей цены")
    if cfg.stop_on_close:
        out.append("стоп по закрытию минутной свечи, а не по касанию")
    if cfg.no_target:
        out.append("без цели формации: выход стопом или трейлингом" if cfg.no_timeout else "без цели формации: выход стопом, трейлингом или по времени")
    if cfg.daily_loss:
        out.append(f"дневной лимит убытка {cfg.daily_loss:.1%} капитала")
    if cfg.cooldown_min:
        out.append(f"пауза по монете {cfg.cooldown_min} мин после стопа")
    if cfg.pause_after:
        out.append(f"пауза всех входов на {cfg.pause_min} мин после {cfg.pause_after} убыточных подряд")
    if cfg.max_side:
        out.append(f"не больше {cfg.max_side} позиций в одну сторону")
    if cfg.funding:
        out.append("фандинг учитывается в результате")
    return out


def _r(x) -> str:
    return "—" if x is None else f"{x:+.2f}"


def build(ledger: Ledger, st: BotState, *, policy: str, trend: str, cfg: Config,
          now_ms: int, signals: int = 0, trend_err: str | None = None) -> str:
    j = ledger.journal()
    current_trend=trend_display.load(now_ms)
    closed = [r for r in j if r["kind"] == "close"]
    skips = [r for r in j if r["kind"] == "skip"]
    errors = [r for r in j if r["kind"] == "error"][-10:]
    eq_rows = [r for r in j if r["kind"] == "equity"]
    values = [st.start_equity] + [r["equity"] for r in eq_rows] + [st.equity()]
    eq = st.equity()

    rs = [r["r_net"] for r in closed]
    wins = sum(1 for r in rs if r > 0)
    nets = [_net(r) for r in closed]
    gross_win = sum(x for x in nets if x > 0)
    gross_loss = -sum(x for x in nets if x < 0)
    pf = gross_win / gross_loss if gross_loss > 0 else None
    funding = sum(r.get("funding") or 0 for r in closed)
    fees = sum(r.get("fee") or 0 for r in closed) + sum(p.fee_in for p in st.pos()) + sum(r.get("fee") or 0 for r in j if r["kind"] == "partial")
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
        ("profit factor", f"{pf:.2f}" if pf is not None else "—"),
        ("лучшая / худшая сделка, R", f"{max(rs):+.2f} / {min(rs):+.2f}" if rs else "—"),
        ("результат за сутки МСК, USDT", f"{st.day_pnl:+.2f}"),
        ("фандинг, USDT", f"{-funding:+.2f}"),
        ("комиссии, USDT", f"{fees:.2f}"),
        ("проскальзывание входа, б.п.", f"{sum(slips) / len(slips):.1f}" if slips else "—"),
        ("открыто сейчас", f"{len(st.positions)} из {cfg.max_open}"),
        ("сигналов в последнем круге", f"{signals}"),
    ]
    if st.streak:
        stats.append(("убыточных подряд", f"{st.streak}"))
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

    controls_html = ''.join(f"<li>{t(r['ts'])} {e(r.get('operation') or r['kind'])}: {e(r.get('symbol',''))} {e(r.get('reason',''))}</li>" for r in j if r['kind'] in ('control','control_rejected','attempt_review'))
    control_script = (Path(__file__).with_name("position-controls.js")).read_text("utf-8")
    pos_rows = []
    for p in sorted(st.pos(), key=lambda p: p.opened_ms):
        now_r = p.r_of(p.mark or p.entry)
        from src.trade.position_metrics import metrics
        pm=metrics(p,st.equity());pnl_net=pm['pnl_usdt']
        exposure=f"{pm['exposure_pct']:.2f}%" if pm['exposure_pct'] is not None else '—'
        pos_rows.append(
            f"<tr><td class={p.side}>{'ЛОНГ' if p.side == 'long' else 'ШОРТ'}</td>"
            f"<td>{e(p.symbol)}</td><td>{e(p.title)} · {e(p.tf)}</td>"
            f"<td class=num>{p.entry:.6g}</td><td class=num>{p.stop:.6g}</td>"
            f"<td class=num>{p.target:.6g}</td><td class=num>{(p.mark or p.entry):.6g}</td>"
            f"<td class='num {'long' if now_r > 0 else 'short'}'>{now_r:+.2f}</td>"
            f"<td class='num {'long' if pnl_net >= 0 else 'short'}'>{pnl_net:+.2f}</td>"
            f"<td class='num {'long' if pnl_net >= 0 else 'short'}'>{pm['pnl_pct']:+.2f}%</td><td class=num>{exposure}</td>"
            f"<td><button data-action=edit data-key='{e(p.key)}' data-bot='{e(ledger.root.name.removeprefix("screener-"))}' data-stop='{p.stop}' data-target='{p.target}'>Стоп / тейк</button> <button data-action=close data-key='{e(p.key)}' data-bot='{e(ledger.root.name.removeprefix("screener-"))}'>Выйти</button></td>"
            f"<td>{e(trend_display.text(p,current_trend))}</td><td>{t(p.opened_ms)}</td><td>{"выключен" if cfg.no_timeout else t(p.expires_ms)}</td></tr>")

    tr_rows = []
    for r in reversed(closed[-40:]):
        result_pct=f"{100*_net(r)/(r['entry']*r['qty']):+.2f}%" if r.get('qty') else '—'
        tr_rows.append(
            f"<tr><td>{t(r['ts'])}</td><td class={r['side']}>{'ЛОНГ' if r['side'] == 'long' else 'ШОРТ'}</td>"
            f"<td>{e(r['symbol'])}</td><td>{e(r.get('title') or r['kind'])} · {e(r['tf'])}</td>"
            f"<td class=num>{r['entry']:.6g}</td><td class=num>{r['exit']:.6g}</td>"
            f"<td>{e(REASON.get(r['reason'], r['reason']) + (' · '+r.get('manual_reason','') if r.get('manual_reason') else '') + (' · funding ожидает расчёта; PnL предварительный' if r.get('funding_pending') else ''))}</td>"
            f"<td class='num {'long' if r['r_net'] > 0 else 'short'}'>{r['r_net']:+.2f}</td>"
            f"<td class=num>{_net(r):+.2f}</td><td class=num>{result_pct}</td><td>{e(r.get('trend') or '—')}</td></tr>")

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
          font:14px/1.6 system-ui,Segoe UI,sans-serif; max-width:1600px; }}
  h1 {{ font-size:18px; margin:0 0 4px; }}
  h2 {{ font-size:15px; margin:26px 0 8px; }}
  .meta, .sub {{ color:var(--dim); }} .sub {{ font-size:12px; }}
  .box {{ padding:10px 12px; border-left:3px solid var(--line); background:#161922; }}
  .warn {{ border-left-color:var(--bad); }}
  table {{ border-collapse:collapse; width:100%; }}
  th,td {{ text-align:left; padding:10px 12px; border-bottom:1px solid var(--line); }}
  th {{ color:var(--dim); font-weight:400; }}
  td.num {{ text-align:right; white-space:nowrap; }}
  .long {{ color:var(--good); }} .short {{ color:var(--bad); }}
  .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:24px; }}
  @media (max-width:800px) {{ .grid {{ grid-template-columns:1fr; }} body {{ padding:16px; }} }}
  .chart {{ width:100%; height:120px; background:#161922; }}
  .wrap {{ overflow-x:auto; }}
  td.num {{font-variant-numeric:tabular-nums;}}
  tbody tr:nth-child(even) {{background:#181e28;}}
  tbody tr:hover {{background:#253044;}}
  button {{padding:6px 10px;background:#243653;color:#e6e8ee;border:1px solid #47628c;border-radius:7px;cursor:pointer;}}
  .wrap {{border:1px solid var(--line);border-radius:10px;}}
  ul {{ margin:6px 0 0; padding-left:18px; }}
</style></head><body>
<h1>Бот по сигналам скринера · фьючерсы · лонг и шорт</h1>
<div class="meta">режим: бумажный · обновлено {t(now_ms)} МСК</div>
{f'<div class="box warn">ОСТАНОВЛЕН: {e(halted)}</div>' if halted else ''}
{'<div class="box warn">Новые входы на паузе (команда /pause).</div>' if (ledger.root / "PAUSE").exists() else ''}

<h2>Что делает бот</h2>
<div class="box">
Раз в минуту-две бот берёт сигналы нашего скринера: формации на 5m, 15m и 1h
по фьючерсам Binance. Берёт {e(POLICY_TEXT.get(policy, policy))}, {e(TREND_TEXT.get(trend, trend))}.
Вход — рыночной заявкой по живому стакану, стоп и цель — из разбора формации,
{"Выход по стопу / тейку; закрытие по времени выключено." if cfg.no_timeout else f"Выход по стопу, цели или через {HORIZON} свечей."}
<p class=sub>{f'Funding ожидает расчёта: {st.pending_funding} сделок. Их PnL предварительный; новые входы профиля приостановлены, выходы продолжаются.' if st.pending_funding else ''}</p>
Лимит использования капитала — {cfg.capital_fraction:.0%}. Риск на сделку — {cfg.risk_pct:.2%} капитала, позиций не больше {cfg.max_open},
плеча нет. При просадке {cfg.max_drawdown:.0%} от пика новые входы прекращаются.
<ul>
{''.join(f"<li>{e(x)}</li>" for x in settings(cfg))}
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

<h2>По монетам и сторонам</h2>
<div class="grid">
<div class="wrap"><table><thead><tr><th>монета</th><th>сделок</th><th>в плюсе</th><th>средний R</th><th>USDT</th></tr></thead>
<tbody>{''.join(_group(closed, lambda r: r["symbol"])) or '<tr><td colspan=5 class=sub>сделок ещё нет</td></tr>'}</tbody></table></div>
<div class="wrap"><table><thead><tr><th>сторона / причина</th><th>сделок</th><th>в плюсе</th><th>средний R</th><th>USDT</th></tr></thead>
<tbody>{''.join(_group(closed, lambda r: ('лонг' if r['side'] == 'long' else 'шорт'))) + ''.join(_group(closed, lambda r: 'выход: ' + REASON.get(r['reason'], r['reason']) + (' · '+r.get('manual_reason','') if r.get('manual_reason') else ''))) or '<tr><td colspan=5 class=sub>сделок ещё нет</td></tr>'}</tbody></table></div>
</div>

<h2>Открытые позиции</h2><p class=sub>PnL учитывает частичные выходы, комиссию входа и оценку комиссии выхода; funding будет уточнён при закрытии. Цена обновляется на цикле бота, время обновления указано выше.</p>
<div class="wrap"><table>
<thead><tr><th></th><th>монета</th><th>формация</th><th>вход</th><th>стоп</th><th>цель</th>
<th>сейчас</th><th>R</th><th>PnL USDT ≈</th><th title="PnL с частичными выходами и комиссиями / первоначальный номинал позиции">PnL % ≈</th><th title="Текущий номинал оставшейся позиции / equity этого бота">Доля капитала %</th><th>Управление</th><th>тренд: на входе / сейчас</th><th>открыта</th><th>Выход по времени</th></tr></thead>
<tbody>{''.join(pos_rows) or '<tr><td colspan=13 class=sub>позиций нет</td></tr>'}</tbody>
</table></div>

<h2>Закрытые сделки</h2>
<div class="wrap"><table>
<thead><tr><th>время МСК</th><th></th><th>монета</th><th>формация</th><th>вход</th><th>выход</th>
<th>причина</th><th>R</th><th>USDT</th><th>PnL %</th><th>тренд</th></tr></thead>
<tbody>{''.join(tr_rows) or '<tr><td colspan=10 class=sub>сделок ещё нет</td></tr>'}</tbody>
</table></div>

{f'<h2>Почему сигналы не взяты · история попыток</h2><div class="box"><p class="sub">Счётчики за весь журнал этого профиля. Повторные проверки одного сигнала учитываются отдельно. Уникальные сигналы и свежий период — в <a href="/execution-stats.html" target="_top">статистике исполнения</a>.</p><ul>{skip_html}</ul></div>' if skip_html else ''}
{f'<h2>Журнал ошибок · последние 10 записей</h2><div class="box"><p class="sub">Записи сохраняются после восстановления. Наличие записи здесь не означает, что ошибка продолжается сейчас. Текущий статус сопровождения — в индикаторе контроля ботов в верхнем меню.</p><ul>{err_html}</ul></div>' if err_html else ''}
<p class="sub">Источник: журнал {e(ledger.journal_path)}. Код: src/trade/intraday.py, tools/trade/screener_bot.py.</p>
{f"<h2>Команды и разбор попыток</h2><ul>{controls_html}</ul>" if controls_html else ""}
{control_script}
</body></html>
"""


def to_json(ledger: Ledger, st: BotState, *, policy: str, trend: str, cfg: Config,
            now_ms: int, signals: int = 0, trend_err: str | None = None) -> dict:
    """Данные страницы для внешней вёрстки (страница скринера рисует по ним графики).

    По каждой сделке — монета, таймфрейм, формация и почему она найдена,
    уровни (вход, стоп начальный и текущий, первый тейк, цель), стакан на
    входе (спред, перекос, плотность в полосе 10 б.п.) и итог. Свечи здесь
    не лежат: их берут из базы скринера или с биржи по symbol/tf и времени.
    """
    j = ledger.journal()
    opens = {r["key"]: r for r in j if r["kind"] == "open"}
    current_trend=trend_display.load(now_ms)
    partials = defaultdict(list)
    for r in j:
        if r["kind"] == "partial":
            partials[r["key"]].append({"ts": r["ts"], "price": r["price"], "qty": r["qty"],
                                       "reason": r["reason"]})

    def levels(o: dict) -> dict:
        return {"entry": o.get("entry"), "stop": o.get("stop"), "target": o.get("target"),
                "tp1": o.get("tp1"), "signal_entry": o.get("signal_entry")}

    open_rows = []
    for p in st.pos():
        from src.trade.position_metrics import metrics
        o = opens.get(p.key, {})
        open_rows.append({
            "key": p.key, "symbol": p.symbol, "tf": p.tf, "side": p.side,
            "formation": p.kind, "title": p.title, "reasons": p.reasons, "book": p.book,
            "trend": p.trend, **trend_display.fields(p,current_trend),"opened_ms": p.opened_ms, "expires_ms": p.expires_ms,
            "market_context": p.market_context, "rules_at_entry": p.entry_rules, "qty": p.qty, "qty0": p.qty0 or p.qty, "levels": {**levels(o), "entry": p.entry,
                                                          "stop_now": p.stop,
                                                          "target": p.target or None},
            "mark": p.mark, "r_now": p.r_of(p.mark or p.entry),
            **metrics(p,st.equity()),
            "measured_r": p.measured_r, "measured_n": p.measured_n,
            "partials": partials.get(p.key, [])})
    closed_rows = []
    for r in [r for r in j if r["kind"] == "close"][-200:]:
        o = opens.get(r["key"], {})
        closed_rows.append({
            "key": r["key"], "symbol": r["symbol"], "tf": r["tf"], "side": r["side"],
            "formation": r.get("formation"), "title": r.get("title"),
            "reasons": o.get("reasons", []), "book": o.get("book", {}), "trend": r.get("trend"),
            "opened_ms": r.get("opened_ms"), "closed_ms": r.get("exit_ts") or r["ts"],
            "market_context": o.get("market_context", []), "rules_at_entry": o.get("rules", {}), "levels": dict(levels(o), stop_final=r.get("stop_final")), "exit": r["exit"], "exit_reason": r["reason"],
            "funding_pending":bool(r.get('funding_pending')),"funding":r.get('funding',0),
            "r_net": r["r_net"], "pnl_usdt": _net(r), "pnl_pct":100*_net(r)/(r['entry']*r['qty']) if r.get('qty') else None, "measured_r": r.get("measured_r"),
            "partials": partials.get(r["key"], [])})
    eq = st.equity()
    return {
        "bot": ledger.root.name, "mode": "paper", "market": "binance_futures",
        "updated_ms": now_ms, "policy": policy, "trend_filter": trend,
        "rules": settings(cfg), "halted": ledger.halted,
        "paused": (ledger.root / "PAUSE").exists() or st.pending_funding>0,"pending_funding":st.pending_funding,
        "equity": eq, "start_equity": st.start_equity, "peak": st.peak,
        "day_pnl": st.day_pnl, "signals_last_round": signals, "trend_error": trend_err,
        "equity_curve": [[r["ts"], r["equity"]] for r in j if r["kind"] == "equity"][-2000:],
        "open": open_rows, "closed": closed_rows,
    }


def write(ledger: Ledger, st: BotState, out: Path, **kw) -> Path:
    """bot.html и рядом bot.json (те же данные для внешней страницы)."""
    import json
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(build(ledger, st, **kw), encoding="utf-8")
    tmp.replace(out)
    js = out.with_suffix(".json")
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(to_json(ledger, st, **kw), ensure_ascii=False), encoding="utf-8")
    tmp.replace(js)
    return out
