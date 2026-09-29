"""Ликвидность по историческому стакану Binance (bookDepth).

Работает с кумулятивным снимком глубины: bookDepth отдаёт 12 полос, где
depth/notional — это объём ОТ середины ДО полосы, а не объём самой полосы
(проверено: растёт монотонно от 0 к ±5 %). Поэтому «стенка» ищется не по
сырому notional, а по ПРИРАЩЕНИЮ между соседними полосами — объёму,
который реально стоит в ценовом коридоре между двумя границами.

Определения:

  Стенка — полоса (коридор между двумя соседними процентными границами),
  чей прирост notional резко выше медианного по тому же снимку. Порог —
  во сколько раз полоса крупнее медианы (WALL_K), а не абсолютная сумма:
  у BTC и у мелкой монеты «крупная стена» — разные числа.

  Перекос стакана (imbalance) — суммарный объём снизу против сверху в
  пределах ±1 % от середины.

  Миграция ликвидности — стенка стоит на месте (прибита к абсолютной цене)
  или ползёт за ценой (держится на постоянном проценте от середины). Число
  одно: корреляция между абсолютной ценой стенки и серединой стакана.

  Выведенные пулы по структуре — уровни, где по свечам стоит толпа:
  равные хаи/лои (два и более экстремума в допуске) и круглые числа. Это
  замена настоящего стакана, когда его нет; сравнение с bookDepth
  проверяет, законна ли замена.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from src.analysis.levels import find_pivots
from src.data.archive import BookDepthSnapshot
from src.data.market import Candle

WALL_K = 3.0          # во сколько раз полоса крупнее медианы снимка = стенка
EQUAL_EXTREMES_TOL = 0.15   # допуск «равных» экстремумов, % (0.1–0.2 % по ТЗ)
MIN_EQUAL_TOUCHES = 2       # сколько экстремумов нужно для пула


@dataclass(frozen=True)
class Slice:
    """Коридор между двумя соседними процентными границами снимка.

    notional — прирост объёма именно в этом коридоре, USDT.
    """
    side: str          # "bid" | "ask"
    # Границы в процентах от середины, по возрастанию числа (lo < hi). На
    # bid-стороне ближе к середине именно hi, на ask — lo; стороны в коде
    # не различаются, потому что приращение считается по знаку глубины.
    lo_pct: float
    hi_pct: float
    depth: float       # прирост объёма в базовой монете
    notional: float    # прирост объёма в USDT

    @property
    def center_pct(self) -> float:
        """Середина коридора в процентах от середины (со знаком)."""
        return (self.lo_pct + self.hi_pct) / 2

    @property
    def distance_pct(self) -> float:
        return abs(self.center_pct)

    @property
    def width_pct(self) -> float:
        return abs(self.hi_pct - self.lo_pct)


@dataclass(frozen=True)
class Migration:
    """Итог измерения миграции ликвидности."""
    n: int                       # сколько снимков вошло
    corr_wall_mid: float         # корреляция цены стенки с серединой, -1..1
    median_distance_pct: float   # типичное расстояние стенки от середины, %
    side_consistency: float      # доля переходов, где сторона стенки не сменилась


@dataclass(frozen=True)
class StructurePool:
    """Выведенный по свечам пул ликвидности (без настоящего стакана)."""
    price: float
    kind: str          # "equal_high" | "equal_low" | "round"
    touches: int = 1   # для equal — сколько экстремумов совпало
    first_ts: int = 0
    last_ts: int = 0


def _band_edges(snap: BookDepthSnapshot) -> list[tuple[str, float, float]]:
    """Коридоры снимка: (сторона, ближняя к середине граница, дальняя).

    Границы берутся из фактических процентов снимка, а не фиксируются:
    bookDepth до 2026-01-15 имел 10 полос (без ±0.2 %), позже — 12.
    Середина (0 %) — кумулятивная граница с нулевой глубиной, она в
    полосах не числится.
    """
    pcts = sorted(b.percentage for b in snap.bands)
    neg = sorted((p for p in pcts if p < 0), reverse=True)  # ближние первыми
    pos = sorted(p for p in pcts if p > 0)
    edges: list[tuple[str, float, float]] = []
    prev = 0.0
    for p in neg:
        edges.append(("bid", p, prev))   # p дальше от середины, prev ближе
        prev = p
    prev = 0.0
    for p in pos:
        edges.append(("ask", prev, p))
        prev = p
    return edges


def marginal_slices(snap: BookDepthSnapshot) -> list[Slice]:
    """Разложить кумулятивный снимок на коридоры с приростом объёма.

    Для bid прирост = depth(дальняя граница) − depth(ближняя), для ask —
    наоборот. Внутренний коридор на 12-полосных днях (±0.2 %) уже прочих
    по ширине цены (0.2 % против ~1 %), на 10-полосных (январь до 15-го)
    все коридоры по ~1 % — это стоит держать в уме при сравнении notional.
    """
    d = {b.percentage: b.depth for b in snap.bands}
    n = {b.percentage: b.notional for b in snap.bands}
    out: list[Slice] = []
    for side, lo, hi in _band_edges(snap):
        # 0.0 % отсутствует в полосах — это сама середина, кумулятивная
        # глубина на ней равна нулю.
        dlo, dhi = d.get(lo, 0.0), d.get(hi, 0.0)
        nlo, nhi = n.get(lo, 0.0), n.get(hi, 0.0)
        if side == "bid":
            out.append(Slice(side, lo, hi, dlo - dhi, nlo - nhi))
        else:
            out.append(Slice(side, lo, hi, dhi - dlo, nhi - nlo))
    return out


def find_walls(snap: BookDepthSnapshot, k: float = WALL_K) -> list[Slice]:
    """Стенки снимка: коридоры с notional ≥ k × медианы, крупные — первыми.

    Порог 3 взят эмпирически: в снимке 12 коридоров, и самый крупный
    обычно в ~3.3 раза больше медианы (проверено на суточном BTCUSDT).
    Поэтому k=3 выделяет доминирующий коридор, не вырезая всё подряд.
    """
    slices = marginal_slices(snap)
    vals = [s.notional for s in slices]
    med = median(vals)
    if med <= 0:
        return []
    walls = [s for s in slices if s.notional >= k * med]
    walls.sort(key=lambda s: -s.notional)
    return walls


def wall_price(s: Slice, mid: float) -> float:
    """Абсолютная цена центра коридора при середине стакана mid."""
    return mid * (1 + s.center_pct / 100)


def imbalance(snap: BookDepthSnapshot, band_pct: float = 1.0) -> float:
    """Дисбаланс объёмов бид/аск в пределах ±band_pct % от середины.

    От −1 (всё в асках) до +1 (всё в бидах). Используется кумулятивный
    объём до границы band_pct — это ровно «суммарный объём снизу против
    сверху в пределах ±1 %».
    """
    d = {b.percentage: b.notional for b in snap.bands}
    bid = d.get(-band_pct, 0.0)
    ask = d.get(band_pct, 0.0)
    if bid + ask == 0:
        return 0.0
    return (bid - ask) / (bid + ask)


def migration_stats(snapshots: list[BookDepthSnapshot],
                    prices: list[float], k: float = WALL_K) -> Migration:
    """Как профиль глубины меняется во времени.

    prices[i] — середина стакана в момент snapshots[i]. Для каждого снимка
    берётся самая крупная стенка; считается корреляция между её абсолютной
    ценой и серединой. ~1 — стенка ползёт за ценой (постоянный процент),
    ~0 — стенка прибита к абсолютному уровню.
    """
    wall_prices: list[float] = []
    mids: list[float] = []
    prev_side: str | None = None
    same = 0
    for snap, mid in zip(snapshots, prices):
        walls = find_walls(snap, k)
        if not walls or mid <= 0:
            prev_side = None
            continue
        w = walls[0]
        wall_prices.append(wall_price(w, mid))
        mids.append(mid)
        if prev_side is not None and w.side == prev_side:
            same += 1
        prev_side = w.side
    n = len(mids)
    if n < 3:
        return Migration(0, 0.0, 0.0, 0.0)
    corr = _pearson(wall_prices, mids)
    dists = [abs(wp - m) / m * 100 for wp, m in zip(wall_prices, mids)]
    consistency = same / max(n - 1, 1)
    return Migration(n, corr, median(dists), consistency)


def _pearson(a: list[float], b: list[float]) -> float:
    n = len(a)
    if n < 2:
        return 0.0
    ma = sum(a) / n
    mb = sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = sum((x - ma) ** 2 for x in a)
    db = sum((y - mb) ** 2 for y in b)
    if da == 0 or db == 0:
        return 0.0
    return num / (da * db) ** 0.5


def find_equal_extremes(candles: list[Candle], tf: str = "5m",
                        tol_pct: float = EQUAL_EXTREMES_TOL
                        ) -> list[StructurePool]:
    """Равные хаи и лои: 2+ экстремума одного типа в допуске tol_pct %.

    Это «пулы стопов по структуре»: толпа ставит стопы за равными
    экстремумами, и эти места должны притягивать цену так же, как стенка
    в настоящем стакане.
    """
    pivots = find_pivots(candles)
    if not pivots:
        return []
    tol = tol_pct / 100
    clusters: dict[str, list[list[tuple[int, float]]]] = {"high": [], "low": []}

    def ref_price(cluster: list[tuple[int, float]]) -> float:
        return sum(p for _, p in cluster) / len(cluster)

    for idx, price, kind in pivots:
        best, best_d = None, float("inf")
        for c in clusters[kind]:
            rp = ref_price(c)
            if rp <= 0:
                continue
            d = abs(price - rp) / rp
            if d <= tol and d < best_d:
                best, best_d = c, d
        if best is not None:
            best.append((idx, price))
        else:
            clusters[kind].append([(idx, price)])

    pools: list[StructurePool] = []
    for kind, cs in clusters.items():
        for c in cs:
            if len(c) < MIN_EQUAL_TOUCHES:
                continue
            avg = sum(p for _, p in c) / len(c)
            pools.append(StructurePool(
                avg, "equal_high" if kind == "high" else "equal_low",
                len(c), candles[c[0][0]].ts, candles[c[-1][0]].ts))
    pools.sort(key=lambda p: p.price)
    return pools


def round_levels_near(price: float, step: float = 1000.0,
                      half: int = 1) -> list[float]:
    """Круглые уровни около цены: кратные step и полушаги, ±половина шага."""
    if price <= 0 or step <= 0:
        return []
    lo = price - half * step
    hi = price + half * step
    k0 = int(lo // step)
    out: list[float] = []
    k = k0
    while k * step <= hi:
        if k * step >= lo:
            out.append(k * step)
            out.append(k * step + step / 2)
        k += 1
    return sorted(set(out))


def nearest_round_distance_pct(price: float, step: float = 1000.0) -> float:
    """Расстояние от цены до ближайшего кратного step, в % от цены."""
    if price <= 0 or step <= 0:
        return float("inf")
    m = round(price / step) * step
    return abs(price - m) / price * 100


def is_near_level(price: float, level: float, tol_pct: float) -> bool:
    if price <= 0 or level <= 0:
        return False
    return abs(price - level) / price * 100 <= tol_pct


if __name__ == "__main__":
    from datetime import date

    from src.data.archive import load_book_depth
    from src.data.market import ohlcv

    snapshots = load_book_depth("BTCUSDT", date(2026, 9, 28), date(2026, 9, 28),
                                workers=1).rows
    s = snapshots[1000]
    print(f"снимков {len(snapshots)}, пример ts={s.ts}")
    print("коридоры (side, границы, notional):")
    for sl in marginal_slices(s):
        mark = " <== стенка" if sl.notional >= WALL_K * median(
            [x.notional for x in marginal_slices(s)]) else ""
        print(f"  {sl.side:3} {sl.lo_pct:>5}..{sl.hi_pct:>5}%  "
              f"${sl.notional:>15,.0f}{mark}")
    walls = find_walls(s)
    print(f"стенок: {len(walls)}")
    print(f"дисбаланс ±1%: {imbalance(s):+.3f}")

    cs = ohlcv("binance", "BTCUSDT", "5m", 500)
    eq = find_equal_extremes(cs)
    print(f"равных экстремумов: {len(eq)}")
    for p in eq[:5]:
        print(f"  {p.kind:11} {p.price:10.2f}  касаний {p.touches}")
    print("круглые уровни около 83461:", round_levels_near(83461.0)[:8])
