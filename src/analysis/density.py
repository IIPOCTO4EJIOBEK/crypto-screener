"""Плотности стакана: крупные лимитные заявки, их жизнь и признаки спуфинга.

Плотность — уровень в стакане, объём которого резко выделяется на фоне
остальных. Порог задаётся относительно самого стакана (во сколько раз
уровень крупнее медианного), а не абсолютной суммой: у BTC и у мелкой
монеты «крупная заявка» — разные числа.

Отдельно решается главный вопрос по плотностям: настоящая она или
выставлена для вида. Настоящая стоит на месте и её объём растёт, когда цена
подходит (её начинают есть — значит, за ней реально стоят). Спуфинг исчезает
при приближении цены: заявка была нужна, чтобы сдвинуть стакан, а не чтобы
по ней торговали.

Про айсберг судить по одному снимку нельзя — видно только верхушку.
Признак айсберга — плотность, которая долго не кончается: объём съедается
сделками, а размер заявки на том же уровне восстанавливается.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median

from src.data.market import OrderBook

MIN_LEVELS = 8           # меньше уровней — стакан слишком тонкий для оценок
OUTLIER_K = 6.0          # во сколько раз уровень крупнее медианного
MIN_NOTIONAL = 50_000    # отсечка пыли, в котируемой валюте
MIN_DISTANCE_PCT = 0.1   # ближе этого к цене — это верх стакана, не плотность
MAX_DISTANCE_PCT = 3.0   # дальше этого цена до плотности не дойдёт

# Порог 0.1 % — это 10 базисных пунктов. Измерения на крипторынке
# (arXiv 2504.15908, разбор в docs/research/03-formacii-i-otrabotki.md,
# раздел 2) дают: подозрительные на спуфинг заявки стоят в среднем в
# 7.45 б.п. от середины, обычные — в 1.03 б.п. То есть настоящая крупная
# заявка живёт дальше от лучшей цены, чем поток мелких.

# Верх стакана крупный всегда: у лучшей цены скапливается весь поток.
# Как уровень, от которого цена отскакивает, он бесполезен — она и так
# стоит на нём. Плотностью считается только то, до чего цене надо дойти.


@dataclass
class Density:
    """Крупная лимитная заявка в стакане."""
    side: str            # "bid" | "ask"
    price: float
    size: float          # объём в базовой монете
    notional: float      # объём в котируемой (обычно USDT)
    distance_pct: float  # от середины стакана, со знаком
    first_seen: int = 0
    last_seen: int = 0
    snapshots: int = 1
    min_size: float = 0.0
    max_size: float = 0.0
    eaten: float = 0.0   # сколько объёма исчезло за время наблюдения

    @property
    def age_ms(self) -> int:
        return max(0, self.last_seen - self.first_seen)

    def absorb_seconds(self, avg_volume_2h_base: float) -> float | None:
        """Сколько секунд рынок будет «съедать» эту плотность.

        Оценка Digash: размер заявки ÷ (средний объём за 2 часа × 2).
        Число условное — оно говорит о порядке величины, не о точном сроке.
        """
        if avg_volume_2h_base <= 0 or self.size <= 0:
            return None
        return self.size / (avg_volume_2h_base * 2) * 3600


def median_level_size(ob: OrderBook, depth: int = 50) -> float:
    sizes = [l.size for l in ob.bids[:depth]] + [l.size for l in ob.asks[:depth]]
    if len(sizes) < MIN_LEVELS:
        return 0.0
    return median(sizes)


def find_densities(ob: OrderBook, top: int = 5,
                   outlier_k: float = OUTLIER_K,
                   depth: int = 500) -> list[Density]:
    """Крупные заявки в стакане, самые близкие к цене — первыми.

    depth по умолчанию велик намеренно: у BTC весь стакан на сотню уровней
    укладывается в десятые доли процента, и при depth=100 плотностей дальше
    0.1 % от цены просто не видно.
    """
    base = median_level_size(ob, depth)
    if base <= 0:
        return []
    threshold = base * outlier_k
    mid = ob.mid
    if mid <= 0:
        return []

    out: list[Density] = []
    for side, levels in (("bid", ob.bids), ("ask", ob.asks)):
        for lv in levels[:depth]:
            if lv.size < threshold:
                continue
            notional = lv.size * lv.price
            if notional < MIN_NOTIONAL:
                continue
            dist = (lv.price - mid) / mid * 100
            if abs(dist) < MIN_DISTANCE_PCT or abs(dist) > MAX_DISTANCE_PCT:
                continue
            out.append(Density(side, lv.price, lv.size, notional, dist,
                               ob.ts, ob.ts, 1, lv.size, lv.size))
    out.sort(key=lambda d: abs(d.distance_pct))
    return out[:top]


@dataclass
class DensityTracker:
    """Следит за плотностями между снимками стакана.

    Нужен, чтобы отличить настоящую заявку от спуфинга: сравнивать можно
    только наблюдая одну и ту же цену в динамике.
    """
    tolerance_pct: float = 0.05   # в пределах какого расстояния считать «та же цена»
    min_life_ms: int = 5_000      # короче этого срока судить не о чем
    history: list[Density] = field(default_factory=list)
    _live: dict[tuple[str, float], Density] = field(default_factory=dict)

    def _key(self, d: Density) -> tuple[str, float]:
        # округляем цену, чтобы микро-сдвиги не плодили новые плотности
        step = max(d.price * self.tolerance_pct / 100, 1e-12)
        return (d.side, round(d.price / step) * step)

    def update(self, ob: OrderBook, **kwargs) -> list[Density]:
        """Принять новый снимок, вернуть плотности с накопленной историей."""
        current = find_densities(ob, **kwargs)
        seen: set[tuple[str, float]] = set()

        for d in current:
            k = self._key(d)
            seen.add(k)
            old = self._live.get(k)
            if old is None:
                self._live[k] = d
                continue
            # заявка «выросла» — значит, её не только ели, но и подставляли.
            # eaten — это не сумма перепадов, а сколько объёма нет сейчас
            # относительно наблюдавшегося максимума: суммировать перепады
            # нельзя, при монотонном убывании пик 100 → 90 → 80 даёт 30
            # вместо 20.
            old.size = d.size
            old.notional = d.notional
            old.distance_pct = d.distance_pct
            old.last_seen = d.last_seen
            old.snapshots += 1
            old.max_size = max(old.max_size, d.size)
            old.min_size = min(old.min_size, d.size)
            old.eaten = max(0.0, old.max_size - old.size)

        # исчезнувшие переносим в историю. Живые сюда НЕ добавляем: иначе
        # history превращается в лог наблюдений, и vanished_near, который
        # ищет исчезнувшие, возвращает то, что стоит в стакане прямо сейчас.
        for k in [k for k in self._live if k not in seen]:
            self.history.append(self._live.pop(k))

        return current

    def vanished_near(self, price: float, within_pct: float = 0.3) -> list[Density]:
        """Плотности, которые исчезли рядом с ценой — возможный спуфинг."""
        out = []
        for d in self.history[-40:]:
            if abs(d.price - price) / price * 100 <= within_pct:
                out.append(d)
        return out

    def spoof_score(self, d: Density) -> float:
        """От 0 (настоящая) до 1 (похоже на спуфинг).

        Признаки спуфинга: живёт недолго, исчезает при подходе цены,
        объём не растёт, а падает.
        """
        score = 0.0
        if d.age_ms < self.min_life_ms:
            score += 0.3
        # Стоит близко к цене и при этом не съедается — заявка для вида.
        # Нижняя граница обязательна: всё, что ближе MIN_DISTANCE_PCT,
        # find_densities отбрасывает как верх стакана, и условие вида
        # «distance_pct < MIN_DISTANCE_PCT» не выполнилось бы никогда.
        if (d.eaten == 0 and MIN_DISTANCE_PCT <= abs(d.distance_pct)
                < MIN_DISTANCE_PCT * 2.5):
            score += 0.2
        if d.size < d.max_size * 0.5:
            score += 0.3  # объём просел вдвое
        if d.snapshots <= 2:
            score += 0.2  # наблюдалась слишком мало
        return min(score, 1.0)


if __name__ == "__main__":
    from src.data.market import orderbook

    for ex in ("binance", "bybit", "okx"):
        ob = orderbook(ex, "BTCUSDT", 100)
        ds = find_densities(ob, top=4)
        print(f"\n{ex}: середина {ob.mid:.2f}, спред {ob.spread:.2f}, "
              f"дисбаланс {ob.imbalance(50):+.3f}, медианный уровень "
              f"{median_level_size(ob):.4f} BTC")
        for d in ds:
            print(f"   {d.side:3} {d.price:10.2f}  {d.size:8.4f} BTC  "
                  f"${d.notional:>12,.0f}  до цены {d.distance_pct:+.2f}%")
