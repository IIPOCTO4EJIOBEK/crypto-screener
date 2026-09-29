"""Проверка: предсказывает ли поток заявок будущее движение цены.

Сравниваются два взгляда на стакан:

  форма (imbalance)   — сколько объёма стоит снизу против верха прямо сейчас;
  изменение (OFI)     — насколько стакан сдвинулся с прошлого снимка.

Разбор микроструктуры утверждает, что почти всё короткое преимущество — во
втором: заявку можно выставить для вида, и форма обманет, а чтобы изменить
стакан, надо совершить действие. Проверяем это на архиве Binance.

Данные: bookDepth (кумулятивная глубина по полосам, снимок каждые ~30 с) и
свечи 1m для цены.

Как считается доходность. Снимок попадает внутрь минуты, и брать цену закрытия
этой минуты нельзя: она ставится через несколько десятков секунд ПОСЛЕ снимка,
то есть признак частично подглядывает в собственный ответ. Поэтому вход — цена
открытия первой свечи, открывшейся не раньше снимка, а горизонт H — закрытие
H-й такой свечи. Окно доходности целиком лежит после признака.

Окна не перекрываются: снимки берутся не подряд, а через H минут. Иначе десяти
соседним снимкам отвечает одна и та же будущая доходность, наблюдения
перестают быть независимыми, и на чистом шуме выходит «значимость» с p≈1e-9
(в этом проекте так уже случалось). Контроль — тот же признак, сдвинутый на
сутки назад: он обязан дать ноль, иначе расчёт сломан.
"""

from __future__ import annotations

import bisect
import sys
from datetime import date, timedelta

import numpy as np

from src.analysis.liquidity import imbalance
from src.analysis.micro import ofi_from_depth
from src.data import archive

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT")
HORIZONS = (1, 5, 15, 30)     # минуты вперёд
SNAPSHOTS_PER_MIN = 2         # bookDepth снимается каждые ~30 секунд
CONTROL_SHIFT_MIN = 1440      # контроль: тот же признак сутками раньше
MIN_OBS = 100                 # ниже этого вывод не делаем


def _candles_lookup(candles):
    ts = [c.ts for c in candles]
    op = np.array([c.open for c in candles], dtype=float)
    cl = np.array([c.close for c in candles], dtype=float)
    return ts, op, cl


def _forward_return(ts: list[int], op: np.ndarray, cl: np.ndarray,
                    snap_ts: int, minutes: int) -> float:
    """Доходность на H минут вперёд, окно строго после снимка.

    k — первая свеча, открывшаяся не раньше снимка; вход по её открытию,
    выход по закрытию свечи k+H-1.
    """
    k = bisect.bisect_left(ts, snap_ts)
    j = k + minutes - 1
    if k >= len(ts) or j >= len(ts):
        return np.nan
    base = op[k]
    if base <= 0:
        return np.nan
    return (cl[j] / base - 1) * 100


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    """Корреляция рангов. Своими руками: scipy в этом окружении нет."""
    if len(a) < 3:
        return 0.0
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    ra -= ra.mean()
    rb -= rb.mean()
    denom = float(np.sqrt((ra ** 2).sum() * (rb ** 2).sum()))
    return float((ra * rb).sum() / denom) if denom else 0.0


def _corr_pair(s: np.ndarray, f: np.ndarray) -> tuple[int, float, float, float]:
    mask = np.isfinite(s) & np.isfinite(f)
    s, f = s[mask], f[mask]
    if len(s) < MIN_OBS:
        return len(s), np.nan, np.nan, np.nan
    if s.std() == 0 or f.std() == 0:
        return len(s), 0.0, 0.0, float((np.sign(s) == np.sign(f)).mean() * 100)
    pear = float(np.corrcoef(s, f)[0, 1])
    spear = _spearman(s, f)
    hit = float((np.sign(s) == np.sign(f)).mean() * 100)
    return len(s), pear, spear, hit


