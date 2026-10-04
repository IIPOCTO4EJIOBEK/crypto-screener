"""Главная скринера: одна таблица монет, как у Digash и подобных скринеров.

Строка — монета: цена, изменение за 1ч и 24ч, оборот, волатильность, тренд
на 15м / 1ч / 4ч (лонг, шорт, флэт), ближайшая плотность, свежие формации и
мини-график за сутки. Данные — то, что круг уже собрал: свечи из базы,
плотности и формации из соседних страниц (`densities.html`,
`structures.html`), оборот — из среза вселенной.

Рядом кладётся `trend-now.json` — те же метки тренда машинно-читаемо,
чтобы их мог брать торговый бот.

    python -m tools.live.board --html docs/live/board.html \
        --universe data/universe-turnover.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.storage import db  # noqa: E402

TEMPLATE = Path(__file__).resolve().parent / "board.html"
EXCHANGE = "binance_futures"
FAST, SLOW = 9, 21          # EMA для тренда
SLOPE_BARS = 3              # наклон медленной EMA — за столько свечей
FRESH = 3                   # формация «свежая», если ей не больше N свечей
TRADE = Path("/opt/crypto-trade/data/trade")
# страницы бумажного бота: адрес на сервере → откуда Caddy её отдаёт
BOTS = (("bot.html", "Бот: спот", TRADE / "paper-spot" / "bot.html"),
        ("bot-future.html", "Бот: фьючерсы", TRADE / "paper-future" / "bot.html"),
        ("bot-future-ls.html", "Бот: лонг/шорт", TRADE / "paper-future-longshort" / "bot.html"),
        ("bot-screener.html", "Бот: по скринеру", TRADE / "screener-all" / "bot.html"),
        ("bot-screener-trend.html", "Бот: скринер + тренд", TRADE / "screener-all-trend-tf" / "bot.html"))


# --------------------------------------------------------------------------
# данные
# --------------------------------------------------------------------------
def _embedded(path: Path) -> dict | None:
    """JSON, вшитый в соседнюю страницу (`<script type=application/json>`)."""
    if not path.exists():
        return None
    s = path.read_text(encoding="utf-8")
    m = re.search(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', s, re.S)
    return json.loads(m.group(1)) if m else None


def _candles(conn, symbol: str, tf: str, limit: int = 300) -> list[tuple]:
    """(ts, o, h, l, c, quote_volume) по возрастанию времени."""
    rows = conn.execute(
        "SELECT open_ts, open, high, low, close, quote_volume FROM candles "
        "WHERE symbol=? AND timeframe=? AND exchange=? "
        "ORDER BY open_ts DESC LIMIT ?", (symbol, tf, EXCHANGE, limit)).fetchall()
    return [tuple(r) for r in reversed(rows)]


def _to_4h(h1: list[tuple]) -> list[tuple]:
    """Склеить часовые свечи в 4-часовые по границам UTC."""
    out, cur = [], None
    for ts, o, h, l, c, v in h1:
        start = ts - ts % (4 * 3600_000)
        if cur is None or cur[0] != start:
            if cur:
                out.append(tuple(cur))
            cur = [start, o, h, l, c, v]
        else:
            cur[2], cur[3] = max(cur[2], h), min(cur[3], l)
            cur[4] = c
            cur[5] += v
    if cur:
        out.append(tuple(cur))
    return out


def _ema(xs: list[float], n: int) -> list[float]:
    k, out = 2 / (n + 1), []
    for x in xs:
        out.append(x if not out else out[-1] + k * (x - out[-1]))
    return out


def trend(cands: list[tuple]) -> dict | None:
    """Лонг / шорт / флэт по закрытым свечам.

    Лонг: цена выше быстрой EMA, быстрая выше медленной, медленная растёт.
    Шорт — зеркально. Иначе флэт. `strength` — разрыв EMA в процентах.
    """
    closed = [c[4] for c in cands[:-1]]       # последняя свеча ещё идёт
    if len(closed) < SLOW + SLOPE_BARS:
        return None
    f, s = _ema(closed, FAST), _ema(closed, SLOW)
    px, rising = closed[-1], s[-1] > s[-1 - SLOPE_BARS]
    falling = s[-1] < s[-1 - SLOPE_BARS]
    if px > f[-1] > s[-1] and rising:
        side = "long"
    elif px < f[-1] < s[-1] and falling:
        side = "short"
    else:
        side = "flat"
    return {"side": side, "strength": round((f[-1] - s[-1]) / s[-1] * 100, 3)}


def overall(tr: dict) -> str:
    """Общий тренд монеты: 1ч и 4ч согласны — их сторона, иначе флэт."""
    a, b = (tr.get("1h") or {}).get("side"), (tr.get("4h") or {}).get("side")
    if a and a == b:
        return a
    return "flat"


def _pct(now: float, then: float) -> float | None:
    return None if not then else round((now - then) / then * 100, 2)


def _natr(cands: list[tuple], n: int = 14) -> float | None:
    if len(cands) < n + 2:
        return None
    trs = []
    for prev, cur in zip(cands[-n - 1:-1], cands[-n:]):
        trs.append(max(cur[2], prev[4]) - min(cur[3], prev[4]))
    return round(sum(trs) / n / cands[-1][4] * 100, 2)


def charts(struct: dict) -> dict:
    """Данные для графиков таблицы: свечи и разметка каждой пары из структур.

    Отдельным файлом `charts.json`: таблица грузит его, только когда
    открывают график, а сама страница остаётся лёгкой.
    """
    out: dict[str, dict] = {}
    for p in struct.get("pairs", []):
        cs = p.get("candles") or []
        out.setdefault(p["symbol"], {})[p["tf"]] = {
            "candles": cs,
            "levels": [{"price": l["price"], "kind": l["kind"], "touches": l.get("touches")}
                       for l in p.get("levels", [])],
            "lines": [{"kind": t["kind"], "touches": t.get("touches"),
                       "a": [t["from_idx"], t["from_value"]], "b": [t["to_idx"], t["to_value"]]}
                      for t in p.get("trendlines", [])],
            "forms": [{"title": f.get("title") or f["kind"], "dir": f.get("direction"),
                       "idx": f.get("idx"), "entry": f.get("entry"), "stop": f.get("stop"),
                       "target": f.get("target"), "rr": f.get("rr"),
                       "invalid": f.get("invalid"), "reasons": f.get("reasons", [])[:4],
                       "exp": (f.get("measured") or {}).get("exp_net"),
                       "sig": bool((f.get("measured") or {}).get("significant"))}
                      for f in p.get("formations", [])],
            "splashes": p.get("splashes", []),
            "regime": (p.get("regime") or {}).get("label"),
        }
    return out


def _dedupe(fs: list[dict]) -> list[dict]:
    """Одна и та же формация на одном ТФ и в одну сторону — один раз."""
    seen, out = set(), []
    for f in fs:
        key = (f["tf"], f["kind"], f["dir"])
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def build(db_path: str | None, live_dir: Path, universe_path: Path | None) -> dict:
    uni = json.loads(universe_path.read_text()) if universe_path and universe_path.exists() else {}
    dens = _embedded(live_dir / "densities.html") or {}
    struct = _embedded(live_dir / "structures.html") or {}
    dens_by = {c["symbol"]: c for c in dens.get("coins", [])}

    forms: dict[str, list] = {}
    all_forms: list[dict] = []
    for p in struct.get("pairs", []):
        for f in p.get("formations", []):
            m = f.get("measured") or {}
            all_forms.append({
                "symbol": p["symbol"], "coin": p["symbol"].removesuffix("USDT"),
                "tf": p["tf"], "title": f.get("title") or f["kind"], "kind": f["kind"],
                "dir": f.get("direction"), "entry": f.get("entry"), "stop": f.get("stop"),
                "target": f.get("target"), "rr": f.get("rr"), "risk": f.get("risk_pct"),
                "age": f.get("age_candles"), "exp": m.get("exp_net"), "n": m.get("n"),
                "sig": bool(m.get("significant")), "price": p.get("price"),
                "regime": (p.get("regime") or {}).get("label"),
            })
        for f in p.get("formations", []):
            if f.get("age_candles", 99) > FRESH:
                continue
            m = f.get("measured") or {}
            forms.setdefault(p["symbol"], []).append({
                "tf": p["tf"], "title": f.get("title") or f["kind"],
                "kind": f["kind"], "dir": f.get("direction"),
                "rr": f.get("rr"), "age": f.get("age_candles"),
                "exp": m.get("exp_net"), "sig": bool(m.get("significant")),
            })

    conn = db.connect(db_path)
    symbols = uni.get("symbols") or sorted(dens_by)
    rows, trends = [], {}
    for sym in symbols:
        m1, m15, h1 = (_candles(conn, sym, tf) for tf in ("1m", "15m", "1h"))
        if not h1:
            continue
        price = (m1 or h1)[-1][4]
        h4 = _to_4h(h1)
        tr = {"15m": trend(m15), "1h": trend(h1), "4h": trend(h4)}
        tr_all = overall(tr)
        trends[sym] = {**{k: (v or {}).get("side") for k, v in tr.items()},
                       "overall": tr_all}
        ago1h = m1[-61][4] if len(m1) > 60 else (h1[-2][4] if len(h1) > 1 else None)
        ago24 = h1[-25][4] if len(h1) > 24 else None
        d = dens_by.get(sym) or {}
        spark = [round(c[4], 8) for c in h1[-48:]]
        rows.append({
            "symbol": sym, "coin": sym.removesuffix("USDT"),
            "price": price,
            "ch1h": _pct(price, ago1h) if ago1h else None,
            "ch24h": _pct(price, ago24) if ago24 else None,
            "volume": (uni.get("volumes") or {}).get(sym),
            "natr": _natr(h1),
            "trend": tr, "overall": tr_all,
            "dens_pct": d.get("nearest_pct"), "dens_usd": d.get("nearest_notional"),
            "dens_n": d.get("n_densities"),
            "imbalance": d.get("imbalance"),
            "forms": _dedupe(sorted(forms.get(sym, []), key=lambda f: (f["age"], f["tf"]))),
            "spark": spark,
        })
    conn.close()
    now = datetime.now().astimezone()
    return {
        "meta": {
            "built_at": now.strftime("%Y-%m-%d %H:%M:%S МСК"),
            "built_unix": int(time.time()),
            "market": "Binance USDT-M фьючерсы",
            "universe": uni.get("min_quote_volume"),
            "trend_rule": f"EMA{FAST}/EMA{SLOW}: лонг — цена > EMA{FAST} > EMA{SLOW} и EMA{SLOW} растёт; шорт — зеркально; иначе флэт. Общий тренд — когда 1ч и 4ч совпадают.",
            "fresh": FRESH,
            "bots": [{"href": h, "title": n} for h, n, src in BOTS if src.exists()],
        },
        "rows": rows,
        "trends": trends,
        "charts": charts(struct),
        "forms": all_forms,
        "dens": [{"symbol": c["symbol"], "coin": c["symbol"].removesuffix("USDT"),
                  "side": d["side"], "price": d["price"], "dist": d.get("distance_pct"),
                  "notional": d.get("notional"), "k": d.get("k_median"),
                  "absorb": d.get("absorb_seconds"), "absorb_text": d.get("absorb_text"),
                  "t5": ((d.get("touches") or {}).get("5m") or {}).get("n"),
                  "t1h": ((d.get("touches") or {}).get("1h") or {}).get("n"),
                  "mid": c.get("mid")}
                 for c in dens.get("coins", []) for d in c.get("densities", [])],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None)
    ap.add_argument("--html", required=True)
    ap.add_argument("--universe", default=None)
    ap.add_argument("--template", default=str(TEMPLATE))
    a = ap.parse_args(argv)
    out = Path(a.html)
    data = build(a.db, out.parent, Path(a.universe) if a.universe else None)
    chart_data = data.pop("charts")
    tmpc = out.parent / "charts.json.tmp"
    dens_by_sym: dict[str, list] = {}
    for d in data["dens"]:
        dens_by_sym.setdefault(d["symbol"], []).append(
            {"side": d["side"], "price": d["price"], "notional": d["notional"]})
    tmpc.write_text(json.dumps({"built_unix": data["meta"]["built_unix"], "pairs": chart_data,
                                "dens": dens_by_sym},
                               separators=(",", ":")), encoding="utf-8")
    tmpc.replace(out.parent / "charts.json")
    page = Path(a.template).read_text(encoding="utf-8").replace(
        "__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    tmp = out.with_suffix(".tmp")
    tmp.write_text(page, encoding="utf-8")
    tmp.replace(out)
    trend_path = out.parent / "trend-now.json"
    trend_path.write_text(json.dumps({"built_at": data["meta"]["built_at"],
                                      "built_unix": data["meta"]["built_unix"],
                                      "rule": data["meta"]["trend_rule"],
                                      "coins": data["trends"]},
                                     ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"монет {len(data['rows'])} → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
