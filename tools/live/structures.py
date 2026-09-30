"""Скринер структур: график со структурой плюс честная оценка под ним.

Что это. Владелец просил скринер, который **рисует** — уровни, наклонные
линии, режим (боковик или тренд), объём, — а не только выдаёт таблицу
сигналов. Такой скринер строится из того, что уже есть в проекте:

  * уровни — `src/analysis/levels.py` (`horizontal_levels`), алгоритм снят с
    документации Digash: экстремумы окном, допуск по таймфрейму, счёт касаний,
    пробитые уровни не показываются;
  * наклонные линии — там же (`find_trend_lines`): тройки экстремумов на одной
    прямой, с продлением вправо;
  * режим — по коэффициенту эффективности Кауфмана и сжатию диапазона;
    пороги берутся из `src/analysis/formations.py` (SQUEEZE_RATIO, SQUEEZE_ER),
    то есть те же, по которым работает детектор сжатия, а не выдуманные здесь;
  * объём — `metrics.volume_splash`, порог всплеска SPLASH_MIN оттуда же;
  * формации — `detect_all`, те же детекторы, что в измерении;
  * стакан — снимки из базы (`book_snapshots`), полосы 0.05–0.5 % от цены.

Чем это отличается от таблицы сигналов (`tools/live/screen.py`). Там —
список сегодняшних сигналов и ранжирование по измеренной ожидаемости. Здесь —
структура как таковая по каждой паре монета×таймфрейм, включая пары, где
формаций не нашлось вовсе: пустой экран «сигналов нет» не отвечает на вопрос
«что на рынке происходит».

Чего здесь нет и почему:

  * **плотностей стакана в широком коридоре.** Digash рисует карту заявок в
    коридоре до нескольких процентов от цены и считает «время разъедания»
    заявки. В базе лежат только полосы 0.05/0.1/0.25/0.5 % — этого мало для
    карты плотностей, и подставлять вместо неё что-то похожее нельзя.
    Разбор архива `bookDepth` с коридорами есть отдельно
    (`src/analysis/liquidity.py`, `marginal_slices`), но это архив прошлых
    суток, а не текущий стакан;
  * **дельты и CVD по сделкам.** Их не из чего считать: ленты сделок
    (aggTrades) в базе нет ни в живом виде, ни в архиве, который мы берём;
  * **обещаний доходности.** Найденная формация сама по себе ничего не
    значит: рядом с каждой стоит измеренный на архиве результат её типа
    (таблица `formation_stats`) с поправкой на перебор, и он в большинстве
    строк отрицательный. Это справка, а не рекомендация.

Все числа считает код. Языковая модель их не вычисляет и не подставляет:
если срез не собрался — в JSON идёт null и строка в gaps.
"""

from __future__ import annotations

import argparse
import json
import traceback
from datetime import datetime
from pathlib import Path

from src.analysis.formations import (NR_PERIOD, SPLASH_MIN, SQUEEZE_ER,
                                     SQUEEZE_RATIO, detect_all)
from src.analysis.levels import (PIVOT_GAP, adaptive_span, find_pivots,
                                 find_trend_lines, horizontal_levels,
                                 trend_tolerance_pct)
from src.analysis.metrics import (correlation, efficiency_ratio, natr,
                                  narrow_range, volume_splash)
from src.backtest import significance
from src.backtest.expectancy import MIN_TRADES
from src.storage import db
from tools.live.screen import MSK, _candles, _market_label

TFS = ("5m", "15m", "1h")
WINDOW = 300          # сколько свечей просим у базы (даст сколько есть)
CHART = 200           # сколько свечей рисуем (все индексы — по этому массиву)
LEVELS_MAX = 6
LINES_MAX = 3
RVOL_WINDOW = 20      # окно «обычного» объёма для полос под графиком

TEMPLATE = Path(__file__).resolve().parent / "structures.html"
PLACEHOLDER = "__DATA_JSON__"


def _msk(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, MSK).strftime("%d.%m %H:%M МСК")


