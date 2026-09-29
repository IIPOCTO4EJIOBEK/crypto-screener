"""Long-only тренд-фильтр на дневных барах — измерение портфелем.

Зачем это здесь. Скринер измеряет формации на 5m-1h: вход и выход по стопу и
цели внутри дня. Единственная направленная стратегия, у которой в разборе
источников есть подтверждение после издержек, — не про это: это time-series
momentum на дневных барах, длинная сторона, удержание неделями (Han, Kang,
Ryu, ACFR WP: lookback 28 дней, удержание 5 дней, позиция 48 % времени,
Sharpe 1.51 против 0.85 у рынка, net 15 б.п.). Скринер её не измеряет вообще.

Правило, которое здесь считается (без вариаций «на глазок»):

  на закрытии дня t считается моментум за lookback дней — close(t)/close(t−L)−1;
  если он положителен, монета держится следующие holding дней, иначе не
  держится вовсе (кэш — не шорт: шорт-версия у Han et al. убыточна даже без
  издержек).

  Портфель равновзвешенный по монетам, у которых условие выполнено; если
  таких нет, капитал стоит в кэше и не даёт ничего. Решения принимаются
  каждые holding дней, поэтому интервалы удержания стыкуются: позиция,
  открытая на решении k, закрывается ровно в тот момент, когда открывается
  позиция решения k+1.

Чего здесь нет и что из-за этого нельзя читать как обещание:

  Исполнение. По умолчанию вход и выход — по открытию следующего дня после
  сигнала. Закрытие дня сигнала известно только в момент закрытия, и вход по
  нему был бы исполнением по цене, которой в этот момент уже нет. Режим
  `use_open=False` (вход по закрытию сигнального дня) оставлен для сверки:
  разница между ним и основным — цена допущения «торгуем на закрытии».

  Издержки считаются по обороту портфеля, а не по числу сделок: на каждом
  ребалансе платится за изменение веса каждой монеты, включая дрейф веса
  внутри удержанной позиции. Фандинг — по фактическим начислениям, если
  передан расписанием; по умолчанию выключен, и тогда длинная сторона
  перпетуала показана лучше, чем она есть (лонг платит положительный
  фандинг всегда).

  Одна монета из портфеля не выпадает: если по монете нет бара (ещё не
  листилась), она в этом решении не участвует, а веса оставшихся
  пересчитываются на их число. Это не «равный вес с нулём у отсутствующей».

Никакой подгонки параметров: lookback и holding задаются снаружи, а вердикт
о том, переживает ли результат перебор по сетке параметров, считается
отдельно (`src/backtest/significance.py`) — по всем конфигурациям сразу.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from src.backtest.costs import Costs, funding_between
from src.data.archive import FundingRate
from src.data.market import Candle

DAYS_PER_YEAR = 365.0


@dataclass(frozen=True)
class Params:
    """Настройки правила. Значения по умолчанию — из Han, Kang, Ryu."""

    lookback: int = 28
    holding: int = 5
    # long — в позиции, когда моментум положителен; short — когда отрицателен;
    # longshort — и там, и там; reverse — контр-тренд (знак перевёрнут);
    # all — держим всегда (это и есть рынок, равновзвешенный);
    # random — случайный вход с заданной плотностью (контроль).
    mode: str = "long"
    density: float | None = None   # плотность входа для mode="random"
    seed: int = 0
    # вход по открытию следующего дня (True) или по закрытию сигнального (False)
    use_open: bool = True
    # через сколько дней после сигнального дня исполняется вход. 1 —
    # поведение по умолчанию (открытие следующего дня); больше — проверка
    # времени жизни сигнала: если преимущество держится только при входе на
    # следующий день, руками (сигнал виден вечером, вход утром) его не снять.
    lag: int = 1


@dataclass
class Result:
    """Итог прогона одной конфигурации."""

    params: Params
    equity: list[tuple[int, float]]      # (ts конца интервала, капитал)
    returns: list[float]                 # доходность портфеля за интервал
    exposure: float                      # доля монето-дней в позиции, 0..1
    in_market: float                     # доля дней, когда портфель не в кэше
    turnover_year: float                 # оборот в год, в долях капитала
    n_intervals: int

    @property
    def years(self) -> float:
        if len(self.equity) < 2:
            return 0.0
        span = self.equity[-1][0] - self.equity[0][0]
        return span / (DAYS_PER_YEAR * 86_400_000)

    @property
    def total(self) -> float:
        """Накопленная доходность, доля (0.36 = +36 %)."""
        if len(self.equity) < 2:
            return 0.0
        return self.equity[-1][1] / self.equity[0][1] - 1.0

    @property
    def cagr(self) -> float | None:
        y = self.years
        if y <= 0 or len(self.equity) < 2:
            return None
        return (self.equity[-1][1] / self.equity[0][1]) ** (1.0 / y) - 1.0

    @property
    def sharpe(self) -> float | None:
        """Годовой Sharpe по интервалам решения.

        Интервалы одной длины (holding дней), поэтому годовой множитель —
        корень из числа интервалов в году, а не из дней. Считать по дневным
        доходностям было бы неверно: между решениями капитал не пересчитан.
        """
        r = self.returns
        if len(r) < 2:
            return None
        mean = sum(r) / len(r)
        var = sum((x - mean) ** 2 for x in r) / (len(r) - 1)
        if var <= 0:
            return None
        per_year = DAYS_PER_YEAR / max(1, self.params.holding)
        return mean / math.sqrt(var) * math.sqrt(per_year)

    @property
    def max_drawdown(self) -> float:
        """Худшая просадка от максимума, доля (0.42 = −42 %)."""
        peak, worst = float("-inf"), 0.0
        for _, v in self.equity:
            peak = max(peak, v)
            if peak > 0:
                worst = min(worst, v / peak - 1.0)
        return worst


def _momentum(bars: list[Candle | None], i: int, lookback: int) -> float | None:
    """Моментум close(t)/close(t−L)−1 или None, если цен нет."""
    now, then = bars[i], bars[i - lookback]
    if now is None or then is None or then.close <= 0 or now.close <= 0:
        return None
    return now.close / then.close - 1.0


def _interval_gross(bars: list[Candle | None], i: int, p: Params) -> float | None:
    """Доходность монеты за интервал удержания, начавшийся решением дня i.

    При use_open вход по открытию дня i+lag, выход — через holding дней
    (по умолчанию lag=1, то есть открытие следующего дня). При use_open=False
    — по закрытиям дня i и дня i+holding.

    Интервалы стыкуются при любом lag: следующий интервал входит в тот же
    день, в который вышел предыдущий (i+holding+lag = i+lag+holding), поэтому
    капитал не простаивает в кэше между интервалами.
    """
    if p.use_open:
        entry, exit_ = i + p.lag, i + p.lag + p.holding
        price = lambda c: c.open
    else:
        entry, exit_ = i, i + p.holding
        price = lambda c: c.close
    if exit_ >= len(bars):
        return None
    a, b = bars[entry], bars[exit_]
    if a is None or b is None or price(a) <= 0:
        return None
    return price(b) / price(a) - 1.0


def _funding_paid(schedule: list[FundingRate] | None, entry_ms: int,
                  exit_ms: int) -> float:
    if not schedule:
        return 0.0
    return funding_between(schedule, entry_ms, exit_ms)


def _signs(mode: str, mom: float, rng: random.Random,
           density: float | None) -> int:
    """Знак позиции по монете: +1 лонг, −1 шорт, 0 вне рынка."""
    if mode == "all":
        return 1
    if mode == "long":
        return 1 if mom > 0 else 0
    if mode == "short":
        return -1 if mom < 0 else 0
    if mode == "longshort":
        return 1 if mom > 0 else (-1 if mom < 0 else 0)
    if mode == "reverse":
        return 1 if mom < 0 else 0
    if mode == "random":
        # именно `is None`, а не `or`: плотность 0 — законное значение
        # (не торговать вовсе), а `0.0 or 0.5` дало бы половину
        p = 0.5 if density is None else density
        return 1 if rng.random() < p else 0
    raise ValueError(f"неизвестный режим: {mode}")


def run(panel: "Panel", params: Params, *, costs: Costs,
        funding: dict[str, list[FundingRate]] | None = None,
        start_capital: float = 1.0, since_ms: int | None = None,
        until_ms: int | None = None) -> Result:
    """Прогнать правило по выровненной панели дневных баров.

    `since_ms` отсекает начало: интервалы раньше этой отметки считаются и
    компаундируются, но в кривую и в доходности не пишутся. Нужно для
    разбивки по годам: срез без прогрева начинался бы с пустого портфеля, и
    первое решение нового года опиралось бы на моментум без истории.
    `until_ms` закрывает период сверху — прогон на нём останавливается.
    """
    ts, bars = panel.ts, panel.bars
    n = len(ts)
    h = params.holding
    rng = random.Random(params.seed)
    if params.lookback < 1 or h < 1:
        raise ValueError("lookback и holding должны быть не меньше 1")

    equity: list[tuple[int, float]] = [(ts[0], start_capital)]
    returns: list[float] = []
    capital = start_capital
    drifted: dict[str, float] = {}          # веса на конец прошлого интервала
    held_days = 0                           # монеты в позиции, сумма по интервалам
    available_days = 0                      # монеты с посчитанным моментумом
    days_in_market = 0                      # интервалы, где портфель не в кэше
    turnover_total = 0.0
    intervals = 0
    counting = since_ms is None             # пишем ли текущие интервалы наружу

    for i in range(params.lookback, n - h - params.lag, h):
        entry_ms = (ts[i + params.lag] if params.use_open else ts[i])
        exit_ms = (ts[i + params.lag + h] if params.use_open else ts[i + h])
        if until_ms is not None and entry_ms >= until_ms:
            break

        # --- состав на интервал: сигнал дня i, доходность интервала
        signs: dict[str, int] = {}
        gains: dict[str, float] = {}
        available = 0
        for sym, b in bars.items():
            mom = _momentum(b, i, params.lookback)
            if mom is None:
                continue
            available += 1
            s = _signs(params.mode, mom, rng, params.density)
            if not s:
                continue
            g = _interval_gross(b, i, params)
            if g is None:
                continue    # цена входа или выхода недоступна — монета мимо
            if costs.funding:
                g -= _funding_paid((funding or {}).get(sym), entry_ms, exit_ms)
            signs[sym] = s
            gains[sym] = g
        gross_weight = sum(abs(s) for s in signs.values())
        target = ({sym: s / gross_weight for sym, s in signs.items()}
                  if gross_weight else {})

        # --- ребаланс в начале интервала: платим за изменение веса
        names = set(target) | set(drifted)
        turnover = sum(abs(target.get(s, 0.0) - drifted.get(s, 0.0))
                       for s in names)
        # с этого интервала числа идут наружу; точку на начало периода
        # ставим до ребаланса, иначе первый интервал периода потерялся бы
        if not counting and since_ms is not None and exit_ms >= since_ms:
            counting = True
            equity = [(entry_ms, capital)]
        if turnover:
            capital *= (1.0 - costs.per_side * turnover)
        if counting:
            turnover_total += turnover
            intervals += 1
            available_days += available
            if target:
                days_in_market += 1
            held_days += len(target)

        # --- доходность интервала
        gross = sum(w * gains[sym] for sym, w in target.items())
        capital *= (1.0 + gross)

        # --- дрейф весов внутри интервала: с ним сравнится следующий ребаланс
        if gross > -1.0:
            drifted = {s: target[s] * (1.0 + gains[s]) / (1.0 + gross)
                       for s in target}
        else:
            drifted = dict(target)
        if counting:
            returns.append((1.0 - costs.per_side * turnover) * (1.0 + gross) - 1.0)
            equity.append((exit_ms, capital))

    span = (ts[-1] - (since_ms or ts[0])) / (DAYS_PER_YEAR * 86_400_000)
    return Result(params=params, equity=equity, returns=returns,
                  exposure=(held_days / available_days if available_days else 0.0),
                  in_market=(days_in_market / intervals if intervals else 0.0),
                  turnover_year=(turnover_total / span if span > 0 else 0.0),
                  n_intervals=intervals)


@dataclass
class Panel:
    """Дневные бары монет, выровненные по общему календарю.

    Календарь — объединение дней всех монет: монета, у которой бара нет
    (ещё не листилась), стоит в этом дне как None и в решении не участвует.
    """

    ts: list[int]
    bars: dict[str, list[Candle | None]] = field(default_factory=dict)

    @classmethod
    def build(cls, series: dict[str, list[Candle]]) -> "Panel":
        days = sorted({c.ts for cs in series.values() for c in cs})
        where = {t: i for i, t in enumerate(days)}
        bars: dict[str, list[Candle | None]] = {}
        for sym, cs in series.items():
            arr: list[Candle | None] = [None] * len(days)
            for c in cs:
                arr[where[c.ts]] = c
            bars[sym] = arr
        return cls(ts=days, bars=bars)

    def symbols(self) -> list[str]:
        return sorted(self.bars)

    def span(self) -> tuple[int, int]:
        return self.ts[0], self.ts[-1]
