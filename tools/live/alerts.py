"""Алерты скринера в Telegram.

События (каждое — не чаще раза на монету и сторону, см. `COOLDOWN`):

* плотность съедена — сверка стакана (`dens_watch`) видит, что крупная
  заявка ушла сделками;
* всплеск объёма — закрытая 5м свеча с оборотом от `SPIKE`× среднего за
  2 часа до неё;
* цена у хая или лоя дня — ближе `NEAR_DAY` %;
* новый сетап «можно торговать» — свежий сработавший сигнал по тренду своего
  ТФ с R:R от 1:3; цель — первый уровень за 3R, уровень ближе 3R сделку отменяет.

К каждому алерту — картинка графика с наклонными, уровнями и разметкой события
(`tools.live.alert_chart`), два сценария («удержим — к …», «пробьём — к …») и
контекст монеты: место в росте за сутки, оборот, сделки, сколько дней держался
уровень.

Куда слать: токен бота `TELEGRAM_BOT_TOKEN` и чат `SCREENER_ALERT_CHAT`
(по умолчанию канал проекта) берутся из окружения или из
`/opt/crypto-screener/.env`. Бот должен быть администратором канала. Если
Telegram из сети ВМ не открывается, адрес прокси — `TELEGRAM_PROXY`
(`http://логин:пароль@хост:порт`).
Без токена модуль молчит и один раз пишет об этом в журнал.
"""

from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CHAT = "-1004297324007"
COOLDOWN = 3600.0           # одно и то же событие по монете — не чаще раза в час
SPIKE = 3.0
MIN_SPIKE_USD = 300_000      # всплеск на 5м свече меньше этого оборота — шум мелкой монеты
NEAR_DAY = 0.3
MIN_DENS = 100_000          # съеденные плотности меньше этого не шлём
MAX_PER_MIN = 15            # потолок сообщений в минуту, чтобы не залить канал
MIN_RR = 3.0                # сделки по тренду — только от 1:3 (правило markus)
# Монеты «в игре» (разбор алертов Дигаша): алерты — только по ним, остальные
# хай/лой дня — одной сводкой раз в DIGEST_MIN минут. Дневной лимит — на всё, кроме отработок.
PLAY_TOP = 15               # топ роста за сутки
PLAY_CH = 5.0               # или изменение за сутки по модулю от, %
PLAY_NATR = 1.5             # или NATR 1ч от, %
PLAY_SPIKE = 3.0            # или 5м свеча за последний час с оборотом от N× среднего
DIGEST_MIN = 30
DAY_LIMIT = 60
SCREENER_URL = "https://vpn.markus.tw1.su"


def _env() -> dict[str, str]:
    out = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)", line)
            if m:
                out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    out.update({k: v for k, v in os.environ.items()
                if k in ("TELEGRAM_BOT_TOKEN", "SCREENER_ALERT_CHAT", "TELEGRAM_PROXY")})
    return out


def _fmt(p: float) -> str:
    a = abs(p)
    d = 1 if a >= 1000 else 2 if a >= 100 else 3 if a >= 1 else 5 if a >= 0.01 else 7
    return f"{p:,.{d}f}".replace(",", " ")


def _money(v: float) -> str:
    return f"{v / 1e6:.1f} млн" if v >= 1e6 else f"{v / 1e3:.0f} тыс"


