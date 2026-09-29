"""Замер делистингов по парам, извлечённым ИЗ ТЕЛ анонсов.

В отличие от замера по заголовкам (delist_measure.py), здесь событие определено
точно: удаляется именно пара BASE/USDT, а не любая другая котировка.
Спот и margin считаются отдельно: у margin-делистинга спот-пара обычно
продолжает торговаться, это другое событие.
"""
import json, statistics as st, sys, time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "../..")
from src.data.market import ohlcv

T = json.load(open("delist_bodies_usdt_targets.json"))


def measure(tk, am):
    sym = tk + "USDT"
    try:
        s = ohlcv("binance", sym, "1h", 1000, end_ms=am + 96 * 3600_000)
    except Exception as e:
        return {"err": f"{type(e).__name__}"}
    if not s:
        return {"err": "пусто"}
    idx = [i for i, c in enumerate(s) if c.ts <= am]
    if not idx:
        return {"err": "история позже анонса"}
    base = idx[-1]
    pre = s[base].close
    if pre <= 0:
        return {"err": "нулевая цена"}
    o = {"n": len(s), "lag_h": round((s[base].ts + 3600_000 - am) / 3600_000, 2),
         "pre": pre, "end_after_am_h": round((s[-1].ts - am) / 3600_000, 1)}
    for h in (-72, -24, -4):
        if base + h >= 0:
            o[f"pre{h}"] = s[base + h].close / pre - 1
    for h in (1, 4, 24, 72):
        if base + h < len(s):
            o[f"post{h}"] = s[base + h].close / pre - 1
            o[f"comp{h}"] = True
            seg = s[base:base + h + 1]
            o[f"max{h}"] = max(x.high for x in seg) / pre - 1
            o[f"min{h}"] = min(x.low for x in seg) / pre - 1
        else:
            o[f"post{h}"] = None
    return o


def run(pairs, label):
    res = {}
    def work(it):
        tk, r = it
        return tk, measure(tk, r["am"])
    with ThreadPoolExecutor(max_workers=2) as ex:
        for tk, m in ex.map(work, pairs):
            res[tk] = m
            time.sleep(0.25)
    ok = {k: v for k, v in res.items() if "err" not in v}
    err = {k: v["err"] for k, v in res.items() if "err" in v}
    print(f"\n=== {label}: целей {len(pairs)}, измерено {len(ok)}, ошибок {len(err)} {dict(Counter(err.values()))}")
    bad = sorted(k for k, v in ok.items() if v["lag_h"] < -2)
    if bad:
        print(f"    серия обрывается до анонса: {len(bad)} -> {bad}")
        ok = {k: v for k, v in ok.items() if v["lag_h"] >= -2}
    json.dump(res, open(f"delist_bodies_{label}_measured.json", "w"),
              ensure_ascii=False, indent=1)
    return ok


def S(vals):
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    n = len(vals)
    q = st.quantiles(vals, n=4) if n >= 4 else [vals[0], st.median(vals), vals[-1]]
    k = max(1, int(n * .1))
    return (n, st.median(vals), st.fmean(vals),
            st.fmean(vals[k:n - k]) if n > 2 * k else st.fmean(vals),
            q[0], q[2], sum(1 for v in vals if v > 0) / n)


def show(ok, label):
    print(f"\n--- {label} (n={len(ok)}) ---")
    print(f"{'метрика':16}{'n':>5}{'медиана':>10}{'среднее':>10}{'усеч':>10}{'p25':>10}{'p75':>10}{'>0':>8}")
    for nm, k in [("цена до -72ч выше", "pre-72"), ("цена до -24ч выше", "pre-24"),
                  ("цена до -4ч выше", "pre-4"), ("+1ч", "post1"), ("+4ч", "post4"),
                  ("+24ч", "post24"), ("+72ч", "post72"),
                  ("макс +72ч", "max72"), ("мин +72ч", "min72")]:
        s = S([v.get(k) for v in ok.values()])
        print(f"{nm:16}{0:>5}  нет данных" if not s else
              f"{nm:16}{s[0]:>5}{s[1]*100:>9.1f}%{s[2]*100:>9.1f}%{s[3]*100:>9.1f}%"
              f"{s[4]*100:>9.1f}%{s[5]*100:>9.1f}%{s[6]*100:>7.1f}%")
    print("дожило до:", {h: sum(1 for v in ok.values() if v.get(f'comp{h}')) for h in (1, 4, 24, 72)}, "из", len(ok))
    for h in (4, 24, 72):
        sub = [v[f"post{h}"] for v in ok.values() if v.get(f"comp{h}")]
        if sub:
            neg = sum(1 for x in sub if x < 0)
            print(f"  ниже цены анонса на +{h}ч: {neg}/{len(sub)} = {neg/len(sub)*100:.1f}%")


for fam in ("spot", "margin"):
    pairs = sorted([(k, v) for k, v in T.items() if v["fam"] == fam], key=lambda x: x[1]["am"])
    if not pairs:
        continue
    ok = run(pairs, fam)
    show(ok, f"{fam.upper()} — удаление пары к USDT (по телам)")
