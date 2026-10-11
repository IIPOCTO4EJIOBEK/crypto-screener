"""Страница «Самообучение» (data/trade/learning.html) и строка для отчёта в Telegram.

Показывает, что бот сейчас берёт с урезанным риском и почему, что недавно
урезал или вернул к полному риску, последние входы с их риском и результат
профилей с самообучением до и после включения. Пишется после каждого
пересчёта модели (`src.trade.learning.refresh`), только чтение журналов.
"""

from __future__ import annotations

import html
import json
import os
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

MSK = timezone(timedelta(hours=3))
HISTORY_KEEP = 500


def _r(v) -> str:
    return "—" if v is None else f"{v:+.2f}"


def _t(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, MSK).strftime("%d.%m %H:%M")


def learn_profiles(trade_root: Path) -> list[str]:
    try:
        profiles = json.loads((Path(trade_root) / "screener-profiles.json").read_text(encoding="utf-8"))
    except Exception:                                       # noqa: BLE001
        return []
    return [p[p.index("--name") + 1] for p in profiles if "--learn" in p and "--name" in p]


def _journal(db: Path, kinds: tuple[str, ...]) -> list[dict]:
    if not db.exists():
        return []
    like = " OR ".join("payload LIKE ?" for _ in kinds)
    try:
        with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)) as c:
            rows = c.execute(f"SELECT payload FROM journal WHERE {like}", [f'%"kind": "{k}"%' for k in kinds]).fetchall()
    except sqlite3.Error:
        return []
    out = [json.loads(p) for (p,) in rows]
    return [r for r in out if r.get("kind") in kinds]


