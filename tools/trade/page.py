"""Страница бумажного бота: описание, портфель, сделки, статистика, сверка с бэктестом.

Собирается из журнала бота (`journal.jsonl`) и его состояния — никаких
отдельных расчётов: что бот записал, то и показано. Пересобирается после
каждого шага бота (`tools/trade/run.py`) и вручную:

    python -m tools.trade.page                      # data/trade/paper-spot/bot.html
    python -m tools.trade.page --out /path/bot.html
    python -m tools.trade.page --all                # data/trade/bots.html: все боты, по вкладке

Общая страница `bots.html` собирает в одном месте всех ботов из
`profiles.json` (тренд-фильтр) и `screener-profiles.json` (боты по скринеру).
Вкладка тренд-бота рисуется здесь же, вкладка бота по скринеру показывает его
собственную `bot.html` во встроенном окне.
"""

from __future__ import annotations

import argparse
import html
import json
import os
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


def section(ledger: Ledger, *, mode: str = "paper", market: str = "spot",
          holding: int = 5, lookback: int = 28, side: str = "long",
          now_ms: int | None = None) -> str:
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
    funding = sum(r["total"] for r in rows if r["kind"] == "funding")
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
        ("фандинг уплачен", f"{funding:.2f} USDT" if market == "future" else "— (спот)"),
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
            pos_rows.append(f"<tr><td class={'long' if q > 0 else 'short'}>{e(s)} {'лонг' if q > 0 else 'шорт'}</td><td class=num>{q:.6g}</td>"
                            f"<td class=num>{'—' if px is None else f'{px:g}'}</td>"
                            f"<td class=num>{'—' if val is None else f'{abs(val):.2f}'}</td>"
                            f"<td class=num>{'—' if w is None else f'{abs(w) * 100:.1f} %'}</td></tr>")

    sig_rows = []
    if sig:
        shorts = set(sig.get("shorts") or [])
        for s, m in sorted(sig["momentum"].items(), key=lambda x: -x[1]):
            if s in sig["longs"]:
                cls, what = "long", "лонг"
            elif s in shorts:
                cls, what = "short", "шорт"
            else:
                cls, what = "dim", "вне портфеля"
            sig_rows.append(f"<tr class={cls}><td>{e(s)}</td><td class=num>{m * 100:+.2f} %</td>"
                            f"<td>{what}</td></tr>")
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
    fee_text = ("комиссия тейкера USDT-M 0.05 % и фандинг по фактическим начислениям"
                if market == "future" else "комиссия спота 0.1 %")
    side_text = ("Рост — лонг, падение — шорт, равными долями; сумма позиций по модулю "
                 "не больше капитала, плеча нет. В бэктесте такой вариант дал Sharpe 1.22 "
                 "и просадку −52 % на восьмёрке, но шорт сам по себе там был убыточен "
                 "(−92 %): весь плюс — от лонгов, шорт снижает просадку на падениях."
                 if side == "longshort" else
                 "Монеты с ростом — в портфель равными долями, остальные — нет, деньги "
                 "ждут в USDT. Шорта и плеча нет.")
    rebalance_text = ("каждый день" if holding == 1 else f"раз в {holding} дней")
    mode_name = {"paper": "бумажный", "testnet": "тестовая сеть", "live": "ЖИВОЙ СЧЁТ"}.get(mode, mode)

    return f"""<h1>Торговый бот: тренд-фильтр {lookback}/{holding} · {'фьючерсы' if market == 'future' else 'спот'}{' · лонг и шорт' if side == 'longshort' else ''}</h1>
<div class="meta">режим: {e(mode_name)} · рынок: {e(market)} · обновлено {t(now_ms) if now_ms else '—'} МСК</div>
{f'<div class="box warn">ОСТАНОВЛЕН: {e(halted)}</div>' if halted else ''}

<h2>Что делает бот</h2>
<div class="box">
Каждый день в 03:05 МСК, после закрытия дневной свечи, бот берёт дневные свечи
восьми монет (BTC, ETH, SOL, XRP, DOGE, ADA, LINK, AVAX) и считает моментум:
насколько цена изменилась за {lookback} дней. {side_text}
Портфель пересобирается {rebalance_text}.{' Рынок — бессрочные фьючерсы USDT-M Binance, плечо 1, только лонг: продажа идёт только на закрытие позиции.' if market == 'future' else ''} Если капитал упадёт ниже пика
больше чем на 35 %, бот продаёт всё и останавливается до ручного решения.
<ul>
<li>Режим «бумажный»: заявки на биржу не уходят. Цена каждой сделки считается
по живому стакану Binance — так, как прошла бы настоящая рыночная заявка, — плюс
{fee_text}.</li>
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
<li><b>Издержки.</b> В бэктесте — 0.05 % + 1 б.п. за сторону. Здесь — {fee_text} и проскальзывание, замеренное на каждой сделке (в статистике слева).</li>
<li><b>Разница с рынком.</b> Бэктест обещает преимущество над рынком в основном на
падениях: правило уходит в кэш. На росте оно идёт почти вровень с рынком.</li>
<li><b>Просадка.</b> Стоп 35 % в бэктесте не проверялся; исторически правило
проходило −76…−79 %, то есть стоп сработал бы.</li>
<li>В бэктесте ежедневный ребаланс (L28 H1) дал тот же Sharpe, что и раз в 5 дней
(1.22 против 1.24 на восьмёрке), но оборот втрое больше: 99 против 36 капиталов в год.</li>
<li>За месяц бот принимает около {30 // holding} решений — выводы имеют смысл через несколько
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
"""


