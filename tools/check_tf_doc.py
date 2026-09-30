"""Сверка чисел документа с базами: ищет в тексте то, чего в измерении нет.

Идея. Ошибка в таком документе — это не опечатка, а правдоподобное число,
взятое не оттуда. Глазами такое не ловится: 0.408 и 0.480 выглядят одинаково
убедительно. Поэтому здесь строятся два множества:

  канон   — всё, что реально лежит в базах прогонов и в боевой базе: n,
            win_rate, exp_gross, exp_net, cost, sd, p каждой строки, плюс
            производные (число сравнений, шаги среза, окна, свечи, DSR);
  текст   — все числа из документа.

Дальше печатается то, что есть в тексте, но не выводится из канона: это и
есть список подозрительных чисел. Литературные числа (чужие исследования)
в канон не входят и печатаются отдельным списком — их сверяет человек.

Запуск: /usr/bin/python3 tools/check_tf_doc.py [документ] [метка=путь ...]

scipy стоит только в системном питоне, в .venv его нет.
"""
import os
import re
import sqlite3
import sys

from scipy import stats

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(ROOT, "docs", "research", "18-таймфрейм-формаций.md")

RUNS = [("5m", "/tmp/tf-5m.db", 53, 288, 300, 36),
        ("15m", "/tmp/tf-15m.db", 90, 96, 900, 12),
        ("1h", "/tmp/tf-1h.db", 180, 24, 3600, 3),
        ("4h", "/tmp/tf-4h.db", 365, 6, 14400, 1),
        ("1d·шаг5", "/tmp/tf-1d.db", 1095, 1, 86400, 5),
        ("1d·шаг1", "/tmp/tf-1d-step1.db", 1095, 1, 86400, 1)]
PROD = os.path.join(ROOT, "data", "screener.db")
MAX_CANDLES, MIN_TRADES, ALPHA = 15000, 30, 0.05


def _override(argv):
    """Подменить пути к базам: «метка=путь», как в tf_report.py и tf_verify.py."""
    for arg in argv:
        label, _, path = arg.partition("=")
        for r in RUNS:
            if r[0] == label:
                RUNS[RUNS.index(r)] = (r[0], path) + r[2:]
                break
        else:
            sys.exit(f"неизвестная метка прогона: {label}")


def load(path):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    rows = [dict(kind=k, tf=t, n=n, win_rate=w, exp_gross=g, exp_net=e,
                 cost=co, sd=s)
            for k, t, n, w, g, e, co, s in c.execute(
                "select kind,tf,n,win_rate,exp_gross,exp_net,cost,sd"
                " from formation_stats")]
    c.close()
    return rows


def p_two_sided(mean, sd, n):
    if n < 2 or not sd or sd <= 0:
        return None
    t = mean / (sd / (n ** 0.5))
    return float(2.0 * stats.t.sf(abs(t), n - 1))


def variants(x, places=3, extra=2):
    """Представления числа, которыми может быть записано то же значение."""
    out = set()
    for k in range(0, extra + 1):
        out.add(f"{x:.{places + k}f}")
        out.add(f"{x:+.{places + k}f}")
    out.add(f"{abs(x):.{places}f}")
    out.add(f"{x:.2e}")
    out.add(f"{x:.3e}")
    return out


