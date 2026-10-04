"""Управление ботами из Telegram: статус, пауза, «закрыть всё».

Команды принимаются только из чата TELEGRAM_CHAT_ID (тот же, куда бот пишет):

    /status    капитал, результат и открытые позиции всех ботов
    /pause     остановить новые входы у ботов по скринеру (открытые ведутся дальше)
    /resume    снять паузу (и остановку по просадке, если была)
    /closeall  закрыть все позиции ботов по скринеру по рынку в ближайший круг
    /help      список команд

Опрос — один запрос getUpdates за круг бота, без отдельного процесса.
Все боты бумажные: команды меняют только их файлы в data/trade/.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

HELP = ("/status — капитал и позиции\n/pause — пауза новых входов\n"
        "/resume — снять паузу\n/closeall — закрыть всё по рынку\n/help — команды")


def poll(token: str, chat_id: str, offset_path: Path, get=None) -> list[str]:
    """Новые команды из своего чата; смещение хранится в файле, чтобы не повторять."""
    if get is None:
        import requests

        def get(url, params):
            return requests.get(url, params=params, timeout=15).json()
    offset = int(offset_path.read_text()) if offset_path.exists() else 0
    data = get(f"https://api.telegram.org/bot{token}/getUpdates",
               {"offset": offset, "timeout": 0})
    cmds = []
    for u in data.get("result", []):
        offset = max(offset, u["update_id"] + 1)
        msg = u.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != str(chat_id):
            continue                                   # чужой чат — игнор
        text = (msg.get("text") or "").strip().split("@")[0].split()
        if text and text[0].startswith("/"):
            cmds.append(text[0].lower())
    offset_path.write_text(str(offset))
    return cmds


def bot_dirs(trade_root: Path) -> list[Path]:
    return sorted(d for d in trade_root.iterdir()
                  if d.is_dir() and (d / "state.json").exists())


def status_text(trade_root: Path) -> str:
    lines = []
    for d in bot_dirs(trade_root):
        st = json.loads((d / "state.json").read_text())
        flags = " ⏸" if (d / "PAUSE").exists() else ""
        flags += " ⛔" if (d / "HALT").exists() else ""
        if "positions" in st and isinstance(next(iter(st["positions"].values()), {}), dict):
            # бот по скринеру
            pos = st["positions"].values()
            eq = st["cash"] + sum((1 if p["side"] == "long" else -1) * p["qty"]
                                  * ((p.get("mark") or p["entry"]) - p["entry"]) for p in pos)
            lines.append(f"{d.name}{flags}: {eq:.2f} USDT ({eq / st['start_equity'] - 1:+.2%}), "
                         f"открыто {len(st['positions'])}")
            for p in pos:
                lines.append(f"  {'Л' if p['side'] == 'long' else 'Ш'} {p['symbol']} {p['tf']} "
                             f"вход {p['entry']:.6g} стоп {p['stop']:.6g}")
        else:
            lines.append(f"{d.name}{flags}: позиций {len(st.get('positions') or {})}, "
                         f"старт {st.get('start_equity', 0):.2f}")
    return "\n".join(lines) or "ботов нет"


def handle(cmds: list[str], trade_root: Path) -> list[str]:
    """Выполнить команды; ответы — списком строк."""
    out = []
    screener = [d for d in bot_dirs(trade_root) if d.name.startswith("screener-")]
    for c in cmds:
        if c in ("/status", "/start"):
            out.append(status_text(trade_root))
        elif c == "/pause":
            for d in screener:
                (d / "PAUSE").write_text("пауза из Telegram")
            out.append(f"Пауза новых входов: {len(screener)} бот(ов). Открытые позиции ведутся.")
        elif c == "/resume":
            for d in screener:
                for f in ("PAUSE", "HALT"):
                    (d / f).unlink(missing_ok=True)
            out.append(f"Пауза снята: {len(screener)} бот(ов).")
        elif c == "/closeall":
            for d in screener:
                (d / "CLOSEALL").write_text("из Telegram")
            out.append("Закрою все позиции ботов по скринеру в ближайший круг.")
        else:
            out.append(HELP)
    return out


def run(trade_root: Path) -> None:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return
    from tools.trade.run import notify
    cmds = poll(token, chat, trade_root / "tg_offset")
    for text in handle(cmds, trade_root):
        notify(text)
