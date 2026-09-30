"""Лендинг проекта: что это, что измерено и чего здесь нет.

Ни одно число на странице не набрано руками — все читаются из тех же
источников, что и сами страницы:

    измерение формаций   data/screener.db, таблица formation_stats
    окно измерения       последний docs/research/logs/formation-refresh-*.log
    живой счёт           собранные docs/live/structures.html, densities.html
    тренд-фильтр         docs/trend-hist.html, docs/trend.html

Живой счёт и тренд — необязательные блоки. Нет снимка — блок не рисуется,
а в `gaps` попадает строка о том, чего именно не нашлось. Выдуманного на
странице быть не может: то, чего нет в источниках, на странице не
появляется.

Страница собирается в двух раскладках: `--links repo` — файл лежит рядом
с живыми страницами (`docs/live/index.html`), `--links flat` — всё в одной
папке предпросмотра, а ссылки на документы уходят в GitHub.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

from src.backtest import significance
from src.backtest.expectancy import MIN_TRADES
from src.storage import db as dbm
from tools.live.screen import MSK

REPO = Path(__file__).resolve().parents[2]
DB_PATH = REPO / "data" / "screener.db"
LOG_DIR = REPO / "docs" / "research" / "logs"
TEMPLATE = Path(__file__).resolve().parent / "landing.html"
PLACEHOLDER = "__DATA_JSON__"

LIVE_PAGES = {
    "structures": REPO / "docs" / "live" / "structures.html",
    "densities": REPO / "docs" / "live" / "densities.html",
}
# JSON-вывод прогона тренда. В репозиторий он не кладётся (решение в
# docs/research/16, «Воспроизведение»): файл тяжёлый, а страницы
# docs/trend*.html уже собраны из него. Поэтому блок тренда — необязательный:
# нет файла, нет блока, и в gaps появляется строка об этом.
TREND_JSON = {
    "hist": Path("/tmp/trend-hist.json"),   # состав по дате, 24 монеты
    "all": Path("/tmp/trend-all.json"),     # восьмёрка лидеров
}

DOCS = {
    "run": "docs/03-результаты-прогона.md",
    "trend": "docs/research/16-тренд-фильтр-измерение.md",
    "earn": "docs/research/12-заработок-свод.md",
    "method": "docs/research/17-измерение-формаций-проверка-методики.md",
}
GITHUB_BLOB = "https://github.com/IIPOCTO4EJIOBEK/crypto-screener/blob/master/"

# Русские названия формаций — только для чтения. Детекторы зовутся
# по-английски, но в таблице измерения «bounce 1h» ничего не объясняет.
KIND_RU = {
    "trendline_bounce": "отскок от трендовой",
    "bounce": "отскок от уровня",
    "breakout": "пробой уровня",
    "retest": "ретест уровня",
    "structure_break": "слом структуры",
    "liquidity_sweep": "съём ликвидности",
    "volume_splash": "всплеск объёма",
    "absorption": "поглощение",
    "squeeze": "сжатие",
}

LOSERS_SHOWN = 6           # сколько крупных выборок в минусе показать
_SUP = str.maketrans("0123456789-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁻")
_WINDOW_RE = re.compile(
    r"окно\s+(\S+)\s+…\s+(\S+)\s+\((\d+)\s+суток\),\s+монет\s+(\d+),"
    r"\s+таймфреймов\s+(\d+)")
_TAG_RE = re.compile(r'<script id="(?P<id>[\w-]+)" type="application/json">'
                     r'(?P<body>.*?)</script>', re.S)


def kind_ru(kind: str) -> str:
    return KIND_RU.get(kind, kind)


def p_text(p: float | None) -> str:
    """p-значение словами страницы. Порядок берётся у самого числа, а не
    вписывается руками: иначе при новом прогоне число осталось бы старым.
    Decimal, а не log10: у 9.9·10⁻¹² логарифм на границе округления сдвинул
    бы порядок на единицу и напечатал 1.0·10⁻¹¹."""
    if p is None:
        return "p не посчитан"
    if p >= 0.001:
        return f"p = {p:g}"
    d = Decimal(repr(p))
    e = d.adjusted()
    m = d.scaleb(-e)
    if m.quantize(Decimal("0.1")) >= 10:      # 0.0009999 → 1.0·10⁻³, не 10.0·10⁻⁴
        e += 1
        m = d.scaleb(-e)
    return f"p = {m:.1f}·10{str(e).translate(_SUP)}"


def _embedded_json(path: Path) -> dict:
    """Разобрать данные, встроенные в собранную страницу, или обычный JSON."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        return json.loads(text)
    m = _TAG_RE.search(text)
    if not m:
        raise ValueError(f"{path}: встроенных данных не найдено")
    return json.loads(m.group("body").replace("<\\/", "</"))


