"""Живой контур: сбор данных → пересборка страниц → публикация, по кругу.

Один круг делает то, что раньше делалось руками в четыре команды:

    1. `collect_round` — свечи и стаканы в sqlite (глубина 100: её хватает
       структурам и скринеру; плотности тянут свой стакан сами, на 1000);
    2. `tools.live.screen`, `tools.live.structures`, `tools.live.densities`
       — страницы в `docs/live/`;
    3. `tools.live.landing` — страница входа, в двух раскладках: рядом с
       живыми страницами (repo) и для папки предпросмотра (flat);
    4. копирование в папку предпросмотра.

Страницы — отдельными процессами, а не вызовами в общем: падение сборки
одной страницы не должно уносить контур и лишать остальные свежего круга.
Собранное не удаляется: страница, которая не пересобралась, остаётся от
прошлого круга, а в статусе появляется строка об ошибке. Молчаливой
подмены старого снимка свежим не бывает — либо время сборки новое, либо
статус говорит, что круг не удался.

Запуск:

    tools/live/online.py --once                 # один круг и выход
    tools/live/online.py --interval 60          # по кругу каждую минуту
    tools/live/online.py --interval 60 --no-preview

Публикация и работа по кругу — разные режимы, и различает их `--out`:

* `--out docs/live` (по умолчанию) — страницы ложатся в проект, рядом
  появляется `index.html` со ссылками-соседями. Так собирается снимок,
  который коммитится.
* `--out` в рабочую папку (`~/.local/share/crypto-live` и подобную) —
  страницы собираются, но проект не трогается: так контур может идти
  сутками, не оставляя `git status` грязным. `index.html` в этом режиме
  не собирается вовсе — ей нечего делать рядом.

Предпросмотр (`~/.local/share/ai-preview/`) — плоская папка: подкаталогов
сервер не отдаёт, поэтому туда кладутся страницы со ссылками-соседями
(`--links flat`), а ссылки на документы уходят в GitHub. Открытая вкладка
перезагрузится сама: сервер дописывает в страницы врезку живого
обновления.

Тренд-фильтр сюда не входит намеренно: он считается по месячным архивам
Binance, а архив текущего месяца публикуется только после его конца. Его
страница — измерение, а не поток.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data import market            # noqa: E402
from src.storage import db             # noqa: E402
from tools.live import universe        # noqa: E402
from tools.live.collect import (DEFAULT_CANDLE_LIMIT, DEFAULT_DEPTH,  # noqa: E402
                                DEFAULT_SYMBOLS, DEFAULT_TIMEFRAMES,
                                collect_round)

LIVE_DIR = ROOT / "docs" / "live"
PREVIEW_DIR = Path.home() / ".local/share/ai-preview"
UNIVERSE_PATH = ROOT / "data" / "universe-turnover.json"
DEFAULT_INTERVAL = 60.0
DEFAULT_MIN_VOLUME = "100m"

# Страницы живого контура: модуль → куда кладётся html. Порядок важен —
# лендинг читает уже собранные страницы и считает по ним плитки.
#
# Третий элемент — какие таймфреймы странице нужны (`None` — все, что
# собирает круг). Скринеру нужен минутный: он про свежесть сигнала.
# Структурам он не нужен — формации на минутном ниже порога полезности, а
# страница платила за него лишними парами (39 монет × лишний ТФ ≈ 0.5 МБ
# полезной нагрузки и столько же графиков).
STRUCT_TFS = ("5m", "15m", "1h")
PAGES = (("screen", "screener.html", None),
         ("structures", "structures.html", STRUCT_TFS),
         ("densities", "densities.html", ()))

# Что кладём в плоскую папку предпросмотра: страницы контура плюс уже
# собранные страницы тренда (они обновляются измерением, не кругом).
PREVIEW_FILES = ("screener.html", "structures.html", "densities.html",
                 "trend.html", "trend-hist.html")


def _stamp() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%H:%M:%S")


def _msk() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M МСК")


def _run(module: str, args: list[str]) -> tuple[bool, str]:
    """Собрать одну страницу отдельным процессом.

    Возвращает (успех, хвост вывода). Ошибка сборки не поднимается наружу:
    круг обязан доходить до конца, иначе одна упавшая страница остановит
    и сбор данных, и публикацию остальных.
    """
    cmd = [sys.executable, "-m", f"tools.live.{module}", *args]
    try:
        r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                           timeout=300)
    except subprocess.TimeoutExpired:
        return False, f"{module}: не уложился в 300 с"
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip().splitlines()
        return False, f"{module}: код {r.returncode}, {tail[-1] if tail else '—'}"
    return True, ""


def refresh_universe(exchange: str, min_quote_volume: float,
                     contracts: list[dict] | None,
                     path: Path) -> tuple[dict | None, str | None]:
    """Пересчитать состав монет по обороту и сохранить срез.

    Классификация контрактов (что монета, а что токенизированная акция)
    приходит из `exchangeInfo` и меняется раз в листинг, поэтому передаётся
    снаружи и запрашивается один раз за процесс. Оборот меняется каждую
    минуту, поэтому запрашивается заново.

    Возвращает (срез, ошибка). При ошибке срез остаётся прошлым: пустой
    состав хуже устаревшего — он бы вычистил страницы в ноль.
    """
    try:
        data = universe.pick(exchange, min_quote_volume, contracts=contracts)
    except Exception as e:                          # noqa: BLE001
        return None, f"вселенная: {type(e).__name__}: {str(e)[:120]}"
    universe.save(path, data)
    return data, None


def round_pages(db_path: str, out_dir: Path, tfs: list[str],
                universe_path: Path | None,
                landing: bool) -> tuple[list[str], dict]:
    """Пересобрать страницы контура. Возвращает (ошибки, времена этапов).

    Состав монет страницы берут из среза вселенной, а не из базы: база
    хранит всё, что когда-либо собиралось, и монета, упавшая ниже порога,
    оставалась бы на странице навсегда.
    """
    errors: list[str] = []
    timings: dict[str, float] = {}
    out_dir.mkdir(parents=True, exist_ok=True)
    uni = ["--universe", str(universe_path)] if universe_path else []

    for module, name, own_tfs in PAGES:
        target = out_dir / name
        args = ["--db", db_path, "--html", str(target), *uni]
        if module == "screen":          # --tfs у скринера принимает список
            args += ["--tfs", *tfs]
        elif module == "structures":    # а у структур — одну строку через запятую
            page_tfs = [t for t in tfs if t in own_tfs]
            if not page_tfs:
                errors.append(
                    f"structures: ни один из таймфреймов круга "
                    f"({', '.join(tfs)}) не годится для формаций "
                    f"({', '.join(own_tfs)}) — страница не пересобрана")
                continue
            args += ["--tfs", ",".join(page_tfs)]
        started = time.time()
        ok, err = _run(module, args)
        timings[module] = time.time() - started
        if not ok:
            errors.append(err)

    if landing and out_dir.resolve() == LIVE_DIR.resolve():
        # Страница входа рядом с живыми страницами — только для репозитория.
        # В рабочей папке живого прогона ей места нет: она тянула бы за
        # собой правку репозитория на каждом круге.
        started = time.time()
        ok, err = _run("landing", ["--links", "repo", "--html",
                                   str(out_dir / "index.html")])
        timings["landing_repo"] = time.time() - started
        if not ok:
            errors.append(err)
    return errors, timings


def round_flat(landing: bool) -> tuple[Path | None, str | None]:
    """Плоская страница входа — для папки предпросмотра (ссылки в GitHub).

    Собирается во временный файл: это не содержимое проекта, а то, что
    отдаётся серверу предпросмотра, и в репозитории ему делать нечего.
    """
    if not landing:
        return None, None
    fd, path = tempfile.mkstemp(prefix="landing-flat-", suffix=".html")
    os.close(fd)
    ok, err = _run("landing", ["--links", "flat", "--html", path])
    if ok:
        return Path(path), None
    Path(path).unlink(missing_ok=True)
    return None, err



def round_preview(out_dir: Path, preview: Path, flat_html: Path | None,
                  landing: bool) -> tuple[list[str], str | None]:
    """Разложить собранное по плоской папке предпросмотра."""
    errors: list[str] = []
    preview.mkdir(parents=True, exist_ok=True)
    for name in PREVIEW_FILES:
        src = out_dir / name
        if src.exists():
            shutil.copy2(src, preview / name)
    if landing:
        if flat_html is None or not flat_html.exists():
            return ["landing(flat): страница не собрана"], None
        shutil.copy2(flat_html, preview / "index.html")
        return errors, str(preview / "index.html")
    return errors, None


def write_status(path: Path, st: dict) -> None:
    path.write_text(json.dumps(st, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def one_round(conn, *, db_path: str, tfs: list[str], exchange: str,
              depth: int, candle_limit: int, out_dir: Path,
              preview: Path | None, landing: bool, quiet: bool,
              uni: dict | None, universe_path: Path | None) -> dict:
    """Один круг: собрать данные по составу среза и пересобрать страницы."""
    started = time.time()
    errors: list[str] = []

    symbols = universe.symbols_of(uni) if uni else list(DEFAULT_SYMBOLS)
    st = collect_round(conn, symbols, exchange, tfs, candle_limit, depth,
                       verbose=not quiet)
    errors += [f"{stage}/{sym}: {exc}" for (stage, sym, exc), n in st["errors"].items()]
    page_errors, timings = round_pages(db_path, out_dir, tfs, universe_path,
                                       landing)

    flat_html, flat_err = round_flat(landing)
    if flat_err:
        errors.append(flat_err)
    errors += page_errors

    try:
        if preview is not None:
            prev_errors, _ = round_preview(out_dir, preview, flat_html, landing)
            errors += prev_errors
    finally:
        if flat_html is not None:
            flat_html.unlink(missing_ok=True)

    total = time.time() - started
    return {
        "at": _msk(),
        "at_unix": int(time.time()),
        "elapsed": round(total, 2),
        "universe": ({"n": len(symbols),
                      "min_quote_volume": uni.get("min_quote_volume"),
                      "at": uni.get("at")} if uni else None),
        "collected": {"symbols": st["symbols"], "books": st["books"],
                      "candles": st["candles"]},
        "pages": {k: round(v, 2) for k, v in timings.items()},
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--once", action="store_true", help="один круг и выход")
    ap.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                    metavar="СЕК", help=f"пауза между кругами (по умолчанию "
                                        f"{DEFAULT_INTERVAL:g} с)")
    ap.add_argument("--db", default=str(db.DEFAULT_DB_PATH))
    ap.add_argument("--out", default=str(LIVE_DIR),
                    help=f"куда класть живые страницы (по умолчанию {LIVE_DIR})")
    ap.add_argument("--preview", default=str(PREVIEW_DIR),
                    help=f"папка предпросмотра (по умолчанию {PREVIEW_DIR})")
    ap.add_argument("--no-preview", action="store_true",
                    help="не трогать папку предпросмотра")
    ap.add_argument("--no-landing", action="store_true",
                    help="не пересобирать страницу входа")
    ap.add_argument("--min-volume", default=DEFAULT_MIN_VOLUME, metavar="ОБОРОТ",
                    help="порог суточного оборота монеты: 100m, 100e6, "
                         f"100000000 (по умолчанию {DEFAULT_MIN_VOLUME})")
    ap.add_argument("--universe", default=str(UNIVERSE_PATH),
                    help=f"файл среза вселенной (по умолчанию {UNIVERSE_PATH})")
    ap.add_argument("--no-universe-refresh", action="store_true",
                    help="взять состав из файла среза, к бирже не ходить")
    ap.add_argument("--symbols", default=None,
                    help="фиксированный список монет через запятую; если задан, "
                         "отбор по обороту не делается и срез не пишется")
    ap.add_argument("--timeframes", default=",".join(DEFAULT_TIMEFRAMES))
    ap.add_argument("--exchange", default="binance_futures",
                    choices=sorted(market.EXCHANGES))
    ap.add_argument("--depth", type=int, default=DEFAULT_DEPTH)
    ap.add_argument("--candle-limit", type=int, default=DEFAULT_CANDLE_LIMIT)
    ap.add_argument("--status", default=None,
                    help="файл состояния (по умолчанию <out>/status.json)")
    ap.add_argument("-q", "--quiet", action="store_true")
    a = ap.parse_args(argv)

    fixed = ([s.strip().upper() for s in a.symbols.split(",") if s.strip()]
             if a.symbols else None)
    tfs = [t.strip() for t in a.timeframes.split(",") if t.strip()]
    out_dir = Path(a.out)
    preview = None if a.no_preview else Path(a.preview)
    status_path = Path(a.status) if a.status else out_dir / "status.json"
    universe_path = None if fixed else Path(a.universe)

    conn = db.connect(a.db)
    print(f"живой контур: {a.exchange}, таймфреймы {','.join(tfs)}, "
          f"пауза {a.interval:g} с")
    print(f"страницы: {out_dir}; предпросмотр: {preview or 'выключен'}")
    if fixed:
        print(f"состав: фиксированный список, монет {len(fixed)}")
    else:
        print(f"состав: монеты с оборотом от "
              f"{universe.volume_text(universe.parse_volume(a.min_volume))} "
              f"USDT за сутки; срез — {universe_path}")
    sys.stdout.flush()

    # Что монета, а что токенизированная акция, приходит из exchangeInfo и
    # меняется раз в листинг: берём один раз за процесс, а не каждый круг.
    contracts = None
    if not fixed and a.exchange in universe.SUPPORTED:
        try:
            contracts = market.binance_futures_contracts()
        except Exception as e:                      # noqa: BLE001
            print(f"    предупреждение: классификация контрактов не получена "
                  f"({type(e).__name__}), состав будет взят из файла среза")
    uni: dict | None = None

    round_no = 0
    while True:
        round_no += 1
        errors: list[str] = []

        if fixed:
            uni = None
        elif a.no_universe_refresh:
            if uni is None:
                uni = universe.load(universe_path)
                print(f"[{_stamp()}] состав из файла: монет "
                      f"{len(universe.symbols_of(uni))}")
        else:
            fresh, uni_err = refresh_universe(
                a.exchange, universe.parse_volume(a.min_volume), contracts,
                universe_path)
            if uni_err:
                errors.append(uni_err)
                if uni is None:
                    uni = universe.load(universe_path)   # прошлый срез лучше пустого
            else:
                uni = fresh
                print(f"[{_stamp()}] состав обновлён: монет "
                      f"{len(universe.symbols_of(uni))}, оборот от "
                      f"{universe.volume_text(uni['min_quote_volume'])} USDT")

        try:
            st = one_round(conn, db_path=a.db, tfs=tfs, exchange=a.exchange,
                           depth=a.depth, candle_limit=a.candle_limit,
                           out_dir=out_dir, preview=preview,
                           landing=not a.no_landing, quiet=a.quiet,
                           uni=uni, universe_path=universe_path)
        except Exception as e:                      # круг не должен падать
            st = {"at": _msk(), "at_unix": int(time.time()),
                  "elapsed": 0.0, "collected": {}, "pages": {},
                  "errors": [f"круг: {type(e).__name__}: {e}"]}
        st["round"] = round_no
        st["errors"] = errors + st["errors"]
        write_status(status_path, st)

        errs = st["errors"]
        line = (f"[{_stamp()}] круг {round_no}: монет "
                f"{st['collected'].get('symbols', 0)}, строк "
                f"{st['collected'].get('books', 0) + st['collected'].get('candles', 0)}, "
                f"страниц за {sum(st['pages'].values()):.1f} с, "
                f"весь круг {st['elapsed']:.1f} с, ошибок {len(errs)}")
        print(line)
        for e in errs:
            print(f"    ошибка: {e}")
        sys.stdout.flush()

        if a.once:
            return 0 if not errs else 1
        time.sleep(max(1.0, a.interval - st["elapsed"]))


if __name__ == "__main__":
    raise SystemExit(main())
