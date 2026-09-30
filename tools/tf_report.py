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
    cur = c.execute("select kind,tf,n,win_rate,exp_gross,exp_net,cost,sd"
                    " from formation_stats")
    rows = [dict(kind=k, tf=t, n=n, win_rate=w, exp_gross=g, exp_net=e,
                 cost=c_, sd=s) for k, t, n, w, g, e, c_, s in cur]
    c.close()
    return rows


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

    print(f"потолок MAX_CANDLES = {MAX_CANDLES}, порог MIN_TRADES = {MIN_TRADES}, "
          f"FDR alpha = {v.alpha}")
    print(f"проверялось сравнений {v.n_tested}, из них с плюсом {v.n_positive}, "
          f"значимо после поправки {v.n_significant}, DSR лучшей "
          f"{'—' if v.dsr is None else f'{v.dsr:.3f}'}")
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
    print(f"{'формация':26} {'прогон':7} {'сделок':>7} {'win %':>6} {'R gross':>8} "
          f"{'издерж':>8} {'R net':>8} {'p':>9} {'FDR':>5}")
    for r in order:
        if r["n"] < MIN_TRADES:
            continue
        p = significance.p_value(r["exp_net"], r.get("sd") or 0.0, r["n"])
        mark = "—" if r["sig"] is None else ("да" if r["sig"] else "нет")
        ps = "—" if p is None else (f"{p:.2e}" if p < 1e-4 else f"{p:.4f}")
        print(f"{r['kind']:26} {r['src']:7} {r['n']:7} {r['win_rate']:6.1f} "
              f"{r['exp_gross']:+8.3f} {r['cost']:8.3f} {r['exp_net']:+8.3f} "
              f"{ps:>9} {mark:>5}")

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
        print(f"  {'формация':26} {'ТФ':4} {'сделок':>7} {'R gross':>8} "
              f"{'R net':>8} {'p':>9} {'FDR':>5}")
        for r in porder:
            if r["n"] < MIN_TRADES:
                continue
            p = significance.p_value(r["exp_net"], r.get("sd") or 0.0, r["n"])
            mark = "—" if r["sig"] is None else ("да" if r["sig"] else "нет")
            ps = "—" if p is None else (f"{p:.2e}" if p < 1e-4 else f"{p:.4f}")
            print(f"  {r['kind']:26} {r['tf']:4} {r['n']:7} "
                  f"{r['exp_gross']:+8.3f} {r['exp_net']:+8.3f} {ps:>9} {mark:>5}")

    print()
    sig_pos = [r for r in order if r["sig"] and r["exp_net"] > 0]
    print(f"Значимых после FDR {sum(1 for r in order if r['sig'])}, "
          f"из них с плюсом {len(sig_pos)}:")
    for r in sig_pos:
        print(f"  + {r['kind']} {r['src']}: R net {r['exp_net']:+.3f} "
              f"на {r['n']} сделках, окно {r['days']:.0f} суток")