def now_msk() -> str:
    return datetime.now(MSK).strftime("%Y-%m-%d %H:%M:%S МСК")


def _box(candles) -> tuple[float, float]:
    return (max(c.high for c in candles), min(c.low for c in candles))


def regime(cands) -> dict:
    """Режим по правилу, а не по ощущению.

    Правило (пороги — из `src/analysis/formations.py`, те же, по которым
    работает детектор сжатия):

      сжатие — диапазон последних 20 свечей не шире SQUEEZE_RATIO (0.55) от
        диапазона предыдущих 40, волатильность не выросла (NATR не больше
        0.8 от прежней) и коэффициент эффективности ниже SQUEEZE_ER (0.65);
      тренд — коэффициент эффективности не ниже SQUEEZE_ER (0.65), знак — по
        знаку движения цены за те же 20 свечей;
      боковик — всё остальное.
    """
    er = efficiency_ratio(cands, 20)
    out = {
        "er": None if er is None else round(er, 3),
        "natr": None,
        "range_ratio": None,
        "narrow": bool(narrow_range(cands, NR_PERIOD)),
        "label": "нет данных",
    }
    n14 = natr(cands, 14)
    if n14 is not None:
        out["natr"] = round(n14, 3)
    if len(cands) < 60:
        return out
    hi, lo = _box(cands[-20:])
    phi, plo = _box(cands[-60:-20])
    if phi > plo:
        out["range_ratio"] = round((hi - lo) / (phi - plo), 3)
    n_recent = natr(cands[-21:], 14)
    n_prior = natr(cands[-41:-21], 14)
    vol_ok = (n_recent is None or n_prior is None or n_prior <= 0
              or n_recent <= n_prior * 0.8)
    squeeze = (out["range_ratio"] is not None
               and out["range_ratio"] <= SQUEEZE_RATIO
               and vol_ok
               and (er is None or er <= SQUEEZE_ER))
    if squeeze:
        out["label"] = "сжатие"
    elif er is not None and er >= SQUEEZE_ER:
        change = cands[-1].close - cands[-21].close
        out["label"] = "тренд вверх" if change >= 0 else "тренд вниз"
    elif er is not None:
        out["label"] = "боковик"
    return out


def _rvol_series(cands) -> tuple[list[float | None], list[int]]:
    """Объём каждой свечи к среднему за предыдущие RVOL_WINDOW свечей."""
    series: list[float | None] = []
    splashes: list[int] = []
    for i, c in enumerate(cands):
        if i < RVOL_WINDOW:
            series.append(None)
            continue
        window = cands[i - RVOL_WINDOW:i]
        avg = sum(x.quote_volume for x in window) / len(window)
        if avg <= 0:
            series.append(None)
            continue
        r = c.quote_volume / avg
        series.append(round(r, 3))
        if r >= SPLASH_MIN:
            splashes.append(i)
    return series, splashes


def _trend_lines(cands, tf: str) -> list[dict]:
    """Наклонки с честной привязкой к экстремумам.

    `find_trend_lines` отдаёт (наклон, сдвиг, касаний, вид), но не говорит, от
    какого экстремума линия начинается. Считаем это сами: ищем первое касание
    среди тех же пивотов — до него линия не проверялась и рисовать её нельзя.
    Вправо продлеваем до конца графика: проверка в `find_trend_lines` идёт до
    последней свечи, поэтому продление внутри проверенного участка.
    """
    lines = find_trend_lines(cands, tf, max_lines=LINES_MAX)
    if not lines:
        return []
    tol = trend_tolerance_pct(tf) / 100
    pivots = find_pivots(cands, span=adaptive_span(len(cands)))[-PIVOT_GAP * 8:]
    out: list[dict] = []
    last = len(cands) - 1
    for slope, offset, touches, kind in lines:
        on_line = [i for i, p, _ in pivots
                   if (slope * i + offset) > 0
                   and abs(p - (slope * i + offset)) / (slope * i + offset) <= tol]
        if not on_line:
            # линия есть, а экстремума на ней не нашлось — не рисуем: без
            # привязки она читалась бы как проверенная на всей длине
            continue
        start = min(on_line)
        out.append({
            "slope": slope, "offset": offset, "touches": touches, "kind": kind,
            "from_idx": start, "to_idx": last,
            "from_value": slope * start + offset,
            "to_value": slope * last + offset,
        })
    return out


