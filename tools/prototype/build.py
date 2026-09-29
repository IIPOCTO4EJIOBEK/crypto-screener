"""Сбор данных и сборка страницы прототипа скринера.

Запуск из корня проекта:

    cd /mnt/data/projects/crypto-exchange
    .venv/bin/python -m tools.prototype.build

Пишет рядом с собой два файла:

    data.json   — сырые собранные данные (для проверки глазами)
    index.html  — самодостаточная страница: JSON вшит в <script>, внешних
                  запросов нет, работает по file://

Ничего в src/ не меняется и не пишется: модули только вызываются.
Если срез не собрался (сеть, нет архива) — в JSON пишется null, а в список
gaps честная строка. Правдоподобные числа вместо данных не подставляются.
"""

from __future__ import annotations

import json
import time
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from src.analysis.formations import detect_all
from src.analysis.levels import horizontal_levels
from src.analysis.liquidity import (find_walls, imbalance, marginal_slices,
                                    wall_price)
from src.analysis.metrics import correlation, efficiency_ratio, natr, volume_splash
from src.data.archive import load_book_depth, load_klines, load_metrics
from src.data.market import ohlcv

SYMBOLS = (
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
    "ADAUSDT", "AVAXUSDT", "LINKUSDT", "TONUSDT", "LTCUSDT", "DOTUSDT",
)
TFS = ("5m", "15m", "1h")
CANDLES = 400
CHART_CANDLES = 120
LEVELS_MAX = 8
LIMIT = 12
BOOK_SYMBOLS = ("BTCUSDT", "ETHUSDT")
EXCHANGE = "binance"

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
TEMPLATE = HERE / "page.html"
DATA_JSON = HERE / "data.json"
INDEX_HTML = HERE / "index.html"
PLACEHOLDER = "__DATA_JSON__"


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def day_str(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


class Collector:
    """Сбор с честным учётом того, что не получилось."""

    def __init__(self) -> None:
        self.gaps: list[str] = []
        self.context: dict[str, dict] = {}
        self.formations: list[dict] = []
        self.levels: list[dict] = []
        self.charts: dict[str, list] = {}
        self.orderbook: list[dict | None] = []
        self.positioning: dict[str, dict] = {}
        self.pairs: list[str] = []
        self.btc: dict[str, list] = {}

    def gap(self, what: str, exc: Exception | None = None) -> None:
        text = what
        if exc is not None:
            text += f": {type(exc).__name__}: {str(exc)[:160]}"
        self.gaps.append(text)
        print(f"  !! {text}")

    # -- свечи ------------------------------------------------------------
    def fetch_btc(self) -> None:
        for tf in TFS:
            try:
                self.btc[tf] = ohlcv(EXCHANGE, "BTCUSDT", tf, CANDLES)
                print(f"  BTCUSDT {tf}: свечей {len(self.btc[tf])}")
            except Exception as e:
                self.gap(f"свечи BTCUSDT {tf}", e)

    def collect_pair(self, sym: str, tf: str) -> None:
        key = f"{sym}|{tf}"
        try:
            cands = (self.btc.get(tf) if sym == "BTCUSDT"
                     else ohlcv(EXCHANGE, sym, tf, CANDLES))
            if not cands:
                self.gap(f"свечи {sym} {tf}: пустой ответ")
                return
        except Exception as e:
            self.gap(f"свечи {sym} {tf}", e)
            return

        btc = self.btc.get(tf)
        self.pairs.append(key)
        self.charts[key] = [
            [c.ts, round(c.open, 10), round(c.high, 10),
             round(c.low, 10), round(c.close, 10)]
            for c in cands[-CHART_CANDLES:]
        ]

        corr = None
        if btc and len(btc) > 3 and sym != "BTCUSDT":
            try:
                corr = correlation(cands, btc)
            except Exception as e:
                self.gap(f"корреляция {sym}/{tf}", e)
        chg20 = None
        if len(cands) > 21 and cands[-21].close > 0:
            chg20 = (cands[-1].close - cands[-21].close) / cands[-21].close * 100

        def safe(name, fn):
            try:
                return fn()
            except Exception as e:
                self.gap(f"{name} {sym} {tf}", e)
                return None

        self.context[key] = {
            "symbol": sym, "tf": tf,
            "price": cands[-1].close,
            "last_ts": cands[-1].ts,
            "rvol": safe("volume_splash", lambda: volume_splash(cands, 20)),
            "natr": safe("natr", lambda: natr(cands, 14)),
            "er": safe("efficiency_ratio", lambda: efficiency_ratio(cands, 20)),
            "corr_btc": corr,
            "change20": chg20,
        }

        try:
            found = detect_all(cands, tf, sym, EXCHANGE, btc=btc, limit=LIMIT)
        except Exception as e:
            self.gap(f"detect_all {sym} {tf}", e)
            found = []
        for f in found:
            self.formations.append({
                "kind": f.kind, "title": f.title, "direction": f.direction,
                "symbol": f.symbol, "exchange": f.exchange, "tf": f.tf,
                "ts": f.ts, "price": f.price, "entry": f.entry, "stop": f.stop,
                "targets": [round(t, 10) for t in f.targets],
                "triggered": bool(f.triggered),
                "confidence": f.confidence,
                "reasons": list(f.reasons), "invalid": f.invalid,
                "style": f.style, "age_candles": f.age_candles,
                "risk_pct": round(f.risk_pct, 6), "rr": round(f.rr, 6),
                "reward_pct": round(f.reward_pct, 6),
            })

        lv = None
        try:
            lv = horizontal_levels(cands, tf, max_levels=LEVELS_MAX)
        except Exception as e:
            self.gap(f"horizontal_levels {sym} {tf}", e)
        for l in (lv or []):
            self.levels.append({
                "symbol": sym, "tf": tf, "price": round(l.price, 10),
                "kind": l.kind, "touches": l.touches,
                "broken": bool(l.broken),
                "last_ts": l.last_ts,
            })

    # -- стакан -----------------------------------------------------------
    def _price_at_snapshot(self, sym: str, day: date, snap_ts: int) -> float | None:
        """Цена 5-минутной свечи, в которую попал снимок.

        Сам bookDepth абсолютной цены не содержит — только проценты от
        середины. Поэтому середину берём из архива свечей: закрытие той
        5-минутной свечи, внутри которой лежит время снимка.
        """
        try:
            series = load_klines(sym, "5m", day, day, workers=4)
        except Exception as e:
            self.gap(f"свечи 5m за {day} для цены снимка {sym}", e)
            return None
        if not series.rows:
            self.gap(f"свечей 5m за {day} нет — цену снимка {sym} взять неоткуда")
            return None
        chosen = None
        for c in series.rows:
            if c.ts <= snap_ts < c.ts + 300_000:
                chosen = c
                break
        if chosen is None:
            chosen = min(series.rows, key=lambda c: abs(c.ts - snap_ts))
        return chosen.close

    def collect_book(self) -> None:
        start = date.today() - timedelta(days=1)
        for sym in BOOK_SYMBOLS:
            series = None
            day = start
            for back in range(4):
                day = start - timedelta(days=back)
                try:
                    series = load_book_depth(sym, day, day, workers=4)
                except Exception as e:
                    self.gap(f"стакан {sym} за {day}", e)
                    series = None
                if series is not None and series.rows:
                    break
                print(f"  {sym}: архива за {day} нет, пробую раньше")
                series = None
            if series is None or not series.rows:
                self.gap(f"стакан {sym}: в архиве за 4 дня нет ни одного файла bookDepth")
                self.orderbook.append(None)
                continue

            snap = series.rows[len(series.rows) // 2]  # один снимок, средний по времени
            try:
                slices = marginal_slices(snap)
                walls = find_walls(snap)
                imb = imbalance(snap, 1.0)
            except Exception as e:
                self.gap(f"разбор стакана {sym}", e)
                self.orderbook.append(None)
                continue

            wall_keys = {(w.side, w.lo_pct, w.hi_pct) for w in walls}
            mid = self._price_at_snapshot(sym, day, snap.ts)
            top = walls[0] if walls else None
            self.orderbook.append({
                "symbol": sym, "day": str(day), "ts": snap.ts,
                "rows": len(series.rows), "loaded": series.loaded,
                "skipped": series.skipped,
                "bands": len(snap.bands),
                "imbalance_1pct": round(imb, 6),
                "walls": [{"side": w.side, "lo_pct": w.lo_pct, "hi_pct": w.hi_pct,
                           "notional": round(w.notional, 2)} for w in walls],
                "wall_price": (round(wall_price(top, mid), 10)
                               if (top and mid) else None),
                "mid": mid,
                "slices": sorted([{
                    "side": s.side, "lo_pct": s.lo_pct, "hi_pct": s.hi_pct,
                    "center_pct": round(s.center_pct, 4),
                    "distance_pct": round(s.distance_pct, 4),
                    "depth": round(s.depth, 6),
                    "notional": round(s.notional, 2),
                    "wall": (s.side, s.lo_pct, s.hi_pct) in wall_keys,
                } for s in slices], key=lambda s: s["center_pct"]),
            })
            print(f"  стакан {sym} {day}: снимков {len(series.rows)}, "
                  f"коридоров {len(slices)}, стенок {len(walls)}")

    # -- позиционирование -------------------------------------------------
    def collect_positioning(self) -> None:
        day = date.today() - timedelta(days=1)
        for sym in SYMBOLS:
            for back in range(4):
                d = day - timedelta(days=back)
                try:
                    s = load_metrics(sym, d, d, workers=4)
                except Exception as e:
                    self.gap(f"metrics {sym} за {d}", e)
                    break
                if s.rows:
                    last = s.rows[-1]
                    self.positioning[sym] = {
                        "day": str(d), "rows": len(s.rows),
                        "taker_long_short": last.sum_taker_long_short_vol_ratio,
                        "count_long_short": last.count_long_short_ratio,
                        "oi_value": last.sum_open_interest_value,
                        "last_ts": last.create_time,
                    }
                    break
            else:
                self.gap(f"metrics {sym}: в архиве за 4 дня строк нет")
        print(f"  позиционирование: символов с данными {len(self.positioning)}")


def build_data() -> dict:
    t0 = time.time()
    col = Collector()
    print("сбор свечей BTC (контекст)…")
    col.fetch_btc()
    print("сбор срезов монета×ТФ…")
    for sym in SYMBOLS:
        for tf in TFS:
            col.collect_pair(sym, tf)
    print(f"разборов собрано: {len(col.formations)}")
    print("сбор стакана (архив bookDepth)…")
    col.collect_book()
    print("сбор позиционирования (архив metrics)…")
    col.collect_positioning()
    dur = round(time.time() - t0, 1)

    by_kind: dict[str, int] = {}
    by_dir = {"long": 0, "short": 0}
    for f in col.formations:
        by_kind[f["kind"]] = by_kind.get(f["kind"], 0) + 1
        by_dir[f["direction"]] = by_dir.get(f["direction"], 0) + 1

    days = sorted({x["day"] for x in col.orderbook if x})
    return {
        "collected_at": now_utc(),
        "duration_sec": dur,
        "exchange": EXCHANGE,
        "repo": str(REPO),
        "candles": CANDLES,
        "symbols": list(SYMBOLS),
        "tfs": list(TFS),
        "pairs": col.pairs,
        "formations": col.formations,
        "context": col.context,
        "levels": col.levels,
        "charts": col.charts,
        "orderbook": col.orderbook,
        "positioning": col.positioning,
        "archive_day": days[0] if days else None,
        "counts": {
            "coins": len(SYMBOLS),
            "tfs": len(TFS),
            "pairs": len(col.pairs),
            "formations": len(col.formations),
            "triggered": sum(1 for f in col.formations if f["triggered"]),
            "levels": len(col.levels),
            "by_kind": by_kind,
            "by_direction": by_dir,
        },
        "gaps": col.gaps,
    }


def write_page(data: dict) -> None:
    if not TEMPLATE.exists():
        raise SystemExit(f"нет шаблона страницы: {TEMPLATE}")
    html = TEMPLATE.read_text(encoding="utf-8")
    if PLACEHOLDER not in html:
        raise SystemExit(f"в шаблоне нет метки {PLACEHOLDER}")
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    # JSON вшивается внутрь <script>, поэтому закрывающий тег надо погасить
    blob = blob.replace("</", "<\\/")
    INDEX_HTML.write_text(html.replace(PLACEHOLDER, blob), encoding="utf-8")


def main() -> None:
    print(f"=== прототип скринера: сбор {now_utc()} ===")
    try:
        data = build_data()
    except Exception:
        traceback.print_exc()
        raise SystemExit("сбор упал целиком — data.json и index.html не переписаны")
    DATA_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                         encoding="utf-8")
    write_page(data)
    c = data["counts"]
    size = INDEX_HTML.stat().st_size / 1024
    print(f"--- монет {c['coins']}, ТФ {c['tfs']}, срезов {c['pairs']}, "
          f"разборов {c['formations']} (готовых {c['triggered']}), "
          f"уровней {c['levels']}")
    print(f"--- типы: {c['by_kind']}")
    print(f"--- направления: {c['by_direction']}")
    if data["gaps"]:
        print(f"--- пробелов сбора: {len(data['gaps'])}")
        for g in data["gaps"]:
            print(f"    - {g}")
    print(f"--- {DATA_JSON} и {INDEX_HTML} ({size:.0f} КБ), "
          f"сбор занял {data['duration_sec']} с")


if __name__ == "__main__":
    main()
