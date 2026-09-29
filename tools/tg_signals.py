"""Сигналы из публичных телеграм-каналов: сбор и независимая проверка.

Канал публикует сигнал со своей ценой входа, стопом и целями. Проверяем его
не по отчётам канала («цель взята ✅»), а по свечам биржи: считаем сами, дошла
ли цена до входа, что случилось раньше — стоп или первая цель, и какой
получился результат в единицах риска (R).

Почему нельзя верить отчётам канала. Пост об успехе пишется, когда успех уже
случился, и пишется только о нём: неудачные сигналы в ленте не всплывают.
Плюс цена входа и стоп часто остаются за платной подпиской — тогда сверить
нечего. Здесь берутся только те посты, где вход и хотя бы одна граница выхода
названы публично, а результат считается по свечам.

Два контроля. Первый — та же сделка обратным направлением: если рынок в этот
период рос, все лонги покажут плюс, и надо смотреть, добавляет ли канал что-то
к самому факту роста. Второй — тот же сигнал, поставленный неделей раньше,
чтобы отделить мастерство выбора момента от геометрии самой сделки.

Цена берётся из архива Binance (data.binance.vision) — тот же источник, что у
остального проекта; живой fapi.binance.com из нашего региона отвечает 451.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.data import archive

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "Chrome/120.0 Safari/537.36")
CORPUS = Path("data/tg")
ENTRY_WINDOW_HOURS = 48         # сколько ждём входа в названную зону
TIMEOUT_HOURS = 7 * 24          # сколько держим сделку после входа
CONTROL_SHIFT_DAYS = 7

POST = re.compile(
    r'tgme_widget_message_wrap.*?'
    r'data-post="[^/]+/(\d+)".*?'
    r'datetime="([^"]+)".*?'
    r'tgme_widget_message_text[^>]*>(.*?)</div>',
    re.S)
COIN = re.compile(r"\$?\b([A-Z0-9]{2,12})\s*/\s*(USDT|USDC|USD)\b")
DIRECTION = re.compile(r"(ЛОНГ|SHORT|ЛОНГ|ШОРТ|LONG)", re.I)
NUM = r"(\d+(?:[.,]\d+)?)"
ZONE = re.compile(r"(?:вход|entry)[^\d]{0,20}" + NUM + r"(?:\s*[-–—]\s*" + NUM + r")?", re.I)
STOP = re.compile(r"(?:стоп|stop|sl)[^\d]{0,20}" + NUM, re.I)
TARGETS = re.compile(r"(?:цел|тейк|target|take)[^\d]{0,20}((?:\d+(?:[.,]\d+)?[\s\-–—]*)+)", re.I)


# ---------------------------------------------------------------- сбор
def fetch(channel: str, before: int | None = None) -> str:
    url = f"https://t.me/s/{channel}"
    if before:
        url += f"?before={before}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.read().decode("utf-8", "replace")
    except Exception:                                  # noqa: BLE001
        return ""


def parse_posts(raw: str) -> list[dict]:
    out = []
    for msg_id, when, body in POST.findall(raw):
        text = html.unescape(re.sub(r"<br\s*/?>", "\n", body))
        text = html.unescape(re.sub(r"<[^>]+>", "", text)).strip()
        if not text:
            continue
        out.append({"id": int(msg_id), "ts": when, "text": text})
    return out


def collect(channel: str, pages: int) -> list[dict]:
    seen: dict[int, dict] = {}
    before = None
    for _ in range(pages):
        raw = fetch(channel, before)
        posts = parse_posts(raw)
        if not posts:
            break
        fresh = 0
        for p in posts:
            if p["id"] not in seen:
                seen[p["id"]] = p
                fresh += 1
        before = min(p["id"] for p in posts)
        if fresh == 0:
            break
    return sorted(seen.values(), key=lambda p: p["id"])


# ---------------------------------------------------------------- разбор
def _num(text: str) -> float:
    return float(text.replace(",", "").replace(" ", ""))


def parse_signal(text: str) -> dict | None:
    up = text.upper()
    coin = COIN.search(up)
    direction = DIRECTION.search(up)
    zone = ZONE.search(text)
    if not (coin and direction and zone):
        return None
    symbol = f"{coin.group(1)}{coin.group(2)}"
    side = "long" if direction.group(1).upper() in ("ЛОНГ", "LONG") else "short"
    lo = _num(zone.group(1))
    hi = _num(zone.group(2)) if zone.group(2) else lo
    if lo > hi:
        lo, hi = hi, lo
    stop_m = STOP.search(text)
    stop = _num(stop_m.group(1)) if stop_m else None
    targets: list[float] = []
    for tm in TARGETS.finditer(text):
        for piece in re.findall(r"\d+(?:[.,]\d+)?", tm.group(1)):
            value = _num(piece)
            if value > 0 and (not targets or abs(value - targets[0]) > 1e-12):
                targets.append(value)
    if not targets:
        targets = []
    return {"symbol": symbol, "side": side, "lo": lo, "hi": hi,
            "stop": stop, "targets": targets}


# ---------------------------------------------------------------- проверка
def _candles(symbol: str, start: datetime, end: datetime) -> list:
    series = archive.load_klines(symbol, "1m", start.date(), end.date(), workers=6)
    return [c for c in series.rows
            if start.timestamp() * 1000 - 60_000 <= c.ts
            <= end.timestamp() * 1000]


def _evaluate(sig: dict, candles: list, when: datetime) -> dict:
    """Исход сделки по свечам. Возвращает вид исхода и результат в R."""
    if not candles:
        return {"outcome": "нет данных", "r": None}
    fill = (sig["lo"] + sig["hi"]) / 2
    stop = sig["stop"]
    targets = sig["targets"]
    if stop is None or not targets:
        return {"outcome": "нечего считать", "r": None}

    long = sig["side"] == "long"
    risk = (fill - stop) if long else (stop - fill)
    if risk <= 0:
        return {"outcome": "стоп не с той стороны", "r": None}

    start_ms = int(when.timestamp() * 1000)
    entry_deadline = start_ms + ENTRY_WINDOW_HOURS * 3_600_000
    window = [c for c in candles if c.ts >= start_ms]
    filled = False
    for c in window:
        if c.ts > entry_deadline:
            break
        if c.low <= sig["hi"] and c.high >= sig["lo"]:
            filled = True
            break
    if not filled:
        return {"outcome": "цена не вошла", "r": None}

    fill_ms = c.ts
    deadline = fill_ms + TIMEOUT_HOURS * 3_600_000
    last_close = fill
    for c in window:
        if c.ts < fill_ms:
            continue
        last_close = c.close
        hit_stop = c.low <= stop if long else c.high >= stop
        hit_tp = c.high >= targets[0] if long else c.low <= targets[0]
        if hit_stop and hit_tp:
            return {"outcome": "стоп (в одной свече с целью)", "r": -1.0}
        if hit_stop:
            return {"outcome": "стоп", "r": -1.0}
        if hit_tp:
            gain = (targets[0] - fill) if long else (fill - targets[0])
            return {"outcome": "первая цель", "r": gain / risk}
        if c.ts >= deadline:
            tail = (last_close - fill) if long else (fill - last_close)
            return {"outcome": "истёк срок", "r": tail / risk}
    tail = (last_close - fill) if long else (fill - last_close)
    return {"outcome": "истёк срок", "r": tail / risk}


def _report(name: str, results: list[dict]) -> list[str]:
    done = [r for r in results if r["r"] is not None]
    skipped = len(results) - len(done)
    if not done:
        return [f"  {name:22} сделок нет (отсеяно {skipped})"]
    rs = [r["r"] for r in done]
    wins = [r for r in rs if r > 0]
    kinds: dict[str, int] = {}
    for r in results:
        kinds[r["outcome"]] = kinds.get(r["outcome"], 0) + 1
    tail = "  ".join(f"{k}:{v}" for k, v in sorted(kinds.items()))
    return [f"  {name:22} сделок {len(done):>3} (отсеяно {skipped:>3})  "
            f"плюсовых {len(wins) / len(done) * 100:5.1f}%  "
            f"сумма R {sum(rs):+8.2f}  средний R {sum(rs) / len(rs):+.3f}",
            f"  {'':22} {tail}"]


def main() -> None:
    ap = argparse.ArgumentParser(description="Сигналы из телеграм-каналов.")
    ap.add_argument("command", choices=("collect", "eval"))
    ap.add_argument("channels", nargs="*")
    ap.add_argument("--pages", type=int, default=40)
    ap.add_argument("--days", type=int, default=45)
    args = ap.parse_args()

    CORPUS.mkdir(parents=True, exist_ok=True)

    if args.command == "collect":
        for channel in args.channels:
            posts = collect(channel, args.pages)
            path = CORPUS / f"{channel}.jsonl"
            with path.open("w", encoding="utf-8") as fh:
                for p in posts:
                    fh.write(json.dumps(p, ensure_ascii=False) + "\n")
            signals = [p for p in posts if parse_signal(p["text"])]
            print(f"{channel:22} постов {len(posts):>4}  сигналов {len(signals):>4}  "
                  f"период {posts[0]['ts'][:10]}..{posts[-1]['ts'][:10]}" if posts
                  else f"{channel}: ничего не собрано", flush=True)
        return

    for channel in args.channels:
        path = CORPUS / f"{channel}.jsonl"
        if not path.exists():
            print(f"{channel}: нет корпуса, сначала collect")
            continue
        posts = [json.loads(line) for line in path.open(encoding="utf-8")]
        parsed = [(p, parse_signal(p["text"])) for p in posts]
        parsed = [(p, s) for p, s in parsed if s]
        print(f"\n=== {channel}: сигналов {len(parsed)} "
              f"из {len(posts)} постов ===")
        if not parsed:
            continue

        end = datetime.now(timezone.utc)
        start = end - timedelta(days=args.days)
        by_symbol: dict[str, list] = {}
        for post, sig in parsed:
            when = datetime.fromisoformat(post["ts"]).astimezone(timezone.utc)
            if when < start:
                continue
            by_symbol.setdefault(sig["symbol"], []).append((when, sig))

        plain, flipped, shifted = [], [], []
        for symbol, items in by_symbol.items():
            try:
                candles = _candles(symbol, start - timedelta(days=CONTROL_SHIFT_DAYS),
                                   end)
            except Exception as exc:                    # noqa: BLE001
                print(f"  {symbol}: свечи не загрузились — "
                      f"{type(exc).__name__}: {exc}")
                continue
            for when, sig in items:
                plain.append(_evaluate(sig, candles, when))
                other = dict(sig, side="short" if sig["side"] == "long" else "long")
                flipped.append(_evaluate(other, candles, when))
                back = when - timedelta(days=CONTROL_SHIFT_DAYS)
                shifted.append(_evaluate(sig, candles, back))
        for line in _report("как опубликовано", plain):
            print(line)
        for line in _report("обратное направление", flipped):
            print(line)
        for line in _report(f"тот же вход −{CONTROL_SHIFT_DAYS} дн", shifted):
            print(line)


if __name__ == "__main__":
    main()
