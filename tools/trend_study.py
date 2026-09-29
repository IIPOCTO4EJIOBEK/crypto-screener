"""Измерение long-only тренд-фильтра на дневных барах.

Правило и его оговорки — в `src/backtest/trend.py`. Здесь: загрузка дневной
истории из архива, прогон конфигураций и контроль на «эффект — просто выход
в кэш».

Контроль устроен так. Правило держит позицию примерно половину времени, и
поэтому заведомо отличается от «всегда в рынке»: половину просадок оно
переждало в кэше. Чтобы отделить это от умения выбирать моменты, считается
случайный вход с той же плотностью — то есть портфель, который столько же
времени стоит вне рынка, но выбирает эти моменты броском монеты. Если
правило не отличается от такого контроля, оно не даёт ничего сверх
сокращения экспозиции.

Запуск:

    .venv/bin/python -m tools.trend_study                # базовая конфигурация
    .venv/bin/python -m tools.trend_study --grid         # сетка параметров
    .venv/bin/python -m tools.trend_study --funding      # с фандингом
    .venv/bin/python -m tools.trend_study --lag-sweep    # задержка исполнения

Флаги можно сочетать: прогон с `--funding --years --walk --grid` грузит
архив один раз и считает всё сразу. Повторные прогоны ускоряются кешем
(`--cache DIR`): бары и расписание фандинга восьми монет за шесть с половиной
лет — это сотни сетевых запросов к архиву, и каждый запуск без кеша платит
за них снова.
"""

from __future__ import annotations

import argparse
import json
import pickle
import random
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path

from src.backtest import significance
from src.backtest.costs import Costs
from src.backtest.trend import DAYS_PER_YEAR, Panel, Params, Result, run
from src.data import archive

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT",
           "DOGEUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT"]

FIRST_MONTH = "2020-01"     # первая полная история BTCUSDT в архиве
CONTROLS = 200              # сколько случайных входов считать для контроля

# Описание состава в отчёт. По умолчанию — список SYMBOLS выше, но вселенную
# можно задать иначе (например, составом на дату, §9 документа 16), и тогда
# оговорка на странице обязана это сказать: иначе страница противоречит
# собственным числам. Заодно здесь указывается, как состав обошёлся с монетами,
# снятыми с торгов, — это и есть главная оговорка к таким прогонам.
DEFAULT_UNIVERSE_NOTE = ("восемь крупнейших перпетуалов по объёму на 2026 год, "
                         "применённые к истории с 2020-го; монеты, снятые "
                         "с торгов, в этот состав не входят")


def _cache_file(cache: str | None, kind: str, symbols: list[str],
                first: str, last: str) -> Path | None:
    """Путь кеша для набора данных.

    В имени лежит всё, от чего зависит содержимое: символы, границы периода и
    вид данных. Кеш на то и кеш, что его можно удалить; но подсунуть в него
    другие данные он не даёт — ключ другой, и файл другой.
    """
    if not cache:
        return None
    key = "-".join(symbols)
    return Path(cache) / f"{kind}-{key}-{first}-{last}.pkl"


def load_panel(symbols: list[str], first: str, last: str, progress=print,
               cache: str | None = None) -> Panel:
    path = _cache_file(cache, "panel", symbols, first, last)
    if path and path.exists():
        with path.open("rb") as f:
            panel = pickle.load(f)
        progress(f"  панель из кеша: дней {len(panel.ts)}, "
                 f"монет {len(panel.bars)}")
        return panel
    series = {}
    for sym in symbols:
        s = archive.load_monthly_klines(sym, "1d", first, last, workers=8)
        if not s.rows:
            progress(f"  {sym}: нет данных")
            continue
        series[sym] = s.rows
        progress(f"  {sym:9} свечей {len(s.rows):5}  месяцев {s.loaded}"
                 f" (+{s.skipped} нет в архиве)")
    panel = Panel.build(series)
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as f:
            pickle.dump(panel, f)
    return panel