STYLE = """
  :root { --bg:#12141a; --fg:#e6e8ee; --dim:#8b93a7; --line:#252a36;
          --good:#3ddc97; --bad:#ff6b6b; }
  body { margin:0; padding:24px; background:var(--bg); color:var(--fg);
         font:14px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace; max-width:1100px; }
  h1 { font-size:18px; margin:0 0 4px; }
  h2 { font-size:15px; margin:26px 0 8px; color:var(--fg); }
  .meta, .sub, .dim td { color:var(--dim); }
  .sub { font-size:12px; }
  .box { padding:10px 12px; border-left:3px solid var(--line); background:#161922; }
  .warn { border-left-color:var(--bad); }
  table { border-collapse:collapse; width:100%; }
  th,td { text-align:left; padding:6px 9px; border-bottom:1px solid var(--line); }
  th { color:var(--dim); font-weight:400; }
  td.num { text-align:right; white-space:nowrap; }
  .long, tr.long td:first-child { color:var(--good); } .short { color:var(--bad); }
  .grid { display:grid; grid-template-columns:1fr 1fr; gap:24px; }
  @media (max-width:800px) { .grid { grid-template-columns:1fr; } body { padding:16px; } }
  .chart { width:100%; height:120px; background:#161922; }
  .wrap { overflow-x:auto; }
  ul { margin:6px 0 0; padding-left:18px; }
  .tabs { display:flex; flex-wrap:wrap; gap:6px; margin:0 0 18px; border-bottom:1px solid var(--line); }
  .tabs button { font:inherit; color:var(--dim); background:none; border:0;
                 border-bottom:2px solid transparent; padding:8px 12px; cursor:pointer; text-align:left; }
  .tabs button[aria-selected=true] { color:var(--fg); border-bottom-color:var(--good); }
  .tabs .sub { display:block; }
  .tab[hidden] { display:none; }
  @media (max-width:800px) { .tabs { flex-wrap:nowrap; overflow-x:auto; }
                             .tabs button { flex:0 0 auto; } }
  iframe.bot { width:100%; height:85vh; border:1px solid var(--line); background:var(--bg); }
  a { color:var(--good); }
"""


