"""Формации по таймфреймам: сводная таблица по всем прогонам сразу.

Каждый ТФ считался своим прогоном, и окно у каждого своё. Так вышло не от
небрежности, а из-за двух свойств измерения:

1. `cs = cs[-MAX_CANDLES:]` (src/backtest/expectancy.py:92) — ряд молча
   обрезается до последних 15 000 свечей. Для 5m и 15m это срабатывает, для
   1h/4h/1d — нет.
2. `walk` отдаёт детекторам весь префикс свечей на каждом срезе, поэтому
   работа растёт как свечи²/шаг (шаг — STEP_BY_TF). На 15 000 свечей 1h это
   ~72 минуты на монету, то есть ~10 часов на восемь монет: полный ряд 1h
   позволить нельзя. У 1h и 4h окно выбрано так, чтобы прогон влезал.

Отсюда честная оговорка: календарные отрезки у ТФ РАЗНЫЕ (52 / 90 / 180 /
365 / 1095 суток). Сравнение «равное число суток» между 5m и 1h тут
невозможно: 5m за 180 суток — это 51 840 свечей, вчетверо сверх потолка.

Правило выбора окна одно: самый длинный горизонт, чья работа (свечи²/шаг) не
превышает ~3 млн единиц, то есть ~50 минут прогона на восемь монет. Потолок
свечей связывает только 5m — он и так упирается в 15 000 свечей (52 суток).

Строки всех прогонов прогоняются через ОДИН вызов FDR: вопрос «есть ли
преимущество хоть где-то» — это один поиск по всей таблице, а не пять
независимых. Иначе порог значимости был бы впятеро мягче правды.

Как получены базы (драйвер — tools/live/refresh.py, монеты — восемь по
умолчанию, издержки и фандинг — по умолчанию):

    .venv/bin/python -m tools.live.refresh --db /tmp/tf-5m.db  --days 53   --tfs 5m
    .venv/bin/python -m tools.live.refresh --db /tmp/tf-15m.db --days 90   --tfs 15m
    .venv/bin/python -m tools.live.refresh --db /tmp/tf-1h.db  --days 180  --tfs 1h
    .venv/bin/python -m tools.live.refresh --db /tmp/tf-4h.db  --days 365  --tfs 4h
    .venv/bin/python -m tools.live.refresh --db /tmp/tf-1d.db  --days 1095 --tfs 1d --step 5
    .venv/bin/python -m tools.live.refresh --db /tmp/tf-1d-step1.db --days 1095 --tfs 1d

Последняя пара — не два таймфрейма, а одна и та же история суток при разном
шаге среза: --step 5 повторяет поведение до правки STEP_BY_TF, где у суток
ключа не было и брался фолбэк 5. Разница видна в строке liquidity_sweep:
−0.173 R net при шаге 5 против +0.115 при шаге 1 — смена знака.

Запуск: .venv/bin/python tools/tf_report.py [метка=путь ...]
"""
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.backtest import significance  # noqa: E402
from src.backtest.expectancy import MAX_CANDLES, MIN_TRADES  # noqa: E402

# метка, база, суток просили, свечей в сутках, секунд в свече, шаг среза
RUNS = [
    ("5m", "/tmp/tf-5m.db", 53, 288, 300, 36),
    ("15m", "/tmp/tf-15m.db", 90, 96, 900, 12),
    ("1h", "/tmp/tf-1h.db", 180, 24, 3600, 3),
    ("4h", "/tmp/tf-4h.db", 365, 6, 14400, 1),
    ("1d·шаг5", "/tmp/tf-1d.db", 1095, 1, 86400, 5),
    ("1d·шаг1", "/tmp/tf-1d-step1.db", 1095, 1, 86400, 1),
]
PROD = os.path.join(ROOT, "data", "screener.db")
# Измерено по факту: 3.1 млн единиц работы (~n²/2·шаг) — это ~47 минут на
# восемь монет, то есть 15 минут на миллион.
MIN_PER_MUNIT = 15.0


def _override(argv: list[str]) -> None:
    """Подменить пути к базам: «метка=путь». Нужно тому, кто пересобрал базы
    в другом месте и хочет сверить числа."""
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
    c.row_factory = sqlite3.Row
    # select *: у баз, измеренных до поправки на перекрытие, нет se и n_eff
    rows = [dict(r) for r in c.execute("select * from formation_stats")]
    c.close()
    return rows