def load_funding(symbols: list[str], first: str, last: str,
                 cache: str | None = None, progress=print) -> dict:
    path = _cache_file(cache, "funding", symbols, first, last)
    if path and path.exists():
        with path.open("rb") as f:
            out = pickle.load(f)
        progress(f"  фандинг из кеша: монет {len(out)}, "
                 f"начислений {sum(len(v) for v in out.values())}")
        return out
    out = {}
    for sym in symbols:
        s = archive.load_funding(sym, first, last, workers=4)
        out[sym] = s.rows
        progress(f"  фандинг {sym:9} начислений {len(s.rows):5} "
                 f"месяцев {s.loaded} (+{s.skipped} нет в архиве)")
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as f:
            pickle.dump(out, f)
    return out


def line(name: str, r: Result) -> str:
    return (f"{name:28} {r.params.lookback:>3} {r.params.holding:>3} "
            f"{r.n_intervals:>6} {r.exposure:>7.3f} {r.in_market:>7.2f} "
            f"{r.turnover_year:>10.1f} {r.total * 100:>+9.1f} "
            f"{(r.cagr or 0) * 100:>+7.1f} {r.sharpe or 0:>7.2f} "
            f"{r.max_drawdown * 100:>7.1f}")


HEADER = (f"{'режим':28} {'L':>3} {'H':>3} {'интерв':>6} {'экспоз':>7} "
          f"{'в рынке':>7} {'оборот/год':>10} {'всего %':>9} {'CAGR %':>7} "
          f"{'Sharpe':>7} {'maxDD %':>7}")


GRID_LB = (7, 14, 28, 56, 112)
GRID_H = (1, 3, 5, 10, 20)


def grid_params() -> list[Params]:
    """Сетка «lookback × holding» без вырожденных пар (L ≤ H)."""
    return [Params(lookback=lb, holding=hd, mode="long")
            for lb in GRID_LB for hd in GRID_H if lb > hd]


