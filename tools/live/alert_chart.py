"""Картинка графика к алерту: свечи, объём и разметка события.

Рисуется в процессе свечей из того, что уже в памяти (kline биржи), плюс
уровни из `charts.json`. Тёмная тема как на главной.
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

BG, GRID, TXT = "#0f1115", "#1c212b", "#c7cdd8"
UP, DOWN, ACC, WARN, MUTE = "#1fbf75", "#ef4f5a", "#4c8dff", "#f0b429", "#8b93a3"
MSK = timezone(timedelta(hours=3))


def _fmt(p: float) -> str:
    a = abs(p)
    d = 1 if a >= 1000 else 2 if a >= 100 else 3 if a >= 1 else 5 if a >= 0.01 else 7
    return f"{p:,.{d}f}".replace(",", " ")


def render(sym: str, tf: str, cs: list[list], *, title: str = "", levels=(), dens=(),
           hlines=(), zone: dict | None = None, mark_t: int | None = None, n: int = 90,
           lines=(), hlv=()) -> bytes:
    """PNG. cs — [t, o, h, l, c, qv]; levels — [{price, kind}]; dens — [{price, side, notional}];
    hlines — [(price, color, label)]; zone — {entry, stop, target, t}; mark_t — время свечи события."""
    full = len(cs)
    cs = cs[-n:]
    off = full - len(cs)                # наклонные считаны по всей истории: сдвиг номеров свечей
    if len(cs) < 5:
        return b""
    lo = min(c[3] for c in cs)
    hi = max(c[2] for c in cs)
    extra = [l["price"] for l in levels] + [d["price"] for d in dens] + [h[0] for h in hlines]
    if zone:
        extra += [v for v in (zone.get("entry"), zone.get("stop"), zone.get("target")) if v is not None]
    rng = hi - lo or hi * 0.01
    keep = [p for p in extra if lo - rng * 0.6 <= p <= hi + rng * 0.6]   # далёкие уровни не сжимают свечи
    lo, hi = min([lo] + keep), max([hi] + keep)
    pad = (hi - lo) * 0.06

    fig = plt.figure(figsize=(10, 5.6), dpi=110, facecolor=BG)
    ax = fig.add_axes([0.05, 0.25, 0.80, 0.68], facecolor=BG)
    av = fig.add_axes([0.05, 0.07, 0.80, 0.16], facecolor=BG, sharex=ax)
    for a in (ax, av):
        a.tick_params(colors=TXT, labelsize=8)
        for s in a.spines.values():
            s.set_color(GRID)
        a.grid(color=GRID, linewidth=0.6)
    ax.yaxis.tick_right()
    av.set_yticks([])
    idx = {c[0]: i for i, c in enumerate(cs)}
    w = 0.65
    for i, (t, o, h, l, c, q) in enumerate(cs):
        col = UP if c >= o else DOWN
        ax.plot([i, i], [l, h], color=col, linewidth=0.8)
        ax.add_patch(Rectangle((i - w / 2, min(o, c)), w, max(abs(c - o), (hi - lo) * 0.001),
                               color=col, linewidth=0))
        av.bar(i, q, width=w, color=WARN if t == mark_t else col, alpha=0.9 if t == mark_t else 0.45)
    right = len(cs) + 6
    ax.set_xlim(-1, right)
    ax.set_ylim(lo - pad, hi + pad)

    def hline(p, col, label, style="--", lw=1.0):
        if not (lo - pad <= p <= hi + pad):
            return
        ax.axhline(p, color=col, linestyle=style, linewidth=lw, alpha=0.9)
        ax.text(right - 0.3, p, f" {label} {_fmt(p)}", color="#0f1115" if col.startswith("#e") else "white",
                fontsize=7.5, va="center", ha="right",
                bbox=dict(boxstyle="round,pad=0.2", fc=col, ec="none", alpha=0.85))

    for l in levels:
        hline(l["price"], UP if l.get("kind") == "support" else DOWN,
              "подд." if l.get("kind") == "support" else "сопр.", ":", 0.9)
    for d in dens:
        hline(d["price"], WARN, f"плотн. {'bid' if d['side'] == 'bid' else 'ask'} {d['notional'] / 1e3:.0f}к", "-", 1.6)
    for p, col, label in hlines:
        hline(p, col, label, "-", 1.4)
    if zone and zone.get("entry") is not None:
        x0 = idx.get(zone.get("t"), len(cs) - 1)
        e, st, tg = zone["entry"], zone.get("stop"), zone.get("target")
        if tg is not None:
            ax.add_patch(Rectangle((x0, min(e, tg)), right - x0, abs(tg - e), color=UP, alpha=0.13, linewidth=0))
            hline(tg, UP, "цель")
        if st is not None:
            ax.add_patch(Rectangle((x0, min(e, st)), right - x0, abs(st - e), color=DOWN, alpha=0.13, linewidth=0))
            hline(st, DOWN, "стоп")
        hline(e, ACC, "вход", "-", 1.4)
    for x in lines:
        i0 = max(x["a"]["i"] - off, 0)
        x0v = x["a"]["p"] + x["sl"] * (i0 + off - x["a"]["i"])
        x1 = len(cs) - 1 + 4
        ax.plot([i0, x1], [x0v, x0v + x["sl"] * (x1 - i0)], color="#ff7a1a" if x["t"] == "L" else "#ff5ca8", linewidth=1.6)
    for p in hlv:
        hline(p, "#e6e9ef", "уровень", "-", 0.9)
    if mark_t in idx:
        i = idx[mark_t]
        ax.annotate("", xy=(i, cs[i][3]), xytext=(i, cs[i][3] - (hi - lo) * 0.08),
                    arrowprops=dict(arrowstyle="-|>", color=WARN, lw=1.5))
    ticks = list(range(0, len(cs), max(1, len(cs) // 6)))
    av.set_xticks(ticks)
    av.set_xticklabels([datetime.fromtimestamp(cs[i][0] / 1000, MSK).strftime("%d.%m %H:%M") for i in ticks])
    plt.setp(ax.get_xticklabels(), visible=False)
    fig.text(0.05, 0.955, f"{sym.removesuffix('USDT')} / USDT · {tf}", color="white", fontsize=12, weight="bold")
    if title:
        fig.text(0.30, 0.955, title, color=TXT, fontsize=10)
    fig.text(0.86, 0.955, f"цена {_fmt(cs[-1][4])}", color=TXT, fontsize=10)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG)
    plt.close(fig)
    return buf.getvalue()


# --------------------------------------------------------------------------
# разбор: развороты, наклонные, уровни и два сценария, как в авторских обзорах
# (та же логика, что у разметки графика на главной)
# --------------------------------------------------------------------------
def _atr(cs: list[list], k: int = 100) -> float:
    k = min(k, len(cs) - 1)
    return sum(c[2] - c[3] for c in cs[-k:]) / k if k > 0 else 0.0


def zigzag(cs: list[list]) -> list[dict]:
    n = len(cs)
    if n < 30:
        return []
    thr = _atr(cs) * 3
    piv: list[dict] = []
    d, ext, hi_i, lo_i = 0, None, 0, 0
    for i in range(1, n):
        h, l = cs[i][2], cs[i][3]
        if d == 0:
            if h >= cs[hi_i][2]:
                hi_i = i
            if l <= cs[lo_i][3]:
                lo_i = i
            if cs[hi_i][2] - cs[lo_i][3] >= thr:
                if hi_i > lo_i:
                    piv.append({"i": lo_i, "p": cs[lo_i][3], "t": "L"}); d, ext = 1, (hi_i, cs[hi_i][2])
                else:
                    piv.append({"i": hi_i, "p": cs[hi_i][2], "t": "H"}); d, ext = -1, (lo_i, cs[lo_i][3])
        elif d == 1:
            if h >= ext[1]:
                ext = (i, h)
            elif ext[1] - l >= thr:
                piv.append({"i": ext[0], "p": ext[1], "t": "H"}); d, ext = -1, (i, l)
        else:
            if l <= ext[1]:
                ext = (i, l)
            elif h - ext[1] >= thr:
                piv.append({"i": ext[0], "p": ext[1], "t": "L"}); d, ext = 1, (i, h)
    if d:
        piv.append({"i": ext[0], "p": ext[1], "t": "H" if d == 1 else "L", "open": True})
    return piv


def trendlines(cs: list[list], piv: list[dict]) -> list[dict]:
    n, atr = len(cs), _atr(cs)
    if n < 30 or len(piv) < 3 or not atr:
        return []
    tol, out = atr * 0.35, []
    for t in ("L", "H"):
        ps = [q for q in piv if q["t"] == t and not q.get("open")][-8:]
        for a in range(len(ps)):
            for b in range(a + 1, len(ps)):
                A, B = ps[a], ps[b]
                if B["i"] - A["i"] < 5:
                    continue
                sl = (B["p"] - A["p"]) / (B["i"] - A["i"])
                if any((cs[i][4] < A["p"] + sl * (i - A["i"]) - tol) if t == "L"
                       else (cs[i][4] > A["p"] + sl * (i - A["i"]) + tol) for i in range(A["i"], n - 1)):
                    continue
                now = A["p"] + sl * (n - 1 - A["i"])
                if abs(now - cs[-1][4]) > atr * 12:
                    continue
                touch = sum(1 for q in ps if q["i"] >= A["i"] and abs(q["p"] - (A["p"] + sl * (q["i"] - A["i"]))) <= tol)
                out.append({"t": t, "a": A, "sl": sl, "now": now, "touch": touch,
                            "score": touch * 2 - abs(now - cs[-1][4]) / atr * 0.3 + B["i"] / n})
    pick: list[dict] = []
    for t in ("L", "H"):
        for x in sorted((x for x in out if x["t"] == t), key=lambda x: -x["score"]):
            if sum(1 for y in pick if y["t"] == t) >= 2:
                break
            if any(y["t"] == t and abs(y["now"] - x["now"]) < tol * 2 for y in pick):
                continue
            pick.append(x)
    return pick


def hlevels(cs: list[list], piv: list[dict]) -> list[float]:
    tol, cl = _atr(cs) * 0.6, []
    for q in piv:
        if q.get("open"):
            continue
        for c in cl:
            if abs(c[0] - q["p"]) <= tol:
                c[0] = (c[0] * c[1] + q["p"]) / (c[1] + 1); c[1] += 1
                break
        else:
            cl.append([q["p"], 1])
    return [c[0] for c in cl if c[1] >= 2]


def scenario(cs: list[list], lines: list[dict], levels: list[float], piv: list[dict]) -> str:
    """Два сценария: удержание поддержки и пробой её — с ближайшими целями."""
    p, gap = cs[-1][4], _atr(cs) * 1.5    # цели ближе полутора средних свечей — не цели
    sup = sorted([x["now"] for x in lines if x["t"] == "L" and x["now"] < p] + [l for l in levels if l < p], reverse=True)
    res = sorted([x["now"] for x in lines if x["t"] == "H" and x["now"] > p] + [l for l in levels if l > p])
    highs = sorted(q["p"] for q in piv if q["t"] == "H" and q["p"] > p)
    lows = sorted((q["p"] for q in piv if q["t"] == "L" and q["p"] < p), reverse=True)
    s = sup[0] if sup else (lows[0] if lows else None)
    up_t = next((x for x in res + highs if x > p + gap), None)
    down_t = next((x for x in sup[1:] + lows if s is not None and x < s - gap), None)
    def pc(x: float) -> str:                # проценты от текущей цены
        return f"{_fmt(x)} ({(x - p) / p * 100:+.2f}%)"
    out = []
    if s is not None:
        kind = "трендовой" if any(abs(x["now"] - s) < 1e-12 for x in lines) else "уровне"
        out.append(f"↗️ Если удержимся на {kind} {pc(s)}" + (f" — дорога к {pc(up_t)}." if up_t else " — продолжение вверх."))
        if down_t:
            out.append(f"↘️ Если пробьём {_fmt(s)} закрытием свечи — ждём снижения к {pc(down_t)}.")
    elif up_t:
        out.append(f"↗️ Сверху ближайшая цель {pc(up_t)}.")
    return "\n".join(out)


def trade13(cs: list[list], direction: str, dens=(), min_rr: float = 3.0) -> dict:
    """Сделка по тренду от текущей цены: стоп за ближайшей опорой (поддержкой для
    лонга, сопротивлением для шорта), цель — по `plan` с правилом 1:3."""
    if len(cs) < 30 or direction not in ("long", "short"):
        return {"ok": False, "why": "нет тренда"}
    p, atr = cs[-1][4], _atr(cs)
    piv = zigzag(cs)
    ls, lv = trendlines(cs, piv), hlevels(cs, piv)
    up = direction == "long"
    # стоп не ближе 1,2 средней свечи и 0,4% от цены: ближе его снимет шум, а комиссия съест R
    mind = max(atr * 1.2, p * 0.004)
    if up:
        sup = [x["now"] for x in ls if x["t"] == "L" and x["now"] < p] + [l for l in lv if l < p] + \
              [q["p"] for q in piv if q["t"] == "L" and q["p"] < p]
        base = max(sup) if sup else p - atr
        stop = min(base - atr * 0.3, p - mind)
    else:
        res = [x["now"] for x in ls if x["t"] == "H" and x["now"] > p] + [l for l in lv if l > p] + \
              [q["p"] for q in piv if q["t"] == "H" and q["p"] > p]
        base = min(res) if res else p + atr
        stop = max(base + atr * 0.3, p + mind)
    pl = plan(cs, p, stop, direction, dens=dens, min_rr=min_rr)
    return dict(pl, entry=p, stop=stop, direction=direction)


def trade13_text(t: dict) -> str:
    p = t.get("entry")
    if not p:
        return ""
    side = "лонг" if t["direction"] == "long" else "шорт"
    def pc(x: float) -> str:
        return f"{_fmt(x)} ({(x - p) / p * 100:+.2f}%)"
    if not t.get("ok"):
        return f"📐 1:3 по тренду ({side}): не набирается — {t.get('why', '')}."
    return (f"📐 1:3 по тренду: {side} от {_fmt(p)} · стоп {pc(t['stop'])} · цель {pc(t['target'])} "
            f"— {t['why']}, R:R 1:{t['rr']:.1f}")


def analysis(cs: list[list]) -> tuple[list[dict], list[float], str]:
    piv = zigzag(cs)
    ls, lv = trendlines(cs, piv), hlevels(cs, piv)
    return ls, lv, scenario(cs, ls, lv, piv)


def plan(cs: list[list], entry: float, stop: float, direction: str, dens=(), min_rr: float = 3.0) -> dict:
    """Цель сделки по тренду с учётом уровней: правило «минимум 1:3».

    Препятствия по ходу сделки — перехай/перелой, горизонтальные уровни,
    наклонные, Фибо-расширения последнего хода и крупные плотности. Если
    препятствие стоит ближе 3R — до цели цена, скорее всего, не дойдёт, сделки
    нет. Иначе цель — первое препятствие за 3R (или ровно 3R, если дальше
    ничего нет)."""
    risk = abs(entry - stop)
    if not risk or len(cs) < 30:
        return {"ok": False, "why": "мало данных"}
    up = direction != "short"
    s = 1 if up else -1
    piv = zigzag(cs)
    obs: list[tuple[float, str]] = []
    for q in piv[:-1]:
        if (up and q["t"] == "H") or (not up and q["t"] == "L"):
            obs.append((q["p"], "перехай" if up else "перелой"))
    for p in hlevels(cs, piv):
        obs.append((p, "уровень"))
    for x in trendlines(cs, piv):
        if (up and x["t"] == "H") or (not up and x["t"] == "L"):
            obs.append((x["now"], "трендовая"))
    if len(piv) >= 2:
        A, B = piv[-2], piv[-1]
        if (B["p"] > A["p"]) == up:
            for e in (1.272, 1.618):
                obs.append((A["p"] + (B["p"] - A["p"]) * e, f"фибо {e}"))
    for d in dens:
        if (up and d.get("side") == "ask") or (not up and d.get("side") == "bid"):
            obs.append((d["price"], "плотность"))
    ahead = sorted(((s * (p - entry) / risk, p, why) for p, why in obs if s * (p - entry) > risk * 0.3), key=lambda x: x[0])
    block = next((x for x in ahead if x[0] < min_rr), None)
    if block:
        return {"ok": False, "why": f"{block[2]} {_fmt(block[1])} на {block[0]:.1f}R — до 1:{min_rr:g} не пускает"}
    nxt = next((x for x in ahead if x[0] >= min_rr), None)
    if nxt:
        return {"ok": True, "target": nxt[1], "rr": round(nxt[0], 2), "why": nxt[2]}
    return {"ok": True, "target": entry + s * risk * min_rr, "rr": min_rr, "why": f"{min_rr:g}R, препятствий выше нет" if up else f"{min_rr:g}R, препятствий ниже нет"}
