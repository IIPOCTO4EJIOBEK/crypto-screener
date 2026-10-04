"""Сигнал тренд-фильтра на живых дневных барах — то же правило, что в бэктесте.

Правило (`src/backtest/trend.py`): на закрытии дня t моментум
close(t)/close(t−L)−1; положительный — монета в портфеле на следующие
holding дней, равными весами; иначе вне портфеля (кэш, не шорт). Вход — на
открытии следующего дня, поэтому живой прогон запускается сразу после
полуночи UTC: последний закрытый бар — день сигнала, текущая цена — его
следующее открытие.

Два места, где живой счёт обязан совпасть с бэктестом и где легко ошибиться:

* **Только закрытые бары.** Биржа отдаёт последним незакрытый бар текущего
  дня. Его close — это цена «сейчас», а не закрытие дня; моментум по нему —
  другое правило. Такой бар отбрасывается по времени, а не по позиции в списке.
* **День решения.** В бэктесте решения идут каждые holding дней от начала
  панели. Живому контуру начало панели не задано, поэтому день решения
  привязан к календарю: номер дня от 1970-01-01 делится на holding без
  остатка. Сдвиг фазы на правило не влияет (сетка в документе 16 показывает
  плато H1…H5), но фазу нельзя менять между запусками — иначе интервалы
  перестают стыковаться.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.data.market import Candle

DAY_MS = 86_400_000


@dataclass(frozen=True)
class Decision:
    day_ts: int                     # открытие дня сигнала (последний закрытый бар)
    is_decision_day: bool
    momentum: dict[str, float]      # монеты, у которых моментум посчитан
    longs: list[str]                # монеты с моментумом > 0
    shorts: list[str] = field(default_factory=list)          # монеты с моментумом < 0
    skipped: dict[str, str] = field(default_factory=dict)   # монета → почему мимо

    @property
    def weights(self) -> dict[str, float]:
        """Равные веса среди лонгов; пусто — весь капитал в кэше."""
        if not self.longs:
            return {}
        w = 1.0 / len(self.longs)
        return {s: w for s in self.longs}

    def signed_weights(self, side: str = "long") -> dict[str, float]:
        """Целевые веса со знаком: + лонг, − шорт; сумма модулей — 1.

        long — только рост (правило бэктеста по умолчанию); longshort — рост
        в лонг, падение в шорт (в бэктесте: Sharpe 1.22, просадка −52 % на
        восьмёрке; шорт сам по себе там убыточен).
        """
        if side == "long":
            return self.weights
        if side != "longshort":
            raise ValueError(f"неизвестная сторона: {side}")
        names = self.longs + self.shorts
        if not names:
            return {}
        w = 1.0 / len(names)
        return {**{s: w for s in self.longs}, **{s: -w for s in self.shorts}}


def closed_bars(bars: list[Candle], now_ms: int) -> list[Candle]:
    """Отбросить бары, которые ещё не закрылись к моменту now_ms."""
    return [b for b in bars if b.ts + DAY_MS <= now_ms]


def is_decision_day(day_ts: int, holding: int) -> bool:
    return (day_ts // DAY_MS) % holding == 0


def decide(series: dict[str, list[Candle]], *, now_ms: int, lookback: int = 28,
           holding: int = 5) -> Decision:
    """Посчитать сигнал на последнем закрытом дне.

    День сигнала — общий для всех монет: самый поздний закрытый день среди
    них. Монета, у которой этого бара нет (биржа не отдала, торги стояли),
    в решении не участвует — как в бэктесте монета с None в панели.
    """
    closed = {s: closed_bars(b, now_ms) for s, b in series.items()}
    days = [b[-1].ts for b in closed.values() if b]
    if not days:
        raise ValueError("нет ни одного закрытого дневного бара")
    day = max(days)
    momentum: dict[str, float] = {}
    skipped: dict[str, str] = {}
    for sym, bars in closed.items():
        by_ts = {b.ts: b for b in bars}
        now, then = by_ts.get(day), by_ts.get(day - lookback * DAY_MS)
        if now is None:
            skipped[sym] = "нет бара дня сигнала"
            continue
        if then is None:
            skipped[sym] = f"нет бара {lookback} дней назад"
            continue
        if then.close <= 0 or now.close <= 0:
            skipped[sym] = "нулевая цена"
            continue
        momentum[sym] = now.close / then.close - 1.0
    longs = sorted(s for s, m in momentum.items() if m > 0)
    shorts = sorted(s for s, m in momentum.items() if m < 0)
    return Decision(day_ts=day, is_decision_day=is_decision_day(day, holding),
                    momentum=momentum, longs=longs, shorts=shorts, skipped=skipped)