def walk_forward(panel: Panel, *, costs: Costs, funding: dict | None,
                 first_oos: int, last_year: int, purge_days: int,
                 fixed: Params, progress=print) -> list[dict]:
    """Проверка на данных вне выбора параметров.

    На каждом шаге конфигурация выбирается по Sharpe на всей истории до года
    Y, затем считается на самом году Y, который в выбор не входил. Purge-gap
    нужен из-за удержания: последний интервал IS заканчивается на holding
    дней позже решения, и без разрыва его доходность заглядывала бы в OOS.

    Кроме выбранной конфигурации на том же году считаются ещё две величины:
    правило с параметрами из литературы (`fixed`) и равновзвешенный рынок.
    Без них таблица отвечает только на вопрос «работает ли подгонка», но не
    на вопрос «работает ли само правило»: подобранная конфигурация может
    провалиться, а фиксированная — устоять, и это разные выводы.
    """
    rows = []
    grid = grid_params()
    for year in range(first_oos, last_year + 1):
        until_is = (int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
                    - purge_days * 86_400_000)
        since_oos = int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        until_oos = int(datetime(year + 1, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        scored = []
        for p in grid:
            r = run(panel, p, costs=costs, funding=funding, until_ms=until_is)
            if r.sharpe is not None:
                scored.append((r.sharpe, p))
        if not scored:
            continue
        scored.sort(key=lambda t: -t[0])
        is_sharpe, best = scored[0]
        # медиана Sharpe по сетке: с ней сравнивается выбор лучшего на OOS
        mid = scored[len(scored) // 2][0]
        oos = run(panel, best, costs=costs, funding=funding,
                  since_ms=since_oos, until_ms=until_oos)
        fx = run(panel, fixed, costs=costs, funding=funding,
                 since_ms=since_oos, until_ms=until_oos)
        mkt = run(panel, Params(lookback=fixed.lookback, holding=fixed.holding,
                                mode="all"), costs=costs, funding=funding,
                  since_ms=since_oos, until_ms=until_oos)
        rows.append(dict(year=year, lookback=best.lookback,
                         holding=best.holding, is_sharpe=is_sharpe,
                         is_median=mid, oos_sharpe=oos.sharpe,
                         oos_total=oos.total, oos_dd=oos.max_drawdown,
                         oos_exposure=oos.exposure, n_oos=oos.n_intervals,
                         fixed_sharpe=fx.sharpe, fixed_total=fx.total,
                         market_sharpe=mkt.sharpe, market_total=mkt.total))
        progress(f"  {year}: выбрано L{best.lookback} H{best.holding}, "
                 f"IS Sharpe {is_sharpe:.2f} (медиана сетки {mid:.2f}), "
                 f"на году {oos.sharpe if oos.sharpe is not None else float('nan'):.2f}; "
                 f"фиксированное правило "
                 f"{fx.sharpe if fx.sharpe is not None else float('nan'):.2f}, "
                 f"рынок {mkt.sharpe if mkt.sharpe is not None else float('nan'):.2f}")
    return rows


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols", nargs="+", default=SYMBOLS)
    p.add_argument("--first", default=FIRST_MONTH, help="первый месяц, YYYY-MM")
    p.add_argument("--last", default=None,
                   help="последний месяц; по умолчанию — прошлый полный")
    p.add_argument("--lookback", type=int, default=28)
    p.add_argument("--holding", type=int, default=5)
    p.add_argument("--funding", action="store_true",
                   help="учитывать ставки финансирования (загрузка займёт время)")
    p.add_argument("--grid", action="store_true", help="сетка параметров")
    p.add_argument("--years", action="store_true", help="разбивка по годам")
    p.add_argument("--walk", action="store_true",
                   help="проверка вне выбора параметров (год за годом)")
    p.add_argument("--walk-first", type=int, default=2024,
                   help="первый год, считающийся вне выбора")
    p.add_argument("--purge", type=int, default=30,
                   help="разрыв между выбором и проверкой, дней")
    p.add_argument("--controls", type=int, default=CONTROLS)
    p.add_argument("--lag-sweep", nargs="*", type=int, default=None,
                   metavar="ДН",
                   help="перебор задержки исполнения: вход по открытию дня "
                        "i+лаг вместо i+1 (без значений — 1 2 3 5 10)")
    p.add_argument("--universe", default=DEFAULT_UNIVERSE_NOTE,
                   help="как выбран состав монет — эта строка попадает "
                        "в отчёт как оговорка (по умолчанию: восьмёрка "
                        "лидеров 2026 года)")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--json", default=None, help="куда сложить числа")
    p.add_argument("--cache", default=None, metavar="DIR",
                   help="каталог кеша загруженных баров и фандинга "
                        "(повторный запуск не ходит в архив)")
    a = p.parse_args()

    last = a.last
    if last is None:
        today = date.today()
        last = f"{today.year:04d}-{today.month - 1:02d}" if today.month > 1 \
            else f"{today.year - 1:04d}-12"
    print(f"дневные бары {a.first} … {last}, монет {len(a.symbols)}")
    panel = load_panel(a.symbols, a.first, last, cache=a.cache)
    costs = Costs(funding=a.funding)
    funding = None
    if a.funding:
        print("фандинг:")
        funding = load_funding(a.symbols, a.first, last, cache=a.cache)
    print(f"календарь {len(panel.ts)} дней, монет в панели {len(panel.bars)}, "
          f"издержки {costs.round_trip * 1e4:.1f} б.п. на круг\n")

    results: dict[str, Result] = {}
    extra: dict[str, object] = {}       # то, что не кривая: вердикты и таблицы

    def go(name: str, params: Params) -> Result:
        r = run(panel, params, costs=costs, funding=funding)
        results[name] = r
        return r

    base = Params(lookback=a.lookback, holding=a.holding, mode="long")

    print(HEADER)
    r = go("тренд long-only", base)
    print(line("тренд long-only", r))
    market = None
    for name, mode in (("рынок: всегда в позиции", "all"),
                       ("контр-тренд (наоборот)", "reverse"),
                       ("шорт по тому же правилу", "short"),
                       ("long-short", "longshort")):
        rr = go(name, Params(lookback=a.lookback, holding=a.holding, mode=mode))
        if mode == "all":
            market = rr
        print(line(name, rr))

    # тренд против рынка: два Sharpe на одних и тех же данных коррелированы,
    # и сравнивать их как независимые нельзя
    if market is not None and r.sharpe is not None and market.sharpe is not None:
        got = significance.sharpe_diff(r.returns, market.returns,
                                       DAYS_PER_YEAR / a.holding)
        if got:
            z, pval = got
            verdict = "значимо" if pval < 0.05 else "не значимо"
            print(f"\nтренд против рынка: Sharpe {r.sharpe:.2f} против "
                  f"{market.sharpe:.2f}, различие {verdict} (z {z:.2f}, "
                  f"p {pval:.3f})")
            extra["против рынка"] = dict(sharpe_trend=r.sharpe,
                                         sharpe_market=market.sharpe,
                                         z=z, p=pval,
                                         значимо=bool(pval < 0.05))
        else:
            print("\nтренд против рынка: ряды совпали или их слишком мало — "
                  "различие не определено")

    # случайный вход с той же плотностью: контроль на «эффект — это кэш»
    if a.controls:
        sharpes, curves = [], []
        density = r.exposure if r.exposure > 0 else 0.5
        for seed in range(a.controls):
            rc = run(panel, Params(lookback=a.lookback, holding=a.holding,
                                   mode="random", density=density, seed=seed),
                     costs=costs, funding=funding)
            if rc.sharpe is not None:
                sharpes.append(rc.sharpe)
                curves.append([v for _, v in rc.equity])
        if sharpes:
            order = sorted(range(len(sharpes)), key=lambda i: sharpes[i])
            sharpes = [sharpes[i] for i in order]
            curves = [curves[i] for i in order]
            share = sum(1 for s in sharpes if s >= (r.sharpe or 0)) / len(sharpes)
            print(f"\nконтроль: случайный вход с той же плотностью "
                  f"{density:.3f}, {len(sharpes)} прогонов")
            print(f"  Sharpe правила {r.sharpe:.2f}; у случайных медиана "
                  f"{sharpes[len(sharpes) // 2]:.2f}, "
                  f"5 % лучших от {sharpes[int(len(sharpes) * 0.95)]:.2f}, "
                  f"лучший {sharpes[-1]:.2f}")
            print(f"  доля случайных прогонов не хуже правила: {share:.1%}")
            # полосы контроля в кривых капитала: без них на графике случайный
            # вход не виден, а именно он отделяет правило от простого кэша
            if curves and curves[0]:
                n = min(len(c) for c in curves)
                for tag, q in (("5", 0.05), ("50", 0.50), ("95", 0.95)):
                    k = min(len(curves) - 1, int(len(curves) * q))
                    band = [(r.equity[i][0], curves[k][i]) for i in range(n)]
                    results[f"контроль {tag}%"] = Result(
                        params=Params(lookback=a.lookback, holding=a.holding,
                                      mode="random", density=density),
                        equity=band, returns=[], exposure=r.exposure,
                        in_market=r.in_market, turnover_year=0.0,
                        n_intervals=n)

    # разбивка по годам: без неё средний Sharpe не отличить от «эффект был
    # только в бычьем 2021-м». Прогрев — вся история до года, счёт — только
    # внутри года.
    if a.years:
        print(f"\nпо годам (L {a.lookback}, H {a.holding}, издержки "
              f"{costs.round_trip * 1e4:.1f} б.п. на круг)\n{HEADER}")
        rows = []
        base_year = int(a.first.split("-")[0])
        for year in range(base_year + 1, int(last.split("-")[0]) + 1):
            since = int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
            until = int(datetime(year + 1, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
            rr = run(panel, base, costs=costs, funding=funding,
                     since_ms=since, until_ms=until)
            mkt = run(panel, Params(lookback=a.lookback, holding=a.holding,
                                    mode="all"), costs=costs, funding=funding,
                      since_ms=since, until_ms=until)
            results[f"{year} тренд"] = rr
            results[f"{year} рынок"] = mkt
            print(line(f"{year} тренд", rr))
            print(line(f"{year} рынок", mkt))
            if rr.sharpe is not None and len(rr.returns) > 1:
                mean = sum(rr.returns) / len(rr.returns)
                sd = (sum((x - mean) ** 2 for x in rr.returns)
                      / (len(rr.returns) - 1)) ** 0.5
                rows.append(dict(kind="trend", tf=str(year), n=len(rr.returns),
                                 exp_net=mean, sd=sd))

    # проверка вне выбора параметров: конфигурация выбирается по прошлому,
    # считается на следующем годе
    if a.walk:
        print(f"\nпроверка вне выбора параметров: сетка {len(grid_params())} "
              f"конфигураций, выбор по всей истории до года, purge "
              f"{a.purge} дней")
        rows = walk_forward(panel, costs=costs, funding=funding,
                            first_oos=a.walk_first, last_year=int(last.split("-")[0]),
                            purge_days=a.purge, fixed=base)

        def sh(x) -> str:
            return f"{x:.2f}" if x is not None else "—"

        print(f"\n{'год':>5} {'выбрано':>10} {'IS Sharpe':>10} {'медиана':>8} "
              f"{'OOS выбор':>10} {'OOS 28/5':>9} {'OOS рынок':>10} "
              f"{'всего %':>9} {'maxDD %':>9}")
        for r in rows:
            print(f"{r['year']:>5} {'L%d H%d' % (r['lookback'], r['holding']):>10} "
                  f"{r['is_sharpe']:>10.2f} {r['is_median']:>8.2f} "
                  f"{sh(r['oos_sharpe']):>10} {sh(r['fixed_sharpe']):>9} "
                  f"{sh(r['market_sharpe']):>10} "
                  f"{r['oos_total'] * 100:>+9.1f} {r['oos_dd'] * 100:>9.1f}")
        if rows:
            def median(xs: list[float]) -> float:
                xs = sorted(xs)
                return xs[len(xs) // 2]

            oos_sh = [r["oos_sharpe"] for r in rows if r["oos_sharpe"] is not None]
            fx_sh = [r["fixed_sharpe"] for r in rows if r["fixed_sharpe"] is not None]
            mk_sh = [r["market_sharpe"] for r in rows if r["market_sharpe"] is not None]
            med = [r["is_median"] for r in rows]
            if oos_sh and fx_sh and mk_sh:
                print(f"\nмедиана Sharpe по годам вне выбора: выбор "
                      f"{median(oos_sh):.2f}, правило 28/5 {median(fx_sh):.2f}, "
                      f"рынок {median(mk_sh):.2f}")
                wins = sum(1 for r in rows
                           if r["fixed_sharpe"] is not None
                           and r["market_sharpe"] is not None
                           and r["fixed_sharpe"] > r["market_sharpe"])
                print(f"правило 28/5 обошло рынок в {wins} из {len(rows)} лет вне "
                      f"выбора")
            if med and oos_sh:
                print(f"медиана Sharpe по сетке на тех же IS-окнах: "
                      f"{sum(med) / len(med):.2f} (с ней и надо сравнивать: "
                      f"если выбор лучшего на OOS не лучше медианы, "
                      f"выбор параметров не работает)")
            extra["вне выбора"] = rows

    # сетка параметров и поправка на перебор
    if a.grid:
        rows = []
        print(f"\nсетка: lookback × holding\n{HEADER}")
        for lb in (7, 14, 28, 56, 112):
            for hd in (1, 3, 5, 10, 20):
                if lb <= hd:
                    continue
                rr = run(panel, Params(lookback=lb, holding=hd, mode="long"),
                         costs=costs, funding=funding)
                name = f"long L{lb} H{hd}"
                results[name] = rr
                print(line(name, rr))
                if rr.sharpe is not None and len(rr.returns) > 1:
                    mean = sum(rr.returns) / len(rr.returns)
                    sd = (sum((x - mean) ** 2 for x in rr.returns)
                          / (len(rr.returns) - 1)) ** 0.5
                    rows.append(dict(kind="trend", tf=f"L{lb}H{hd}",
                                     n=len(rr.returns), exp_net=mean, sd=sd))
        v = significance.judge(rows, min_n=30)
        print(f"\nсравнений {v.n_tested}, из них с плюсом {v.n_positive}; "
              f"после поправки на перебор (FDR {v.alpha:g}) значимых "
              f"{v.n_significant}")
        extra["сетка"] = dict(n_tested=v.n_tested, n_positive=v.n_positive,
                              n_significant=v.n_significant, alpha=v.alpha,
                              dsr=v.dsr,
                              порог_n=30)

    # время жизни сигнала: сигнал дня i исполняется на день i+lag. Рынок
    # считается с той же задержкой — иначе сравнивались бы разные окна, а не
    # разные сроки входа.
    if a.lag_sweep is not None:
        lags = a.lag_sweep or [1, 2, 3, 5, 10]
        print(f"\nвремя жизни сигнала: сигнал по закрытию дня i, вход по "
              f"открытию дня i+лаг (лаг 1 — по умолчанию)\n")
        print(f"{'лаг':>4} {'интерв':>7} {'экспоз':>7} {'оборот/год':>10} "
              f"{'всего %':>9} {'тренд':>7} {'рынок':>7} {'p':>7} {'различие':>10}")
        lag_rows = []
        for lag in lags:
            pl = Params(lookback=a.lookback, holding=a.holding, mode="long",
                        lag=lag)
            pm = Params(lookback=a.lookback, holding=a.holding, mode="all",
                        lag=lag)
            rl = run(panel, pl, costs=costs, funding=funding)
            rm = run(panel, pm, costs=costs, funding=funding)
            got = (significance.sharpe_diff(rl.returns, rm.returns,
                                            DAYS_PER_YEAR / a.holding)
                   if rl.sharpe is not None and rm.sharpe is not None else None)
            z, pval = got if got else (None, None)
            word = ("—" if pval is None else
                    ("значимо" if pval < 0.05 else "не значимо"))
            print(f"{lag:>4} {rl.n_intervals:>7} {rl.exposure:>7.3f} "
                  f"{rl.turnover_year:>10.1f} {rl.total * 100:>+9.1f} "
                  f"{(rl.sharpe or 0):>7.2f} {(rm.sharpe or 0):>7.2f} "
                  f"{(f'{pval:.3f}' if pval is not None else '—'):>7} {word:>10}")
            lag_rows.append(dict(lag=lag, n=rl.n_intervals, exposure=rl.exposure,
                                 in_market=rl.in_market,
                                 turnover=rl.turnover_year, total=rl.total,
                                 cagr=rl.cagr, sharpe=rl.sharpe,
                                 dd=rl.max_drawdown, market_sharpe=rm.sharpe,
                                 market_total=rm.total, z=z, p=pval,
                                 значимо=(None if pval is None else bool(pval < 0.05))))
        extra["задержка"] = dict(
            правило=f"L{a.lookback} H{a.holding}", строки=lag_rows,
            описание=("сигнал считается по закрытию дня i, вход — по открытию "
                      "дня i+лаг; лаг 1 — вход на следующее утро, лаг 2 и "
                      "больше — если решение принимается с промедлением"))

    if a.json:
        payload = {"прогоны": {name: {"params": asdict(rr.params),
                                      "sharpe": rr.sharpe, "cagr": rr.cagr,
                                      "total": rr.total,
                                      "max_drawdown": rr.max_drawdown,
                                      "exposure": rr.exposure,
                                      "in_market": rr.in_market,
                                      "turnover_year": rr.turnover_year,
                                      "n_intervals": rr.n_intervals,
                                      "equity": rr.equity}
                               for name, rr in results.items()},
                   "условия": {"символы": a.symbols, "первый месяц": a.first,
                               "последний месяц": last,
                               "вселенная": a.universe,
                               "издержки за круг": costs.round_trip,
                               "фандинг": bool(a.funding),
                               "lookback": a.lookback, "holding": a.holding,
                               "контролей": a.controls},
                   "вердикты": extra}
        with open(a.json, "w") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"\nчисла записаны в {a.json}")


if __name__ == "__main__":
    main()
