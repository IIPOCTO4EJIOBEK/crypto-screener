"""Живые свечи для графиков главной: kline каждого ТФ прямо с Binance.

Браузер до Binance часто не достаёт (системный прокси, гео-ограничение
«restricted location»), а ВМ достаёт. Поэтому свечи берёт ВМ и кладёт
рядом со страницами, Caddy отдаёт их как обычные файлы:

* `kl/<SYM>_<tf>.json` — история, до 499 свечей `[t, o, h, l, c, qv]`;
  пишется на старте и при закрытии свечи;
* `kl/<SYM>.live.json` — текущая свеча каждого ТФ, не чаще раза в секунду.

Свечи каждого ТФ — родные `kline_<tf>` биржи (REST на старте, дальше
WebSocket), а не пересборка из 5m. Состав монет берётся из файла среза
вселенной и перечитывается раз в несколько минут.

Запуск (обычно его поднимает `tools/live/online.py`):

    python -m tools.live.klines --out docs/live/kl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import aiohttp

print = __import__('functools').partial(print, flush=True)   # stdout у сервиса — журнал

ROOT = Path(__file__).resolve().parents[2]
REST = "https://fapi.binance.com/fapi/v1/klines"
# рыночные потоки фьючерсов живут под /market: старый адрес /stream соединение
# принимает, но данных не шлёт
WS = "wss://fstream.binance.com/market/stream?streams="
SILENT = 30.0               # столько секунд без сообщений — переподключение
TFS = ("5m", "15m", "1h")
LIMIT = 499                 # до 500 свечей вес запроса 2, дальше 5
UNIVERSE_EVERY = 300.0      # как часто перечитывать состав, с


def _write(path: Path, obj) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def _row(k: list) -> list:
    """REST-строка kline → [t, o, h, l, c, qv]."""
    return [int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[7])]


class Feed:
    def __init__(self, out: Path, universe: Path, tfs: tuple[str, ...]):
        self.out, self.universe, self.tfs = out, universe, tfs
        self.hist: dict[tuple[str, str], list] = {}
        self.pending: dict[tuple[str, str], list] = {}   # пришло по WS раньше истории
        self.dirty_live: set[str] = set()
        self.dirty_hist: set[tuple[str, str]] = set()
        self.syms: list[str] = []

    def read_symbols(self) -> list[str]:
        try:
            u = json.loads(self.universe.read_text(encoding="utf-8"))
            return sorted(set(s.upper() for s in u.get("symbols", [])))
        except Exception as e:                      # noqa: BLE001
            print(f"klines: состав не прочитан ({type(e).__name__}), остаюсь на старом")
            return self.syms

    def merge(self, sym: str, tf: str, c: list) -> None:
        key = (sym, tf)
        cs = self.hist.get(key)
        if cs is None:
            self.pending[key] = c
            return
        if cs and c[0] == cs[-1][0]:
            cs[-1] = c
        elif not cs or c[0] > cs[-1][0]:
            if cs:
                self.dirty_hist.add(key)            # прошлая свеча закрылась
            cs.append(c)
            del cs[:-LIMIT]
        else:
            return
        self.dirty_live.add(sym)

    async def load(self, http: aiohttp.ClientSession, sym: str, tf: str,
                   sem: asyncio.Semaphore) -> None:
        async with sem:
            for attempt in range(3):
                try:
                    async with http.get(REST, params={"symbol": sym, "interval": tf,
                                                      "limit": LIMIT}) as r:
                        data = await r.json()
                    if isinstance(data, list):
                        break
                    raise RuntimeError(str(data)[:200])
                except Exception as e:              # noqa: BLE001
                    if attempt == 2:
                        print(f"klines: история {sym} {tf} не получена: {type(e).__name__} {e}")
                        return
                    await asyncio.sleep(2 + attempt * 3)
        key = (sym, tf)
        self.hist[key] = [_row(k) for k in data]
        if key in self.pending:
            self.merge(sym, tf, self.pending.pop(key))
        self.dirty_hist.add(key)
        self.dirty_live.add(sym)

    def flush(self) -> None:
        for key in list(self.dirty_hist):
            self.dirty_hist.discard(key)
            cs = self.hist.get(key)
            if cs:
                _write(self.out / f"{key[0]}_{key[1]}.json", cs)
        now = int(time.time() * 1000)
        for sym in list(self.dirty_live):
            self.dirty_live.discard(sym)
            k = {tf: self.hist[(sym, tf)][-1] for tf in self.tfs
                 if self.hist.get((sym, tf))}
            if k:
                _write(self.out / f"{sym}.live.json", {"t": now, "k": k})

    async def flusher(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            try:
                self.flush()
            except Exception as e:                  # noqa: BLE001
                print(f"klines: запись не удалась: {type(e).__name__} {e}")

    async def session(self, http: aiohttp.ClientSession) -> None:
        """Одно подключение: WS на весь состав + догрузка истории."""
        syms = self.syms
        streams = "/".join(f"{s.lower()}@kline_{tf}" for s in syms for tf in self.tfs)
        sem = asyncio.Semaphore(6)
        async with http.ws_connect(WS + streams, heartbeat=60, max_msg_size=0) as ws:
            self.hist.clear()
            self.pending.clear()
            loader = asyncio.gather(*(self.load(http, s, tf, sem)
                                      for s in syms for tf in self.tfs))
            print(f"klines: подключён, монет {len(syms)}, ТФ {','.join(self.tfs)}")
            sys.stdout.flush()
            checked = last = time.monotonic()
            try:
                while True:
                    try:
                        msg = await ws.receive(timeout=5)
                    except asyncio.TimeoutError:
                        msg = None
                        if time.monotonic() - last > SILENT:
                            raise ConnectionError(f"биржа молчит {SILENT:g} с")
                    if msg is not None:
                        last = time.monotonic()
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            raise ConnectionError(f"ws: {msg.type.name}")
                        d = json.loads(msg.data).get("data") or {}
                        k = d.get("k")
                        if k:
                            self.merge(k["s"], k["i"], [int(k["t"]), float(k["o"]), float(k["h"]),
                                                        float(k["l"]), float(k["c"]), float(k["q"])])
                    if time.monotonic() - checked > UNIVERSE_EVERY:
                        checked = time.monotonic()
                        fresh = self.read_symbols()
                        if fresh and fresh != self.syms:
                            print(f"klines: состав сменился ({len(self.syms)} → {len(fresh)}), переподключаюсь")
                            self.syms = fresh
                            return
            finally:
                loader.cancel()

    async def run(self) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        asyncio.get_running_loop().create_task(self.flusher())
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as http:
            while True:
                self.syms = self.read_symbols() or self.syms
                if not self.syms:
                    await asyncio.sleep(10)
                    continue
                try:
                    await self.session(http)
                except Exception as e:              # noqa: BLE001
                    print(f"klines: связь оборвалась ({type(e).__name__} {e}), переподключаюсь")
                    sys.stdout.flush()
                    await asyncio.sleep(3)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(ROOT / "docs" / "live" / "kl"))
    ap.add_argument("--universe", default=str(ROOT / "data" / "universe-turnover.json"))
    ap.add_argument("--tfs", default=",".join(TFS))
    a = ap.parse_args(argv)
    tfs = tuple(t.strip() for t in a.tfs.split(",") if t.strip())
    try:
        asyncio.run(Feed(Path(a.out), Path(a.universe), tfs).run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
