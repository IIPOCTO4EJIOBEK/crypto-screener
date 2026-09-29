"""Страница отчёта по тренд-фильтру: числа и кривые капитала.

Читает JSON, который пишет `tools/trend_study.py --json`, и строит один
самодостаточный HTML-файл: таблицу режимов, кривые капитала в логарифмической
шкале и выводы (поправка на перебор, проверка вне выбора параметров, сравнение
с рынком). Кривые и полосы контроля рисуются здесь, а не берутся готовыми:
сторонних библиотек в проекте нет, а ломаная по точкам — это двадцать строк.

Запуск:

    .venv/bin/python -m tools.trend_report --json /tmp/trend-all.json \
        --out /tmp/trend.html
"""

from __future__ import annotations

import argparse
import html
import json
import math
import re
from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))
DAY_MS = 86_400_000

# Какие кривые рисовать и в каком порядке. Ключ — имя прогона в JSON.
CURVES = [
    ("тренд long-only", "#1a7f37", 2.6),
    ("рынок: всегда в позиции", "#57606a", 1.8),
    ("long-short", "#8250df", 1.4),
    ("контр-тренд (наоборот)", "#bf8700", 1.4),
    ("шорт по тому же правилу", "#cf222e", 1.4),
]
BAND = ("контроль 5%", "контроль 95%")


def _esc(x: object) -> str:
    return html.escape(str(x))


def _fmt_date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, MSK).strftime("%Y-%m-%d")


def _fmt_money(v: float) -> str:
    """Капитал: крупные значения — во сколько раз, мелкие — в процентах."""
    if v >= 10:
        return f"×{v:,.0f}".replace(",", " ")
    return f"{(v - 1) * 100:+.1f}%"


def chart(runs: dict, width: int = 1120, height: int = 460) -> str:
    """Кривые капитала в логарифмической шкале плюс полоса случайного входа.

    Логарифм, а не линейная шкала: размах от −90 % до ×80 не читается на
    линейной оси — все мелкие движения слиплись бы в ноль.
    """
    used = [(n, runs[n]["equity"]) for n, _, _ in CURVES
            if n in runs and runs[n]["equity"]]
    band_lo = runs.get(BAND[0], {}).get("equity")
    band_hi = runs.get(BAND[1], {}).get("equity")
    if not used:
        return "<p>кривых нет</p>"

    all_x = [t for _, c in used for t, _ in c]
    x0, x1 = min(all_x), max(all_x)
    vals = [v for _, c in used for _, v in c if v > 0]
    if band_lo and band_hi:
        vals += [v for _, v in band_lo if v > 0] + [v for _, v in band_hi if v > 0]
    lo, hi = min(vals), max(vals)
    lo, hi = lo * 0.85, hi * 1.15

    ml, mr, mt, mb = 74, 18, 16, 34
    pw, ph = width - ml - mr, height - mt - mb
    span = max(1, x1 - x0)

    def px(t: int) -> float:
        return ml + (t - x0) / span * pw

    def py(v: float) -> float:
        v = max(v, lo)
        return mt + (math.log(hi) - math.log(v)) / (math.log(hi) - math.log(lo)) * ph

    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" '
           f'role="img" aria-label="кривые капитала">']

    # сетка по горизонтали: круглые значения капитала
    ticks = []
    e = 0
    while 10 ** e <= hi:
        for m in (1, 2, 5):
            v = m * 10 ** e
            if lo <= v <= hi:
                ticks.append(v)
        e += 1
    for v in ticks:
        y = py(v)
        out.append(f'<line x1="{ml}" y1="{y:.1f}" x2="{width - mr}" y2="{y:.1f}" '
                   f'stroke="#d8dee4" stroke-width="1"/>')
        out.append(f'<text x="{ml - 8}" y="{y + 4:.1f}" text-anchor="end" '
                   f'font-size="11" fill="#57606a">{_esc(_fmt_money(v))}</text>')

    # годы по горизонтальной оси
    year = datetime.fromtimestamp(x0 / 1000, MSK).year + 1
    while True:
        ts = int(datetime(year, 1, 1, tzinfo=MSK).timestamp() * 1000)
        if ts > x1:
            break
        x = px(ts)
        out.append(f'<line x1="{x:.1f}" y1="{mt}" x2="{x:.1f}" y2="{mt + ph}" '
                   f'stroke="#eaeef2" stroke-width="1"/>')
        out.append(f'<text x="{x:.1f}" y="{height - 12}" text-anchor="middle" '
                   f'font-size="11" fill="#57606a">{year}</text>')
        year += 1

    # полоса случайного входа: между 5-м и 95-м процентом
    if band_lo and band_hi:
        n = min(len(band_lo), len(band_hi))
        up = " ".join(f"{px(band_hi[i][0]):.1f},{py(band_hi[i][1]):.1f}"
                      for i in range(n))
        dn = " ".join(f"{px(band_lo[i][0]):.1f},{py(band_lo[i][1]):.1f}"
                      for i in reversed(range(n)))
        out.append(f'<polygon points="{up} {dn}" fill="#8c959f" opacity="0.18"/>')

    for name, colour, w in CURVES:
        if name not in runs or not runs[name]["equity"]:
            continue
        pts = " ".join(f"{px(t):.1f},{py(v):.1f}" for t, v in runs[name]["equity"])
        out.append(f'<polyline points="{pts}" fill="none" stroke="{colour}" '
                   f'stroke-width="{w}" stroke-linejoin="round"/>')

    out.append("</svg>")

    legend = []
    for name, colour, _ in CURVES:
        if name in runs:
            legend.append(f'<span class="key"><i style="background:{colour}"></i>'
                          f'{_esc(name)}</span>')
    if band_lo and band_hi:
        legend.append('<span class="key"><i class="band"></i>'
                      'случайный вход той же плотности, полоса 5–95 %</span>')
    return "\n".join(out) + '<div class="legend">' + "".join(legend) + "</div>"


