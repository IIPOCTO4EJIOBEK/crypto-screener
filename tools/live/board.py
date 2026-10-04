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


def _rvol(m15: list[tuple], n: int = 96) -> float | None:
    """Оборот последней закрытой 15м свечи к среднему за сутки до неё."""
    closed = m15[:-1]
    if len(closed) < 20:
        return None
    base = [c[5] for c in closed[-n - 1:-1] if c[5]]
    avg = sum(base) / len(base) if base else 0
    return round(closed[-1][5] / avg, 2) if avg else None


def _rets(h1: list[tuple], n: int = 48) -> dict[int, float]:
    out, closed = {}, h1[:-1][-n - 1:]
    for a, b in zip(closed, closed[1:]):
        if a[4]:
            out[b[0]] = b[4] / a[4] - 1
    return out


def _corr(a: dict[int, float], b: dict[int, float]) -> float | None:
    """Корреляция часовых доходностей по общим часам (Пирсон)."""
    ks = [k for k in a if k in b]
    if len(ks) < 24:
        return None
    xs, ys = [a[k] for k in ks], [b[k] for k in ks]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sx = sum((x - mx) ** 2 for x in xs) ** 0.5
    sy = sum((y - my) ** 2 for y in ys) ** 0.5
    return round(sxy / (sx * sy), 2) if sx and sy else None


def _adx(cands: list[tuple], n: int = 14) -> float | None:
    """ADX Уайлдера по закрытым свечам: сила тренда без учёта стороны."""
    cs = cands[:-1]
    if len(cs) < 3 * n:
        return None
    tr_s = pdm_s = mdm_s = 0.0
    dxs: list[float] = []
    adx = None
    for i in range(1, len(cs)):
        h, l, pc = cs[i][2], cs[i][3], cs[i - 1][4]
        up, dn = h - cs[i - 1][2], cs[i - 1][3] - l
        tr = max(h, pc) - min(l, pc)
        pdm = up if up > dn and up > 0 else 0.0
        mdm = dn if dn > up and dn > 0 else 0.0
        if i <= n:
            tr_s, pdm_s, mdm_s = tr_s + tr, pdm_s + pdm, mdm_s + mdm
            if i < n:
                continue
        else:
            tr_s += tr - tr_s / n
            pdm_s += pdm - pdm_s / n
            mdm_s += mdm - mdm_s / n
        if not tr_s:
            continue
        pdi, mdi = pdm_s / tr_s * 100, mdm_s / tr_s * 100
        dx = abs(pdi - mdi) / (pdi + mdi) * 100 if pdi + mdi else 0.0
        if adx is None:
            dxs.append(dx)
            if len(dxs) == n:
                adx = sum(dxs) / n
        else:
            adx = (adx * (n - 1) + dx) / n
    return round(adx, 1) if adx is not None else None


def _stats(live_dir: Path, max_age: float = 900) -> dict:
    """Рыночные метрики, которые держит процесс свечей (kl/stats.json)."""
    path = live_dir / "kl" / "stats.json"
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception:                               # noqa: BLE001
        return {}
    return d.get("coins", {}) if time.time() - d.get("at", 0) / 1000 < max_age else {}


def _near(px: float, hi: float | None, lo: float | None) -> dict:
    """Расстояние до хая и лоя в процентах от цены."""
    if not px or hi is None or lo is None:
        return {}
    return {"hi": round((hi - px) / px * 100, 2), "lo": round((px - lo) / px * 100, 2)}


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
                "triggered": bool(f.get("triggered")), "invalid": f.get("invalid"),
                "reasons": f.get("reasons", [])[:4],
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
    stats = _stats(live_dir)
    btc = _rets(_candles(conn, "BTCUSDT", "1h"))
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
        st = stats.get(sym) or {}
        near = [x for x in (d.get("densities") or []) if x.get("distance_pct") is not None]
        near = min(near, key=lambda x: abs(x["distance_pct"])) if near else {}
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
            "dens_px": near.get("price"), "dens_side": near.get("side"),
            "rvol": _rvol(m15), "adx": _adx(h1),
            "corr": 1.0 if sym == "BTCUSDT" else _corr(_rets(h1), btc),
            "tpm": st.get("tpm"), "tpm_avg": st.get("tpm_avg"),
            "day": _near(price, st.get("day_hi"), st.get("day_lo")),
            "week": _near(price, st.get("week_hi"), st.get("week_lo")),
            "funding": st.get("funding"), "oi": st.get("oi"),
            "oi1h": st.get("oi1h"), "oi24h": st.get("oi24h"),
            "ls": st.get("ls"), "cvd1h": st.get("cvd1h"),
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
            "watch": uni.get("watch") or [],
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