def _stats_join(stats: dict) -> tuple[dict, dict]:
    """Измерение формаций с поправкой на перебор по строкам-опорам."""
    anchors = {k: m for k, m in stats.items() if m["n"] >= MIN_TRADES}
    keys = list(anchors)
    verdict = significance.judge([dict(anchors[k]) for k in keys],
                                 min_n=MIN_TRADES)
    flags = {k: ok for k, ok in zip(keys, verdict.flags)}
    return verdict, flags


def collect_pair(conn, symbol: str, tf: str, exchange: str,
                 btc: list | None, stats: dict, flags: dict,
                 window: int) -> tuple[dict | None, list[str]]:
    notes: list[str] = []
    raw = _candles(conn, symbol, tf, exchange, limit=window)
    if len(raw) < 61:
        return None, [f"{symbol} {tf}: свечей {len(raw)}, структур не собрать "
                      f"(нужно 60)"]
    raw = raw[-CHART:]
    # Два массива, и путать их нельзя.
    #
    # `raw` — как отдаёт база, вместе с последней, ещё формирующейся свечой.
    # Именно этот массив идёт в детекторы: `_scan` сам отбрасывает незакрытую
    # свечу, и возраст сигнала считается от неё. Тот же массив рисуется — по
    # нему же считаются индексы, поэтому метка формации встаёт на свою свечу.
    #
    # `cands` — только закрытые свечи. По ним считаются уровни, наклонки,
    # режим и объём: незакрытая свеча прожила секунды против среднего за 20
    # полных, и в полосах объёма она читалась бы как «рынок затих» на ровном
    # месте. Её объём — не измерение, а недобор.
    live = raw[-1]
    cands = raw[:-1]
    price_now = cands[-1].close
    price_live = live.close
    btc_all = btc[-CHART:] if btc else None
    btc_closed = btc_all[:-1] if btc_all else None

    reg = regime(cands)
    volumes, splashes = _rvol_series(cands)

    levels = []
    try:
        for lv in horizontal_levels(cands, tf, max_levels=LEVELS_MAX):
            levels.append({
                "price": lv.price, "kind": lv.kind, "touches": lv.touches,
                "dist_pct": round(lv.distance_pct(price_live), 3),
            })
    except Exception as exc:                                    # noqa: BLE001
        notes.append(f"{symbol} {tf}: уровни не собрались — "
                     f"{type(exc).__name__}")

    lines = []
    try:
        lines = _trend_lines(cands, tf)
    except Exception as exc:                                    # noqa: BLE001
        notes.append(f"{symbol} {tf}: наклонки не собрались — "
                     f"{type(exc).__name__}")

    formations = []
    try:
        found = detect_all(raw, tf, symbol, exchange, btc=btc_all)
    except Exception as exc:                                    # noqa: BLE001
        notes.append(f"{symbol} {tf}: разбор упал — {type(exc).__name__}")
        found = []
    for f in found:
        m = stats.get((f.kind, f.tf))
        measured = None
        if m is not None:
            measured = {
                "n": m["n"], "exp_net": round(m["exp_net"], 3),
                "measured_on": m["measured_on"],
                "significant": flags.get((f.kind, f.tf)),
            }
        formations.append({
            "kind": f.kind, "title": f.title, "direction": f.direction,
            "entry": f.entry, "stop": f.stop,
            "target": f.targets[0] if f.targets else None,
            "rr": round(f.rr, 3), "risk_pct": round(f.risk_pct, 3),
            "confidence": f.confidence, "triggered": bool(f.triggered),
            "age_candles": f.age_candles, "style": f.style,
            "ts": f.ts, "invalid": f.invalid,
            # сигнальная свеча: _scan отбрасывает формирующуюся и отсчитывает
            # возраст назад — при age 0 это последняя закрытая свеча массива
            "idx": len(raw) - 2 - f.age_candles,
            "reasons": list(f.reasons),
            "measured": measured,
        })

    # Сводка по паре считается здесь, а не на странице: страница только
    # показывает то, что посчитано, — иначе одно и то же число считалось бы
    # дважды и в двух местах могло разойтись.
    measured = [f for f in formations if f["measured"]]
    best = max(measured, key=lambda f: f["measured"]["exp_net"]) \
        if measured else None
    summary = {
        "n_formations": len(formations),
        "n_measured": len(measured),
        "best": None if best is None else {
            "kind": best["kind"], "title": best["title"],
            "exp_net": best["measured"]["exp_net"],
            "n": best["measured"]["n"],
            "significant": best["measured"]["significant"],
        },
    }
    nearest = min(levels, key=lambda l: abs(l["dist_pct"]), default=None)
    summary["nearest_level_pct"] = None if nearest is None else nearest["dist_pct"]

    corr = None
    if btc_closed and symbol != "BTCUSDT":
        try:
            corr = correlation(cands, btc_closed)
        except Exception as exc:                                # noqa: BLE001
            notes.append(f"{symbol} {tf}: корреляция не собралась — "
                         f"{type(exc).__name__}")
    change20 = None
    if len(cands) > 21 and cands[-21].close > 0:
        change20 = round((price_live - cands[-21].close) / cands[-21].close * 100, 3)

    pair = {
        "symbol": symbol, "tf": tf,
        "price": price_live,
        "price_closed": price_now,
        "last_ts": live.ts, "last_msk": _msk(live.ts),
        "closed_ts": cands[-1].ts, "closed_msk": _msk(cands[-1].ts),
        "change20_pct": change20,
        "corr_btc": None if corr is None else round(corr, 1),
        "vol_splash": None if not volumes[-1] else volumes[-1],
        "regime": reg,
        "candles": [[c.ts, c.open, c.high, c.low, c.close, c.quote_volume]
                    for c in cands],
        "volumes": volumes,
        "splashes": splashes,
        "levels": levels,
        "trendlines": lines,
        "formations": formations,
        "summary": summary,
        "notes": notes,
    }
    return pair, notes