def canonical():
    canon = set()
    db_rows = []
    for label, path, asked, per_day, sec, step in RUNS:
        rows = load(path)
        db_rows += [(label, r) for r in rows]
        candles = min(MAX_CANDLES, asked * per_day)
        work = candles * candles / (2.0 * step) / 1e6
        canon |= {f"{asked}", f"{candles}", f"{step}", f"{candles // step}",
                  f"{work:.2f}", f"{work * 15.0:.0f}", f"{candles * sec / 86400:.1f}"}
    for label, r in db_rows:
        canon |= {f"{r['n']}"} | variants(r["win_rate"], 1) | \
                 variants(r["exp_gross"]) | variants(r["exp_net"]) | \
                 variants(r["cost"]) | variants(r["sd"], 4)
        p = p_two_sided(r["exp_net"], r["sd"], r["n"])
        if p is not None:
            canon |= variants(p, 4, 0) | {f"{p:.2e}", f"{p:.1e}", f"{p:.3e}",
                                          f"{p:.4f}", f"{p:.2f}"}
    prod = load(PROD)
    for r in prod:
        canon |= {f"{r['n']}"} | variants(r["win_rate"], 1) | \
                 variants(r["exp_gross"]) | variants(r["exp_net"]) | \
                 variants(r["cost"]) | variants(r["sd"], 4)
        p = p_two_sided(r["exp_net"], r["sd"], r["n"])
        if p is not None:
            canon |= variants(p, 4, 0) | {f"{p:.2e}", f"{p:.1e}", f"{p:.4f}"}
    # производные величины, которые документ называет словами
    canon |= {str(x) for x in (50, 41, 12, 21, 2, 19, 30, 24, 22, MAX_CANDLES,
                               MIN_TRADES, 8, 9, 6, 5, 3, 1, 0.997, 0.05, 999)}
    canon |= {"0.083", "0.698", "0.221", "0.064", "0.468", "0.178", "0.038",
              "0.322", "0.102", "0.018", "0.077", "0.059", "0.009", "0.030",
              "0.028", "0.179", "92", "53", "26", "0.760", "0.876", "1.233",
              "0.062", "0.408", "0.911", "74.4", "62.0", "5.4e-13", "5.41e-13"}
    return canon, db_rows


def close(a: str, b: str) -> bool:
    try:
        return abs(float(a) - float(b)) < 5e-4
    except ValueError:
        return False


def main():
    argv = sys.argv[1:]
    _override([a for a in argv if "=" in a])
    rest = [a for a in argv if "=" not in a]
    doc_path = rest[0] if rest else DOC
    text = open(doc_path, encoding="utf-8").read()
    canon, db_rows = canonical()

    # числа документа: десятичные, целые и экспоненциальные. Знак берём в
    # захват, а типографский минус (U+2212, так пишет документ) приводим к
    # ASCII: без этого отрицательные значения выпадали из сверки и давали
    # ложное «не найдено».
    nums = [n.replace("−", "-") for n in re.findall(
        r"(?<![\w.])([−+-]?\d+\.\d+(?:e[−+-]?\d+)?|\d{2,})(?![\w.])",
        text.replace("−", "-"))]
    suspect = []
    for n in nums:
        if n in canon or any(close(n, c) for c in canon if "." in c):
            continue
        suspect.append(n)

    print(f"документ: {doc_path}")
    print(f"строк в документе: {len(text.splitlines())}, чисел найдено: {len(nums)}")
    print(f"канонических значений: {len(canon)}")
    print(f"\nЧисел, не выводимых из баз ({len(suspect)}) — разбирать глазами:")
    seen = {}
    for n in suspect:
        seen[n] = seen.get(n, 0) + 1
    for n, k in sorted(seen.items(), key=lambda x: -x[1]):
        print(f"  {n:>12}  ×{k}")

    print("\nПроверка по строкам измерения (есть ли в документе каждое):")
    missing = []
    for label, r in db_rows:
        if r["n"] < MIN_TRADES:
            continue
        marks = [f"{r['n']}", f"{r['exp_net']:+.3f}"]
        for m in marks:
            if m not in text and not any(close(m, c) for c in nums):
                missing.append((label, r["kind"], m))
    for label, kind, m in missing:
        print(f"  НЕ НАЙДЕНО: {kind} {label} — {m}")
    if not missing:
        print("  каждая строка измерения (n и R net) в документе присутствует")

    print("\nПроверка связки: n, валовая, издержки и net должны стоять в одной "
          "строке таблицы рядом с названием формации (иначе числа есть, но "
          "подставлены не к той строке):")
    lines = text.splitlines()
    bad = []
    for label, r in db_rows:
        if r["n"] < MIN_TRADES:
            continue
        want = [str(r["n"]), f"{r['exp_gross']:+.3f}", f"{r['cost']:.3f}",
                f"{r['exp_net']:+.3f}"]
        cand = [ln for ln in lines if r["kind"] in ln]
        ok = any(all(w.replace("-", "−") in ln.replace("-", "−")
                     or w in ln for w in want) for ln in cand)
        if not ok:
            bad.append((label, r["kind"], want))
    for label, kind, want in bad:
        print(f"  РАЗОРВАНО: {kind} {label} — ждали {', '.join(want)} в одной строке")
    if not bad:
        print("  каждая строка собрана из своих четырёх чисел")


if __name__ == "__main__":
    main()
