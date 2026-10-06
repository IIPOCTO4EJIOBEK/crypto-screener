"""Живые свечи для графиков главной: kline каждого ТФ прямо с Binance.

Браузер до Binance часто не достаёт (системный прокси, гео-ограничение
«restricted location»), а ВМ достаёт. Поэтому свечи берёт ВМ и кладёт
рядом со страницами, Caddy отдаёт их как обычные файлы:

* `kl/<SYM>_<tf>.json` — история, до 499 свечей `[t, o, h, l, c, qv]`;
  пишется на старте и при закрытии свечи;
* `kl/<SYM>.live.json` — текущая свеча каждого ТФ, не чаще раза в секунду;
* `kl/stats.json` — funding, OI, long/short, сделки в минуту, CVD, дневной и
  недельный хай/лой (`tools.live.market_stats`), раз в минуту;
* `kl/dens_state.json` — живы ли плотности последнего снимка: стоят, съедены
  или сняты (`tools.live.dens_watch`), раз в полминуты.

Тот же процесс шлёт алерты в Telegram (`tools.live.alerts`).

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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
REST = "https://fapi.binance.com/fapi/v1/klines"
# рыночные потоки фьючерсов живут под /market: старый адрес /stream соединение
# принимает, но данных не шлёт
WS = "wss://fstream.binance.com/market/stream?streams="
SILENT = 30.0               # столько секунд без сообщений — переподключение
TFS = ("1m", "5m", "15m", "1h")
LIMIT = 499                 # до 500 свечей вес запроса 2, дальше 5
UNIVERSE_EVERY = 300.0      # как часто перечитывать состав, с
STATS_EVERY = float(os.environ.get("SCREENER_STATS_EVERY", "120"))         # пауза между проходами рыночных метрик, с
DENS_EVERY = float(os.environ.get("SCREENER_DENS_EVERY", "90"))           # пауза между сверками плотностей со стаканом, с


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
        self.alerts = None
        self.first_seen: dict[str, float] = {}
        self.live_prices = {}
        self.ticks = {}
        self.manual_queued = set()
        self.manual_retry = {}

    def read_symbols(self) -> list[str]:
        try:
            u = json.loads(self.universe.read_text(encoding="utf-8"))
            return sorted(set(s.upper() for s in u.get("symbols", [])))
        except Exception as e:                      # noqa: BLE001
            print(f"klines: состав не прочитан ({type(e).__name__}), остаюсь на старом")
            return self.syms

    def merge(self, sym: str, tf: str, c: list, quote_ms: int = 0) -> None:
        self.live_prices[sym] = (float(c[4]), time.time())
        if tf == '1m' and quote_ms:
            trace = self.ticks.setdefault(sym, [])
            if not trace or (quote_ms > trace[-1][0] and c[4] != trace[-1][1]):
                trace.append([quote_ms, float(c[4])])
                del trace[:-512]
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
                if self.alerts:
                    try:
                        self.alerts.candle_closed(sym, tf, cs[-1], cs[:-1])
                    except Exception as e:          # noqa: BLE001
                        print(f"klines: алерт свечи: {type(e).__name__} {e}")
            cs.append(c)
            del cs[:-LIMIT]
        else:
            return
        self.dirty_live.add(sym)

    async def load(self, http: aiohttp.ClientSession, sym: str, tf: str,
                   sem: asyncio.Semaphore) -> None:
        async with sem:
            await asyncio.sleep(0.25)               # история на старте: не больше ~12 запросов в секунду
            for attempt in range(3):
                try:
                    from src.data import binance_limits
                    await asyncio.to_thread(binance_limits.acquire, REST, {"limit": LIMIT})
                    async with http.get(REST, params={"symbol": sym, "interval": tf,
                                                      "limit": LIMIT}) as r:
                        await asyncio.to_thread(binance_limits.observe, REST, r.status, dict(r.headers))
                        if r.status in (418, 429):
                            raise RuntimeError("-1003 shared Binance cooldown")
                        data = await r.json()
                    if isinstance(data, list):
                        break
                    raise RuntimeError(str(data)[:200])
                except Exception as e:              # noqa: BLE001
                    if "-1003" in str(e) or "banned" in str(e):
                        print(f"klines: Binance ограничил IP, история {sym} {tf} пропущена")
                        return                      # повтор только продлит бан
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
                _write(self.out / f"{sym}.live.json", {"t": now, "k": k, "ticks": self.ticks.get(sym, [])})

    async def flusher(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            try:
                self.flush()
            except Exception as e:                  # noqa: BLE001
                print(f"klines: запись не удалась: {type(e).__name__} {e}")

    async def manual_loop(self) -> None:
        import html, os
        from src.data.manual_levels import Store
        store = Store(os.environ.get("MANUAL_LEVELS_DB", "/var/lib/screener/manual-levels.db"))
        while True:
            await asyncio.sleep(5)
            try:
                now = time.time()
                prices = {s:p for s,(p,t) in self.live_prices.items() if now-t < 15}
                await asyncio.to_thread(store.observe, prices, int(now*1000))
                pending = (await asyncio.to_thread(store.snapshot))["events"]
                for e in reversed(pending):
                    ident = e["id"]
                    if e["delivered"] or ident in self.manual_queued or now < self.manual_retry.get(ident, 0) or not self.alerts.token:
                        continue
                    text = ("🔔 <b>" + html.escape(e["symbol"].removesuffix("USDT")) + "</b>: сигнальный уровень "
                            + str(e["price"]) + (" пересечён вверх" if e["direction"] == "up" else " пересечён вниз")
                            + "\n" + html.escape(e["label"]) + " · " + e["tf"]
                            + "\nhttps://vpn.markus.tw1.su/ · уведомление об уровне")
                    self.manual_queued.add(ident)
                    def done(mid, ident=ident):
                        try:
                            if mid: store.change("delivered", ident)
                        finally:
                            self.manual_queued.discard(ident)
                            self.manual_retry[ident] = time.time()+60
                    self.alerts.q.put(("manual|"+ident, lambda text=text, done=done: (text,b"",None,None,done)))
            except Exception as exc:
                print("manual levels:", type(exc).__name__, flush=True)

    async def stats_loop(self) -> None:
        """Раз в минуту — funding, OI, long/short, сделки, хай/лой (kl/stats.json)."""
        from tools.live import market_stats
        while True:
            syms = list(self.syms)
            if syms:
                try:
                    coins = await asyncio.to_thread(market_stats.fetch, syms)
                    took = coins.pop("_elapsed", None)
                    _write(self.out / "stats.json", {"at": int(time.time() * 1000),
                                                     "took": took, "coins": coins})
                    if self.alerts:
                        self.alerts.stats_updated(coins)
                except Exception as e:              # noqa: BLE001
                    print(f"klines: метрики не собраны: {type(e).__name__} {e}")
            await asyncio.sleep(STATS_EVERY)

    def vol5m(self, sym: str, n: int) -> float:
        """Максимальный оборот 5м свечи за последние n свечей (с текущей)."""
        cs = self.hist.get((sym, "5m")) or []
        return max((c[5] for c in cs[-(n + 1):]), default=0.0)

    def touched(self, sym: str, side: str, price: float, since: float) -> bool:
        """Доходила ли цена до price с момента since (по 5м свечам в памяти)."""
        t0 = int(since * 1000) - 300_000
        for c in self.hist.get((sym, "5m")) or []:
            if c[0] >= t0 and ((side == "ask" and c[2] >= price) or (side == "bid" and c[3] <= price)):
                return True
        return False

    async def nav_loop(self) -> None:
        """Общее меню на страницах, в том числе ботов: пересобранную страницу чиним за секунду."""
        from tools.live import navinject
        while True:
            try:
                await asyncio.to_thread(navinject.ensure, self.out.parent)
            except Exception as e:                  # noqa: BLE001
                print(f"klines: меню не вставлено: {type(e).__name__} {e}")
            await asyncio.sleep(1.0)

    async def dens_loop(self) -> None:
        """Раз в полминуты — живы ли плотности снимка (kl/dens_state.json)."""
        from tools.live import dens_watch
        src = self.out.parent / "densities.html"
        seen, last = 0.0, 0.0
        while True:
            await asyncio.sleep(5)
            if self.alerts:
                try:
                    await asyncio.to_thread(self.alerts.follow)
                except Exception as e:              # noqa: BLE001
                    print(f"klines: отработки: {type(e).__name__} {e}")
                try:
                    await asyncio.to_thread(self.alerts.setups, self.out.parent)
                except Exception as e:              # noqa: BLE001
                    print(f"klines: алерт сетапов: {type(e).__name__} {e}")
            if not src.exists():
                continue
            mtime = src.stat().st_mtime        # новый снимок сверяем сразу
            if mtime == seen and time.monotonic() - last < DENS_EVERY:
                continue
            if time.monotonic() - last < 30:   # но не чаще раза в 30 с
                continue
            seen, last = mtime, time.monotonic()
            try:
                st = await asyncio.to_thread(dens_watch.check, src, self.touched, 3, self.vol5m, self.first_seen)
                st["at"] = int(time.time() * 1000)
                _write(self.out / "dens_state.json", st)
                if self.alerts:
                    snap = dens_watch.snapshot(src)[1]
                    await asyncio.to_thread(self.alerts.dens_checked, st, snap)
            except Exception as e:                  # noqa: BLE001
                print(f"klines: плотности не сверены: {type(e).__name__} {e}")

    async def session(self, http: aiohttp.ClientSession) -> None:
        """Одно подключение: WS на весь состав + догрузка истории."""
        syms = self.syms
        streams = "/".join(f"{s.lower()}@kline_{tf}" for s in syms for tf in self.tfs)
        sem = asyncio.Semaphore(3)
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
                                                        float(k["l"]), float(k["c"]), float(k["q"])], int(d.get('E') or 0))
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
        from tools.live.alerts import Alerts
        self.alerts = Alerts(self.out, candles=lambda sym, tf: list(self.hist.get((sym, tf)) or []))
        asyncio.get_running_loop().create_task(self.flusher())
        asyncio.get_running_loop().create_task(self.manual_loop())
        self.syms = self.read_symbols()
        asyncio.get_running_loop().create_task(self.stats_loop())
        asyncio.get_running_loop().create_task(self.dens_loop())
        asyncio.get_running_loop().create_task(self.nav_loop())
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
