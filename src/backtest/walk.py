"""Прогон формаций по истории: проверка, что детекторы вообще работают.

Это не проверка доходности стратегии. Смысл другой: детектор, который за
сотню исторических срезов не нашёл ни одной формации, — сломан, а не
«рынок спокойный». Поэтому первый вопрос к прогону: сколько раз каждая
формация сработала. Второй — чем заканчивался вход: дошла ли цена до цели
раньше стопа.

Честные оговорки, без которых числа ниже читаются неверно:

  Заглядывания в будущее нет: детектор видит только закрытые свечи среза
  candles[:i] — последнюю, ещё формирующуюся, отбрасывает _scan, — а
  результат считается по свечам, которых он не видел.

  Комиссии, фандинг и проскальзывание не учтены. На 1m-15m они съедают
  заметную часть движения — реальный результат хуже показанного.

  Если свеча задела и стоп, и цель, засчитывается стоп. Без тиковых
  данных порядок внутри свечи неизвестен, и в такую неопределённость
  честнее записать убыток.

  Пороги детекторов подобраны не на этом же отрезке: они заданы априори и
  взяты из источников (docs/research/03-formacii-i-otrabotki.md), а не
  подогнаны под результат.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from statistics import stdev

from src.analysis.formations import Formation, detect_all
from src.backtest import significance
from src.backtest.costs import Costs, cost_r
from src.data.archive import FundingRate
from src.data.market import Candle

HISTORY = 300     # сколько свечей видит детектор в каждом срезе
STEP = 5          # через сколько свечей делается следующий срез
HORIZON = 40      # сколько свечей даётся сделке до истечения


@dataclass
class Trade:
    formation: Formation
    outcome: str    # "target" | "stop" | "timeout"
    r: float        # результат в единицах риска: −1 это стоп
    bars: int       # через сколько свечей закрылась
    entry: float = 0.0
    stop: float = 0.0
    side: str = ""
    entry_ms: int = 0
    exit_ms: int = 0
    cost_r: float = 0.0    # издержки в единицах риска
    r_net: float = 0.0     # результат за вычетом издержек

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.formation.kind, self.formation.direction,
                self.formation.ts)

    @property
    def stop_pct(self) -> float:
        """Расстояние до стопа в долях цены — знаменатель издержек в R."""
        return abs(self.entry - self.stop) / self.entry if self.entry else 0.0


def simulate(formation: Formation, candles: list[Candle], start: int,
             horizon: int = HORIZON) -> Trade | None:
    """Просчитать одну сделку по свечам после момента входа.

    start — индекс свечи, с которой считается результат: та свеча, которой
    детектор ещё не видел. Цена входа (formation.entry) — уровень закрытия
    сигнальной свечи, взять её можно было в этот момент.
    """
    entry, stop = formation.entry, formation.stop
    if entry <= 0 or stop <= 0 or entry == stop:
        return None
    if not formation.targets:
        return None
    target = formation.targets[0]
    if target <= 0:
        return None
    long = formation.direction == "long"

    risk = abs(entry - stop)

    def make(outcome: str, r: float, bars: int) -> Trade:
        idx = min(start + bars - 1, len(candles) - 1)
        return Trade(formation, outcome, r, bars, entry=entry, stop=stop,
                     side=formation.direction, entry_ms=candles[start].ts,
                     exit_ms=candles[idx].ts)

    for k, c in enumerate(candles[start:start + horizon], start=1):
        if long:
            hit_stop = c.low <= stop
            hit_target = c.high >= target
        else:
            hit_stop = c.high >= stop
            hit_target = c.low <= target
        if hit_stop:      # стоп проверяется первым: см. оговорку выше
            return make("stop", -1.0, k)
        if hit_target:
            return make("target", abs(target - entry) / risk, k)

    last = candles[min(start + horizon, len(candles)) - 1]
    move = (last.close - entry) if long else (entry - last.close)
    return make("timeout", move / risk, horizon)


def walk(candles: list[Candle], tf: str, symbol: str, exchange: str,
         *, history: int = HISTORY, step: int = STEP,
         horizon: int = HORIZON, btc: list[Candle] | None = None,
         limit: int = 8, costs: Costs | None = None,
         funding: list[FundingRate] | None = None) -> list[Trade]:
    """Пройти историю срезами и собрать сделки по найденным формациям.

    Если переданы costs, у каждой сделки считается r_net — результат за
    вычетом комиссии, проскальзывания и (при наличии расписания) фандинга.
    Без costs r_net остаётся равным r: прогон без издержек не должен
    выглядеть как прогон с нулевыми издержками.
    """
    trades: list[Trade] = []
    # Ключ — как у Trade.key: формация, направление и время. Одного ts мало:
    # на одной свече срабатывает несколько детекторов, а detect_all отдаёт их
    # отсортированными по уверенности. Дедупликация по ts молча оставляла
    # самый уверенный и теряла остальные — то есть в измерение попадал отбор
    # по уверенности детектора, а не формация как таковая.
    seen: set[tuple[str, str, int]] = set()
    for i in range(max(history, 40), len(candles) - 1, step):
        sub = candles[:i]
        try:
            found = detect_all(sub, tf, symbol, exchange, btc=btc, limit=limit)
        except Exception:
            continue
        for f in found:
            key = (f.kind, f.direction, f.ts)
            if not f.triggered or key in seen:
                continue
            # Сигнальная свеча — последняя закрытая в срезе: _scan отбрасывает
            # формирующуюся свечу и отсчитывает возраст назад, поэтому при
            # age 0 сигнал стоит на i-2, при age 1 на i-3. Сделку считаем со
            # следующей за сигналом свечи — при age 0 это i-1; при age > 0
            # окно начинается на age свечей позже сигнала. Измерено
            # (docs/research/17, §2): это стоит около 0.01 R net на сделку с
            # возрастом, знак разницы между монетами не устойчив, итог
            # измерения меняется на 0.006 R. Вход — цена закрытия сигнальной
            # свечи, то есть при age > 0 она устарела на 1-2 свечи.
            tr = simulate(f, candles, i - 1, horizon)
            if tr is None:
                continue
            seen.add(key)
            if costs is not None:
                tr.cost_r = cost_r(tr.entry, tr.stop, tr.entry_ms, tr.exit_ms,
                                   tr.side, costs, funding)
                tr.r_net = tr.r - tr.cost_r
            else:
                tr.r_net = tr.r
            trades.append(tr)
    return trades


@dataclass
class Stats:
    kind: str
    tf: str
    n: int
    target: int
    stop: int
    timeout: int
    total_r: float
    total_r_net: float = 0.0
    total_cost: float = 0.0
    sd: float = 0.0        # разброс результата по сделкам, в R
    # Поправка на перекрытие сделок (src/backtest/significance.py): ошибка
    # среднего R net по кластерам пересекающихся сделок, число кластеров —
    # эффективный размер выборки — и p блочного бутстрэпа для сверки.
    se: float = 0.0
    n_eff: int = 0
    p_boot: float | None = None

    @property
    def win_rate(self) -> float:
        return self.target / self.n * 100 if self.n else 0.0

    @property
    def expectancy(self) -> float:
        return self.total_r / self.n if self.n else 0.0

    @property
    def expectancy_net(self) -> float:
        return self.total_r_net / self.n if self.n else 0.0

    @property
    def cost(self) -> float:
        return self.total_cost / self.n if self.n else 0.0


def rr_bucket(rr: float) -> str:
    if rr < 1.0:
        return "меньше 1"
    if rr < 1.5:
        return "1.0-1.4"
    if rr < 2.0:
        return "1.5-1.9"
    return "2.0 и выше"


def _build(groups: dict) -> list[Stats]:
    out = []
    for key, ts in groups.items():
        kind, tf = key if isinstance(key, tuple) else (key, "")
        nets = [t.r_net for t in ts]
        # разброс нужен для значимости среднего: без него нельзя отличить
        # ровный плюс от среднего, собранного из редких крупных выигрышей
        sd = stdev(nets) if len(nets) > 1 else 0.0
        # Сделки строки идут внахлёст — по восьми монетам сразу и при
        # удержании дольше шага среза, — и sd/√n считает их независимыми.
        # Кластер — связная группа пересекающихся по времени сделок.
        clusters = significance.overlap_clusters(
            [(t.entry_ms, t.exit_ms) for t in ts])
        se = significance.cluster_se(nets, clusters) or 0.0
        n_eff = len(set(clusters))
        p_boot = significance.cluster_bootstrap_p(nets, clusters)
        out.append(Stats(kind, tf, len(ts),
                         sum(1 for t in ts if t.outcome == "target"),
                         sum(1 for t in ts if t.outcome == "stop"),
                         sum(1 for t in ts if t.outcome == "timeout"),
                         sum(t.r for t in ts),
                         sum(t.r_net for t in ts),
                         sum(t.cost_r for t in ts),
                         sd, se, n_eff, p_boot))
    out.sort(key=lambda s: -s.n)
    return out


def report(trades: list[Trade]):
    """Разрезы: по формациям, по таймфреймам, по направлению, по R:R."""
    by_kind: dict[tuple[str, str], list[Trade]] = defaultdict(list)
    by_tf: dict[str, list[Trade]] = defaultdict(list)
    by_dir: dict[str, list[Trade]] = defaultdict(list)
    by_rr: dict[str, list[Trade]] = defaultdict(list)
    for t in trades:
        by_kind[(t.formation.kind, t.formation.tf)].append(t)
        by_tf[t.formation.tf].append(t)
        by_dir[t.formation.direction].append(t)
        by_rr[rr_bucket(t.formation.rr)].append(t)
    return (_build(by_kind), _build(by_tf), _build(by_dir), _build(by_rr))


def format_report(trades: list[Trade], title: str = "") -> str:
    kinds, tfs, dirs, rrs = report(trades)
    lines = []
    if title:
        lines.append(f"=== {title} ===")
    lines.append(f"сделок всего: {len(trades)}")
    lines.append("")
    lines.append("формация                 ТФ     сделок  цель  стоп  таймаут  "
                 "доля цели  средний R  издержки  средний R net  сумма R net")
    for s in kinds:
        lines.append(f"{s.kind:23} {s.tf:5} {s.n:7} {s.target:6} "
                     f"{s.stop:5} {s.timeout:8} {s.win_rate:9.1f}% "
                     f"{s.expectancy:+9.3f} {s.cost:9.3f} "
                     f"{s.expectancy_net:+13.3f} {s.total_r_net:+12.2f}")
    for head, group in (("таймфрейм", tfs), ("направление", dirs),
                        ("соотношение прибыль/риск", rrs)):
        lines.append("")
        lines.append(f"{head:26} сделок  цель  стоп  таймаут  доля цели  "
                     f"средний R  издержки  средний R net  сумма R net")
        for s in group:
            lines.append(f"{s.kind:26} {s.n:7} {s.target:6} {s.stop:5} "
                         f"{s.timeout:8} {s.win_rate:9.1f}% "
                         f"{s.expectancy:+9.3f} {s.cost:9.3f} "
                         f"{s.expectancy_net:+13.3f} {s.total_r_net:+12.2f}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    import time

    from src.data.market import ohlcv

    symbols = sys.argv[1:] or ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
    tfs = ("5m", "15m", "1h")
    all_trades: list[Trade] = []
    t0 = time.time()

    for sym in symbols:
        for tf in tfs:
            try:
                cs = ohlcv("binance", sym, tf, 1000)
                btc = ohlcv("binance", "BTCUSDT", tf, 1000)
            except Exception as e:
                print(f"{sym} {tf}: ошибка загрузки {type(e).__name__}")
                continue
            t = time.time()
            trades = walk(cs, tf, sym, "binance", btc=btc)
            all_trades.extend(trades)
            print(f"{sym} {tf}: {len(trades)} сделок за {time.time()-t:.1f} с")
    print()
    print(format_report(all_trades, f"прогон по {len(symbols)} монетам"))
    print(f"\nвсего времени: {time.time()-t0:.1f} с")
