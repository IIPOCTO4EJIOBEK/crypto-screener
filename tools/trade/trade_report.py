"""Сводка по бумажным ботам в Telegram — дважды в день (8:30 и 18:30 МСК).

Только чтение bot.json профилей в data/trade; ничего в ботах не меняет.
Окно сделок — от предыдущего слота отчёта до сейчас. Токен и чат — из
окружения (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID, EnvironmentFile службы).

python -m tools.trade.trade_report [--print] [--root data/trade]
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
from pathlib import Path

MSK = dt.timezone(dt.timedelta(hours=3))
SLOTS = ((8, 30), (18, 30))
SITE = "https://vpn.markus.tw1.su/bots.html"


def window_start(now: dt.datetime) -> dt.datetime:
    """Слот перед последним наступившим (с запасом 10 минут на запуск таймера)."""
    slots = sorted(dt.datetime.combine(now.date() + dt.timedelta(days=d), dt.time(h, m), MSK)
                   for d in (-2, -1, 0) for h, m in SLOTS)
    past = [s for s in slots if s <= now + dt.timedelta(minutes=10)]
    return past[-2]


def money(v: float) -> str:
    return f"{v:+,.2f}".replace(",", " ")


def num(v: float, nd: int = 0) -> str:
    return f"{v:,.{nd}f}".replace(",", " ")


def closes(folder: Path, d: dict, since_ms: int) -> list[dict]:
    """Закрытия из journal.jsonl (полные; в bot.json хранятся только последние 200)."""
    j = folder / "journal.jsonl"
    out = []
    if j.is_file():
        with j.open(encoding="utf-8") as fh:
            for line in fh:
                if '"close"' not in line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("kind") == "close" and (e.get("exit_ts") or e.get("ts") or 0) >= since_ms:
                    out.append({"r_net": e.get("r_net"), "pnl_usdt": e.get("pnl"), "exit_reason": e.get("reason")})
        return out
    return [{"r_net": c.get("r_net"), "pnl_usdt": (c.get("pnl_usdt") or 0) + (c.get("funding") or 0),
             "exit_reason": c.get("exit_reason")}
            for c in d.get("closed", []) if (c.get("closed_ms") or 0) >= since_ms]


def screener_block(name: str, d: dict, closed: list[dict], since_ms: int) -> str:
    opened = d.get("open", [])
    r = sum(c.get("r_net") or 0 for c in closed)
    pnl = sum(c.get("pnl_usdt") or 0 for c in closed)
    wins = sum(1 for c in closed if (c.get("pnl_usdt") or 0) > 0)
    stops = sum(1 for c in closed if c.get("exit_reason") == "stop")
    upnl = sum(o.get("pnl_usdt") or 0 for o in opened)
    longs = sum(1 for o in opened if o.get("side") == "long")
    new = sum(1 for o in opened if (o.get("opened_ms") or 0) >= since_ms)
    flags = " ⛔ HALT" if d.get("halted") else " ⏸ пауза" if d.get("paused") else ""
    lines = [f"<b>{html.escape(name)}</b>{flags}: капитал {num(d.get('equity') or 0)} USDT, "
             f"торговый итог с начала {money(d.get('trading_pnl') or 0)} USDT"]
    if closed:
        lines.append(f"  закрыто {len(closed)}: в плюс {wins}, по стопу {stops}, "
                     f"{r:+.2f}R, {money(pnl)} USDT")
    else:
        lines.append("  закрытых сделок нет")
    lines.append(f"  открыто {len(opened)} (лонг {longs}, шорт {len(opened) - longs}, новых {new}), "
                 f"плавающий {money(upnl)} USDT")
    return "\n".join(lines)


def daily_block(name: str, d: dict) -> str:
    eq, start = d.get("equity") or 0, d.get("start_equity") or 0
    ret = (eq / start - 1) * 100 if start else 0
    return (f"<b>{html.escape(name)}</b>: капитал {num(eq, 2)} ({ret:+.2f}% от {num(start)}), "
            f"позиций {len(d.get('positions') or [])}")


def build(root: Path, now: dt.datetime) -> str:
    start = window_start(now)
    since_ms = int(start.timestamp() * 1000)
    head = (f"📒 <b>Отчёт по бумажным ботам</b> {now:%d.%m %H:%M} МСК\n"
            f"Сделки за период с {start:%d.%m %H:%M}. Все профили бумажные.")
    scr, daily, tot_r, tot_pnl, tot_n, tot_open = [], [], 0.0, 0.0, 0, 0
    for p in sorted(root.iterdir()):
        f = p / "bot.json"
        if not f.is_file():
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            scr.append(f"<b>{html.escape(p.name)}</b>: не прочитан ({type(e).__name__})")
            continue
        if "closed" in d:
            name = p.name.removeprefix("screener-")
            cl = closes(p, d, since_ms)
            scr.append(screener_block(name, d, cl, since_ms))
            tot_n += len(cl)
            tot_r += sum(c.get("r_net") or 0 for c in cl)
            tot_pnl += sum(c.get("pnl_usdt") or 0 for c in cl)
            tot_open += len(d.get("open", []))
        else:
            daily.append(daily_block(p.name, d))
    parts = [head, f"Итого по скринер-профилям: закрыто {tot_n}, {tot_r:+.2f}R, {money(tot_pnl)} USDT; "
                   f"открыто {tot_open}. Профили независимы, сумма только для ориентира."]
    parts += scr
    if daily:
        parts.append("Дневные трендовые боты:\n" + "\n".join(daily))
    parts.append(f'<a href="{SITE}">Подробно на странице ботов</a>')
    return "\n\n".join(parts)


def send(text: str) -> None:
    import requests
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        raise SystemExit("нет TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID")
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=20,
                      json=dict(chat_id=chat, text=text[:4096], parse_mode="HTML", disable_web_page_preview=True))
    if r.status_code != 200 or not r.json().get("ok"):
        raise SystemExit(f"Telegram {r.status_code}: {r.text[:200]}")
    print("sent message_id", r.json()["result"]["message_id"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="data/trade")
    ap.add_argument("--print", action="store_true", help="только напечатать, не отправлять")
    a = ap.parse_args()
    text = build(Path(a.root), dt.datetime.now(MSK))
    print(text)
    if not a.print:
        send(text)


if __name__ == "__main__":
    main()