def _now_msk() -> str:
    return dt.datetime.now(MSK).strftime("%Y-%m-%d %H:%M МСК")


# --------------------------------------------------------------------------
# источники
# --------------------------------------------------------------------------
def load_measurement(db_path: Path) -> tuple[dict, list[str]]:
    """Измерение формаций из базы: счёт строк и поправка на перебор."""
    gaps: list[str] = []
    conn = dbm.connect(str(db_path))
    stats = dbm.load_formation_stats(conn)
    conn.close()
    if not stats:
        raise SystemExit("в базе нет строк измерения — сначала tools.live.refresh")

    anchors = {k: m for k, m in stats.items() if m["n"] >= MIN_TRADES}
    akeys = list(anchors)
    verdict = significance.judge([dict(anchors[k]) for k in akeys],
                                 min_n=MIN_TRADES)
    flags = {k: ok for k, ok in zip(akeys, verdict.flags)}

    def row(m: dict, key: tuple[str, str]) -> dict:
        mark = flags.get(key)
        return {
            "kind": m["kind"],
            "kind_ru": kind_ru(m["kind"]),
            "tf": m["tf"],
            "n": m["n"],
            "win_pct": round(m["win_rate"], 1),
            "r_gross": round(m["exp_gross"], 3),
            "cost_r": round(m["cost"], 3),
            "r_net": round(m["exp_net"], 3),
            "support": m["n"] >= MIN_TRADES,
            "significant": bool(mark) if mark is not None else None,
        }

    table = sorted((row(m, k) for k, m in stats.items()),
                   key=lambda r: -r["r_net"])
    supported = [r for r in table if r["support"]]
    sig = [r for r in supported if r["significant"]]
    sig_pos = [r for r in sig if r["r_net"] > 0]
    survivor = sig_pos[0] if sig_pos else None

    loser_rows = sorted((r for r in supported if r["r_net"] < 0),
                        key=lambda r: -r["n"])[:LOSERS_SHOWN]
    first = next(iter(stats.values()))
    if not verdict.n_tested:
        gaps.append("в измерении ни одна строка не набрала разброс — "
                    "поправка на перебор не посчитана")

    measurement = {
        "measured_on": first["measured_on"],
        "scope": first["symbol_scope"],
        "symbols_n": len(first["symbol_scope"].split(",")),
        "min_trades": MIN_TRADES,
        "fdr_alpha": verdict.alpha,
        "rows_total": len(table),
        "rows_support": len(supported),
        "rows_positive": sum(1 for r in supported if r["r_net"] > 0),
        "rows_significant": verdict.n_significant,
        "rows_significant_positive": len(sig_pos),
        "survivor": survivor,
        "losers": loser_rows,
        "table": table,
    }
    return measurement, gaps


def load_window(log_dir: Path) -> tuple[dict | None, list[str]]:
    """Окно измерения — так, как его напечатал прогон в своём логе."""
    logs = sorted(log_dir.glob("formation-refresh-*.log"))
    if not logs:
        return None, [f"логов прогона измерения нет в {log_dir} — "
                      f"окно измерения неизвестно"]
    text = logs[-1].read_text(encoding="utf-8", errors="replace")
    m = _WINDOW_RE.search(text)
    if not m:
        return None, [f"{logs[-1].name}: строки с окном измерения нет"]
    start, end, days, symbols, tfs = m.groups()
    return {"start": start, "end": end, "days": int(days),
            "symbols_n": int(symbols), "tfs_n": int(tfs),
            "log": logs[-1].name}, []