YEAR_RE = re.compile(r"^\d{4} ")


def _row(name: str, r: dict) -> str:
    p = r["params"]
    return (
        "<tr>"
        f'<td class="name">{_esc(name)}</td>'
        f'<td>{p["lookback"]}</td><td>{p["holding"]}</td>'
        f'<td>{r["n_intervals"]}</td>'
        f'<td>{r["exposure"]:.3f}</td><td>{r["in_market"]:.2f}</td>'
        f'<td>{r["turnover_year"]:.1f}</td>'
        f'<td class="num">{r["total"] * 100:+.1f}</td>'
        f'<td class="num">{(r["cagr"] or 0) * 100:+.1f}</td>'
        f'<td class="num"><b>{(r["sharpe"] or 0):.2f}</b></td>'
        f'<td class="num">{r["max_drawdown"] * 100:.1f}</td>'
        "</tr>")


HEAD = ('<table><thead><tr><th>режим</th><th>L</th><th>H</th><th>интерв</th>'
        '<th>экспоз</th><th>в рынке</th><th>оборот/год</th><th>всего %</th>'
        '<th>CAGR %</th><th>Sharpe</th><th>maxDD %</th></tr></thead><tbody>')


def modes_table(runs: dict) -> str:
    """Две таблицы: режимы и годовые срезы.

    Годовые срезы — те же прогоны, но с прогревом на истории до года; в общей
    таблице они мешали бы читать сравнение режимов между собой.
    """
    modes, years = [], []
    for name, r in runs.items():
        if not r["equity"] or r["n_intervals"] == 0:
            continue
        (years if YEAR_RE.match(name) else modes).append(_row(name, r))
    out = [HEAD + "".join(modes) + "</tbody></table>"]
    if years:
        out.append("<h2>По годам</h2>" + HEAD + "".join(years)
                   + "</tbody></table>")
    return "".join(out)