def record_changes(prev: dict | None, new: dict, path: Path) -> list[dict]:
    """Что урезано или возвращено к полному риску с прошлого пересчёта — в историю."""
    events = []
    if prev:
        for group, label in (("segments", "сетап"), ("coins", "монета")):
            old, cur = prev.get(group) or {}, new.get(group) or {}
            for name, v in cur.items():
                was = (old.get(name) or {}).get("mult", 1.0)
                if was != v["mult"]:
                    events.append({"ms": new["built_ms"], "group": label, "name": name, "from": was, "to": v["mult"],
                                   "n": v["n"], "mean_r": v["mean_r"]})
            for name, v in old.items():
                if name not in cur and v.get("mult", 1.0) < 1:      # выпал из окна — снова полный риск
                    events.append({"ms": new["built_ms"], "group": label, "name": name, "from": v["mult"], "to": 1.0,
                                   "n": 0, "mean_r": None})
    if events:
        try:
            hist = json.loads(path.read_text(encoding="utf-8"))
        except Exception:                                   # noqa: BLE001
            hist = []
        hist = (hist + events)[-HISTORY_KEEP:]
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(hist, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    return events


def stats(trade_root: Path) -> dict:
    """Входы и закрытия профилей с самообучением: до и после включения."""
    opens, closes = [], []
    for name in learn_profiles(trade_root):
        db = Path(trade_root) / f"screener-{name}" / "execution.db"
        for r in _journal(db, ("open", "close")):
            r["_profile"] = name
            (opens if r["kind"] == "open" else closes).append(r)
    learned = [r.get("ts", 0) for r in opens if "learn_mult" in r]
    enabled = min(learned) if learned else None
    mult = {(r["_profile"], r["key"]): r.get("learn_mult", 1.0) for r in opens}

    def agg(rows):
        net = sum((r.get("pnl") or 0) - (r.get("fee") or 0) - (r.get("funding") or 0) for r in rows)
        rn = [r["r_net"] for r in rows if r.get("r_net") is not None]
        return {"n": len(rows), "net": net, "mean_r": sum(rn) / len(rn) if rn else 0.0}

    before = [r for r in closes if enabled is None or (r.get("opened_ms") or 0) < enabled]
    after = [r for r in closes if enabled is not None and (r.get("opened_ms") or 0) >= enabled]
    cut = [r for r in after if mult.get((r["_profile"], r["key"]), 1.0) < 1]
    full = [r for r in after if mult.get((r["_profile"], r["key"]), 1.0) >= 1]
    days_before = ((enabled or time.time() * 1000) - min((r.get("opened_ms") or r.get("ts") or 0) for r in closes)) / 86_400_000 if closes else 0
    days_after = (time.time() * 1000 - enabled) / 86_400_000 if enabled else 0
    cut_net = agg(cut)["net"]
    return {"enabled": enabled, "before": agg(before), "after": agg(after), "cut": agg(cut), "full": agg(full),
            # при полном риске урезанные сделки дали бы примерно вчетверо больше (в обе стороны)
            "saved": -cut_net * 3,
            "days_before": days_before, "days_after": days_after,
            "recent": sorted(opens, key=lambda r: -r.get("ts", 0))[:30], "closes_after": after}


def line_for_report(trade_root: Path) -> str:
    """Одна-две строки для отчёта в Telegram."""
    try:
        model = json.loads((Path(trade_root) / "learning.json").read_text(encoding="utf-8"))
    except Exception:                                       # noqa: BLE001
        return ""
    s = stats(trade_root)
    seg = model.get("segments") or {}
    coins = model.get("coins") or {}
    cs = sum(v["mult"] < 1 for v in seg.values())
    cc = sum(v["mult"] < 1 for v in coins.values())
    a = s["after"]
    tail = (f"; после включения закрыто {a['n']}, {a['net']:+.2f} USDT, {a['mean_r']:+.2f}R на сделку"
            if s["enabled"] else "")
    return (f"🧠 <b>Самообучение</b>: урезано {cs} сетапов из {len(seg)} и {cc} монет из {len(coins)}{tail}. "
            f'<a href="https://vpn.markus.tw1.su/learning.html">Подробно</a>')


def build(trade_root: Path, model: dict, now_ms: int | None = None) -> str:
    trade_root = Path(trade_root)
    now_ms = now_ms or int(time.time() * 1000)
    s = stats(trade_root)
    try:
        hist = json.loads((trade_root / "learning-history.json").read_text(encoding="utf-8"))
    except Exception:                                       # noqa: BLE001
        hist = []
    e = html.escape
    seg = model.get("segments") or {}
    coins = model.get("coins") or {}

    def table(items, head):
        rows = "".join(f"<tr><td>{e(k)}</td><td>{v['n']}</td><td class='{'neg' if v['mean_r'] < 0 else 'pos'}'>"
                       f"{v['mean_r']:+.2f}</td><td>{v['shrunk_r']:+.2f}</td></tr>" for k, v in items)
        return (f"<table><tr><th>{head}</th><th>Сделок за 7 дней</th><th>Средний итог, R</th>"
                f"<th>С поправкой на малую выборку, R</th></tr>{rows or '<tr><td colspan=4>нет</td></tr>'}</table>")

    cut_seg = sorted(((k, v) for k, v in seg.items() if v["mult"] < 1), key=lambda kv: kv[1]["shrunk_r"])
    cut_coin = sorted(((k, v) for k, v in coins.items() if v["mult"] < 1), key=lambda kv: kv[1]["shrunk_r"])
    full_seg = sorted(((k, v) for k, v in seg.items() if v["mult"] >= 1 and v["n"] >= model.get("min_n", 30)),
                      key=lambda kv: -kv[1]["shrunk_r"])
    full_coin = sorted(((k, v) for k, v in coins.items() if v["mult"] >= 1 and v["n"] >= model.get("min_n_coin", 15)),
                       key=lambda kv: -kv[1]["shrunk_r"])
    changes = "".join(
        f"<tr><td>{_t(h['ms'])}</td><td>{e(h['group'])}</td><td>{e(h['name'])}</td>"
        f"<td class='{'neg' if h['to'] < 1 else 'pos'}'>{'урезан до 1/4 риска' if h['to'] < 1 else 'вернулся к полному риску'}</td>"
        f"<td>{h['n'] or '—'}</td><td>{_r(h['mean_r'])}</td></tr>"
        for h in reversed(hist[-60:]))
    recent = "".join(
        f"<tr><td>{_t(r['ts'])}</td><td>{e(r['_profile'])}</td><td>{e(r['symbol'])}</td><td>{e(r.get('formation') or '')}</td>"
        f"<td>{e(r.get('tf') or '')}</td><td>{'лонг' if r.get('side') == 'long' else 'шорт'}</td>"
        f"<td class='{'neg' if r.get('learn_mult', 1) < 1 else ''}'>"
        f"{'—' if 'learn_mult' not in r else ('1/4' if r['learn_mult'] < 1 else 'полный')}</td></tr>"
        for r in s["recent"])
    b, a, c, f = s["before"], s["after"], s["cut"], s["full"]
    enabled = _t(s["enabled"]) if s["enabled"] else "ещё не было входов"

    def card(title, x, days=None):
        per = f"<div class=sm>{x['n'] / days:.0f} сделок в день, {x['net'] / days:+.2f} USDT в день</div>" if days and days > 0.05 else ""
        return (f"<div class=card><div class=sm>{title}</div><div class=big>{x['net']:+.2f} USDT</div>"
                f"<div class=sm>{x['n']} сделок, {x['mean_r']:+.2f}R на сделку</div>{per}</div>")

    return f"""<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Самообучение ботов</title><meta http-equiv="refresh" content="300">
<style>body{{background:#111827;color:#e5e7eb;font:15px system-ui;margin:0 auto;padding:16px;max-width:1100px}}
a{{color:#93c5fd}}h2{{margin-top:28px}}table{{border-collapse:collapse;width:100%;font-size:14px}}
td,th{{padding:7px 9px;border-bottom:1px solid #374151;text-align:right}}td:first-child,th:first-child{{text-align:left}}
th{{color:#9ca3af;font-weight:500}}.neg{{color:#f87171}}.pos{{color:#4ade80}}.sm{{color:#9ca3af;font-size:13px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}}
.card{{background:#1f2937;border-radius:10px;padding:14px}}.big{{font-size:22px;font-weight:700;margin:4px 0}}
.wrap{{overflow-x:auto}}.grid2{{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:18px}}</style>
<h1>Самообучение ботов</h1>
<p>Последний пересчёт: <b>{_t(model.get('built_ms', now_ms))}</b> МСК (каждые 10 минут). Страница обновляется сама.
Профили с самообучением: {e(', '.join(learn_profiles(trade_root)))}. Все сделки бумажные.</p>
<p class=sm>Как работает: бот смотрит на свои закрытые сделки за {model.get('window_days', 7)} дней. Сетап (формация, ТФ, сторона)
с {model.get('min_n', 30)}+ сделками или монета с {model.get('min_n_coin', 15)}+ сделками, которые в среднем теряют
(хуже {model.get('block_r', -0.05):+.2f}R с поправкой на малую выборку), берутся с риском в 4 раза меньше. Так ошибка почти
ничего не стоит, но данные продолжают копиться. Когда сетап или монета снова зарабатывают, риск возвращается к полному сам.</p>
<h2>Результат</h2>
<p class=sm>Самообучение включено {enabled}. До включения — {s['days_before']:.1f} дн., после — {s['days_after']:.1f} дн.
Сравнение ориентировочное: рынок в эти дни разный.</p>
<div class=cards>{card('До включения', b, s['days_before'])}{card('После включения', a, s['days_after'])}
{card('После: сделки с урезанным риском', c)}{card('После: сделки с полным риском', f)}
<div class=card><div class=sm>Эффект урезания</div><div class="big {'pos' if s['saved'] >= 0 else 'neg'}">{s['saved']:+.2f} USDT</div>
<div class=sm>плюс — столько урезанные сделки потеряли бы сверх фактического при полном риске; минус — недополученная прибыль</div></div></div>
<div class=grid2>
<div><h2>Урезанные сетапы ({len(cut_seg)} из {len(seg)})</h2><div class=wrap>{table(cut_seg, 'Сетап')}</div></div>
<div><h2>Урезанные монеты ({len(cut_coin)} из {len(coins)})</h2><div class=wrap>{table(cut_coin, 'Монета')}</div></div>
<div><h2>Сетапы с полным риском</h2><div class=wrap>{table(full_seg, 'Сетап')}</div></div>
<div><h2>Монеты с полным риском</h2><div class=wrap>{table(full_coin, 'Монета')}</div></div></div>
<h2>Что менялось</h2><div class=wrap><table><tr><th>Когда</th><th>Что</th><th>Название</th><th>Решение</th><th>Сделок</th><th>Средний итог, R</th></tr>
{changes or '<tr><td colspan=6>изменений пока не было: история копится с этой версии</td></tr>'}</table></div>
<h2>Последние входы</h2><div class=wrap><table><tr><th>Время</th><th>Профиль</th><th>Монета</th><th>Формация</th><th>ТФ</th><th>Сторона</th><th>Риск</th></tr>
{recent}</table></div>
<p class=sm>Сформировано {_t(now_ms)} МСК. Модель: data/trade/learning.json, история: data/trade/learning-history.json.</p>
"""


def write(trade_root: Path, model: dict) -> Path:
    path = Path(trade_root) / "learning.html"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(build(trade_root, model), encoding="utf-8")
    os.replace(tmp, path)
    return path