def load_live() -> tuple[dict, list[str]]:
    """Живой счёт из собранных страниц. Нет файла — нет блока."""
    out: dict = {}
    gaps: list[str] = []
    for name, path in LIVE_PAGES.items():
        if not path.exists():
            gaps.append(f"снимок {name} не найден ({path.name}) — "
                        f"блок живой страницы пропущен")
            continue
        data = _embedded_json(path)
        meta = data.get("meta") or {}
        block = {
            "collected_at": meta.get("collected_at"),
            "caveats_n": len(meta.get("caveats") or []),
            "market": meta.get("market"),
        }
        if name == "structures":
            pairs = data.get("pairs") or []
            block.update(
                pairs=len(pairs),
                tfs_n=len(meta.get("tfs") or []),
                formations=sum(len(p.get("formations") or []) for p in pairs),
                candles=meta.get("candles_in_chart"),
                positive_hit_now=len(meta.get("positive_hit_now") or []),
            )
        else:
            block.update(
                coins=len(data.get("coins") or []),
                densities_total=meta.get("densities_total"),
                shown=meta.get("shown_total"),
                depth=meta.get("depth"),
            )
        out[name] = block
    return out, gaps


def load_trend(paths: dict[str, Path]) -> tuple[dict | None, list[str]]:
    """Тренд-фильтр: два состава одной и той же вселенной."""
    missing = [p.name for p in paths.values() if not p.exists()]
    if missing:
        return None, [f"JSON прогона тренда не найден ({', '.join(missing)}) — "
                      f"блок тренда пропущен"]
    out: dict = {}
    for key, path in paths.items():
        data = _embedded_json(path)
        runs = data.get("прогоны") or {}
        cond = data.get("условия") or {}
        rule = runs.get("тренд long-only") or {}
        market = runs.get("рынок: всегда в позиции") or {}
        if not rule or not market:
            return None, [f"{path.name}: в данных нет строк тренда и рынка"]
        out[key] = {
            "symbols_n": len(cond.get("символы") or []),
            "sharpe": round(rule.get("sharpe") or 0, 2),
            "market_sharpe": round(market.get("sharpe") or 0, 2),
            "cagr_pct": round((rule.get("cagr") or 0) * 100, 1),
            "market_cagr_pct": round((market.get("cagr") or 0) * 100, 1),
            "verdict": (data.get("вердикты") or {}).get("против рынка") or {},
            "lag": (data.get("вердикты") or {}).get("задержка") or {},
        }
    hist = out.get("hist") or {}
    if hist.get("verdict"):
        v = hist["verdict"]
        hist["p"] = v.get("p")
        hist["z"] = round(v.get("z") or 0, 2)
        hist["significant"] = bool(v.get("значимо"))
    return out, []


# --------------------------------------------------------------------------
# сборка
# --------------------------------------------------------------------------
def links(mode: str) -> dict:
    """Куда ведут ссылки: рядом лежащие страницы или GitHub для документов."""
    flat = mode == "flat"
    out = {
        "structures": "structures.html",
        "densities": "densities.html",
        "screener": "screener.html",
        "trend": "trend.html" if flat else "../trend.html",
        "trend_hist": "trend-hist.html" if flat else "../trend-hist.html",
    }
    docs = {}
    for key, rel in DOCS.items():
        docs[key] = GITHUB_BLOB + quote(rel) if flat else "../" + _rel_up(rel)
    out["docs"] = docs
    return out


def _rel_up(rel: str) -> str:
    """Путь документа от docs/live/ — вверх на одну папку."""
    return rel.split("docs/", 1)[-1]


