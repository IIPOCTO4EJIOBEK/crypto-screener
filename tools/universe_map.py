"""Первый месяц каждой монеты в архиве Binance USDT-M.

Зачем: проверять правило на вселенной, собранной по сегодняшнему объёму,
нельзя — тогда в числа попадают только те монеты, которые выросли к
сегодняшнему дню. Карта первых месяцев позволяет собрать состав на дату:
взять тех, у кого свечи уже были в начале окна измерения, вместе с теми, кто
с тех пор подешевел или вовсе перестал торговаться. Так сделана проверка в
`docs/research/16-тренд-фильтр-измерение.md`, §9.

Как читать вывод: `universe.json` — отображение «монета → первый месяц с
дневными барами» (`null`, если файлов нет вовсе). Вселенная на дату — это
фильтр по этой карте, а не отдельный список: список пришлось бы обновлять
руками, а карта пересчитывается одной командой.

Список каталогов S3 отдаётся страницами по 1000, поэтому нужен маркер
продолжения. По каждой монете запрашивается каталог её дневных файлов —
оттуда берётся минимальный месяц.

Запуск:

    .venv/bin/python -m tools.universe_map                # в data/universe.json
    .venv/bin/python -m tools.universe_map --out /tmp/u.json --workers 16
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
PREFIX = "data/futures/um/monthly/klines/"

# Архив отвечает 200 и напрямую, и через прокси, но прокси из окружения
# периодически отдаёт 407 «требуется авторизация» — при тысяче запросов это
# роняет сбор. Прямое соединение к data.binance.vision работает, поэтому идём
# мимо прокси (trust_env=False).
_SESSION = requests.Session()
_SESSION.trust_env = False


def list_symbols() -> list[str]:
    """Каталоги монет в архиве: страницы по 1000 с маркером продолжения."""
    names: list[str] = []
    marker = None
    while True:
        params = {"delimiter": "/", "prefix": PREFIX, "max-keys": "1000"}
        if marker:
            params["marker"] = marker
        r = _SESSION.get(ROOT, params=params, timeout=60)
        r.raise_for_status()
        names += re.findall(rf"<Prefix>{PREFIX}([^/]+)/</Prefix>", r.text)
        if "<IsTruncated>true</IsTruncated>" not in r.text:
            break
        found = re.findall(r"<NextMarker>([^<]+)</NextMarker>", r.text)
        if not found:
            break
        marker = found[0]
    return names


def first_month(symbol: str) -> tuple[str, str | None]:
    """Минимальный месяц с дневными барами, либо None.

    Месячные свечи лежат не в каталоге монеты, а внутри подкаталога
    таймфрейма: .../klines/BTCUSDT/1d/BTCUSDT-1d-2020-01.zip. При
    delimiter=/ файлы верхнего уровня не отдаются — оттуда приходят только
    подкаталоги, поэтому каталог монеты спрашивать бесполезно.
    """
    for attempt in range(5):
        try:
            r = _SESSION.get(
                ROOT, params={"delimiter": "/", "max-keys": "1000",
                              "prefix": f"{PREFIX}{symbol}/1d/"},
                timeout=60)
        except requests.RequestException:
            time.sleep(1 + attempt)
            continue
        if r.status_code == 200:
            months = re.findall(r"(\d{4}-\d{2})\.zip</Key>", r.text)
            return symbol, (min(months) if months else None)
        time.sleep(1 + attempt)      # 503 SlowDown при параллельных запросах
    return symbol, None


def build_map(workers: int = 8, progress=print) -> dict[str, str | None]:
    syms = list_symbols()
    progress(f"каталогов в архиве: {len(syms)}")
    out: dict[str, str | None] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, (sym, month) in enumerate(ex.map(first_month, syms), start=1):
            out[sym] = month
            if i % 200 == 0:
                progress(f"  опрошено {i}")
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out", default="data/universe.json", metavar="FILE")
    p.add_argument("--workers", type=int, default=8)
    a = p.parse_args()

    out = build_map(a.workers, progress=lambda s: print(s, file=sys.stderr))
    dst = Path(a.out)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("w", encoding="utf-8") as f:
        json.dump(out, f, indent=0, sort_keys=True)

    early = sorted(s for s, m in out.items() if m and m <= "2020-02")
    print(f"всего {len(out)}, с данными до 2020-02: {len(early)} → {dst}")
    print(", ".join(early))


if __name__ == "__main__":
    main()
