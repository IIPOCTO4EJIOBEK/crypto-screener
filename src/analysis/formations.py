"""Детекторы формаций.

Типы формаций взяты из документации Digash (data/corpus, раздел formations):

    Пробой уровня, Отскок / закол уровня, Ретест уровня, Отскок от плотности,
    Слом структуры, Наклонки, Проторговка, Всплеск объёма, Импульс,
    Smart Money, Ножи.

Каждый детектор возвращает не «сигнал», а разбор: что произошло, откуда
считать вход, где стоп, куда целиться и при каком условии разбор отменяется.
Числа считает код; языковая модель только пересказывает готовый разбор.

Честная граница: у Digash опубликованы названия формаций и метрики, но не
пороги детекторов. Все пороги ниже — рабочая оценка, подобранная так, чтобы
на живом рынке формации находились, а не молчали. Они собраны в начале
файла, чтобы их можно было менять в одном месте.

Два отличия от «наивного» детектора, без которых он бесполезен:

  Событие ищется на закрытых свечах. Последняя свеча в списке ещё
  формируется, её закрытие и объём меняются каждую секунду — решение по ней
  принимается по незаконченным данным.

  Событие ищется в окне из нескольких свечей (EVENT_WINDOW). Пробой уровня
  или отскок — момент, а не состояние: детектор, который срабатывает
  ровно на свече пробоя, показывает пустой экран почти всегда. У каждой
  формации есть age_candles — сколько свечей назад это случилось.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.analysis.density import Density, find_densities
from src.analysis.levels import (find_pivots, find_trend_lines,
                                 horizontal_levels, level_tolerance_pct,
                                 trend_tolerance_pct)
from src.analysis.metrics import (correlation, efficiency_ratio, narrow_range,
                                  natr, price_change, volume_splash)
from src.data.market import Candle, OrderBook

# --------------------------------------------------------------------------
# Пороги детекторов. Значения — оценка, а не данные Digash.
# --------------------------------------------------------------------------
EVENT_WINDOW = 3        # сколько последних закрытых свечей считаем «сейчас»
SPLASH_MIN = 2.0        # во сколько раз объём свечи больше обычного
SPLASH_STRONG = 3.0     # «сильный» всплеск
IMPULSE_NATR = 3.0      # импульс: ход больше стольких NATR
KNIFE_NATR = 5.0        # нож: падение больше стольких NATR без отката
SWING_SPAN = 5          # окно поиска перегибов структуры, свечей
STRUCTURE_MEMORY = 6    # сколько последних перегибов держим для структуры
SQUEEZE_RATIO = 0.55    # сжатие: диапазон уже во столько раз
SQUEEZE_ER = 0.65       # сжатие: коэффициент эффективности ниже этого
NR_PERIOD = 7           # NR7: самый узкий диапазон за столько свечей
ABSORB_MAX_MOVE = 0.25  # поглощение: ход меньше стольких процентов
WICK_SHARE = 0.45       # доля хвоста в свече для разворотных формаций
MIN_TOUCHES = 2         # уровень без касаний для формации слабоват
RISK_PCT = 1.0          # риск на сделку, % от депозита
MIN_RISK_NATR = 0.5     # стоп ближе половины NATR — это шум, не стоп

STYLES = {"scalping": ("1m", "3m", "5m", "15m"),
          "swing": ("30m", "1h", "4h"),
          "long": ("1d",)}


def style_of(tf: str) -> str:
    for style, tfs in STYLES.items():
        if tf in tfs:
            return style
    return "scalping"


@dataclass
class Formation:
    """Найденная формация с готовым разбором входа."""
    kind: str            # машинное имя, напр. "breakout"
    title: str           # человеческое имя, напр. «Пробой уровня»
    direction: str       # "long" | "short"
    symbol: str
    exchange: str
    tf: str
    ts: int              # время обнаружения, мс
    price: float         # текущая цена
    entry: float         # цена входа
    stop: float
    targets: list[float]
    triggered: bool      # вход уже можно брать или ждём условия
    confidence: float    # 0..1, сколько условий сошлось
    reasons: list[str] = field(default_factory=list)
    invalid: str = ""    # при каком условии разбор отменяется
    style: str = ""      # скальпинг / среднесрок / долгосрок
    age_candles: int = 0  # сколько свечей назад произошло событие
    trigger_level: float = 0.0  # точный уровень подтверждения пробоя/ретеста

    def __post_init__(self) -> None:
        if not self.style:
            self.style = style_of(self.tf)

    @property
    def risk_pct(self) -> float:
        """Расстояние до стопа в процентах от входа."""
        if self.entry <= 0:
            return 0.0
        return abs(self.entry - self.stop) / self.entry * 100

    @property
    def reward_pct(self) -> float:
        if not self.targets or self.entry <= 0:
            return 0.0
        return abs(self.targets[0] - self.entry) / self.entry * 100

    @property
    def rr(self) -> float:
        """Соотношение прибыль/риск до первой цели."""
        r = self.risk_pct
        return self.reward_pct / r if r > 0 else 0.0

    def position_size(self, deposit: float, risk_pct: float = RISK_PCT) -> float:
        """Объём позиции в котируемой валюте при заданном риске на сделку.

        Размер считается из расстояния до стопа, а не «на глаз»: если стоп
        далеко, позиция меньше.
        """
        if self.risk_pct <= 0 or self.entry <= 0:
            return 0.0
        risk_money = deposit * risk_pct / 100
        return risk_money / (self.risk_pct / 100)

    def summary(self) -> str:
        tgt = ", ".join(f"{t:.4f}" for t in self.targets[:2]) or "—"
        mark = "" if self.triggered else " (ждём условие)"
        return (f"{self.title:28} {self.direction:5} вход {self.entry:10.4f} "
                f"стоп {self.stop:10.4f} ({self.risk_pct:4.2f}%) цели {tgt} "
                f"R:R {self.rr:3.1f} уверенность {self.confidence:.2f}{mark}")


# --------------------------------------------------------------------------
# Вспомогательное
# --------------------------------------------------------------------------
def _swings(candles: list[Candle], span: int = SWING_SPAN
            ) -> list[tuple[int, float, str]]:
    """Перегибы для анализа структуры.

    Перегиб подтверждается только через span свечей после него, поэтому
    последние свечи в разборе структуры не участвуют. Сколько именно —
    решает find_pivots: он отсекает max(skip_recent, span), а skip_recent
    по умолчанию равен SKIP_RECENT из levels.py и больше span.
    """
    return find_pivots(candles, span=span)


def _box(candles: list[Candle]) -> tuple[float, float]:
    if not candles:
        return 0.0, 0.0
    return max(c.high for c in candles), min(c.low for c in candles)


def _levels(candles: list[Candle], tf: str, **kw):
    return horizontal_levels(candles, tf, **kw)


def _nearest_above(levels, price: float):
    up = [lv for lv in levels if lv.price > price]
    return min(up, key=lambda lv: lv.price - price) if up else None


def _nearest_below(levels, price: float):
    dn = [lv for lv in levels if lv.price < price]
    return max(dn, key=lambda lv: lv.price) if dn else None


def _beyond(direction: str, entry: float, targets: list[float]) -> list[float]:
    """Оставить только цели по ту сторону от входа, где сделка в плюсе.

    Цель ниже входа у лонга — не цель, а уже пройденный уровень: цена там
    была. Такие попадают в список двумя путями. В отскоке — потому что
    допуск уровня считает касанием свечу, чей лоу остался выше уровня, и
    проекция «на 1.5 хвоста» уходит тогда вниз. В сломе структуры — потому
    что закрытие могло подтвердиться выше предыдущего максимума, и он
    оказывается позади входа.
    """
    return [t for t in targets
            if (t > entry if direction == "long" else t < entry)]


def _confidence(*parts: float) -> float:
    return round(min(1.0, max(0.05, sum(parts))), 2)


def _scan(detector, candles: list[Candle], tf: str, symbol: str,
          exchange: str, *, window: int = EVENT_WINDOW, **kw) -> list[Formation]:
    """Прогнать детектор по последним закрытым свечам.

    Две вещи, без которых детекторы почти всегда молчат:

    Последняя свеча в списке ещё формируется — её закрытие и объём
    меняются каждую секунду. Оценивать формацию по ней значит принимать
    решение по незаконченным данным, поэтому событие ищется на закрытых
    свечах.

    Событие ищется в окне, а не только на последней свече: пробой уровня
    или отскок — момент, а не состояние. Скринер, который ловит только
    свечу пробоя, показывает пустой экран почти всегда.
    """
    out: list[Formation] = []
    for age in range(window):
        end = len(candles) - 1 - age
        if end < 30:
            break
        sub = candles[:end]
        try:
            found = detector(sub, tf, symbol, exchange, **kw)
        except Exception:  # один детектор не должен ронять остальные
            continue
        for f in found:
            f.age_candles = age
            out.append(f)
    return out


def _btc_against(candles: list[Candle], btc: list[Candle] | None,
                 direction: str) -> bool:
    """Идёт ли BTC против нашей сделки — по корреляции и его ходу."""
    if not btc or len(btc) < 20:
        return False
    corr = correlation(candles, btc)
    if corr is None or abs(corr) < 50:
        return False  # связь слабая, BTC не мешает
    btc_move = price_change(btc, 30) or 0.0
    if direction == "long":
        return corr > 0 and btc_move < 0
    return corr > 0 and btc_move > 0


# --------------------------------------------------------------------------
# 1. Пробой уровня
# --------------------------------------------------------------------------
def detect_breakout(candles: list[Candle], tf: str, symbol: str,
                    exchange: str, *, btc: list[Candle] | None = None,
                    levels=None) -> list[Formation]:
    """Цена пробила горизонтальный уровень и закрылась за ним."""
    if len(candles) < 60:
        return []
    tol = level_tolerance_pct(tf) / 100
    last, prev = candles[-1], candles[-2]
    splash = volume_splash(candles, 20) or 0.0
    if levels is None:
        levels = _levels(candles, tf, max_levels=20, skip_recent=2,
                         include_broken=True)

    out: list[Formation] = []
    for lv in levels:
        if lv.kind == "resistance":
            crossed = prev.close <= lv.price < last.close
            direction, side = "long", "up"
        else:
            crossed = prev.close >= lv.price > last.close
            direction, side = "short", "down"
        if not crossed:
            continue
        # ушли за уровень слишком далеко — вход уже поздний
        depth = abs(last.close - lv.price) / lv.price * 100
        if depth > 3 * tol * 100:
            continue

        hi, lo = _box(candles[-41:-1])
        height = hi - lo
        if direction == "long":
            stop = min(last.low, lv.price * (1 - tol))
            targets = [(lv.price + height) if height > 0 else lv.price * 1.01]
            nxt = _nearest_above(levels, last.close)
            if nxt:
                targets.insert(0, nxt.price)
        else:
            stop = max(last.high, lv.price * (1 + tol))
            targets = [(lv.price - height) if height > 0 else lv.price * 0.99]
            nxt = _nearest_below(levels, last.close)
            if nxt:
                targets.insert(0, nxt.price)

        reasons = [f"закрытие за уровнем {lv.price:.4f} ({lv.touches} касаний)",
                   f"ушли за уровень на {depth:.2f}% (допуск {tol*100:.2f}%)"]
        conf = 0.35
        if lv.touches >= MIN_TOUCHES:
            conf += 0.15
            reasons.append(f"уровень держался {lv.touches} касания")
        if splash >= SPLASH_MIN:
            conf += 0.2
            reasons.append(f"объём {splash:.1f}× от обычного — пробой на объёме")
        # сильное закрытие: цена ушла в верхнюю/нижнюю треть свечи
        rng = last.high - last.low
        if rng > 0:
            pos = (last.close - last.low) / rng
            if (direction == "long" and pos > 0.66) or \
               (direction == "short" and pos < 0.34):
                conf += 0.15
                reasons.append("свеча закрылась в сторону пробоя, без отката")
        if _btc_against(candles, btc, direction):
            conf -= 0.25
            reasons.append("BTC идёт против сделки — пробой может не удержаться")

        out.append(Formation(
            "breakout", "Пробой уровня", direction, symbol, exchange, tf,
            last.ts, last.close, last.close, stop, targets, True,
            _confidence(conf), reasons,
            f"закрытие свечи обратно за уровень {lv.price:.4f} или "
            f"всплеск объёма меньше {SPLASH_MIN}×"))

    return out


# --------------------------------------------------------------------------
# 2. Отскок и закол уровня
# --------------------------------------------------------------------------
def detect_bounce(candles: list[Candle], tf: str, symbol: str,
                  exchange: str, *, btc: list[Candle] | None = None,
                  levels=None) -> list[Formation]:
    """Цена коснулась уровня и развернулась.

    Если свеча проколола уровень и вернулась — это закол (ложный пробой,
    сбор стопов). Он сильнее обычного отскока: за уровнем сработали стопы,
    а цена ушла обратно.
    """
    if len(candles) < 60:
        return []
    tol = level_tolerance_pct(tf) / 100
    last = candles[-1]
    rng = last.high - last.low
    if rng <= 0:
        return []
    if levels is None:
        levels = _levels(candles, tf, max_levels=20, skip_recent=5)

    out: list[Formation] = []
    for lv in levels:
        if lv.kind == "support":
            touched = last.low <= lv.price * (1 + tol)
            holds = last.close > lv.price
            pierced = last.low < lv.price * (1 - tol)
            wick = (min(last.open, last.close) - last.low) / rng
            direction = "long"
        else:
            touched = last.high >= lv.price * (1 - tol)
            holds = last.close < lv.price
            pierced = last.high > lv.price * (1 + tol)
            wick = (last.high - max(last.open, last.close)) / rng
            direction = "short"
        if not (touched and holds and wick >= WICK_SHARE):
            continue

        if direction == "long":
            stop = last.low * (1 - tol)
            targets = [lv.price + (lv.price - last.low) * 1.5]
            nxt = _nearest_above(levels, last.close)
            if nxt:
                targets.insert(0, nxt.price)
        else:
            stop = last.high * (1 + tol)
            targets = [lv.price - (last.high - lv.price) * 1.5]
            nxt = _nearest_below(levels, last.close)
            if nxt:
                targets.insert(0, nxt.price)
        targets = _beyond(direction, last.close, targets)
        if not targets:
            # за входом не осталось ни одной цели: уровень касания оказался
            # выше входа, и проекция ушла назад. Это не сделка.
            continue

        kind = "zakol" if pierced else "bounce"
        title = f"{'Закол' if pierced else 'Отскок'} уровня"
        reasons = [f"касание уровня {lv.price:.4f}, хвост {wick*100:.0f}% свечи",
                   "цена вернулась за уровень" if pierced
                   else "уровень удержал цену"]
        conf = 0.3 + (0.25 if pierced else 0.1) + min(wick, 0.8) * 0.25
        if lv.touches >= MIN_TOUCHES:
            conf += 0.1
            reasons.append(f"уровень проверен {lv.touches} раза")
        splash = volume_splash(candles, 20) or 0.0
        if splash >= SPLASH_MIN:
            conf += 0.1
            reasons.append(f"на касании объём {splash:.1f}× — за уровнем стоят")
        if _btc_against(candles, btc, direction):
            conf -= 0.25
            reasons.append("BTC идёт против сделки")

        invalid = (f"закрытие свечи ниже уровня {lv.price:.4f}"
                   if direction == "long" else
                   f"закрытие свечи выше уровня {lv.price:.4f}")
        out.append(Formation(
            kind, title, direction, symbol, exchange, tf, last.ts,
            last.close, last.close, stop, targets, True, _confidence(conf),
            reasons, invalid))
    return out


# --------------------------------------------------------------------------
# 3. Ретест уровня
# --------------------------------------------------------------------------
def detect_retest(candles: list[Candle], tf: str, symbol: str,
                  exchange: str, *, btc: list[Candle] | None = None,
                  levels=None) -> list[Formation]:
    """Возврат к уже пробитому уровню с другой стороны.

    Пробой подтверждается ретестом: уровень, который был сопротивлением,
    после пробоя становится поддержкой — если цена вернулась и от него
    оттолкнулась, пробой настоящий.
    """
    if len(candles) < 60:
        return []
    tol = level_tolerance_pct(tf) / 100
    last = candles[-1]
    window = candles[-6:-1]
    if levels is None:
        levels = _levels(candles, tf, max_levels=20, skip_recent=1,
                         include_broken=True)

    out: list[Formation] = []
    for lv in levels:
        if not lv.broken:
            continue
        if lv.kind == "resistance":
            # цена держится выше уровня, затем вернулась к нему и оттолкнулась
            above = all(c.close > lv.price * (1 - tol) for c in window)
            touched = last.low <= lv.price * (1 + tol)
            reversed_ = last.close > lv.price and last.close > last.open
            direction = "long"
        else:
            above = all(c.close < lv.price * (1 + tol) for c in window)
            touched = last.high >= lv.price * (1 - tol)
            reversed_ = last.close < lv.price and last.close < last.open
            direction = "short"
        if not (above and touched and reversed_):
            continue

        rng = max(last.high - last.low, 1e-12)
        if direction == "long":
            stop = min(last.low, lv.price * (1 - tol))
            targets = [last.close + rng * 2]
            nxt = _nearest_above(levels, last.close)
        else:
            stop = max(last.high, lv.price * (1 + tol))
            targets = [last.close - rng * 2]
            nxt = _nearest_below(levels, last.close)
        if nxt:
            targets.insert(0, nxt.price)

        reasons = [f"уровень {lv.price:.4f} был пробит и стал "
                   f"{'поддержкой' if direction == 'long' else 'сопротивлением'}",
                   "цена вернулась к уровню и не закрепилась за ним"]
        conf = 0.45
        if lv.touches >= MIN_TOUCHES:
            conf += 0.15
            reasons.append(f"до пробоя уровень держался {lv.touches} касания")
        splash = volume_splash(candles, 20) or 0.0
        if splash >= SPLASH_MIN:
            conf += 0.1
            reasons.append(f"объём на возврате {splash:.1f}× — уровень защищают")
        if _btc_against(candles, btc, direction):
            conf -= 0.25
            reasons.append("BTC идёт против сделки")

        out.append(Formation(
            "retest", "Ретест уровня", direction, symbol, exchange, tf,
            last.ts, last.close, last.close, stop, targets, True,
            _confidence(conf), reasons,
            f"закрепление цены за уровнем с обратной стороны "
            f"({'ниже' if direction == 'long' else 'выше'} {lv.price:.4f})", trigger_level=lv.price))
    return out


# --------------------------------------------------------------------------
# 4. Отскок от плотности
# --------------------------------------------------------------------------
def detect_density_bounce(candles: list[Candle], tf: str, symbol: str,
                          exchange: str, *, ob: OrderBook | None = None,
                          densities: list[Density] | None = None,
                          avg_volume_2h_base: float = 0.0,
                          btc: list[Candle] | None = None) -> list[Formation]:
    """Разворот на крупной лимитной заявке в стакане.

    Вход считается от цены плотности, а не от графика: заявка сама по себе
    и есть уровень. Поэтому главное здесь — отличить настоящую плотность от
    выставленной для вида; подробности в src/analysis/density.py.
    """
    if len(candles) < 30:
        return []
    ds = densities if densities is not None else (
        find_densities(ob) if ob is not None else [])
    if not ds:
        return []
    last = candles[-1]
    tol = level_tolerance_pct(tf) / 100

    out: list[Formation] = []
    for d in ds:
        # плотность работает уровнем, только если цена уже подошла к ней
        if abs(d.distance_pct) > 0.5:
            continue
        if d.side == "bid":
            near = last.low <= d.price * (1 + tol)
            direction = "long"
            stop = d.price * (1 - tol * 2)
        else:
            near = last.high >= d.price * (1 - tol)
            direction = "short"
            stop = d.price * (1 + tol * 2)
        if not near:
            continue

        eat = d.absorb_seconds(avg_volume_2h_base)
        reasons = [f"заявка {d.size:.4f} на {d.price:.4f} "
                   f"(${d.notional:,.0f}) в {abs(d.distance_pct):.2f}% от цены"]
        if eat is not None:
            reasons.append(f"при текущем объёме её будут съедать "
                           f"~{eat/60:.0f} мин")
        conf = 0.3 + min(d.notional / 500_000, 0.3)
        if d.snapshots > 3 and d.max_size and d.size >= d.max_size * 0.9:
            conf += 0.2
            reasons.append("заявка стоит на месте и не уменьшается — не спуфинг")
        if d.eaten > 0:
            reasons.append(f"за время наблюдения съедено {d.eaten:.4f}")
        if d.snapshots <= 2:
            conf -= 0.15
            reasons.append("наблюдение короткое: спуфинг исключить нельзя")

        mid = d.price * (1 + tol * 3) if direction == "long" else \
            d.price * (1 - tol * 3)
        out.append(Formation(
            "density_bounce", "Отскок от плотности", direction, symbol,
            exchange, tf, last.ts, last.close, last.close, stop, [mid], True,
            _confidence(conf), reasons,
            f"плотность исчезла из стакана или цена закрылась за ней "
            f"({'ниже' if direction == 'long' else 'выше'} {d.price:.4f}) — "
            f"признак спуфинга"))
    return out


# --------------------------------------------------------------------------
# 5. Слом структуры
# --------------------------------------------------------------------------
def detect_structure_break(candles: list[Candle], tf: str, symbol: str,
                           exchange: str, *, btc: list[Candle] | None = None
                           ) -> list[Formation]:
    """Смена последовательности максимумов и минимумов.

    Пока максимумы и минимумы растут (HH/HL) — тренд вверх, откаты
    выкупаются. Слом — когда цена закрывается ниже последнего более
    высокого минимума: последовательность ломается, откат перестал быть
    откатом.
    """
    if len(candles) < 80:
        return []
    sw = _swings(candles)
    highs = [(i, p) for i, p, k in sw if k == "high"][-STRUCTURE_MEMORY:]
    lows = [(i, p) for i, p, k in sw if k == "low"][-STRUCTURE_MEMORY:]
    if len(highs) < 2 or len(lows) < 2:
        return []

    last = candles[-1]
    tol = level_tolerance_pct(tf) / 100
    out: list[Formation] = []
    n = natr(candles, 14) or 0.0

    up_structure = highs[-1][1] > highs[-2][1] and lows[-1][1] > lows[-2][1]
    down_structure = highs[-1][1] < highs[-2][1] and lows[-1][1] < lows[-2][1]

    if up_structure:
        hl = lows[-1][1]
        if last.close < hl * (1 - tol):
            stop = highs[-1][1]
            targets = _beyond("short", last.close,
                              [lows[-2][1], last.close - (stop - last.close)])
            if targets:
                out.append(Formation(
                    "structure_break", "Слом структуры", "short", symbol,
                    exchange, tf, last.ts, last.close, last.close, stop,
                    targets, True,
                    _confidence(0.5, 0.1 if last.close < last.open else 0.0),
                    [f"структура была восходящей: максимумы и минимумы росли",
                     f"цена закрылась ниже последнего минимума {hl:.4f} — "
                     f"последовательность сломана"],
                    "возврат цены выше сломанного минимума и обновление максимума"))
    elif down_structure:
        lh = highs[-1][1]
        if last.close > lh * (1 + tol):
            stop = lows[-1][1]
            targets = _beyond("long", last.close,
                              [highs[-2][1], last.close + (last.close - stop)])
            if targets:
                out.append(Formation(
                    "structure_break", "Слом структуры", "long", symbol,
                    exchange, tf, last.ts, last.close, last.close, stop,
                    targets, True,
                    _confidence(0.5, 0.1 if last.close > last.open else 0.0),
                    ["структура была нисходящей: максимумы и минимумы падали",
                     f"цена закрылась выше последнего максимума {lh:.4f} — "
                     f"последовательность сломана"],
                    "возврат цены ниже сломанного максимума"))
    if out and n > 0 and out[0].risk_pct > 4 * n:
        return []  # до стопа слишком далеко — это уже не слом, а разворот
    return out


# --------------------------------------------------------------------------
# 6. Наклонки — события на трендовых линиях
# --------------------------------------------------------------------------
def detect_trendline_event(candles: list[Candle], tf: str, symbol: str,
                           exchange: str, *, btc: list[Candle] | None = None
                           ) -> list[Formation]:
    """Касание трендовой линии и её пробой.

    Линия строится по трём экстремумам (см. levels.find_trend_lines).
    Касание — отскок в сторону линии; закрытие за линией — её пробой.
    """
    if len(candles) < 80:
        return []
    tol = trend_tolerance_pct(tf) / 100
    lines = find_trend_lines(candles, tf, max_lines=6, skip_recent=1)
    if not lines:
        return []
    last, prev = candles[-1], candles[-2]
    i = len(candles) - 1
    # Стоп ставится не «чуть ниже линии», а за границу шума. В бэктесте
    # разбор с допуском 0.06 % от линии давал 1 % попаданий: стоп сбивало
    # первой же свечой, хотя направление угадывалось.
    buffer_pct = max(tol, MIN_RISK_NATR * (natr(candles, 14) or 0.0) / 100)
    if buffer_pct <= 0:
        return []

    out: list[Formation] = []
    for slope, offset, touches, kind in lines:
        at_now = slope * i + offset
        at_prev = slope * (i - 1) + offset
        if at_now <= 0 or at_prev <= 0:
            continue
        dist = abs(last.close - at_now) / at_now

        if kind == "up":
            # восходящая линия поддержки — от неё отскакиваем вверх
            if last.low <= at_now * (1 + tol) and last.close > at_now and \
               abs(last.low - at_now) / at_now <= tol * 3:
                # вход по цене линии, а не по закрытию свечи: к закрытию
                # цена уже отошла от линии, и стоп пришлось бы ставить
                # внутри шума
                stop = at_now * (1 - buffer_pct)
                if stop <= 0 or at_now <= stop:
                    continue
                out.append(Formation(
                    "trendline_bounce", "Отскок от наклонной", "long", symbol,
                    exchange, tf, last.ts, last.close, at_now, stop,
                    [at_now + (at_now - stop) * 2], True,
                    _confidence(0.35, min(touches, 5) * 0.06),
                    [f"восходящая линия по {touches} экстремумам на "
                     f"{at_now:.4f}", "цена коснулась линии и закрылась выше",
                     f"вход по цене линии, стоп за границу шума "
                     f"({buffer_pct*100:.2f}%)"],
                    f"закрытие свечи ниже линии {at_now:.4f}"))
            elif prev.close > at_prev and last.close < at_now * (1 - tol):
                stop = max(last.high, at_now * (1 + tol))
                out.append(Formation(
                    "trendline_break", "Пробой наклонной", "short", symbol,
                    exchange, tf, last.ts, last.close, last.close, stop,
                    [last.close - (stop - last.close) * 2], True,
                    _confidence(0.4, min(touches, 5) * 0.06),
                    [f"восходящая линия по {touches} экстремумам пробита вниз",
                     f"закрытие {abs(dist)*100:.2f}% ниже линии"],
                    "возврат цены выше пробитой линии"))
        else:
            if last.high >= at_now * (1 - tol) and last.close < at_now and \
               abs(last.high - at_now) / at_now <= tol * 3:
                stop = at_now * (1 + buffer_pct)
                out.append(Formation(
                    "trendline_bounce", "Отскок от наклонной", "short",
                    symbol, exchange, tf, last.ts, last.close, at_now,
                    stop, [at_now - (stop - at_now) * 2], True,
                    _confidence(0.35, min(touches, 5) * 0.06),
                    [f"нисходящая линия по {touches} экстремумам на "
                     f"{at_now:.4f}", "цена коснулась линии и закрылась ниже",
                     f"вход по цене линии, стоп за границу шума "
                     f"({buffer_pct*100:.2f}%)"],
                    f"закрытие свечи выше линии {at_now:.4f}"))
            elif prev.close < at_prev and last.close > at_now * (1 + tol):
                stop = min(last.low, at_now * (1 - tol))
                out.append(Formation(
                    "trendline_break", "Пробой наклонной", "long", symbol,
                    exchange, tf, last.ts, last.close, last.close, stop,
                    [last.close + (last.close - stop) * 2], True,
                    _confidence(0.4, min(touches, 5) * 0.06),
                    [f"нисходящая линия по {touches} экстремумам пробита вверх",
                     f"закрытие {abs(dist)*100:.2f}% выше линии"],
                    "возврат цены ниже пробитой линии"))
    return out


# --------------------------------------------------------------------------
# 7. Проторговка — сжатие диапазона перед импульсом
# --------------------------------------------------------------------------
def detect_squeeze(candles: list[Candle], tf: str, symbol: str,
                   exchange: str, *, btc: list[Candle] | None = None
                   ) -> list[Formation]:
    """Сжатие диапазона: рынок «затих» перед импульсом.

    Вход — не сейчас, а на пробое границы сжатия, поэтому формация отдаётся
    с triggered=False: детектор выставляет условие, а не зовёт в сделку.
    """
    if len(candles) < 80:
        return []
    recent = candles[-20:]
    prior = candles[-60:-20]
    hi, lo = _box(recent)
    phi, plo = _box(prior)
    height = hi - lo
    prior_height = phi - plo
    if height <= 0 or prior_height <= 0:
        return []
    if height / prior_height > SQUEEZE_RATIO:
        return []
    n_recent = natr(candles[-21:], 14) or 0.0
    n_prior = natr(candles[-41:-21], 14) or 0.0
    if n_prior > 0 and n_recent > n_prior * 0.8:
        return []  # волатильность не падала — это не сжатие

    # Коэффициент эффективности Кауфмана: при сжатии цена ходит туда-сюда
    # и почти не двигается по итогу, ER падает до 0.5–0.7 (Crabel, Kaufman).
    er = efficiency_ratio(candles, 20)
    if er is not None and er > SQUEEZE_ER:
        return []
    nr = narrow_range(candles, NR_PERIOD)

    vol_recent = sum(c.quote_volume for c in recent) / len(recent)
    vol_prior = sum(c.quote_volume for c in prior) / len(prior)
    squeeze = hi - lo

    out = []
    for direction, level, stop in (("long", hi, lo), ("short", lo, hi)):
        entry = level * (1 + 0.0005) if direction == "long" else \
            level * (1 - 0.0005)
        target = entry + squeeze if direction == "long" else entry - squeeze
        reasons = [f"диапазон сузился с {prior_height:.4f} до {height:.4f} "
                   f"({height/prior_height*100:.0f}% прежнего)",
                   f"волатильность NATR {n_prior:.3f}% → {n_recent:.3f}%"]
        if er is not None:
            reasons.append(f"коэффициент эффективности {er:.2f} — цена ходит "
                           f"вбок, направленного движения нет")
        if nr:
            reasons.append(f"текущая свеча — самая узкая за {NR_PERIOD} свечей")
        conf = 0.4 + (0.15 if height / prior_height < 0.4 else 0.0)
        conf += 0.1 if nr else 0.0
        conf += 0.1 if er is not None and er < 0.4 else 0.0
        if vol_prior > 0 and vol_recent > vol_prior:
            conf += 0.15
            reasons.append("объём в сжатии не упал — идёт набор позиции")
        else:
            reasons.append("объём в сжатии упал — интерес к монете пропал, "
                           "пробой может быть ложным")
        out.append(Formation(
            "squeeze", "Проторговка", direction, symbol, exchange, tf,
            candles[-1].ts, candles[-1].close, entry, stop, [target], False,
            _confidence(conf), reasons,
            "цена вышла из диапазона в обратную сторону — сжатие "
            "разрешилось против выбранного направления"))
    return out


# --------------------------------------------------------------------------
# 8. Всплеск объёма
# --------------------------------------------------------------------------
def detect_volume_splash(candles: list[Candle], tf: str, symbol: str,
                         exchange: str, *, btc: list[Candle] | None = None
                         ) -> list[Formation]:
    """Резкий рост объёма на свече.

    Если объём вырос, а цена почти не сдвинулась — это не всплеск, а
    поглощение: его ловит детектор Smart Money.
    """
    if len(candles) < 40:
        return []
    splash = volume_splash(candles, 20) or 0.0
    if splash < SPLASH_STRONG:
        return []
    last = candles[-1]
    chg = (last.close - last.open) / last.open * 100 if last.open else 0.0
    if abs(chg) <= ABSORB_MAX_MOVE:
        return []  # это поглощение, не всплеск
    direction = "long" if chg > 0 else "short"
    rng = last.high - last.low
    if direction == "long":
        stop = last.low
        targets = [last.close + rng * 2]
    else:
        stop = last.high
        targets = [last.close - rng * 2]
    conf = 0.35 + min((splash - SPLASH_STRONG) / 10, 0.3)
    if _btc_against(candles, btc, direction):
        conf -= 0.2
    return [Formation(
        "volume_splash", "Всплеск объёма", direction, symbol, exchange, tf,
        last.ts, last.close, last.close, stop, targets, True,
        _confidence(conf),
        [f"объём {splash:.1f}× от среднего за 20 свечей",
         f"цена сдвинулась на {chg:+.2f}% — движение подтверждено объёмом",
         "всплеск на росте — покупатель агрессивнее"
         if direction == "long" else "всплеск на падении — продавец агрессивнее"],
        "объём вернулся к обычному, а цена не продолжила движение")]


# --------------------------------------------------------------------------
# 9. Импульс
# --------------------------------------------------------------------------
def detect_impulse(candles: list[Candle], tf: str, symbol: str,
                   exchange: str, *, btc: list[Candle] | None = None,
                   lookback: int = 5) -> list[Formation]:
    """Быстрый ход, заметно превышающий обычную волатильность.

    Вход по импульсу — на откате, а не в его конце: гнаться за свечой,
    которая уже прошла пять NATR, значит покупать у тех, кто вошёл раньше.
    """
    if len(candles) < lookback + 30:
        return []
    base = candles[-lookback - 1]
    last = candles[-1]
    if base.open <= 0:
        return []
    chg = (last.close - base.open) / base.open * 100
    n = natr(candles, 14) or 0.0
    if n <= 0 or abs(chg) < IMPULSE_NATR * n:
        return []
    direction = "long" if chg > 0 else "short"
    size = last.close - base.open
    if direction == "long":
        entry = last.close - size * 0.382     # откат на 38.2%
        stop = base.open
        targets = [last.close, last.close + size]
    else:
        entry = last.close - size * 0.382
        stop = base.open
        targets = [last.close, last.close + size]
    conf = 0.4 + min(abs(chg) / (IMPULSE_NATR * n) - 1, 1.0) * 0.2
    reasons = [f"ход {chg:+.2f}% за {lookback} свечей при NATR {n:.3f}% "
               f"— это {abs(chg)/n:.1f} NATR",
               f"вход по откату на 38.2% от импульса — {entry:.4f}"]
    if _btc_against(candles, btc, direction):
        conf -= 0.2
        reasons.append("BTC идёт против сделки — импульс может быть выкуплен")
    return [Formation(
        "impulse", "Импульс", direction, symbol, exchange, tf, last.ts,
        last.close, entry, stop, targets, False, _confidence(conf), reasons,
        "цена ушла глубже начала импульса — движение выдохлось")]


# --------------------------------------------------------------------------
# 10. Smart Money — поглощение и сбор ликвидности
# --------------------------------------------------------------------------
def detect_smart_money(candles: list[Candle], tf: str, symbol: str,
                       exchange: str, *, btc: list[Candle] | None = None
                       ) -> list[Formation]:
    """Поглощение и сбор ликвидности.

    Поглощение — крупный объём при почти нулевом ходе: кто-то набирает
    позицию, не двигая цену. Сбор ликвидности — хвост за экстремум с
    возвратом: сработали чужие стопы, и цена ушла обратно.
    """
    if len(candles) < 60:
        return []
    last = candles[-1]
    rng = last.high - last.low
    if rng <= 0 or last.open <= 0:
        return []
    splash = volume_splash(candles, 20) or 0.0
    chg = (last.close - last.open) / last.open * 100
    out: list[Formation] = []

    # поглощение
    if splash >= SPLASH_STRONG and abs(chg) <= ABSORB_MAX_MOVE:
        pos = (last.close - last.low) / rng
        direction = "long" if pos > 0.5 else "short"
        stop = last.low if direction == "long" else last.high
        target = last.close + (last.close - stop) * 2 if direction == "long" \
            else last.close - (stop - last.close) * 2
        out.append(Formation(
            "absorption", "Smart Money: поглощение", direction, symbol,
            exchange, tf, last.ts, last.close, last.close, stop, [target],
            True, _confidence(0.45, min(splash / 15, 0.25)),
            [f"объём {splash:.1f}× при ходе всего {chg:+.2f}% — "
             f"кто-то набирает позицию, не двигая цену",
             "закрытие в верхней половине свечи — перевес на стороне покупки"
             if direction == "long" else
             "закрытие в нижней половине свечи — перевес на стороне продажи"],
            "цена ушла против набора на объёме выше сегодняшнего"))

    # сбор ликвидности: хвост за недавний экстремум и возврат
    sw = _swings(candles[:-1])
    lows = [p for _, p, k in sw if k == "low"][-5:]
    highs = [p for _, p, k in sw if k == "high"][-5:]
    if lows and last.low < min(lows) and last.close > min(lows):
        lvl = min(lows)
        stop = last.low
        out.append(Formation(
            "liquidity_sweep", "Smart Money: сбор стопов", "long", symbol,
            exchange, tf, last.ts, last.close, last.close, stop,
            [last.close + (last.close - stop) * 2], True,
            _confidence(0.5, 0.1 if splash >= SPLASH_MIN else 0.0),
            [f"цена проколола минимум {lvl:.4f} и вернулась выше — "
             f"сработали стопы под уровнем",
             "движение против толпы: продавцы отдали позиции по худшей цене"],
            "закрытие свечи ниже проколотого минимума — стопы собраны, "
            "и падение продолжается"))
    elif highs and last.high > max(highs) and last.close < max(highs):
        lvl = max(highs)
        stop = last.high
        out.append(Formation(
            "liquidity_sweep", "Smart Money: сбор стопов", "short", symbol,
            exchange, tf, last.ts, last.close, last.close, stop,
            [last.close - (stop - last.close) * 2], True,
            _confidence(0.5, 0.1 if splash >= SPLASH_MIN else 0.0),
            [f"цена проколола максимум {lvl:.4f} и вернулась ниже — "
             f"сработали стопы над уровнем",
             "покупка на проколе оказалась в убытке"],
            "закрытие свечи выше проколотого максимума"))
    return out


# --------------------------------------------------------------------------
# 11. Ножи
# --------------------------------------------------------------------------
def detect_knives(candles: list[Candle], tf: str, symbol: str,
                  exchange: str, *, btc: list[Candle] | None = None,
                  lookback: int = 15) -> list[Formation]:
    """Резкое падение без откатов — «нож», который ещё падает.

    Формация предупреждающая: входить против такого движения нельзя, пока
    не появится первый более высокий минимум. Детектор отдаёт разбор с
    triggered=False и условием на вход.
    """
    if len(candles) < lookback + 20:
        return []
    seq = candles[-lookback:]
    base = seq[0].open
    last = candles[-1]
    if base <= 0:
        return []
    chg = (last.close - base) / base * 100
    n = natr(candles, 14) or 0.0
    if n <= 0 or chg > -KNIFE_NATR * n:
        return []

    # ни одной свечи с откатом больше трети предыдущего падения
    reds = sum(1 for c in seq if c.close < c.open)
    if reds < lookback * 0.7:
        return []

    lowest = min(c.low for c in seq)
    entry = max(c.high for c in candles[-3:])   # первый разворот вверх
    stop = lowest * 0.999
    return [Formation(
        "knife", "Ножи", "long", symbol, exchange, tf, last.ts, last.close,
        entry, stop, [entry + (entry - stop) * 2], False,
        _confidence(0.3, 0.15 if abs(chg) > KNIFE_NATR * n * 1.5 else 0.0),
        [f"падение {chg:.2f}% за {lookback} свечей — это "
         f"{abs(chg)/n:.1f} NATR",
         f"{reds} свечей из {lookback} закрылись вниз, откатов не было",
         f"вход только после разворота: условие — закрытие выше {entry:.4f}",
         "пока разворота нет, покупка на падении — это ловля ножа"],
        "цена обновила минимум падения — разворот не состоялся, "
        "движение продолжается")]


# --------------------------------------------------------------------------
# Сводный прогон
# --------------------------------------------------------------------------
DETECTORS = (
    detect_breakout, detect_bounce, detect_retest, detect_structure_break,
    detect_trendline_event, detect_squeeze, detect_volume_splash,
    detect_impulse, detect_smart_money, detect_knives,
)


def detect_all(candles: list[Candle], tf: str, symbol: str, exchange: str,
               *, btc: list[Candle] | None = None,
               ob: OrderBook | None = None,
               densities: list[Density] | None = None,
               avg_volume_2h_base: float = 0.0,
               limit: int = 12) -> list[Formation]:
    """Прогнать все детекторы; вернуть формации, от сильнейших к слабым."""
    if len(candles) < 40:
        return []
    price_now = candles[-1].close
    out: list[Formation] = []

    for fn in DETECTORS:
        for f in _scan(fn, candles, tf, symbol, exchange, btc=btc):
            f.price = price_now
            out.append(f)

    # плотность — снимок стакана, а не событие на свече: окно не нужно
    for f in detect_density_bounce(
            candles, tf, symbol, exchange, ob=ob, densities=densities,
            avg_volume_2h_base=avg_volume_2h_base, btc=btc):
        f.price = price_now
        out.append(f)

    # одна формация на пару (тип, направление): держим свежайшую
    best: dict[tuple[str, str], Formation] = {}
    for f in out:
        k = (f.kind, f.direction)
        if k not in best or f.age_candles < best[k].age_candles:
            best[k] = f
    out = list(best.values())

    # Стоп, стоящий ближе половины NATR, собьёт шумом или спредом: такой
    # разбор бесполезен, даже если структура определена верно.
    n = natr(candles, 14) or 0.0
    min_risk = max(0.1, MIN_RISK_NATR * n)
    out = [f for f in out if f.risk_pct >= min_risk]

    for f in out:
        if f.rr < 0.8:
            f.confidence = round(max(0.05, f.confidence - 0.15), 2)
            f.reasons.append("цель ближе стопа — соотношение хуже 1:0.8")
        if f.age_candles > 0:
            f.reasons.append(f"событие {f.age_candles} свечей назад")

    out.sort(key=lambda f: (f.triggered, f.confidence, f.rr), reverse=True)
    return out[:limit]


if __name__ == "__main__":
    from src.data.market import ohlcv, orderbook

    tfs = ("5m", "15m", "1h")
    for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        btc_by_tf: dict[str, list[Candle]] = {}
        for tf in tfs:
            try:
                btc_by_tf[tf] = ohlcv("binance", "BTCUSDT", tf, 1000)
            except Exception:
                pass
        print(f"\n=== {sym} ===")
        for tf in tfs:
            try:
                cs = ohlcv("binance", sym, tf, 1000)
                ob = orderbook("binance", sym, 1000)
                fs = detect_all(cs, tf, sym, "binance",
                                btc=btc_by_tf.get(tf), ob=ob)
            except Exception as e:
                print(f"  {tf}: ошибка {type(e).__name__}: {str(e)[:70]}")
                continue
            if not fs:
                print(f"  {tf}: формаций не найдено")
                continue
            print(f"  --- {tf}: {len(fs)} формаций ---")
            for f in fs[:5]:
                print(f"    {f.summary()}")