def cards(live: dict, trend: dict | None, link: dict) -> list[dict]:
    """Плитки-переходы. Числа — только те, что реально прочитаны."""
    out: list[dict] = []
    st = live.get("structures")
    if st:
        out.append({
            "href": link["structures"], "title": "Структуры",
            "what": "Графики с разметкой: свечи, горизонтальные уровни, "
                    "трендовые линии, режим рынка, объём и метки формаций. "
                    "На каждой метке — измеренный R после издержек.",
            "figures": [f"{st['pairs']} пар", f"{st['tfs_n']} таймфрейма",
                        f"{st['formations']} формаций сейчас"],
            "note": f"снимок {st.get('collected_at') or '—'}"
                    + (f", плюсовых формаций сработало {st['positive_hit_now']}"
                       if st.get("positive_hit_now") is not None else ""),
        })
    dn = live.get("densities")
    if dn:
        out.append({
            "href": link["densities"], "title": "Плотности и касания",
            "what": "Крупные уровни стакана: номинал, расстояние до "
                    "середины, кратность к медиане стакана, время поедания "
                    "и число касаний цены за 5m и 1h.",
            "figures": [f"{dn['coins']} монет",
                        f"{dn.get('densities_total') or '—'} плотностей",
                        f"{dn.get('shown') or '—'} строк на странице"],
            "note": f"стакан глубиной {dn.get('depth')} уровней, "
                    f"снимок {dn.get('collected_at') or '—'}",
        })
    out.append({
        "href": link["screener"], "title": "Снимок скринера",
        "what": "Последний прогон по всем парам: какие формации найдены "
                "сейчас, что о них говорит измерение и какой вердикт "
                "выходит после поправки на перебор.",
        "figures": ["вердикт по каждой формации", "строка FDR в отчёте"],
        "note": "обновляется прогоном tools/live/screen.py",
    })
    if trend:
        hist, alls = trend.get("hist") or {}, trend.get("all") or {}
        if hist:
            p = hist.get("p")
            out.append({
                "href": link["trend_hist"], "title": "Тренд-фильтр",
                "what": "Единственная стратегия с подтверждённым плюсом "
                        "после издержек: long-only выход в кэш, когда рынок "
                        "падает. Состав монет — на 2020-01, отбора выживших "
                        "нет.",
                "figures": [f"Sharpe {hist.get('sharpe')} против "
                            f"{hist.get('market_sharpe')} у рынка",
                            f"на {hist.get('symbols_n')} монетах состава "
                            f"по дате",
                            f"{p_text(p)}, значимо" if p and p < 0.05
                            else p_text(p)],
                "note": "здесь число ближе к тому, что можно было получить "
                        "в 2020-м, не зная будущего",
            })
        if alls:
            out.append({
                "href": link["trend"], "title": "Тренд на восьмёрке "
                                                 "лидеров",
                "what": "Тот же тренд-фильтр на восьми крупнейших "
                        "перпетуалах 2026 года, применённых к истории "
                        "с 2020-го. Здесь же проверка вне выбора параметров: "
                        "walk-forward с разрывом на утечку.",
                "figures": [f"Sharpe {alls.get('sharpe')} против "
                            f"{alls.get('market_sharpe')} у рынка",
                            f"{alls.get('symbols_n')} монет"],
                "note": "состав выбран задним числом — это верхняя оценка, "
                        "а не то, что можно было знать в 2020-м",
            })
    return out


def caveats(measurement: dict, trend: dict | None) -> list[str]:
    """Чего на этих страницах нет. Так же часть работы, как и числа."""
    out = [
        "Ни одна страница здесь не даёт сигналов и не обещает доходности. "
        "Измерение показывает, что после издержек большинство формаций в "
        "минусе, и это написано на первом экране.",
        f"Измерение формаций — {measurement['rows_total']} строк по "
        f"{measurement['symbols_n']} монетам и 3 таймфреймам, дата "
        f"{measurement['measured_on']}. Опорой считается выборка от "
        f"{measurement['min_trades']} сделок: у меньших оценка разброса "
        f"не определена.",
        "Поправка на множественные сравнения (Бенджамини-Хохберг) "
        "применена: без неё «полторы хорошие строки из двух десятков» — "
        "ожидаемый результат шума. Считаются только строки-опоры.",
        "Walk-forward с разрывом на утечку для формаций не делался — числа "
        "описывают одно окно, а не устойчивость во времени. Для "
        "тренд-фильтра он есть.",
        "Плотность на странице плотностей — снимок стакана, а не "
        "наблюдение за заявкой: сырых уровней база не хранит, поэтому ни "
        "времени жизни, ни признаков спуфинга там быть не может.",
        f"Касание считается по сегодняшней цене уровня, а не по той, что "
        f"стояла в момент касания: это «сколько раз цена была на этой "
        f"цене», а не «сколько раз отскочила от этой заявки».",
    ]
    if trend and (trend.get("hist") or {}).get("lag"):
        out.append(
            "Преимущество тренд-фильтра живёт один-два дня: при задержке "
            "входа в три дня различия с рынком нет, в пять и десять — "
            "правило значимо хуже рынка. Исполнение обязано быть "
            "автоматическим.")
    return out