class Alerts:
    def __init__(self, out_dir: Path, candles=None):
        env = _env()
        self.token = env.get("TELEGRAM_BOT_TOKEN")
        self.chat = env.get("SCREENER_ALERT_CHAT") or DEFAULT_CHAT
        # из сети ВМ api.telegram.org недоступен напрямую — тогда через прокси
        proxy = env.get("TELEGRAM_PROXY")
        self.proxies = {"https": proxy, "http": proxy} if proxy else None
        self.candles = candles or (lambda sym, tf: [])
        self.live_dir = out_dir.parent
        self.state_path = out_dir / "alerts_sent.json"
        try:
            self.sent: dict[str, float] = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:                           # noqa: BLE001
            self.sent = {}
        self.window: list[float] = []
        self.warned = False
        self.dens_prev: dict[str, str] = {}
        self.forms_seen: set[str] = set()
        self.forms_mtime = 0.0
        self.stats: dict[str, dict] = {}
        self.signals: list[dict] = []
        try:
            self.tracks: list[dict] = json.loads(self.state_path.with_name("alerts_track.json").read_text(encoding="utf-8"))
        except Exception:                           # noqa: BLE001
            self.tracks = []
        self._trend: tuple[float, dict] = (0.0, {})
        self.digest: dict[str, str] = {}
        self.dens_age: dict[str, int] = {}
        self.digest_at = time.time()
        self.day_count: tuple[str, int] = (time.strftime("%Y%m%d"), 0)
        self._charts: tuple[float, dict] = (0.0, {})
        # отправка — в своём потоке: живой поток свечей не должен ждать Telegram
        self.q: queue.Queue = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()

    # ---------------------------------------------------------------- отправка
    def _send(self, key: str, build) -> None:
        """build() -> (текст, png | b"") — выполняется в потоке отправки."""
        now = time.time()
        if now - self.sent.get(key, 0) < COOLDOWN:
            return
        if not self.token:
            if not self.warned:
                print("alerts: нет TELEGRAM_BOT_TOKEN в /opt/crypto-screener/.env — алерты не уходят", flush=True)
                self.warned = True
            return
        self.window = [t for t in self.window if now - t < 60]
        if len(self.window) >= MAX_PER_MIN:
            return
        day = time.strftime("%Y%m%d")
        if self.day_count[0] != day:
            self.day_count = (day, 0)
        if self.day_count[1] >= DAY_LIMIT:
            return
        self.day_count = (day, self.day_count[1] + 1)
        self.window.append(now)
        self.sent[key] = now
        self.q.put((key, build))

    def _worker(self) -> None:
        while True:
            key, build = self.q.get()
            try:
                res = build()
            except Exception as e:                  # noqa: BLE001
                print(f"alerts: не собран {key}: {type(e).__name__} {e}", flush=True)
                continue
            text, png = res[0], res[1]
            trade = res[2] if len(res) > 2 else None
            reply_to = res[3] if len(res) > 3 else None
            mid = self._post(text, png, reply_to=reply_to)
            if len(res) > 4 and callable(res[4]):
                try: res[4](mid)
                except Exception as exc: print("manual delivery:", type(exc).__name__, flush=True)
            if mid and trade and trade.get("ok"):
                self._track(mid, trade)

    def _post(self, text: str, png: bytes = b"", reply_to: int | None = None) -> int | None:
        now = time.time()
        api = f"https://api.telegram.org/bot{self.token}"
        data = {"chat_id": self.chat, "parse_mode": "HTML"}
        if reply_to:
            data.update(reply_to_message_id=str(reply_to), allow_sending_without_reply="true")
        try:
            if png:
                if len(text) > 1000:                # подпись к фото — до 1024 символов
                    text = text[:990] + "…"
                r = requests.post(f"{api}/sendPhoto", data=dict(data, caption=text),
                                  files={"photo": ("chart.png", png, "image/png")}, timeout=30, proxies=self.proxies)
            else:
                r = requests.post(f"{api}/sendMessage", data=dict(data, text=text, disable_web_page_preview="true"),
                                  timeout=15, proxies=self.proxies)
            if r.status_code != 200:
                print(f"alerts: Telegram {r.status_code}: {r.text[:200]}", flush=True)
                return None
            mid = (r.json().get("result") or {}).get("message_id")
        except Exception as e:                      # noqa: BLE001
            print(f"alerts: Telegram недоступен: {type(e).__name__}", flush=True)
            return None
        self._log(text, bool(png), mid)
        self.sent = {k: v for k, v in self.sent.items() if now - v < 86400}
        try:
            tmp = self.state_path.with_name(self.state_path.name + ".tmp")
            tmp.write_text(json.dumps(self.sent), encoding="utf-8")
            os.replace(tmp, self.state_path)
        except Exception:                           # noqa: BLE001
            pass
        return mid

    # ---------------------------------------------------------------- отработки
    def link(self, mid: int) -> str:
        """Ссылка на сообщение канала: t.me/c/<id канала без -100>/<номер>."""
        c = str(self.chat)
        return f"https://t.me/c/{c[4:] if c.startswith('-100') else c.lstrip('-')}/{mid}"

    def _track(self, mid: int, t: dict) -> None:
        observed = self.candles(t["sym"], "5m")
        price = observed[-1][4] if observed else t["entry"]
        sign = 1 if t["direction"] == "long" else -1
        risk = abs(t["entry"] - t["stop"])
        reached = sign * (price - t["entry"]) / risk if risk else 0
        hit = [n for n in (1, 2) if reached >= n]
        done = sign * (price - t["stop"]) <= 0 or sign * (price - t["target"]) >= 0
        self.tracks.append({"mid": mid, "sym": t["sym"], "tf": t.get("tf", "5m"), "dir": t["direction"],
                            "entry": t["entry"], "stop": t["stop"], "target": t["target"], "rr": t["rr"],
                            "t": int(time.time() * 1000), "published_price": price, "hit": hit, "done": done})
        self._save_tracks()

    def _save_tracks(self) -> None:
        try:
            path = self.state_path.with_name("alerts_track.json")
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(self.tracks[-500:], ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except Exception:                           # noqa: BLE001
            pass

    def follow(self) -> None:
        """Отработки: по каждой сделке из алерта — +1R, +2R, цель или стоп,
        ответом на исходное сообщение со ссылкой на него."""
        if not self.token or not self.tracks:
            return
        now = int(time.time() * 1000)
        changed = False
        for tr in self.tracks:
            if tr["done"]:
                continue
            if now - tr["t"] > 24 * 3600_000:
                tr["done"] = True; changed = True
                continue
            observed = self.candles(tr["sym"], "5m")
            if not observed:
                continue
            # A candle overlapping publication contains extrema from before the alert.
            # Only later candles and the current observed price are causal evidence.
            cs = [c for c in observed if c[0] >= tr["t"]]
            prices = [observed[-1][4]]
            up, R = tr["dir"] == "long", abs(tr["entry"] - tr["stop"])
            best = max(prices + [c[2] for c in cs]) if up else min(prices + [c[3] for c in cs])
            worst = min(prices + [c[3] for c in cs]) if up else max(prices + [c[2] for c in cs])
            mins = (now - tr["t"]) // 60000
            name, ent = tr["sym"].removesuffix("USDT"), tr["entry"]
            def pc(x: float) -> str:
                return f"{_fmt(x)} ({(x - ent) / ent * 100:+.2f}%)"
            msg = None
            if (worst <= tr["stop"]) if up else (worst >= tr["stop"]):
                msg = f"❌ <b>{name}</b>: стоп {pc(tr['stop'])}, −1R за {mins} мин"
                tr["done"] = True
            elif (best >= tr["target"]) if up else (best <= tr["target"]):
                msg = f"✅ <b>{name}</b>: цель взята {pc(tr['target'])}, +{tr['rr']:.1f}R за {mins} мин"
                tr["done"] = True
            else:
                got = (best - ent) / R if up else (ent - best) / R
                for n in (1, 2):
                    if got >= n and n not in tr["hit"]:
                        tr["hit"].append(n)
                        lvl = ent + (n * R if up else -n * R)
                        msg = f"🟢 <b>{name}</b>: +{n}R {pc(lvl)} за {mins} мин" + \
                              (" — стоп можно перенести в безубыток" if n == 1 else "")
            if msg:
                changed = True
                link = self.link(tr["mid"])
                text = msg + "\nРасчётные уровни сигнала; исполнение ботом не подтверждено." + f"\n<a href=\"{link}\">сигнал</a> · вход {_fmt(ent)} · стоп {_fmt(tr['stop'])} · цель {_fmt(tr['target'])}"
                self.q.put((f"follow|{tr['mid']}|{msg[:2]}", lambda text=text, mid=tr["mid"]: (text, b"", None, mid)))
        if changed:
            self._save_tracks()

    def _write_signals(self) -> None:
        """Сетапы для бумажного профиля «alerts»: только свежие (не старше свечи своего ТФ)."""
        now = time.time() * 1000
        tfms = {"5m": 300_000, "15m": 900_000, "1h": 3_600_000}
        keep = []
        for x in self.signals:
            duration = tfms.get(x["tf"], 300_000)
            # Formation ts is the OPEN time of its closed candle; age 0 starts at CLOSE.
            t0 = x.get("ts")
            ready = x.get("ready_ms")
            if ready is None:
                ready = t0 + duration if isinstance(t0, (int, float)) else x["added"]
            age = max(0, int((now - ready) // duration))
            if age <= 1:
                keep.append(dict(x, age_candles=max(age, 0)))
        self.signals = [x for x in self.signals if any(k["added"] == x["added"] and k["symbol"] == x["symbol"] for k in keep)]
        try:
            path = self.state_path.with_name("alert_signals.json")
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps({"built_unix": int(now / 1000), "signals": keep,
                                       "notes": [f"сетапы алертов: по тренду, R:R от 1:{MIN_RR:g} с учётом уровней"]},
                                      ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except Exception:                           # noqa: BLE001
            pass

    def _log(self, text: str, photo: bool, mid: int | None = None) -> None:
        """Лента для страницы alerts.html: последние 300 алертов."""
        path = self.state_path.with_name("alerts_log.json")
        try:
            log = json.loads(path.read_text(encoding="utf-8"))
        except Exception:                           # noqa: BLE001
            log = []
        log.append({"t": int(time.time() * 1000), "text": text, "photo": photo, "link": self.link(mid) if mid else None})
        try:
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(log[-300:], ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except Exception:                           # noqa: BLE001
            pass

    # ---------------------------------------------------------------- контекст
    def _markup(self, sym: str, tf: str) -> dict:
        """Уровни и плотности из charts.json (кэш по времени файла)."""
        path = self.live_dir / "charts.json"
        try:
            mt = path.stat().st_mtime
            if mt != self._charts[0]:
                self._charts = (mt, json.loads(path.read_text(encoding="utf-8")))
        except Exception:                           # noqa: BLE001
            return {}
        d = self._charts[1]
        return {"levels": ((d.get("pairs") or {}).get(sym) or {}).get(tf, {}).get("levels", []),
                "dens": sorted((d.get("dens") or {}).get(sym, []), key=lambda x: -x.get("notional", 0))[:3]}

    def _context(self, sym: str) -> str:
        st = self.stats.get(sym) or {}
        parts = []
        ch = st.get("ch24h")
        if ch is not None and self.stats:
            ranked = sorted((v.get("ch24h") or 0 for v in self.stats.values()), reverse=True)
            place = ranked.index(ch) + 1 if ch in ranked else None
            if place and place <= 10 and ch > 0:
                parts.append(f"топ-{place} роста за сутки ({ch:+.1f}%)")
            else:
                parts.append(f"за сутки {ch:+.1f}%")
        if st.get("qv24"):
            parts.append(f"оборот {_money(st['qv24'])} $")
        if st.get("trades24"):
            t = st["trades24"]
            parts.append(f"сделок за сутки {t / 1e6:.1f} млн" if t >= 1e6 else f"сделок за сутки {t / 1e3:.0f} тыс")
        if st.get("funding") is not None:
            parts.append(f"funding {st['funding']:+.3f}%")
        return " · ".join(parts)

    def _level_age(self, sym: str, price: float, up: bool) -> str:
        """Сколько дней цена не была за этим уровнем (по дневным свечам, сегодня не считая)."""
        try:
            d1 = requests.get("https://fapi.binance.com/fapi/v1/klines",
                              params={"symbol": sym, "interval": "1d", "limit": 400}, timeout=10).json()
        except Exception:                           # noqa: BLE001
            return ""
        if not isinstance(d1, list) or len(d1) < 3:
            return ""
        days = 0
        for k in reversed(d1[:-1]):
            if (up and float(k[2]) >= price) or (not up and float(k[3]) <= price):
                break
            days += 1
        if days < 2:
            return ""
        more = "+" if days >= len(d1) - 1 else ""
        return f"уровень не пробивали {days}{more} дн."

    def _pack(self, sym: str, tf: str, head: str, *, mark_t=None, hlines=(), zone=None, title="", extra=""):
        """Собрать текст и картинку: событие, сценарии, контекст."""
        from tools.live import alert_chart
        cs = self.candles(sym, tf)
        lines, lv, scen = alert_chart.analysis(cs) if len(cs) >= 30 else ([], [], "")
        mk = self._markup(sym, tf)
        png = b""
        if cs:
            png = alert_chart.render(sym, tf, cs, title=title, levels=mk.get("levels", []), dens=mk.get("dens", []),
                                     hlines=hlines, zone=zone, mark_t=mark_t, lines=lines, hlv=lv[:3])
        trade = None
        if not zone:                                # у сетапа своя зона; остальным — сделка 1:3 по тренду
            trade = alert_chart.trade13(cs, self._trend_side(sym), dens=mk.get("dens", []), min_rr=MIN_RR)
            trade.update(sym=sym, tf=tf)
            t13 = alert_chart.trade13_text(trade)
            scen = (scen + "\n" if scen else "") + t13
            if trade.get("ok"):
                self._add_signal(sym, tf, trade, head)
                zone = {"entry": trade["entry"], "stop": trade["stop"], "target": trade["target"], "t": cs[-1][0]}
                png = alert_chart.render(sym, tf, cs, title=title, levels=mk.get("levels", []), dens=mk.get("dens", []),
                                         hlines=hlines, zone=zone, mark_t=mark_t, lines=lines, hlv=lv[:3])
        else:
            trade = dict(zone, ok=True, sym=sym, tf=tf, direction="long" if zone["target"] > zone["entry"] else "short",
                         rr=abs(zone["target"] - zone["entry"]) / abs(zone["entry"] - zone["stop"]))
        ctx = self._context(sym)
        open_link = f'<a href="{SCREENER_URL}/#chart={sym}&tf={tf}">открыть в скринере</a>'
        text = head + (f"\n{extra}" if extra else "") + (f"\n\n{scen}" if scen else "") + \
            (f"\n\n{ctx}" if ctx else "") + f"\n#{sym} · {open_link}"
        return text, png, trade

    def in_play(self, sym: str) -> bool:
        """Монета «в игре»: топ роста, сильное движение за сутки, высокий NATR или свежий всплеск объёма."""
        st = self.stats.get(sym) or {}
        ch = st.get("ch24h")
        if ch is not None:
            if abs(ch) >= PLAY_CH:
                return True
            ranked = sorted((v.get("ch24h") or 0 for v in self.stats.values()), reverse=True)
            if ch > 0 and ranked.index(ch) < PLAY_TOP:
                return True
        h1 = self.candles(sym, "1h")
        if len(h1) > 16:
            trs = [max(c[2], p[4]) - min(c[3], p[4]) for p, c in zip(h1[-15:-1], h1[-14:])]
            if h1[-1][4] and sum(trs) / len(trs) / h1[-1][4] * 100 >= PLAY_NATR:
                return True
        m5 = self.candles(sym, "5m")
        if len(m5) > 40:
            base = [c[5] for c in m5[-37:-13] if c[5]]
            avg = sum(base) / len(base) if base else 0
            if avg and max(c[5] for c in m5[-13:]) >= PLAY_SPIKE * avg:
                return True
        return False

    def flush_digest(self) -> None:
        """Сводка хай/лой дня по монетам не «в игре» — одним сообщением."""
        if not self.digest or time.time() - self.digest_at < DIGEST_MIN * 60:
            return
        items, self.digest, self.digest_at = sorted(self.digest.items()), {}, time.time()
        hi = [f"{s.removesuffix('USDT')} {t}" for s, t in items if t.startswith("⬆")]
        lo = [f"{s.removesuffix('USDT')} {t}" for s, t in items if t.startswith("⬇")]
        text = f"🗒 <b>Сводка за {DIGEST_MIN} мин</b>: крупные монеты у границ дня (не в игре — без отдельных алертов)"
        if hi:
            text += "\nУ хая: " + ", ".join(x.replace("⬆ ", "") for x in hi)
        if lo:
            text += "\nУ лоя: " + ", ".join(x.replace("⬇ ", "") for x in lo)
        self._send(f"digest|{int(time.time())}", lambda text=text: (text, b""))

    def _trend_side(self, sym: str) -> str | None:
        """Тренд монеты на 15м из trend-now.json (кэш по времени файла)."""
        path = self.live_dir / "trend-now.json"
        try:
            mt = path.stat().st_mtime
            if mt != self._trend[0]:
                self._trend = (mt, json.loads(path.read_text(encoding="utf-8")).get("coins", {}))
        except Exception:                           # noqa: BLE001
            return None
        return (self._trend[1].get(sym) or {}).get("15m")

    def _add_signal(self, sym: str, tf: str, t: dict, head: str) -> None:
        """Сделка 1:3 из алерта — сигнал бумажному профилю «alerts»."""
        title = re.sub(r"<[^>]+>", "", head).split(":")[0][2:].strip() or "алерт"
        self.signals.append({"kind": "alert", "title": title, "tf": tf, "symbol": sym, "exchange": "binance_futures",
                             "direction": t["direction"], "entry": t["entry"], "stop": t["stop"], "target": t["target"],
                             "rr": t["rr"], "triggered": True, "ts": int(time.time() * 1000) // 300_000 * 300_000,
                             "ready_ms": int(time.time() * 1000) // 300_000 * 300_000,
                             "reasons": [t.get("why", "")], "measured": None, "exp_net": None,
                             "added": int(time.time() * 1000)})
        self._write_signals()

    # ---------------------------------------------------------------- события
    def candle_closed(self, sym: str, tf: str, closed: list, prev: list[list]) -> None:
        """Всплеск объёма на закрытой 5м свече: оборот к среднему за 2 часа до неё."""
        if tf != "5m" or len(prev) < 12:
            return
        base = [c[5] for c in prev[-24:] if c[5]]
        avg = sum(base) / len(base) if base else 0
        if not avg or closed[5] < SPIKE * avg or closed[5] < MIN_SPIKE_USD:
            return
        ch = (closed[4] - closed[1]) / closed[1] * 100 if closed[1] else 0
        side = "up" if ch >= 0 else "down"
        k, x = closed[:], closed[5] / avg
        self._send(f"spike|{sym}|{side}", lambda: self._pack(
            sym, "5m", f"📊 <b>{sym.removesuffix('USDT')}</b> всплеск объёма 5м: {x:.1f}× к среднему, "
                       f"{_money(k[5])} $, свеча {ch:+.2f}%, цена {_fmt(k[4])}",
            mark_t=k[0], title=f"всплеск объёма {x:.1f}×"))

    def dens_checked(self, state: dict, snapshot: dict[str, list[dict]]) -> None:
        """Плотность съедена: в прошлую сверку стояла, сейчас ушла сделками."""
        from tools.live import dens_watch
        items = state.get("items", {})
        R = state.get("rules") or dens_watch.rules()
        for sym, ds in snapshot.items():
            for d in ds:
                k = dens_watch.key(sym, d)
                st = (items.get(k) or {}).get("s")
                if st == "eaten" and self.dens_prev.get(k) == "shown" and (d.get("notional") or 0) >= MIN_DENS \
                        and self.in_play(sym):
                    side = "продажу" if d["side"] == "ask" else "покупку"
                    dd = dict(d, age=self.dens_age.get(k))
                    self._send(f"eaten|{k}", lambda sym=sym, dd=dd, side=side: self._pack(
                        sym, "5m", f"🍽 <b>{sym.removesuffix('USDT')}</b> съели плотность: "
                                   f"{'ask' if dd['side'] == 'ask' else 'bid'} {_fmt(dd['price'])} · {_money(dd['notional'])} $"
                                   + (f" · стояла {dd['age'] // 60} мин" if dd.get("age") else ""),
                        hlines=[(dd["price"], "#f0b429", "съели")], title="плотность съедена"))
        # «было на экране»: съеденной считаем только плотность, прошедшую порог
        self.dens_prev = {k: ("shown" if dens_watch.shown(v, R) else v.get("s")) for k, v in items.items()}
        self.dens_age = {k: v["age"] for k, v in items.items() if v.get("age") is not None} |             {k: a for k, a in self.dens_age.items() if k not in items}

    def stats_updated(self, coins: dict[str, dict]) -> None:
        """Цена у хая или лоя дня."""
        self.stats = coins
        self.flush_digest()
        for sym, st in coins.items():
            p, hi, lo = st.get("price"), st.get("day_hi"), st.get("day_lo")
            if not p or hi is None or lo is None:
                continue
            day = time.strftime("%Y%m%d")
            near_hi, near_lo = (hi - p) / p * 100 <= NEAR_DAY, (p - lo) / p * 100 <= NEAR_DAY
            if (near_hi or near_lo) and not self.in_play(sym):
                self.digest[sym] = f"⬆ {_fmt(hi)}" if near_hi else f"⬇ {_fmt(lo)}"
                continue
            if near_hi:
                self._send(f"dayhi|{sym}|{day}", lambda sym=sym, p=p, hi=hi: self._pack(
                    sym, "15m", f"⬆️ <b>{sym.removesuffix('USDT')}</b> у хая дня: цена {_fmt(p)}, хай {_fmt(hi)}",
                    hlines=[(hi, "#4c8dff", "хай дня")], title="у хая дня", extra=self._level_age(sym, hi, True)))
            elif (p - lo) / p * 100 <= NEAR_DAY:
                self._send(f"daylo|{sym}|{day}", lambda sym=sym, p=p, lo=lo: self._pack(
                    sym, "15m", f"⬇️ <b>{sym.removesuffix('USDT')}</b> у лоя дня: цена {_fmt(p)}, лой {_fmt(lo)}",
                    hlines=[(lo, "#4c8dff", "лой дня")], title="у лоя дня", extra=self._level_age(sym, lo, False)))

    def setups(self, live_dir: Path) -> None:
        """Новый сетап «можно торговать»: по тренду своего ТФ и с R:R не меньше 1:3,
        где цель — первое препятствие за 3R (перехай, уровень, наклонная, Фибо,
        плотность), а препятствие ближе 3R сделку отменяет (`alert_chart.plan`).

        Прошедшие сетапы ложатся в `kl/alert_signals.json` — его берёт бумажный
        профиль «alerts» бота по скринеру."""
        from tools.live import alert_chart
        src, tn = live_dir / "structures.html", live_dir / "trend-now.json"
        if not src.exists() or not tn.exists():
            return
        mt = src.stat().st_mtime
        if mt == self.forms_mtime:
            self._write_signals()
            return
        first = self.forms_mtime == 0.0
        self.forms_mtime = mt
        s = src.read_text(encoding="utf-8")
        m = re.search(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', s, re.S)
        if not m:
            return
        pairs = json.loads(m.group(1)).get("pairs", [])
        trends = json.loads(tn.read_text(encoding="utf-8")).get("coins", {})
        for p in pairs:
            tf = p["tf"]
            if tf not in ("5m", "15m", "1h"):
                continue
            tside = (trends.get(p["symbol"]) or {}).get("15m" if tf == "5m" else tf)
            for f in p.get("formations", []):
                if not f.get("triggered") or (f.get("age_candles") or 0) > 1:
                    continue
                if f["kind"] == "trendline_bounce" or f.get("entry") is None or f.get("stop") is None:
                    continue
                if f.get("direction") != tside:
                    continue
                key = f"setup|{p['symbol']}|{tf}|{f['kind']}|{f['direction']}|{f.get('ts')}"
                if key in self.forms_seen:
                    continue
                self.forms_seen.add(key)
                sym = p["symbol"]
                cs = self.candles(sym, tf)
                pl = alert_chart.plan(cs, f["entry"], f["stop"], f["direction"],
                                      dens=self._markup(sym, tf).get("dens", []), min_rr=MIN_RR)
                if not pl.get("ok"):
                    continue                        # до 1:3 мешает уровень — не сделка
                ff = dict(f, target=pl["target"], rr=pl["rr"], target_why=pl["why"])
                self.signals.append({"kind": ff["kind"], "title": ff.get("title") or ff["kind"], "tf": tf,
                                     "symbol": sym, "exchange": "binance_futures", "direction": ff["direction"],
                                     "entry": ff["entry"], "stop": ff["stop"], "target": ff["target"], "rr": ff["rr"],
                                     "triggered": True, "ts": ff.get("ts"), "trigger_level":ff.get('trigger_level'), "reasons": (ff.get("reasons") or [])[:4],
                                     "measured": ff.get("measured"),
                                     "exp_net": (ff.get("measured") or {}).get("exp_net"),
                                     "added": int(time.time() * 1000)})
                if first:                           # на старте старые сетапы не шлём
                    continue
                ms = ff.get("measured") or {}
                exp = ms.get("exp_net")
                verdict = ("прошла замер" if ms.get("significant") and exp and exp > 0
                           else f"замер {exp:+.2f}R" if exp is not None else "замера нет")
                side = "ЛОНГ" if ff["direction"] == "long" else "ШОРТ"
                head = (f"🎯 <b>{sym.removesuffix('USDT')}</b> {side} {tf}: {ff.get('title') or ff['kind']}\n"
                        f"вход {_fmt(ff['entry'])} · стоп {_fmt(ff['stop'])} · цель {_fmt(ff['target'])} "
                        f"({ff['target_why']}) · R:R 1:{ff['rr']:.1f}")
                reasons = "; ".join((ff.get("reasons") or [])[:3])
                self._send(key, lambda sym=sym, tf=tf, ff=ff, head=head, reasons=reasons, verdict=verdict: self._pack(
                    sym, tf, head, title=ff.get("title") or ff["kind"],
                    zone={"entry": ff["entry"], "stop": ff["stop"], "target": ff["target"], "t": ff.get("ts")},
                    mark_t=ff.get("ts"),
                    extra=(f"Почему: {reasons}.\n" if reasons else "") + f"{verdict}. Это сигнал скринера, не совет."))
        self._write_signals()
        if len(self.forms_seen) > 5000:
            self.forms_seen = set(list(self.forms_seen)[-2000:])