def collect_books(conn, symbols: list[str], exchange: str) -> tuple[dict, list[str]]:
    """Последний снимок стакана по каждой монете — один на монету, не на пару.

    Снимок не зависит от таймфрейма, поэтому в данных он лежит на уровне
    монеты: иначе один и тот же стакан повторялся бы трижды.
    """
    books: dict[str, dict] = {}
    notes: list[str] = []
    for sym in symbols:
        rows = db.latest_book_snapshots(conn, 50, symbol=sym, exchange=exchange)
        if not rows:
            notes.append(f"{sym}: снимка стакана в базе нет")
            continue
        b = dict(rows[0])
        books[sym] = {
            "ts": b["ts"], "msk": _msk(b["ts"]),
            "mid": b["mid"], "spread_bps": round(b["spread_bps"], 3),
            "imbalance": round(b["imbalance"], 4),
            "levels": f"{b['n_bids']}/{b['n_asks']}",
            "bands": [
                {"bps": bps, "bid": b[f"bid_{int(bps)}bps"],
                 "ask": b[f"ask_{int(bps)}bps"]}
                for bps in (5, 10, 25, 50)
            ],
        }
    return books, notes


def build(db_path: str | None, tfs: tuple[str, ...], exchange: str,
          window: int) -> dict:
    conn = db.connect(db_path)
    try:
        stats = db.load_formation_stats(conn)
        verdict, flags = _stats_join(stats)
        symbols = db.symbols_present(conn, exchange)
        gaps: list[str] = []
        pairs: list[dict] = []
        btc_by_tf: dict[str, list] = {}
        for sym in symbols:
            for tf in tfs:
                if sym != "BTCUSDT" and tf not in btc_by_tf:
                    btc_by_tf[tf] = _candles(conn, "BTCUSDT", tf, exchange,
                                             limit=window)
                pair, notes = collect_pair(conn, sym, tf, exchange,
                                           btc_by_tf.get(tf), stats, flags,
                                           window)
                gaps.extend(notes)
                if pair is not None:
                    pairs.append(pair)
        anchors = [m for m in stats.values() if m["n"] >= MIN_TRADES]
        pos_keys = {(k, tf) for (k, tf), m in stats.items()
                    if m["n"] >= MIN_TRADES and m["exp_net"] > 0}
        hit = {(f["kind"], p["tf"]) for p in pairs for f in p["formations"]
               if f["measured"]}
        books, book_notes = collect_books(conn, symbols, exchange)
        gaps.extend(book_notes)
        scope = (next(iter(stats.values()))["symbol_scope"] if stats else None)
        measured_on = max((m["measured_on"] for m in stats.values()),
                          default=None)
        meta = {
            "collected_at": now_msk(),
            "market": _market_label(exchange),
            "exchange": exchange,
            "tfs": list(tfs),
            "candles_in_chart": CHART,
            "measured_on": measured_on,
            "scope": scope,
            "min_trades": MIN_TRADES,
            "fdr_alpha": verdict.alpha,
            "stats_n": len(stats),
            "stats_anchors": len(anchors),
            "stats_positive": sum(1 for m in anchors if m["exp_net"] > 0),
            "stats_significant": verdict.n_significant,
            "stats_significant_positive": sum(
                1 for m, ok in zip(anchors, verdict.flags)
                if ok and m["exp_net"] > 0),
            "positive_hit_now": sorted(pos_keys & hit),
            "rvol_window": RVOL_WINDOW,
            "splash_min": SPLASH_MIN,
            "squeeze_ratio": SQUEEZE_RATIO,
            "squeeze_er": SQUEEZE_ER,
            "regime_rule": (
                "сжатие — диапазон 20 свечей ≤ "
                f"{SQUEEZE_RATIO:g} от диапазона предыдущих 40, NATR не вырос "
                f"и коэффициент эффективности ≤ {SQUEEZE_ER:g}; тренд — "
                f"коэффициент эффективности ≥ {SQUEEZE_ER:g} (знак по движению "
                "за 20 свечей); остальное — боковик"),
            "bands_note": (
                "стакан показан полосами 0.05 / 0.1 / 0.25 / 0.5 % от цены — "
                "это то, что собирает живой сбор; карты плотностей в широком "
                "коридоре в базе нет"),
            "caveats": [
                "График — закрытые свечи базы. Последняя, ещё формирующаяся "
                "свеча в расчёт структур не входит (её объём — недобор, а не "
                "измерение), а её цена показана отдельной линией.",
                "Метка режима — правило с порогами, а не найденная "
                "закономерность: она не проверялась на доходность и говорит "
                "только о форме движения.",
                "Вход в измерении формаций — цена закрытия сигнальной свечи, а "
                "не цена момента решения. У формаций с возрастом 1–2 свечи она "
                "устарела, и такие разборы хуже свежих примерно на 0.12 R "
                "(docs/research/17, §2).",
                "Walk-forward с разрывом на утечку для формаций не делался: "
                "числа описывают одно окно, а не устойчивость во времени "
                "(для тренд-фильтра он есть — docs/research/16, §8).",
                "Карты плотностей заявок в широком коридоре и дельты по "
                "сделкам здесь нет: в базе лежат только полосы до 0.5 % от "
                "цены, а ленты сделок (aggTrades) нет ни в живом сборе, ни в "
                "архиве, который мы берём.",
                f"Измерение формаций снято {measured_on or '—'} на монетах "
                f"измерения ({scope or '—'}), а не на этой паре и не на этом "
                f"отрезке: одно число описывает формацию на всех монетах окна.",
            ],
        }
        return {"meta": meta, "pairs": pairs, "books": books, "gaps": gaps}
    finally:
        conn.close()