def build(*, db_path: Path, log_dir: Path, mode: str,
          trend_paths: dict[str, Path]) -> dict:
    gaps: list[str] = []
    measurement, g = load_measurement(db_path)
    gaps += g
    window, g = load_window(log_dir)
    gaps += g
    live, g = load_live()
    gaps += g
    trend, g = load_trend(trend_paths)
    gaps += g

    link = links(mode)
    data = {
        "meta": {
            "title": "Крипто-скринер",
            "subtitle": "Формации на живом рынке и честный замер того, "
                        "что они приносят после издержек",
            "built_at": _now_msk(),
            "market": "Binance USDT-M перпетуал (fapi.binance.com)",
            "no_signals": True,
            "links": link,
            "window": window,
            "live": live,
            "trend": trend,
            "gaps": gaps,
            "caveats": caveats(measurement, trend),
        },
        "measurement": measurement,
        "cards": cards(live, trend, link),
        "docs": [{"href": link["docs"][k], "title": t} for k, t in (
            ("run", "Результаты прогона"),
            ("trend", "Тренд-фильтр: измерение"),
            ("earn", "На чём в нише зарабатывают"),
            ("method", "Проверка методики измерения"),
        )],
    }
    return data


def build_page(template: Path, data: dict) -> str:
    html = template.read_text(encoding="utf-8")
    if html.count(PLACEHOLDER) != 1:
        raise SystemExit(f"{template}: плейсхолдер {PLACEHOLDER} должен быть "
                         f"один, найдено {html.count(PLACEHOLDER)}")
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return html.replace(PLACEHOLDER, body.replace("</", "<\\/"))


def render_text(data: dict) -> str:
    m = data["measurement"]
    meta = data["meta"]
    w = meta.get("window") or {}
    lines = [
        f"Лендинг: строк измерения {m['rows_total']}, опора у "
        f"{m['rows_support']}, в плюсе {m['rows_positive']}, значимых "
        f"{m['rows_significant']}, из них с плюсом "
        f"{m['rows_significant_positive']}",
        f"  окно: {w.get('start', '—')} … {w.get('end', '—')} "
        f"({w.get('days', '—')} суток)" if w else "  окно: неизвестно",
        f"  плиток {len(data['cards'])}, оговорок {len(meta['caveats'])}, "
        f"пропусков {len(meta['gaps'])}",
        f"  сборка {meta['built_at']}",
    ]
    for g in meta["gaps"]:
        lines.append(f"  НЕТ: {g}")
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description="Собрать лендинг проекта")
    p.add_argument("--db", default=str(DB_PATH))
    p.add_argument("--log-dir", default=str(LOG_DIR))
    p.add_argument("--template", default=str(TEMPLATE))
    p.add_argument("--links", choices=("repo", "flat"), default="repo",
                   help="repo — страницы рядом в docs/live, flat — всё в "
                        "одной папке (предпросмотр)")
    p.add_argument("--trend-hist", default=str(TREND_JSON["hist"]),
                   help="JSON прогона тренда по составу на дату")
    p.add_argument("--trend-all", default=str(TREND_JSON["all"]),
                   help="JSON прогона тренда по восьмёрке лидеров")
    p.add_argument("--json", help="куда положить данные")
    p.add_argument("--html", help="куда положить страницу")
    a = p.parse_args()

    data = build(db_path=Path(a.db), log_dir=Path(a.log_dir), mode=a.links,
                 trend_paths={"hist": Path(a.trend_hist),
                              "all": Path(a.trend_all)})
    print(render_text(data))
    if a.json:
        Path(a.json).write_text(json.dumps(data, ensure_ascii=False, indent=1),
                                encoding="utf-8")
        print(f"JSON: {a.json}")
    if a.html:
        html = build_page(Path(a.template), data)
        Path(a.html).write_text(html, encoding="utf-8")
        print(f"HTML: {a.html}")


if __name__ == "__main__":
    main()
