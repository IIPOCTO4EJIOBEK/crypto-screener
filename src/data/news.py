"""Новостной фон: заголовки крупных изданий и индекс страха и жадности.

Нужен не для того, чтобы «торговать по новостям», а чтобы понимать, чем
объясняется движение. Резкий ход цены на 30 % либо совпадает по времени с
новостью, либо нет; во втором случае это поток, а не событие, и искать причину
в ленте бессмысленно. Без такого слоя любой разбор движения упирается в догадки.

Ленты берутся в формате RSS/Atom: разбор идёт по тексту, без внешних
библиотек, потому что набор полей у всех изданий свой, а нужны из него только
заголовок, ссылка и время.
"""

from __future__ import annotations

import html
import json
import re
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "Chrome/120.0 Safari/537.36")
TIMEOUT = 20

FEEDS = (
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Decrypt", "https://decrypt.co/feed"),
    ("The Block", "https://www.theblock.co/rss.xml"),
    ("CryptoSlate", "https://cryptoslate.com/feed/"),
    ("ForkLog", "https://forklog.com/feed"),
    ("Incrypted", "https://incrypted.com/feed/"),
)
FEAR_GREED = "https://api.alternative.me/fng/?limit=1"

_ITEM = re.compile(r"<item[ >].*?</item>|<entry[ >].*?</entry>", re.S)


def _text(markup: str) -> str:
    """Убрать теги и CDATA, раскрыть сущности."""
    markup = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", markup, flags=re.S)
    return html.unescape(re.sub(r"<[^>]+>", "", markup)).strip()


def _field(item: str, *names: str) -> str:
    for name in names:
        m = re.search(rf"<{name}[^>]*>(.*?)</{name}>", item, re.S)
        if m and m.group(1).strip():
            return _text(m.group(1))
    m = re.search(r'<link[^>]*href="([^"]+)"', item)
    return m.group(1) if m else ""


def _when(item: str) -> str | None:
    for name in ("pubDate", "published", "updated", "dc:date"):
        raw = _field(item, name)
        if not raw:
            continue
        try:
            dt = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat(timespec="minutes")
    return None


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


def parse_feed(text: str, source: str, limit: int = 12) -> list[dict]:
    out = []
    for item in _ITEM.findall(text)[:limit]:
        title = _field(item, "title")
        if not title:
            continue
        out.append({"source": source, "title": title,
                    "link": _field(item, "link"), "at": _when(item)})
    return out


def headlines(limit_per_feed: int = 12) -> tuple[list[dict], list[str]]:
    """Свежие заголовки всех лент; вторым значением — что не открылось."""
    items: list[dict] = []
    broken: list[str] = []
    for source, url in FEEDS:
        try:
            items += parse_feed(_get(url), source, limit_per_feed)
        except Exception as exc:                        # noqa: BLE001
            broken.append(f"{source}: {type(exc).__name__}")
    items.sort(key=lambda x: x["at"] or "", reverse=True)
    return items, broken


def fear_greed() -> dict | None:
    """Индекс страха и жадности: 0 — паника, 100 — эйфория."""
    try:
        raw = json.loads(_get(FEAR_GREED))
        row = raw["data"][0]
        return {"value": int(row["value"]),
                "label": row["value_classification"],
                "at": datetime.fromtimestamp(
                    int(row["timestamp"]), timezone.utc).isoformat(timespec="minutes")}
    except Exception:                                   # noqa: BLE001
        return None


def snapshot(limit_per_feed: int = 12) -> dict:
    items, broken = headlines(limit_per_feed)
    return {"headlines": items, "broken": broken, "fear_greed": fear_greed()}