def num(value, width: int, digits: int, sign: bool = False) -> str:
    """Число для текстового отчёта. None печатается прочерком, а не нулём:
    ноль — это измеренное значение, отсутствие данных — не оно."""
    if value is None:
        return "—".rjust(width)
    text = f"{value:+.{digits}f}" if sign else f"{value:.{digits}f}"
    return text.rjust(width)


def render_text(data: dict) -> str:
    meta, pairs = data["meta"], data["pairs"]
    out = [f"рынок: {meta['market']}",
           f"собрано: {meta['collected_at']}",
           f"пар: {len(pairs)}, измерение формаций: {meta['stats_n']} строк "
           f"(опора n ≥ {meta['min_trades']} у {meta['stats_anchors']}, "
           f"в плюсе {meta['stats_positive']}, значимых после FDR "
           f"{meta['fdr_alpha']:g} {meta['stats_significant']}, из них с плюсом "
           f"{meta['stats_significant_positive']})",
           ""]
    out.append(f"{'монета':9} {'ТФ':4} {'режим':12} {'ER':>5} {'NATR':>7} "
               f"{'RVOL':>6} {'20св, %':>8} {'BTC':>6} {'ур':>3} {'накл':>5} "
               f"{'форм':>5} {'до уровня':>10}")
    for p in pairs:
        r = p["regime"]
        nearest = min((l["dist_pct"] for l in p["levels"]),
                      key=abs, default=None)
        out.append(
            f"{p['symbol']:9} {p['tf']:4} {r['label']:12} "
            f"{num(r['er'], 5, 2)} {num(r['natr'], 7, 3)} "
            f"{num(p['vol_splash'], 6, 2)} {num(p['change20_pct'], 8, 2, True)} "
            f"{num(p['corr_btc'], 6, 1, True)} "
            f"{len(p['levels']):3} {len(p['trendlines']):5} "
            f"{len(p['formations']):5} {num(nearest, 10, 2, True)}")
    if data["gaps"]:
        out.append("")
        out.append(f"пробелы ({len(data['gaps'])}):")
        out.extend(f"  · {g}" for g in data["gaps"])
    return "\n".join(out)