def walk_table(rows: list[dict]) -> str:
    if not rows:
        return ""
    body = []
    for r in rows:
        def sh(x):
            return f"{x:.2f}" if x is not None else "—"
        body.append(
            "<tr>"
            f'<td>{r["year"]}</td>'
            f'<td class="name">L{r["lookback"]} H{r["holding"]}</td>'
            f'<td class="num">{r["is_sharpe"]:.2f}</td>'
            f'<td class="num">{r["is_median"]:.2f}</td>'
            f'<td class="num">{sh(r["oos_sharpe"])}</td>'
            f'<td class="num">{sh(r.get("fixed_sharpe"))}</td>'
            f'<td class="num">{sh(r.get("market_sharpe"))}</td>'
            f'<td class="num">{r["oos_total"] * 100:+.1f}</td>'
            f'<td class="num">{r["oos_dd"] * 100:.1f}</td>'
            "</tr>")
    return (
        '<table><thead><tr><th>год</th><th>выбрано по IS</th><th>IS Sharpe</th>'
        '<th>медиана сетки</th><th>OOS выбранное</th><th>OOS правило 28/5</th>'
        '<th>OOS рынок</th><th>выбранное, всего %</th><th>maxDD %</th>'
        "</tr></thead><tbody>" + "".join(body) + "</tbody></table>")


def build(payload: dict) -> str:
    runs = payload.get("прогоны", {})
    cond = payload.get("условия", {})
    extra = payload.get("вердикты", {})

    syms = ", ".join(cond.get("символы", []))
    funding = "учитывается по фактическим начислениям" if cond.get("фандинг") \
        else "не учитывается (длинная сторона показана лучше, чем есть)"
    rt = cond.get("издержки за круг", 0.0) * 1e4

    blocks = []
    vs = extra.get("против рынка")
    if vs:
        word = "значимо" if vs["значимо"] else "не значимо"
        blocks.append(
            f'<div class="verdict"><b>Тренд против рынка.</b> Sharpe '
            f'{vs["sharpe_trend"]:.2f} против {vs["sharpe_market"]:.2f} у '
            f'равновзвешенного рынка. Тест Джобсона-Корки в форме Меммеля '
            f'учитывает, что оба Sharpe посчитаны на одном ряде и '
            f'скоррелированы: z = {vs["z"]:.2f}, p = {vs["p"]:.4f} — различие '
            f'<b>{word}</b> на уровне 5 %.</div>')

    g = extra.get("сетка")
    if g:
        blocks.append(
            f'<div class="verdict"><b>Перебор параметров.</b> Сетка '
            f'lookback × holding: сравнений {g["n_tested"]}, с положительным '
            f'средним {g["n_positive"]}. После поправки Бенджамини-Хохберга '
            f'(FDR {g["alpha"]:g}) значимо отличаются от нуля '
            f'<b>{g["n_significant"]}</b>. Deflated Sharpe лучшей строки: '
            f'{g["dsr"]:.4f}.</div>' if g.get("dsr") is not None else
            f'<div class="verdict"><b>Перебор параметров.</b> Сравнений '
            f'{g["n_tested"]}, значимых после поправки {g["n_significant"]}.'
            f'</div>')

    wf = extra.get("вне выбора")
    if wf:
        wins = sum(1 for r in wf if r.get("fixed_sharpe") is not None
                   and r.get("market_sharpe") is not None
                   and r["fixed_sharpe"] > r["market_sharpe"])
        blocks.append(
            f'<div class="verdict"><b>Проверка вне выбора параметров.</b> '
            f'Конфигурация выбирается по всей истории до года, считается на '
            f'самом году, который в выбор не входил; между ними разрыв '
            f'в удержание, чтобы последний интервал обучения не заглядывал '
            f'в проверочный год. Правило с параметрами из литературы обошло '
            f'рынок в {wins} из {len(wf)} лет.</div>')

    caveats = [
        "Исполнение: вход и выход по открытию следующего дня после сигнала. "
        "Сигнал считается по закрытию дня, и вход по этой цене был бы "
        "исполнением по цене, которой в этот момент уже нет.",
        f"Издержки: {cond.get('издержки за круг', 0) * 1e4:.1f} б.п. за круг "
        "по фактическому обороту портфеля (включая дрейф веса), не по числу "
        "сделок. Фандинг: " + funding + ".",
        "Проскальзывание взято константой (1 б.п. за сторону). Для монет с "
        "широким спредом это занижает издержки.",
        "Вселенная — восемь крупнейших перпетуалов по объёму на 2026 год, "
        "применённые к истории с 2020-го. Это отбор выживших: монеты, "
        "торговавшиеся тогда и не дожившие, в числах отсутствуют.",
        "Плеча нет: позиция — доля капитала, равные веса, свободные деньги "
        "стоят в кэше и не дают ничего. Просадка показана без сглаживания.",
        "Правило проверено на одном горизонте решений (шаг равен удержанию) "
        "и одном способе взвешивания (равные веса). Ни то, ни другое не "
        "подбиралось под данные, но и не проверялось на устойчивость.",
    ]

    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Тренд-фильтр: измерение</title>
