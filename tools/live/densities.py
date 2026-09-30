"""Плотности стакана по монетам: где стоят крупные заявки и как часто цена
туда приходила.

Страница отдельная от скринера формаций и отвечает на два вопроса сразу:

  1. Где сейчас в стакане стоят заявки, резко выделяющиеся на фоне
     остальных, — и на сколько процентов от середины они стоят.
  2. Сколько раз цена уже наведывалась к этой цене за историю свечей.

Что здесь переиспользуется, а не написано заново:

  * `src.analysis.density.find_densities` — правило плотности целиком
    (`OUTLIER_K` = 6 медианных уровней, `MIN_NOTIONAL` = 50 000,
    коридор 0.1–3 % от середины). Параметры не переопределяются, кроме
    `depth`: он выставлен в 1000, потому что это максимум, который отдаёт
    биржа, а на 500 уровнях у BTC плотностей не видно вовсе (стакан
    достаёт до ±0.07 %, порог отсечения — 0.1 %).
  * `Density.absorb_seconds` — оценка Digash «время поедания заявки»:
    размер ÷ (средний объём за 2 часа × 2).
  * `max(0, ...)` оговорки о том, чего здесь нет, — в конце файла.

Чего здесь нет и почему:

  * Времени жизни плотности и признаков спуфинга. `DensityTracker` их
    умеет, но живёт в памяти одного процесса: снимки стакана в базу
    пишутся без сырых уровней, поэтому восстановить жизнь заявки задним
    числом не из чего. Здесь один снимок — одна строка.
  * Прогноза. Плотность — это факт стакана, а не сигнал: страница не
    говорит, отскочит цена или пробьёт.

Все числа считает код. Чего нет — то стоит как «—», а не как 0.

Запуск:

    .venv/bin/python -m tools.live.densities --json /tmp/dens.json
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from src.analysis.density import (MIN_DISTANCE_PCT, MIN_NOTIONAL,
                                  MAX_DISTANCE_PCT, OUTLIER_K, find_densities,
                                  median_level_size)
from src.data import market
from src.data.market import Candle, OrderBook

# Глубина стакана. 1000 — максимум Binance USDT-M; на 500 у BTC плотностей
# не видно (см. docstring), а правило требует, чтобы глубины хватило хотя бы
# до порога 0.1 % от середины.
DEPTH = 1000
MAX_ROWS = 12          # сколько плотностей показывать на монету

# Показываются крупнейшие по номиналу, а не ближайшие к цене. Разница
# существенная: ближайшие у всех монет жмутся к нижней границе коридора
# (0.10-0.15 % — это край видимого стакана у BTC и ETH), и по ним монеты
# неразличимы. Крупнейшая заявка у SOL стоит в 2.7 % от цены, у ADA — в
# 2.1 %, и именно она отвечает на вопрос «где стена». Ближайшая плотность
# при этом не теряется: она стоит в сводке по монете.

# Касания считаются по свечам с биржи: в базе лежит 200 свечей на монету
# (5m — это 16 часов), для вопроса «как часто цена тут бывала» этого мало.
TOUCH_TFS = ("5m", "1h")
TOUCH_CANDLES = 1000   # 5m — 3.5 суток, 1h — 41.7 суток

TEMPLATE = Path(__file__).resolve().parent / "densities.html"
PLACEHOLDER = "__DATA_JSON__"

# Время и название рынка берутся из screen: формат вывода должен быть один
# на проект, а не свой в каждой странице.
from tools.live.screen import MSK, _market_label  # noqa: E402


def touches(candles: list[Candle], price: float) -> tuple[int, int | None]:
    """Сколько раз цена приходила к этой цене.

    Касание — свеча, диапазон [low, high] которой накрыл цену плотности.
    Подряд идущие свечи — одно касание: цена стояла у уровня, а не
    подходила к нему каждый час заново. Возвращает (число касаний,
    время последнего касания).
    """
    n = 0
    last: int | None = None
    inside = False
    for c in candles:
        hit = c.low <= price <= c.high
        if hit and not inside:
            n += 1
        inside = hit
        if hit:
            last = c.ts
    return n, last


def avg_volume_2h(candles_5m: list[Candle]) -> float:
    """Средний объём за 2 часа в базовой монете — знаменатель времени поедания."""
    tail = candles_5m[-24:]
    if not tail:
        return 0.0
    return sum(c.volume for c in tail) / len(tail) * 24


def price_digits(prices: list[float]) -> int:
    """Сколько знаков после запятой нужно, чтобы цены уровней не сливались.

    Разрядность берётся из самих цен, а не из их величины: у XRP уровни
    1.5071 и 1.5072 при двух знаках превратились бы в одну и ту же «1.51»,
    и строка таблицы перестала бы отвечать на вопрос «где стоит заявка».

    Середина стакана сюда входит наравне с уровнями: у монеты без плотностей
    это единственная цена на странице. Но середина — среднее двух float, и её
    repr несёт бинарный шум (0.24764999999999998), поэтому цена сначала
    округляется до 10 знаков: у биржевых котировок столько не бывает, а шум
    снимается. Без этого разрядность выходила бы 8 у всех подряд.

    Пустой список (ни одной плотности) даёт 4 — иначе цена монеты напечаталась
    бы целым числом и 1.5071 превратилось бы в «2».
    """
    if not prices:
        return 4
    d = 0
    for p in prices:
        e = Decimal(str(round(p, 10))).as_tuple().exponent
        if e < 0:
            d = max(d, -e)
    return min(d, 8)


def _human_seconds(sec: float | None) -> str:
    if sec is None:
        return "—"
    if sec < 1:
        return "менее 1 с"
    if sec < 90:
        return f"{sec:.0f} с"
    if sec < 5400:
        return f"{sec / 60:.0f} мин"
    if sec < 172800:
        return f"{sec / 3600:.1f} ч"
    return f"{sec / 86400:.1f} сут"


def collect_coin(symbol: str, exchange: str, tf_candles: dict[str, list[Candle]],
                 gaps: list[str]) -> dict:
    """Одна монета: снимок стакана, плотности, касания по каждой плотности."""
    try:
        ob: OrderBook = market.orderbook(exchange, symbol, DEPTH)
    except Exception as e:
        gaps.append(f"{symbol}: стакан не получен ({type(e).__name__}: "
                    f"{str(e)[:80]})")
        return {"symbol": symbol, "gap": f"стакан не получен: {type(e).__name__}"}

    med = median_level_size(ob, DEPTH)
    # top намеренно огромный: нужны все плотности коридора, чтобы выбрать
    # крупнейшие. Обрезка по top идёт по расстоянию, а не по размеру.
    found = find_densities(ob, top=10_000, depth=DEPTH)
    ds = sorted(found, key=lambda d: -d.notional)[:MAX_ROWS]
    reach = max(abs((l.price - ob.mid) / ob.mid * 100)
                for l in ob.bids + ob.asks)

    # Окно касаний считается по фактическим свечам, а не по номинальным
    # 1000: биржа может отдать меньше, и тогда «за 41 сутки» было бы неправдой.
    windows: dict[str, float] = {}
    for tf, cs in tf_candles.items():
        if len(cs) >= 2:
            span = (cs[-1].ts - cs[0].ts) / 3.6e6 + market.INTERVALS[tf] / 3600
            windows[tf] = round(span, 1)

    avail = {tf: len(cs) for tf, cs in tf_candles.items() if cs}
    base2h = avg_volume_2h(tf_candles.get("5m") or [])

    rows = []
    for d in ds:
        touch = {}
        for tf in TOUCH_TFS:
            cs = tf_candles.get(tf) or []
            if not cs:
                touch[tf] = {"n": None, "last_msk": None}
                continue
            n, last = touches(cs, d.price)
            touch[tf] = {
                "n": n,
                "last_msk": (datetime.fromtimestamp(last / 1000, MSK)
                             .strftime("%Y-%m-%d %H:%M")) if last else None,
            }
        sec = d.absorb_seconds(base2h)
        rows.append({
            "side": d.side,
            "price": d.price,
            "distance_pct": d.distance_pct,
            "size": d.size,
            "notional": d.notional,
            "k_median": d.size / med if med else None,
            "absorb_seconds": sec,
            "absorb_text": _human_seconds(sec),
            "touches": touch,
        })

    return {
        "symbol": symbol,
        "mid": ob.mid,
        "spread_bps": ob.spread / ob.mid * 10000 if ob.mid else None,
        "imbalance": ob.imbalance(20),
        "reach_pct": reach,
        "median_level": med,
        "threshold_size": med * OUTLIER_K,
        "n_densities": len(found),
        "n_shown": len(rows),
        "nearest_pct": (min(abs(d.distance_pct) for d in found)
                        if found else None),
        "nearest_notional": (min(found,
                                 key=lambda d: abs(d.distance_pct)).notional
                             if found else None),
        "corridor_pct": MAX_DISTANCE_PCT,
        "price_digits": price_digits([ob.mid] + [d.price for d in found]),
        "touch_windows": windows,
        # сколько номинала стоит плотностями по каждую сторону от цены
        "wall_notional_bid": sum(d.notional for d in found if d.side == "bid"),
        "wall_notional_ask": sum(d.notional for d in found if d.side == "ask"),
        "candles": avail,
        "avg_volume_2h": base2h,
        "densities": rows,
    }


def build(db_path: str, exchange: str, symbols: list[str] | None) -> dict:
    from src.storage import db as dbm

    conn = dbm.connect(db_path)
    syms = symbols or dbm.symbols_present(conn, exchange)
    conn.close()
    if not syms:
        raise SystemExit(f"в базе нет монет по бирже {exchange}")

    gaps: list[str] = []
    coins = []
    t0 = time.time()
    for i, sym in enumerate(sorted(syms), 1):
        tf_candles: dict[str, list[Candle]] = {}
        for tf in TOUCH_TFS:
            try:
                cs = market.ohlcv(exchange, sym, tf, TOUCH_CANDLES)
                # последняя свеча ещё формируется: её диапазон неполный, и
                # касание по ней считалось бы по куску движения
                tf_candles[tf] = cs[:-1] if cs else []
            except Exception as e:
                tf_candles[tf] = []
                gaps.append(f"{sym} {tf}: свечи не получены "
                            f"({type(e).__name__}: {str(e)[:80]})")
        coins.append(collect_coin(sym, exchange, tf_candles, gaps))
        print(f"  [{i}/{len(syms)}] {sym}: "
              f"{coins[-1].get('n_densities', '—')} плотностей", flush=True)

    live = [c for c in coins if c.get("densities")]
    meta = {
        "collected_at": datetime.now(MSK).strftime("%Y-%m-%d %H:%M"),
        "market": _market_label(exchange),
        "exchange": exchange,
        "symbols": len(coins),
        "seconds": round(time.time() - t0, 1),
        "depth": DEPTH,
        "max_rows": MAX_ROWS,
        "outlier_k": OUTLIER_K,
        "min_notional": MIN_NOTIONAL,
        "min_distance_pct": MIN_DISTANCE_PCT,
        "max_distance_pct": MAX_DISTANCE_PCT,
        "touch_tfs": list(TOUCH_TFS),
        "touch_candles": TOUCH_CANDLES,
        "coins_with_densities": len(live),
        "densities_total": sum(c["n_densities"] for c in live),
        "shown_total": sum(c["n_shown"] for c in live),
    }
    meta["caveats"] = caveats(meta, coins)
    return {"meta": meta, "coins": coins, "gaps": gaps}


def caveats(meta: dict, coins: list[dict]) -> list[str]:
    """Оговорки строятся из данных, а не зашиты: они должны быть верны и
    когда стакан достаёт далеко, и когда не достаёт вовсе."""
    out = [
        "Плотность — снимок стакана на момент сбора, а не наблюдение за "
        "заявкой: в базе хранятся только суммарные полосы, сырых уровней "
        "нет, поэтому ни времени жизни, ни признаков спуфинга здесь быть "
        "не может. Одна плотность — одна строка.",
        "Порог плотности задан относительно самого стакана: уровень "
        f"считается крупным, если он больше медианного в {OUTLIER_K:g} раз, "
        f"и его номинал не меньше {MIN_NOTIONAL // 1000} тыс. USDT. "
        f"Медиана берётся по первым {DEPTH} уровням каждой стороны — это "
        "всё, что отдаёт биржа.",
        f"Коридор поиска — от {MIN_DISTANCE_PCT:g} % до "
        f"{MAX_DISTANCE_PCT:g} % от середины. Ближе "
        f"{MIN_DISTANCE_PCT:g} % уровень не считается плотностью: у лучшей "
        "цены скапливается весь поток, и как уровень для отскока он "
        "бесполезен.",
        f"Показаны {MAX_ROWS} крупнейших по номиналу плотностей каждой "
        f"монеты из всех найденных в коридоре (всего их "
        f"{meta['densities_total']} на {meta['coins_with_densities']} "
        "монетах). Крупнейшая — не значит ближайшая: расстояние до каждой "
        "стоит в таблице, ближайшая вынесена в сводку по монете.",
        "Касание — свеча, чей диапазон накрыл цену плотности; подряд "
        "идущие свечи считаются одним касанием. Касания посчитаны по "
        f"{TOUCH_CANDLES} закрытым свечам с биржи "
        f"({', '.join(TOUCH_TFS)}), последняя, ещё формирующаяся, "
        "отброшена. Цена плотности — сегодняшняя, поэтому «касания» "
        "означают «сколько раз цена была на этой цене за это окно», а не "
        "«сколько раз отскакивала от этой заявки»: сама заявка могла "
        "появиться час назад.",
        "Время поедания — оценка Digash: размер заявки ÷ (средний объём за "
        "2 часа × 2). Считается по последним 24 свечам 5m. Это порядок "
        "величины, а не срок.",
    ]
    narrow = [c["symbol"] for c in coins
              if c.get("reach_pct") is not None and c["reach_pct"] < 0.5]
    if narrow:
        out.append(
            "Стакан отдаётся биржей не на всю глубину: у "
            + ", ".join(narrow) + " последний уровень лежит ближе 0.5 % от "
            "середины, поэтому дальше этого расстояния плотностей не видно "
            "не потому, что их нет, а потому, что их не отдали.")
    if not meta.get("coins_with_densities"):
        out.append("Плотностей по этому правилу не нашлось ни на одной монете.")
    return out


def build_page(data: dict, template: Path = TEMPLATE) -> str:
    """Подставить данные в шаблон. Экранируется только `</` — JSON в теге
    <script> не должен уметь закрыть тег."""
    html = template.read_text(encoding="utf-8")
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    if html.count(PLACEHOLDER) != 1:
        raise SystemExit(f"в шаблоне {template} ожидался ровно один "
                         f"{PLACEHOLDER}, найдено {html.count(PLACEHOLDER)}")
    return html.replace(PLACEHOLDER, blob)


def num(value, width: int = 7, digits: int = 2, sign: bool = False) -> str:
    """None — это «нет данных», а не ноль. Печатать его нулём нельзя."""
    if value is None:
        return "—".rjust(width)
    return f"{value:+{width}.{digits}f}" if sign else f"{value:{width}.{digits}f}"


def _px(value: float, digits: int) -> str:
    """Цена с нужной разрядностью, но без хвостовых нулей: 116.000 → 116."""
    return f"{value:.{digits}f}".rstrip("0").rstrip(".") if digits else f"{value:.0f}"


def render_text(data: dict) -> str:
    m = data["meta"]
    lines = [f"Плотности стакана — {m['market']}",
             f"собрано {m['collected_at']} МСК, монет {m['symbols']}, "
             f"плотностей {m['densities_total']} у {m['coins_with_densities']}",
             ""]
    for c in data["coins"]:
        if c.get("gap"):
            lines.append(f"{c['symbol']:9} {c['gap']}")
            continue
        dg = c["price_digits"]
        lines.append(f"{c['symbol']:9} цена {_px(c['mid'], dg)}  "
                     f"спред {num(c['spread_bps'], 5, 2)} б.п.  "
                     f"стакан до ±{c['reach_pct']:.2f} %  "
                     f"плотностей в коридоре {c['n_densities']}, "
                     f"показано {c['n_shown']}  "
                     f"номинал плотностей бид "
                     f"{num(c['wall_notional_bid'] / 1e6, 6, 2)} млн / аск "
                     f"{num(c['wall_notional_ask'] / 1e6, 6, 2)} млн")
        for d in c["densities"]:
            t5 = d["touches"].get("5m", {})
            t1 = d["touches"].get("1h", {})
            lines.append(
                f"    {d['side']:3} {_px(d['price'], dg):>14} "
                f"{num(d['distance_pct'], 7, 3, sign=True)} %  "
                f"{num(d['notional'] / 1e6, 8, 3)} млн  "
                f"×{num(d['k_median'], 6, 1)} мед  "
                f"съедание {d['absorb_text']:>10}  "
                f"касаний 5m {num(t5.get('n'), 3, 0)} / 1h {num(t1.get('n'), 3, 0)}"
                f"  последнее {t1.get('last_msk') or '—'}")
    if data["gaps"]:
        lines.append("")
        lines.append("не собралось:")
        lines.extend(f"  {g}" for g in data["gaps"])
    lines.append("")
    for i, cv in enumerate(m["caveats"], 1):
        lines.append(f"{i}. {cv}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="data/screener.db")
    ap.add_argument("--exchange", default="binance_futures")
    ap.add_argument("--symbols", nargs="*", default=None)
    ap.add_argument("--json", dest="json_path")
    ap.add_argument("--html", dest="html_path")
    ap.add_argument("--template", default=str(TEMPLATE))
    a = ap.parse_args()

    data = build(a.db, a.exchange, a.symbols)
    print(render_text(data))
    if a.json_path:
        Path(a.json_path).write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nJSON: {a.json_path}")
    if a.html_path:
        Path(a.html_path).write_text(
            build_page(data, Path(a.template)), encoding="utf-8")
        print(f"HTML: {a.html_path}")


if __name__ == "__main__":
    main()
