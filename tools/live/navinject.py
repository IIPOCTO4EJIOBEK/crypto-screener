"""Общее меню на всех страницах: копирует nav.js и ленту алертов в папку страниц
и вставляет `<script src="/nav.js">` в каждую страницу, где его ещё нет.

Страницы ботов пересобирает cron торгового контура раз в 2 минуты, поэтому
процесс свечей вызывает `ensure()` раз в несколько секунд — меню возвращается
сразу после пересборки.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
TAG = '<script src="/nav.js"></script>'
PAGES = ("index.html", "screener.html", "structures.html", "densities.html",
         "bots.html", "bot.html")
TRADE = Path("/opt/crypto-trade/data/trade")
_SEEN: dict[str, float] = {}                        # файл → время изменения, когда уже проверен


def inject(path: Path) -> bool:
    """Вставить меню перед последним </body>. True — файл изменён."""
    try:
        real = path.resolve()                       # bots.html и bot.html — ссылки на файлы торгового контура
        mt = real.stat().st_mtime
        if _SEEN.get(str(real)) == mt:              # не менялся с прошлой проверки — не читаем
            return False
        s = real.read_text(encoding="utf-8")
    except Exception:                               # noqa: BLE001
        return False
    if TAG in s or "</body>" not in s:
        _SEEN[str(real)] = mt
        return False
    i = s.rfind("</body>")
    s = s[:i] + TAG + "\n" + s[i:]
    tmp = real.with_name(real.name + ".navtmp")
    tmp.write_text(s, encoding="utf-8")
    os.replace(tmp, real)
    _SEEN[str(real)] = real.stat().st_mtime
    return True


def ensure(live_dir: Path) -> int:
    for name in ("nav.js", "alerts.html", "manual-levels.js", "manual-drag.js", "trade-overlay.js", "tradingview.html"):
        src, dst = HERE / name, live_dir / name
        try:
            if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
                shutil.copy2(src, dst)
        except Exception:                           # noqa: BLE001
            pass
    positions(live_dir)
    monitor = TRADE / 'monitor-status.json'
    if monitor.exists():
        try:
            tmp = live_dir / 'monitor-status.json.tmp'
            shutil.copyfile(monitor, tmp)
            os.replace(tmp, live_dir / 'monitor-status.json')
        except OSError:
            pass
    n = sum(inject(live_dir / p) for p in PAGES if (live_dir / p).exists())
    if TRADE.exists():
        n += sum(inject(p) for p in TRADE.glob("*/bot.html"))
    return n


def positions(live_dir: Path) -> int:
    """Открытые позиции ботов по скринеру → kl/positions.json (для графика главной)."""
    import json
    out = []
    trades = []
    if TRADE.exists():
        for st in TRADE.glob("screener-*/state.json"):
            try:
                d = json.loads(st.read_text(encoding="utf-8"))
            except Exception:                       # noqa: BLE001
                continue
            bot = st.parent.name.removeprefix("screener-")
            for p in (d.get("positions") or {}).values():
                if not isinstance(p, dict) or not p.get("symbol"):
                    continue
                out.append({k: p.get(k) for k in ("symbol", "tf", "side", "entry", "stop", "target", "risk0",
                                                   "opened_ms", "expires_ms", "title", "mark", "reasons", "key")} | {"bot": bot})
            try:
                data = json.loads((st.parent / "bot.json").read_text(encoding="utf-8"))
                trades.extend(dict(r, bot=bot, phase="open") for r in data.get("open", []))
                trades.extend(dict(r, bot=bot, phase="closed") for r in data.get("closed", [])[-50:])
            except (OSError, ValueError):
                pass
    path = live_dir / "kl" / "positions.json"
    try:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps({"positions": out, "trades": trades}, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:                               # noqa: BLE001
        pass
    return len(out)