def _row(name: str, minutes: int, s: np.ndarray, f: np.ndarray) -> str:
    n, pear, spear, hit = _corr_pair(s, f)
    if n < MIN_OBS:
        return f"  {name:13} {minutes:>3} мин: наблюдений {n} — тест не состоялся"
    return (f"  {name:13} {minutes:>3} мин: n={n:>6}  пирсон {pear:+.4f}  "
            f"спирмен {spear:+.4f}  знак совпал {hit:5.1f}%")


def study(symbol: str, start: date, end: date) -> list[str]:
    bd = archive.load_book_depth(symbol, start, end, workers=8)
    kl = archive.load_klines(symbol, "1m", start, end, workers=8)
    snaps = bd.rows
    if len(snaps) < MIN_OBS or len(kl.rows) < MIN_OBS:
        return [f"{symbol}: снимков {len(snaps)}, свечей {len(kl.rows)} — мало данных"]

    ts, op, cl = _candles_lookup(kl.rows)
    n = len(snaps)

    ofi = np.full(n, np.nan)
    imb = np.full(n, np.nan)
    for i, snap in enumerate(snaps):
        imb[i] = imbalance(snap, 1.0)
        if i:
            ofi[i] = ofi_from_depth(snaps[i - 1], snap)

    fwd: dict[int, np.ndarray] = {}
    for h in HORIZONS:
        col = np.array([_forward_return(ts, op, cl, s.ts, h) for s in snaps])
        fwd[h] = col

    lines = [f"\n=== {symbol}: снимков {n}, свечей {len(kl.rows)}, "
             f"дней {bd.loaded}/{bd.loaded + bd.skipped} ==="]
    valid = fwd[5][np.isfinite(fwd[5])]
    if len(valid):
        lines.append(f"  цена вверх на 5 мин: {np.mean(valid > 0) * 100:.1f}% случаев, "
                     f"разброс {np.std(valid):.3f}%")

    shift = CONTROL_SHIFT_MIN * SNAPSHOTS_PER_MIN
    ctrl = np.concatenate([np.full(shift, np.nan), ofi[:-shift]]) \
        if shift < n else np.full(n, np.nan)

    half = n // 2
    for h in HORIZONS:
        # окна не перекрываются: шаг = H минут = H * снимков в минуте
        step = max(1, h * SNAPSHOTS_PER_MIN)
        sl = slice(None, None, step)
        lines.append(_row("форма", h, imb[sl], fwd[h][sl]))
    lines.append("")
    for h in HORIZONS:
        step = max(1, h * SNAPSHOTS_PER_MIN)
        sl = slice(None, None, step)
        lines.append(_row("изменение", h, ofi[sl], fwd[h][sl]))
    lines.append("")
    for h in HORIZONS:
        step = max(1, h * SNAPSHOTS_PER_MIN)
        sl = slice(None, None, step)
        lines.append(_row("контроль-сдвиг", h, ctrl[sl], fwd[h][sl]))

    lines.append("")
    for name, part in (("первая половина", slice(0, half)),
                       ("вторая половина", slice(half, None))):
        for h in (5, 15):
            step = max(1, h * SNAPSHOTS_PER_MIN)
            idx = range(part.start or 0, part.stop or n, step)
            s = ofi[list(idx)]
            f = fwd[h][list(idx)]
            lines.append(_row(f"изм.·{name}", h, s, f))
    return lines


def main() -> None:
    syms = tuple(sys.argv[1:]) or SYMBOLS
    end = date(2026, 9, 26)
    start = end - timedelta(days=45)
    print(f"период {start} .. {end}, горизонты {HORIZONS} мин")
    for sym in syms:
        try:
            for line in study(sym, start, end):
                print(line, flush=True)
        except Exception as exc:                      # noqa: BLE001
            print(f"{sym}: сбой — {type(exc).__name__}: {exc}", flush=True)


if __name__ == "__main__":
    main()