def naive_p(r):
    """p по старой формуле — как будто сделки независимы. Только для сравнения."""
    return significance.p_value(r["exp_net"], r.get("sd") or 0.0, r["n"])


def fmt_p(p):
    return "—" if p is None else (f"{p:.2e}" if p < 1e-4 else f"{p:.4f}")


if __name__ == "__main__":
    _override(sys.argv[1:])

    rows = []
    for label, path, asked, per_day, sec, step in RUNS:
        try:
            part = load(path)
        except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
            print(f"{label}: база не читается ({e})")
            continue
        if not part:
            print(f"{label}: база пуста — прогон ещё идёт")
            continue
        candles = min(MAX_CANDLES, asked * per_day)
        work = candles * candles / (2.0 * step) / 1e6
        for r in part:
            r.update(src=label, asked=asked, candles=candles, step=step,
                     days=candles * sec / 86400.0, work=work,
                     evals=candles // step)
        rows += part

    if not rows:
        sys.exit("нет данных")

    order = sorted(rows, key=lambda r: -r["exp_net"])
    v = significance.judge(order, min_n=MIN_TRADES)
    for r, ok in zip(order, v.flags):
        r["sig"] = ok
    # тот же FDR, но по старым p — чтобы было видно, что отняла поправка
    vn = significance.judge([{**r, "se": 0, "n_eff": 0} for r in order],
                            min_n=MIN_TRADES)
    for r, ok in zip(order, vn.flags):
        r["sig_naive"] = ok

    print(f"потолок MAX_CANDLES = {MAX_CANDLES}, порог MIN_TRADES = {MIN_TRADES}, "
          f"FDR alpha = {v.alpha}")
    print(f"проверялось сравнений {v.n_tested}, из них с плюсом {v.n_positive}, "
          f"значимо после поправки {v.n_significant}, DSR лучшей "
          f"{'—' if v.dsr is None else f'{v.dsr:.3f}'}")
    print(f"без поправки на перекрытие сделок (как раньше) значимо было бы "
          f"{vn.n_significant}")
    if v.n_naive:
        print(f"ВНИМАНИЕ: у {v.n_naive} строк нет n_eff — базы измерены до "
              f"поправки, их p занижены; пересоберите базы через refresh")
    print()

    seen = []
    for label, *_ in RUNS:
        if label not in seen and any(r["src"] == label for r in rows):
            seen.append(label)
    print("Прогоны (окно у каждого своё — так решил потолок свечей и цена):")
    print(f"{'прогон':7} {'просили':>8} {'свечей':>7} {'это суток':>10} {'шаг':>4} "
          f"{'срезов':>7} {'работа, млн':>12} {'оценка, мин':>12} {'формаций':>9} "
          f"{'n≥30':>5} {'сделок':>7}")
    for label in seen:
        part = [r for r in rows if r["src"] == label]
        p0 = part[0]
        print(f"{label:7} {p0['asked']:>6} с {p0['candles']:>7} "
              f"{p0['days']:>9.1f} {p0['step']:>4} {p0['evals']:>7} "
              f"{p0['work']:>12.2f} {p0['work'] * MIN_PER_MUNIT:>12.0f} "
              f"{len(part):>9} "
              f"{sum(1 for r in part if r['n'] >= MIN_TRADES):>5} "
              f"{sum(r['n'] for r in part):>7}")

    print()
    print("Средний R net по прогону (только формации не короче порога):")
    print(f"{'прогон':7} {'формаций':>9} {'средн R net':>12} {'медиана':>9} "
          f"{'лучшая':>8} {'плюсовых':>9}")
    for label in seen:
        part = [r for r in rows if r["src"] == label and r["n"] >= MIN_TRADES]
        if not part:
            continue
        vals = sorted(r["exp_net"] for r in part)
        mid = vals[len(vals) // 2]
        print(f"{label:7} {len(part):>9} "
              f"{sum(vals) / len(vals):>+12.3f} {mid:>+9.3f} {vals[-1]:>+8.3f} "
              f"{sum(1 for x in vals if x > 0):>9}")

    print()
    kinds = sorted({r["kind"] for r in rows},
                   key=lambda k: -max((r["exp_net"] for r in rows if r["kind"] == k),
                                      default=0))
    print("R net после издержек: формация × прогон "
          "(«·N» — сделок меньше порога, «—» — не считалась)")
    print(f"{'формация':26}" + "".join(f"{s:>10}" for s in seen))
    for k in kinds:
        line = f"{k:26}"
        for label in seen:
            r = next((x for x in rows if x["kind"] == k and x["src"] == label), None)
            if r is None:
                line += f"{'—':>10}"
            elif r["n"] < MIN_TRADES:
                line += f"{'·' + str(r['n']):>10}"
            else:
                line += f"{r['exp_net']:>+10.3f}"
        print(line)

    print()
    print(f"Строки не короче {MIN_TRADES} сделок, по убыванию R net:")
    print("p iid — старая формула (сделки независимы); p — с поправкой на "
          "перекрытие (кластеры, n_eff − 1 ст. св.); p бутстр — блочный "
          "бутстрэп по тем же кластерам.")
    print(f"{'формация':26} {'прогон':7} {'сделок':>7} {'n_eff':>6} {'win %':>6} "
          f"{'R gross':>8} {'издерж':>8} {'R net':>8} {'p iid':>9} {'FDR':>4} "
          f"{'p':>9} {'p бутстр':>9} {'FDR':>5}")
    for r in order:
        if r["n"] < MIN_TRADES:
            continue
        mark = "—" if r["sig"] is None else ("да" if r["sig"] else "нет")
        mn = "—" if r["sig_naive"] is None else ("да" if r["sig_naive"] else "нет")
        print(f"{r['kind']:26} {r['src']:7} {r['n']:7} {r.get('n_eff') or 0:6} "
              f"{r['win_rate']:6.1f} "
              f"{r['exp_gross']:+8.3f} {r['cost']:8.3f} {r['exp_net']:+8.3f} "
              f"{fmt_p(naive_p(r)):>9} {mn:>4} {fmt_p(significance.row_p(r)):>9} "
              f"{fmt_p(r.get('p_boot')):>9} {mark:>5}")

    print()
    print("Для сравнения — боевая таблица скринера (та же база, что кормит "
          "страницы):")
    try:
        prod = load(PROD)
    except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
        print(f"  боевая база не читается: {e}")
        prod = []
    if prod:
        porder = sorted(prod, key=lambda r: -r["exp_net"])
        pv = significance.judge(porder, min_n=MIN_TRADES)
        for r, ok in zip(porder, pv.flags):
            r["sig"] = ok
        print(f"  строк {len(prod)}, проверялось {pv.n_tested}, "
              f"значимо {pv.n_significant}, с плюсом {pv.n_positive}")
        print(f"  {'формация':26} {'ТФ':4} {'сделок':>7} {'n_eff':>6} "
              f"{'R gross':>8} {'R net':>8} {'p iid':>9} {'p':>9} {'FDR':>5}")
        for r in porder:
            if r["n"] < MIN_TRADES:
                continue
            mark = "—" if r["sig"] is None else ("да" if r["sig"] else "нет")
            print(f"  {r['kind']:26} {r['tf']:4} {r['n']:7} "
                  f"{r.get('n_eff') or 0:6} "
                  f"{r['exp_gross']:+8.3f} {r['exp_net']:+8.3f} "
                  f"{fmt_p(naive_p(r)):>9} {fmt_p(significance.row_p(r)):>9} "
                  f"{mark:>5}")

    print()
    sig_pos = [r for r in order if r["sig"] and r["exp_net"] > 0]
    print(f"Значимых после FDR {sum(1 for r in order if r['sig'])}, "
          f"из них с плюсом {len(sig_pos)}:")
    for r in sig_pos:
        print(f"  + {r['kind']} {r['src']}: R net {r['exp_net']:+.3f} "
              f"на {r['n']} сделках ({r.get('n_eff') or '?'} независимых "
              f"событий), окно {r['days']:.0f} суток")
    lost = [r for r in order if r["sig_naive"] and not r["sig"]]
    if lost:
        print("Значимость, которую дало только перекрытие сделок (без "
              "поправки — «да», с поправкой — «нет»):")
        for r in lost:
            print(f"  − {r['kind']} {r['src']}: R net {r['exp_net']:+.3f}, "
                  f"n={r['n']}, n_eff={r.get('n_eff')}, p iid "
                  f"{fmt_p(naive_p(r))} → p {fmt_p(significance.row_p(r))}")