def build_page(data: dict, template: Path, out: Path) -> None:
    if not template.exists():
        raise SystemExit(f"нет шаблона страницы: {template}")
    html = template.read_text(encoding="utf-8")
    if PLACEHOLDER not in html:
        raise SystemExit(f"в шаблоне нет метки {PLACEHOLDER}")
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    # JSON вшивается внутрь <script>, поэтому закрывающий тег надо погасить
    blob = blob.replace("</", "<\\/")
    out.write_text(html.replace(PLACEHOLDER, blob), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Скринер структур: графики со структурами и измерением")
    ap.add_argument("--db", default=None, help="путь к базе (по умолчанию из db)")
    ap.add_argument("--tfs", default=",".join(TFS))
    ap.add_argument("--exchange", default="binance_futures")
    ap.add_argument("--window", type=int, default=WINDOW,
                    help=f"сколько свечей брать из базы (по умолчанию {WINDOW})")
    ap.add_argument("--json", dest="json_path", default=None)
    ap.add_argument("--html", dest="html_path", default=None)
    ap.add_argument("--template", default=str(TEMPLATE))
    a = ap.parse_args(argv)

    tfs = tuple(t.strip() for t in a.tfs.split(",") if t.strip())
    data = build(a.db, tfs, a.exchange, a.window)
    print(render_text(data))
    if a.json_path:
        Path(a.json_path).write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nJSON: {a.json_path}")
    if a.html_path:
        build_page(data, Path(a.template), Path(a.html_path))
        size = Path(a.html_path).stat().st_size / 1024
        print(f"страница: {a.html_path} ({size:.0f} КБ)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
