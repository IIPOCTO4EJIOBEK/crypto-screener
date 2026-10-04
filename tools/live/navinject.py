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


def inject(path: Path) -> bool:
    """Вставить меню перед последним </body>. True — файл изменён."""
    try:
        s = path.read_text(encoding="utf-8")
    except Exception:                               # noqa: BLE001
        return False
    if TAG in s or "</body>" not in s:
        return False
    i = s.rfind("</body>")
    s = s[:i] + TAG + "\n" + s[i:]
    real = path.resolve()                           # bots.html и bot.html — ссылки на файлы торгового контура
    tmp = real.with_name(real.name + ".navtmp")
    tmp.write_text(s, encoding="utf-8")
    os.replace(tmp, real)
    return True


def ensure(live_dir: Path) -> int:
    for name in ("nav.js", "alerts.html"):
        src, dst = HERE / name, live_dir / name
        try:
            if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
                shutil.copy2(src, dst)
        except Exception:                           # noqa: BLE001
            pass
    n = sum(inject(live_dir / p) for p in PAGES if (live_dir / p).exists())
    if TRADE.exists():
        n += sum(inject(p) for p in TRADE.glob("*/bot.html"))
    return n