<style>
 body {{ font: 14px/1.55 -apple-system, "Segoe UI", Roboto, sans-serif;
        margin: 0; padding: 28px 20px 60px; background: #fff; color: #1f2328; }}
 main {{ max-width: 1180px; margin: 0 auto; }}
 h1 {{ font-size: 22px; margin: 0 0 4px; }}
 h2 {{ font-size: 16px; margin: 30px 0 10px; }}
 .sub {{ color: #57606a; margin-bottom: 18px; }}
 table {{ border-collapse: collapse; width: 100%; margin: 6px 0 4px;
          font-size: 13px; }}
 th, td {{ padding: 5px 8px; border-bottom: 1px solid #eaeef2;
           text-align: right; white-space: nowrap; }}
 th {{ background: #f6f8fa; font-weight: 600; text-align: right; }}
 td.name, th:first-child {{ text-align: left; }}
 .num {{ font-variant-numeric: tabular-nums; }}
 .legend {{ display: flex; flex-wrap: wrap; gap: 6px 18px; margin: 8px 0 0;
            font-size: 12px; color: #57606a; }}
 .key {{ display: inline-flex; align-items: center; gap: 6px; }}
 .key i {{ width: 14px; height: 3px; border-radius: 2px; display: inline-block; }}
 .key i.band {{ background: #8c959f; opacity: .35; height: 10px; }}
 .verdict {{ background: #f6f8fa; border-left: 3px solid #0969da;
             padding: 9px 13px; margin: 9px 0; border-radius: 0 4px 4px 0; }}
 .caveat {{ background: #fff8c5; border-left: 3px solid #bf8700;
            padding: 10px 14px; border-radius: 0 4px 4px 0; }}
 .caveat ul {{ margin: 6px 0 0; padding-left: 20px; }}
 .caveat li {{ margin: 4px 0; }}
 .scroll {{ overflow-x: auto; }}
</style></head><body><main>
<h1>Тренд-фильтр на дневных барах: измерение портфелем</h1>
<div class="sub">{_esc(syms)} &middot; {_esc(cond.get('первый месяц'))} …
{_esc(cond.get('последний месяц'))} &middot; издержки {rt:.1f} б.п. за круг
&middot; страница собрана {datetime.now(MSK).strftime('%Y-%m-%d %H:%M')} МСК</div>

<h2>Режимы</h2>
<div class="scroll">{modes_table(runs)}</div>
<p class="sub">экспоз — доля монето-дней в позиции; в рынке — доля интервалов,
когда портфель не в кэше; оборот/год — сумма изменений весов за год.</p>

<h2>Кривые капитала</h2>
{chart(runs)}

<h2>Что показали проверки</h2>
{''.join(blocks) if blocks else '<p class="sub">проверки не запускались</p>'}

<h2>Проверка вне выбора параметров</h2>
<div class="scroll">{walk_table(wf or [])}</div>

<div class="caveat"><b>Чего эти числа не знают</b>
<ul>{''.join(f'<li>{c}</li>' for c in caveats)}</ul></div>
</main></body></html>
"""


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--json", required=True, help="файл с числами от trend_study")
    p.add_argument("--out", required=True, help="куда положить страницу")
    a = p.parse_args()
    with open(a.json, encoding="utf-8") as f:
        payload = json.load(f)
    page = build(payload)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"страница записана в {a.out}, {len(page)} байт")


if __name__ == "__main__":
    main()
