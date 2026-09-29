"""Живой стакан: снимок REST плюс поток изменений с биржи.

Поток отдаёт не стакан, а ИЗМЕНЕНИЯ уровней. Поэтому стакан приходится
собирать самому: взять снимок и наложить на него изменения, начиная со
следующего номера обновления. Без этой сверки локальный стакан расходится с
биржей, и плотности считаются по заявкам, которых уже нет.

Почему не опрашивать REST на каждое измерение: плотность опознаётся только по
НЕПРЕРЫВНОЙ последовательности снимков — иначе не отличить съеденную заявку от
передвинутой. Опрос раз в секунду — это 1200 запросов в минуту на монету, а
поток отдаёт те же изменения десять раз в секунду одним соединением.

Домен `fstream.binance.com` и снимок `fapi.binance.com` — та же биржа, чей
архив лежит в `data.binance.vision`, поэтому живой стакан и история
сопоставимы по построению (у spot-домена глубина другая).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time

import aiohttp

from src.data.market import Level, OrderBook

FSTREAM = "wss://fstream.binance.com/ws"
FAPI = "https://fapi.binance.com/fapi/v1/depth"
SNAPSHOT_LIMIT = 1000     # уровней на сторону в снимке; больше биржа не отдаёт
STREAM_MS = 100           # частота потока изменений
KEEP_LEVELS = 1500        # сколько уровней на сторону держим в памяти


class LiveBook:
    """Стакан, поддерживаемый в актуальном виде по потоку биржи.

    Порядок работы: открыть поток, набрать изменения, взять снимок, наложить
    изменения новее него — и дальше только накладывать. Если номера обновлений
    разошлись, стакан помечается несобранным и берётся заново.
    """

    def __init__(self, symbol: str, *, stream_ms: int = STREAM_MS) -> None:
        self.symbol = symbol.upper()
        self.stream_ms = stream_ms
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}
        self.ts = 0
        self.ready = False
        self.broken = False
        self.resyncs = 0
        self.checks = 0
        self.updates = 0
        self._last_u = 0

    # ------------------------------------------------------------------
    # Наложение данных биржи
    # ------------------------------------------------------------------
    def _apply_snapshot(self, raw: dict) -> None:
        self.bids = {float(p): float(q) for p, q in raw["bids"] if float(q) > 0}
        self.asks = {float(p): float(q) for p, q in raw["asks"] if float(q) > 0}
        self._last_u = int(raw["lastUpdateId"])
        self.ready = True

    def _apply_diff(self, raw: dict) -> None:
        """Наложить изменение на стакан.

        Номера `U`/`u` в потоке — СКВОЗНЫЕ по движку биржи, а не по монете:
        в последовательности событий одной монеты между соседними номерами
        зияют дыры в сотни значений (замер: u=5249489, следующее U=5249763).
        Поэтому «событие должно начинаться ровно со следующего номера» здесь
        невыполнимо и как признак расхождения не годится. Устаревшие события
        (полностью до снимка) отбрасываются, остальные накладываются по
        порядку; согласованность стакана проверяется сверкой со снимком.
        """
        if int(raw["u"]) <= self._last_u:
            return
        for price, qty in raw.get("b", ()):
            self._put(self.bids, float(price), float(qty))
        for price, qty in raw.get("a", ()):
            self._put(self.asks, float(price), float(qty))
        self._last_u = int(raw["u"])
        self.ts = int(raw.get("E") or raw.get("T") or time.time() * 1000)
        self.updates += 1

    def _aligned(self, other: OrderBook, tolerance_pct: float = 0.05,
                 depth: int = 300) -> bool:
        """Совпадает ли собранный стакан со снимком биржи.

        Глубину обеих сторон берём одну и ту же. Снимок приходит на 1000
        уровней, собранный стакан сравнивается по верхним 300 — если суммировать
        объём как есть, у снимка в счёт идут вдвое-втрое больше уровней, и
        отношение выходит около 0.2 независимо от согласованности. Проверка
        браковала бы каждый стакан.

        Сверяем середину, а не лучшие цены: между запросом снимка и сравнением
        поток успевает сдвинуть верх стакана, и требование равенства лучших цен
        давало бы ложные расхождения. Сумма объёма верхних уровней ловит случай,
        когда цена совпала, а глубину стакан потерял.
        """
        mine = self.orderbook(depth)
        if not mine.bids or not mine.asks or not other.bids or not other.asks:
            return False
        if mine.best_bid >= mine.best_ask:
            return False                      # пересечённый стакан — расхождение
        if abs(mine.mid - other.mid) / other.mid * 100 > tolerance_pct:
            return False
        ours = sum(l.price * l.size for l in mine.bids + mine.asks)
        theirs = sum(l.price * l.size for l in other.bids[:depth] + other.asks[:depth])
        if theirs <= 0:
            return False
        return 0.5 <= ours / theirs <= 2.0

    @staticmethod
    def _put(side: dict[float, float], price: float, qty: float) -> None:
        if qty <= 0:
            side.pop(price, None)
        else:
            side[price] = qty

    def _trim(self) -> None:
        """Урезать стакан до KEEP_LEVELS на сторону.

        Цена уходит, а уровни за ней остаются в словаре навсегда — без урезки
        память растёт всё время сбора.
        """
        if len(self.bids) > KEEP_LEVELS:
            self.bids = dict(sorted(self.bids.items(), reverse=True)[:KEEP_LEVELS])
        if len(self.asks) > KEEP_LEVELS:
            self.asks = dict(sorted(self.asks.items())[:KEEP_LEVELS])

    def orderbook(self, depth: int = SNAPSHOT_LIMIT) -> OrderBook:
        """Текущий стакан в общем виде: биды по убыванию, аски по возрастанию.

        Глубина по умолчанию — вся, что даёт снимок: у BTC шаг цены 0.1 при
        цене 83 000, поэтому 1000 уровней укладываются в 0.16 % от середины.
        При урезании до 500 в окно поиска плотностей (0.1–3 %) не попадает
        ничего, и стакан выглядит пустым, хотя он собран.
        """
        bids = sorted(self.bids.items(), reverse=True)[:depth]
        asks = sorted(self.asks.items())[:depth]
        return OrderBook("binance-futures", self.symbol, self.ts or int(time.time() * 1000),
                         [Level(p, q) for p, q in bids],
                         [Level(p, q) for p, q in asks])

    # ------------------------------------------------------------------
    # Работа с сетью
    # ------------------------------------------------------------------
    async def _snapshot(self, session: aiohttp.ClientSession) -> None:
        async with session.get(FAPI, params={"symbol": self.symbol,
                                             "limit": SNAPSHOT_LIMIT}) as r:
            r.raise_for_status()
            self._apply_snapshot(await r.json())

    async def books(self, every_ms: int = 200, verify_ms: int = 15_000):
        """Отдавать стакан каждые every_ms миллисекунд, пока поток жив.

        Чтение и выдача разведены: изменения накладываются сразу, как приходят,
        а стакан отдаётся по такту. Если накладывать их пачкой к моменту выдачи,
        на тихой монете пауза в потоке останавливала бы и выдачу.

        Раз в verify_ms стакан сверяется со свежим снимком: поток может
        потерять событие, и без сверки один уровень остался бы неверным до
        конца сбора, а этого не видно ни по цене, ни по объёму.
        """
        url = f"{FSTREAM}/{self.symbol.lower()}@depth@{self.stream_ms}ms"
        async with aiohttp.ClientSession(trust_env=False) as session:
            while True:
                try:
                    async with session.ws_connect(url, heartbeat=20,
                                                 max_msg_size=0) as ws:
                        await self._snapshot(session)
                        self.broken = False
                        reader = asyncio.create_task(self._read(ws))
                        next_check = time.monotonic() + verify_ms / 1000
                        try:
                            while not self.broken:
                                await asyncio.sleep(every_ms / 1000)
                                if self.broken:
                                    break
                                if time.monotonic() >= next_check:
                                    next_check = time.monotonic() + verify_ms / 1000
                                    await self._verify(session)
                                self._trim()
                                yield self.orderbook()
                        finally:
                            reader.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await reader
                except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
                    # канал отвалился: ждём и собираем стакан заново
                    self.ready = False
                    await asyncio.sleep(1.0)

    async def _read(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """Накладывать изменения по мере прихода, пока поток не закрылся."""
        try:
            async for msg in ws:
                if msg.type is not aiohttp.WSMsgType.TEXT:
                    break
                self._apply_diff(json.loads(msg.data))
        except (aiohttp.ClientError, OSError, ValueError):
            pass
        self.broken = True

    async def _verify(self, session: aiohttp.ClientSession) -> bool:
        """Сверить стакан со свежим снимком и собрать заново, если разошёлся."""
        self.checks += 1
        try:
            async with session.get(FAPI, params={"symbol": self.symbol,
                                                 "limit": SNAPSHOT_LIMIT}) as r:
                r.raise_for_status()
                raw = await r.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            return True                      # сеть прихрамывает — не повод собирать заново
        other = OrderBook("binance-futures", self.symbol, 0,
                          [Level(float(p), float(q)) for p, q in raw["bids"]],
                          [Level(float(p), float(q)) for p, q in raw["asks"]])
        if self._aligned(other):
            return True
        self.resyncs += 1
        self._apply_snapshot(raw)
        return False


if __name__ == "__main__":
    import sys

    from src.analysis.density import DensityTracker, find_densities

    syms = sys.argv[1:] or ["BTCUSDT"]

    async def main() -> None:
        trackers = {s: DensityTracker() for s in syms}
        books = {s: LiveBook(s) for s in syms}

        async def watch(sym: str) -> None:
            n = 0
            async for ob in books[sym].books(every_ms=500):
                current = trackers[sym].update(ob, depth=SNAPSHOT_LIMIT)
                n += 1
                if n % 4 == 0:
                    b = books[sym]
                    print(f"{sym:10} {ob.ts} середина {ob.mid:>12,.2f} "
                          f"спред {ob.spread:>8.2f} уровней "
                          f"{len(ob.bids)}/{len(ob.asks)} плотностей "
                          f"{len(current)} история {len(trackers[sym].history)} "
                          f"событий {b.updates} сверок {b.checks} "
                          f"пересборок {b.resyncs}")

        await asyncio.gather(*(watch(s) for s in syms))

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nостановлено")
