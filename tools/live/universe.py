"""Вселенная контура: какие монеты показываем и почему именно эти.

Отбор идёт по суточному обороту, а не по списку имён: список пришлось бы
править руками при каждом листинге. Порог задаётся в котируемой валюте за
сутки; монеты берутся из перпетуалов USDT-M, а токенизированные акции,
металлы и нефть отсеиваются биржевым полем `underlyingType` (см.
`market.futures_coin_universe`).

Результат кладётся в файл, и страницы читают **его**, а не «что накопилось
в базе». Разница существенная: база хранит всё, что когда-либо собиралось,
поэтому монета, упавшая ниже порога, оставалась бы на странице навсегда.
Файл — это срез на момент отбора, с датой и порогом внутри, чтобы страница
могла сказать, откуда взялся её состав.

Запуск:

    tools/live/universe.py --min-volume 100m --out data/universe-turnover.json
    tools/live/universe.py --min-volume 100e6 --show    # только посмотреть

Имя файла не `universe.json` намеренно: так уже называется карта «монета →
первый месяц с историей» от `tools/universe_map.py` — артефакт измерения
тренд-фильтра, на который ссылается `docs/research/16`. Это разные вещи:
карта пересобирается редко и лежит в репозитории, срез оборота меняется
каждый круг и в репозиторий не идёт.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data import market            # noqa: E402

DEFAULT_PATH = ROOT / "data" / "universe-turnover.json"
WATCHLIST_PATH = ROOT / "data" / "watchlist.txt"
MIN_QUOTE_VOLUME = 100e6
SUPPORTED = ("binance_futures",)

_SUFFIX = {"k": 1e3, "m": 1e6, "b": 1e9}


def parse_volume(text: str) -> float:
    """Порог оборота: `100e6`, `100m`, `100000000` — запись одна и та же."""
    t = str(text).strip().lower().replace("_", "").replace(" ", "")
    mult = 1.0
    if t and t[-1] in _SUFFIX:
        mult = _SUFFIX[t[-1]]
        t = t[:-1]
    try:
        v = float(t) * mult
    except ValueError:
        raise SystemExit(f"не разобрать порог оборота: {text!r}")
    if not v > 0:
        raise SystemExit(f"порог оборота должен быть больше нуля: {text!r}")
    return v


def volume_text(v: float | None) -> str:
    """Оборот словами: 100 000 000 → «100 млн». Округление — до целого
    миллиона ниже десяти миллиардов, иначе знаки перестают что-либо значить."""
    if v is None:
        return "—"
    if abs(v) >= 1e9:
        return f"{v / 1e9:.1f} млрд".replace(".0 ", " ")
    if abs(v) >= 1e6:
        return f"{v / 1e6:.0f} млн"
    if abs(v) >= 1e3:
        return f"{v / 1e3:.0f} тыс"
    return f"{v:.0f}"


def _msk() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M МСК")


def pick(exchange: str, min_quote_volume: float, *,
         contracts: list[dict] | None = None,
         volumes: dict[str, float] | None = None,
         watchlist_path: str | Path | None = None) -> dict:
    """Срез вселенной. Сеть дёргается один раз — тикеры отдают весь рынок.

    К монетам по обороту добавляются монеты из ручного списка
    (`data/watchlist.txt`), даже если их оборот ниже порога.
    """
    if exchange not in SUPPORTED:
        raise SystemExit(
            f"отбор по обороту есть только для {', '.join(SUPPORTED)}; "
            f"для {exchange} состав монет придётся задавать списком (--symbols)")
    if contracts is None:
        contracts = market.binance_futures_contracts()
    if volumes is None:
        volumes = market.binance_futures_volumes()
    pairs = market.futures_coin_universe(min_quote_volume,
                                         contracts=contracts, volumes=volumes)
    symbols = [s for s, _ in pairs]
    vols = {s: v for s, v in pairs}
    # Ручной список: монета остаётся в скринере и тогда, когда её оборот
    # ниже порога. Берём только то, что реально торгуется перпетуалом, —
    # опечатка в файле не должна ронять круг.
    trading = {c.get("symbol") for c in contracts
               if c.get("contractType") == "PERPETUAL" and c.get("status") == "TRADING"}
    watch = [s for s in load_watchlist(watchlist_path) if s in trading]
    for s in watch:
        if s not in vols:
            symbols.append(s)
            if volumes.get(s) is not None:
                vols[s] = volumes[s]
    return {
        "at": _msk(),
        "at_unix": int(time.time()),
        "exchange": exchange,
        "min_quote_volume": float(min_quote_volume),
        "quote": "USDT",
        "symbols": symbols,
        "volumes": vols,
        "watch": watch,
        "source": "fapi/v1/exchangeInfo + fapi/v1/ticker/24hr",
    }


def load_watchlist(path: str | Path | None = None) -> list[str]:
    """Ручной список монет: по одной в строке, `#` — комментарий.

    `GTC`, `gtcusdt` и `GTCUSDT` — одна и та же монета. Нет файла — пустой
    список.
    """
    p = Path(path) if path else WATCHLIST_PATH
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return []
    out: list[str] = []
    for line in text.splitlines():
        s = line.split("#", 1)[0].strip().upper()
        if not s:
            continue
        if not s.endswith("USDT"):
            s += "USDT"
        if s not in out:
            out.append(s)
    return out


def load(path: str | Path) -> dict | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise SystemExit(f"{p}: срез вселенной не читается ({e})")
    if not isinstance(data.get("symbols"), list) or not data["symbols"]:
        raise SystemExit(f"{p}: в срезе нет списка монет")
    return data


def symbols_of(data: dict) -> list[str]:
    return [str(s).upper() for s in data.get("symbols") or []]


def from_arg(path: str | None) -> tuple[list[str] | None, dict | None]:
    """Срез вселенной по пути из аргумента. Без пути — (None, None).

    Возвращает и список монет, и сам срез: странице нужны оба — по списку
    она берёт монеты, из среза печатает, откуда этот список взялся.
    """
    if not path:
        return None, None
    data = load(path)
    return symbols_of(data), data


def note_of(data: dict | None) -> str:
    """Строка для страницы: откуда состав и по какому порогу."""
    if not data:
        return ""
    thr = volume_text(data.get("min_quote_volume"))
    watch = data.get("watch") or []
    extra = (f"; плюс ручной список: {', '.join(s.removesuffix('USDT') for s in watch)}"
             if watch else "")
    return (f"состав — монеты с оборотом от {thr} {data.get('quote', 'USDT')} "
            f"за сутки ({len(symbols_of(data))} шт., срез {data.get('at', '—')}){extra}")


def save(path: str | Path, data: dict) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                 encoding="utf-8")
    return p


def render_text(data: dict) -> str:
    vs = data.get("volumes") or {}
    syms = symbols_of(data)
    lines = [f"Вселенная: {data.get('exchange')}, оборот от "
             f"{volume_text(data.get('min_quote_volume'))} "
             f"{data.get('quote', 'USDT')} за сутки — монет {len(syms)}.",
             f"Срез: {data.get('at')}. Источник: {data.get('source')}.", ""]
    for i, s in enumerate(syms, 1):
        lines.append(f"{i:3d}. {s:<14} {volume_text(vs.get(s)):>9}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--exchange", default="binance_futures", choices=SUPPORTED)
    ap.add_argument("--min-volume", default="100m",
                    help="порог суточного оборота: 100m, 100e6, 100000000")
    ap.add_argument("--out", default=str(DEFAULT_PATH),
                    help=f"куда положить срез (по умолчанию {DEFAULT_PATH})")
    ap.add_argument("--show", action="store_true",
                    help="только напечатать, файл не трогать")
    a = ap.parse_args(argv)

    data = pick(a.exchange, parse_volume(a.min_volume))
    print(render_text(data))
    if a.show:
        return 0
    p = save(a.out, data)
    print(f"\nСрез записан: {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
