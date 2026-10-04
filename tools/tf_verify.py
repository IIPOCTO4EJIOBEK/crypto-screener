"""Независимая сверка чисел измерения: p и порог FDR считаются заново.

Зачем отдельно от `src/backtest/significance.py`. Все числа отчёта и страниц
идут через один модуль значимости, и ошибка в нём подтвердила бы сама себя:
перепроверять его тем же кодом бессмысленно. Здесь p берётся из
`scipy.stats.t` (двустороннее), а поправка Бенджамини-Хохберга написана по
определению: kmax — наибольший ранг, где p ≤ alpha · rank / m, отвергаются
все гипотезы до kmax включительно.

Совпадение с модулем проекта проверено на всех 41 строке шести прогонов и на
боевой таблице: p и метки значимости сошлись до последнего знака.

scipy стоит только в системном питоне, в .venv его нет, поэтому запускать
системным:

    /usr/bin/python3 tools/tf_verify.py [метка=путь ...]
"""
import os
import sqlite3
import sys

from scipy import stats

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RUNS = [
    ("5m", "/tmp/tf-5m.db"),
    ("15m", "/tmp/tf-15m.db"),
    ("1h", "/tmp/tf-1h.db"),
    ("4h", "/tmp/tf-4h.db"),
    ("1d·шаг5", "/tmp/tf-1d.db"),
    ("1d·шаг1", "/tmp/tf-1d-step1.db"),
]
PROD = os.path.join(ROOT, "data", "screener.db")
MIN_TRADES = 30
ALPHA = 0.05


def _override(argv):
    for arg in argv:
        label, _, path = arg.partition("=")
        for r in RUNS:
            if r[0] == label:
                RUNS[RUNS.index(r)] = (r[0], path)
                break
        else:
            sys.exit(f"неизвестная метка прогона: {label}")


def load(path):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    rows = [dict(r, db=path) for r in c.execute("select * from formation_stats")]
    c.close()
    return rows


def p_two_sided(mean, sd, n):
    if n < 2 or not sd or sd <= 0:
        return None
    t = mean / (sd / (n ** 0.5))
    return float(2.0 * stats.t.sf(abs(t), n - 1))


def p_row(r):
    """С поправкой на перекрытие, если база её содержит: t = mean / se,
    степеней свободы n_eff − 1. Иначе — старая формула."""
    se, g = r.get("se"), r.get("n_eff")
    if se and g:
        if g < 2 or se <= 0:
            return None
        return float(2.0 * stats.t.sf(abs(r["exp_net"] / se), g - 1))
    return p_two_sided(r["exp_net"], r["sd"], r["n"])


def bh(pvals, alpha=ALPHA):
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    kmax = 0
    for rank, idx in enumerate(order, start=1):
        if pvals[idx] <= alpha * rank / m and rank > kmax:
            kmax = rank
    return {order[r] for r in range(kmax)}


def verdict(rows, label):
    tested = [(i, r) for i, r in enumerate(rows)
              if r["n"] >= MIN_TRADES and r["sd"] and p_row(r) is not None]
    pv = [p_row(r) for _, r in tested]
    sig = bh(pv)
    print(f"=== {label}: строк {len(rows)}, в счёт {len(tested)}, "
          f"с плюсом {sum(1 for _, r in tested if r['exp_net'] > 0)}, "
          f"значимо {len(sig)}")
    for k, (i, r) in enumerate(tested):
        mark = "да" if k in sig else "нет"
        p = pv[k]
        ps = f"{p:.2e}" if p < 1e-4 else f"{p:.4f}"
        print(f"  {r['kind']:20} {r['tf']:4} n={r['n']:6} "
              f"gross={r['exp_gross']:+.4f} cost={r['cost']:.4f} "
              f"net={r['exp_net']:+.4f} sd={r['sd']:.4f} "
              f"n_eff={r.get('n_eff') or '—'} p={ps:>9} FDR={mark}")
    return len(sig)


def main():
    _override(sys.argv[1:])
    all_rows = []
    per_run = {}
    for label, path in RUNS:
        try:
            part = load(path)
        except sqlite3.Error as e:
            print(f"=== {label}: база не читается ({e})")
            continue
        if not part:
            print(f"=== {label}: база пуста")
            continue
        for r in part:
            r["src"] = label
        per_run[label] = part
        all_rows += part

    for label, _ in RUNS:
        if label in per_run:
            verdict(per_run[label], f"{label} — своя поправка (как в логе прогона)")

    print()
    verdict(all_rows, "общая поправка по всем прогонам")

    print()
    try:
        verdict(load(PROD), "боевая таблица screener.db")
    except sqlite3.Error as e:
        print(f"боевая база не читается: {e}")


if __name__ == "__main__":
    main()