def shell(title: str, inner: str) -> str:
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<style>{STYLE}</style></head><body>
{inner}
</body></html>
"""


def build(ledger: Ledger, **kw) -> str:
    return shell("Торговый бот", section(ledger, **kw))


def ledger_root(data: Path, mode: str, market: str, side: str) -> Path:
    return Path(data) / (f"{mode}-{market}" + ("" if side == "long" else f"-{side}"))


def profile_kw(args: list[str]) -> dict:
    """Параметры страницы из аргументов профиля tools.trade.run (остальные игнорируются)."""
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--mode", default="paper")
    ap.add_argument("--market", default="spot")
    ap.add_argument("--side", default="long")
    ap.add_argument("--holding", type=int, default=5)
    ap.add_argument("--lookback", type=int, default=28)
    a, _ = ap.parse_known_args(args)
    return dict(mode=a.mode, market=a.market, side=a.side, holding=a.holding, lookback=a.lookback)


def tab_label(ledger: Ledger, name: str) -> tuple[str, str]:
    """Подпись вкладки: имя бота и под ним капитал с доходностью от старта."""
    eq = [r for r in ledger.journal() if r["kind"] == "equity"]
    if not eq:
        return name, "ещё не запускался"
    first, last = eq[0]["equity"], eq[-1]["equity"]
    return name, f"{last:.2f} USDT · {pct(last / first - 1.0 if first else None)}"


def trend_name(*, market: str, side: str, holding: int, lookback: int, **_) -> str:
    name = ("Фьючерсы" if market == "future" else "Спот") + f" {lookback}/{holding}"
    return name + (" · лонг+шорт" if side == "longshort" else "")


def screener_root(data: Path, args: list[str]) -> Path:
    """Каталог бота по скринеру — по тем же правилам, что у tools.trade.screener_bot."""
    from tools.trade.screener_bot import data_dir
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--policy", default="all")
    ap.add_argument("--trend", default="off")
    ap.add_argument("--name")
    ap.add_argument("--data")
    a, _ = ap.parse_known_args(args)
    return data_dir(a, base=data)


def screener_tab(data: Path, root: Path) -> str:
    """Своя страница бота по скринеру во встроенном окне (путь — от bots.html)."""
    page = root / "bot.html"
    if not page.exists():
        return ('<div class="box sub">бот ещё не запускался — страница появится '
                'после первого круга</div>')
    try:
        src = Path(os.path.relpath(page, data)).as_posix()
    except ValueError:                    # другой диск на Windows
        src = page.resolve().as_uri()
    return (f'<iframe class="bot" src="{e(src)}" loading="lazy" title="{e(root.name)}"></iframe>'
            f'<div class="sub"><a href="{e(src)}">открыть отдельно</a></div>')


TABS_JS = """<script>
(function () {
  var btns = document.querySelectorAll('.tabs button');
  function show(id) {
    var found = false;
    btns.forEach(function (b) { found = found || b.dataset.tab === id; });
    if (!found) return false;
    btns.forEach(function (b) { var on = b.dataset.tab === id;
      b.setAttribute('aria-selected', on); document.getElementById(b.dataset.tab).hidden = !on; });
    return true;
  }
  btns.forEach(function (b) { b.addEventListener('click', function () {
    show(b.dataset.tab); history.replaceState(null, '', '#' + b.dataset.tab); }); });
  if (location.hash) show(decodeURIComponent(location.hash.slice(1)));
})();
</script>"""


def build_all(data: Path, profiles: list[list[str]],
              screener: list[list[str]] | None = None, *, now_ms: int | None = None) -> str:
    """Одна страница со всеми ботами из профилей, по вкладке на бота."""
    data = Path(data)
    items = []                            # (id, имя, подпись, содержимое)
    for args in profiles:
        kw = profile_kw(args)
        ledger = Ledger(ledger_root(data, kw["mode"], kw["market"], kw["side"]))
        name, sub = tab_label(ledger, trend_name(**kw))
        items.append((ledger.root.name, name, sub, section(ledger, **kw)))
    for args in screener or []:
        root = screener_root(data, args)
        name = "По скринеру · " + root.name.removeprefix("screener-")
        # Ledger создаёт каталог — для бота, который ещё не запускался, не трогаем диск
        name, sub = tab_label(Ledger(root), name) if root.exists() else (name, "ещё не запускался")
        items.append((root.name, name, sub, screener_tab(data, root)))
    if not items:
        return shell("Торговые боты", '<h1>Торговые боты</h1><div class="box sub">'
                     'профилей нет: data/trade/profiles.json и screener-profiles.json</div>')
    buttons = "".join(
        f'<button role="tab" data-tab="{e(tid)}" aria-selected="{"true" if i == 0 else "false"}">'
        f'{e(name)}<span class="sub">{e(sub)}</span></button>'
        for i, (tid, name, sub, _) in enumerate(items))
    tabs = "".join(
        f'<div class="tab" id="{e(tid)}"{"" if i == 0 else " hidden"} role="tabpanel">{body}</div>'
        for i, (tid, _, _, body) in enumerate(items))
    stamp = f'<div class="meta">ботов {len(items)} · собрано {t(now_ms)} МСК</div>' if now_ms else ""
    return shell("Торговые боты", f'<h1>Торговые боты</h1>{stamp}'
                 f'<div class="tabs" role="tablist">{buttons}</div>{tabs}{TABS_JS}')


def load_profiles(data: Path, name: str = "profiles.json", default=([],)) -> list[list[str]]:
    path = Path(data) / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else list(default)


def to_json(ledger: Ledger, *, mode: str = "paper", market: str = "spot",
            holding: int = 5, lookback: int = 28, side: str = "long",
            now_ms: int | None = None) -> dict:
    """Данные страницы тренд-бота для внешней вёрстки (графики по монетам).

    По каждой монете портфеля — сторона, количество, цена и моментум, по
    которому она выбрана; плюс все сделки (fills) и кривая капитала.
    Свечи для графиков берутся по symbol с биржи или из базы скринера.
    """
    j = ledger.journal()
    state = json.loads(ledger.state_path.read_text(encoding="utf-8")) if ledger.state_path.exists() else {}
    sig = next((r for r in reversed(j) if r["kind"] == "signal"), None)
    eqs = [r for r in j if r["kind"] == "equity"]
    prices = (eqs[-1].get("prices") or {}) if eqs else {}
    mom = (sig or {}).get("momentum") or {}
    pos = [{"symbol": s, "side": "long" if q > 0 else "short", "qty": abs(q),
            "price": prices.get(s), "momentum": mom.get(s),
            "why": (f"цена за {lookback} дн. {'выросла' if (mom.get(s) or 0) > 0 else 'упала'} "
                    f"на {abs(mom.get(s) or 0):.1%}") if s in mom else None}
           for s, q in (state.get("positions") or {}).items() if q]
    return {
        "bot": ledger.root.name, "mode": mode, "market": market, "side": side,
        "rule": f"моментум {lookback} дн., ребаланс раз в {holding} дн.",
        "updated_ms": now_ms, "halted": ledger.halted,
        "equity": eqs[-1]["equity"] if eqs else state.get("cash"),
        "start_equity": state.get("start_equity"), "peak": state.get("peak"),
        "positions": pos,
        "signal": sig and {"day_ts": sig.get("day_ts"), "momentum": mom,
                           "longs": sig.get("longs"), "shorts": sig.get("shorts")},
        "fills": [{k: r.get(k) for k in ("ts", "symbol", "side", "qty", "price", "fee",
                                          "slippage_bp", "reduce")}
                  for r in j if r["kind"] == "fill"][-500:],
        "equity_curve": [[r["ts"], r["equity"]] for r in eqs][-2000:],
    }


def _write(out: Path, text: str) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(out)
    return out


def write(ledger: Ledger, out: Path, **kw) -> Path:
    """bot.html и рядом bot.json (те же данные для внешней страницы)."""
    out = _write(out, build(ledger, **kw))
    _write(out.with_suffix(".json"), json.dumps(to_json(ledger, **kw), ensure_ascii=False))
    return out


def write_all(data: Path, out: Path | None = None, now_ms: int | None = None) -> Path:
    """data/trade/bots.html: тренд-боты из profiles.json и боты по скринеру."""
    data = Path(data)
    text = build_all(data, load_profiles(data),
                     load_profiles(data, "screener-profiles.json", default=()),
                     now_ms=now_ms or int(datetime.now(timezone.utc).timestamp() * 1000))
    return _write(out or data / "bots.html", text)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="paper")
    ap.add_argument("--market", default="spot")
    ap.add_argument("--data", default=str(ROOT / "data" / "trade"))
    ap.add_argument("--side", default="long")
    ap.add_argument("--holding", type=int, default=5)
    ap.add_argument("--out")
    ap.add_argument("--all", action="store_true",
                    help="одна страница со всеми ботами из profiles.json (data/trade/bots.html)")
    a = ap.parse_args(argv)
    if a.all:
        print(write_all(Path(a.data), Path(a.out) if a.out else None))
        return 0
    ledger = Ledger(ledger_root(Path(a.data), a.mode, a.market, a.side))
    out = Path(a.out) if a.out else ledger.root / "bot.html"
    print(write(ledger, out, mode=a.mode, market=a.market, side=a.side, holding=a.holding))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
