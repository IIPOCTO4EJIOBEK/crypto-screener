"""Алерты скринера в Telegram.

События (каждое — не чаще раза на монету и сторону, см. `COOLDOWN`):

* плотность съедена — сверка стакана (`dens_watch`) видит, что крупная
  заявка ушла сделками;
* всплеск объёма — закрытая 5м свеча с оборотом от `SPIKE`× среднего за
  2 часа до неё;
* цена у хая или лоя дня — ближе `NEAR_DAY` %;
* новый сетап «можно торговать» — свежий сработавший сигнал по тренду своего
  ТФ, R:R от 1, как на главной.

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
NEAR_DAY = 0.3
MIN_DENS = 100_000          # съеденные плотности меньше этого не шлём
MAX_PER_MIN = 15            # потолок сообщений в минуту, чтобы не залить канал


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
        self.window.append(now)
        self.sent[key] = now
        self.q.put((key, build))

    def _worker(self) -> None:
        while True:
            key, build = self.q.get()
            try:
                text, png = build()
            except Exception as e:                  # noqa: BLE001
                print(f"alerts: не собран {key}: {type(e).__name__} {e}", flush=True)
                continue
            self._post(text, png)

    def _post(self, text: str, png: bytes = b"") -> None:
        now = time.time()
        api = f"https://api.telegram.org/bot{self.token}"
        try:
            if png:
                if len(text) > 1000:                # подпись к фото — до 1024 символов
                    text = text[:990] + "…"
                r = requests.post(f"{api}/sendPhoto", data={"chat_id": self.chat, "caption": text, "parse_mode": "HTML"},
                                  files={"photo": ("chart.png", png, "image/png")}, timeout=30, proxies=self.proxies)
            else:
                r = requests.post(f"{api}/sendMessage", data={"chat_id": self.chat, "text": text, "parse_mode": "HTML",
                                                               "disable_web_page_preview": "true"},
                                  timeout=15, proxies=self.proxies)
            if r.status_code != 200:
                print(f"alerts: Telegram {r.status_code}: {r.text[:200]}", flush=True)
                return
        except Exception as e:                      # noqa: BLE001
            print(f"alerts: Telegram недоступен: {type(e).__name__}", flush=True)
            return
        self.sent = {k: v for k, v in self.sent.items() if now - v < 86400}
        try:
            tmp = self.state_path.with_name(self.state_path.name + ".tmp")
            tmp.write_text(json.dumps(self.sent), encoding="utf-8")
            os.replace(tmp, self.state_path)
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
        ctx = self._context(sym)
        text = head + (f"\n{extra}" if extra else "") + (f"\n\n{scen}" if scen else "") + \
            (f"\n\n{ctx}" if ctx else "") + f"\n#{sym}"
        return text, png

    # ---------------------------------------------------------------- события
    def candle_closed(self, sym: str, tf: str, closed: list, prev: list[list]) -> None:
        """Всплеск объёма на закрытой 5м свече: оборот к среднему за 2 часа до неё."""
        if tf != "5m" or len(prev) < 12:
            return
        base = [c[5] for c in prev[-24:] if c[5]]
        avg = sum(base) / len(base) if base else 0
        if not avg or closed[5] < SPIKE * avg:
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
        for sym, ds in snapshot.items():
            for d in ds:
                k = dens_watch.key(sym, d)
                st = (items.get(k) or {}).get("s")
                if st == "eaten" and self.dens_prev.get(k) == "live" and (d.get("notional") or 0) >= MIN_DENS:
                    side = "продажу" if d["side"] == "ask" else "покупку"
                    dd = dict(d)
                    self._send(f"eaten|{k}", lambda sym=sym, dd=dd, side=side: self._pack(
                        sym, "5m", f"🍽 <b>{sym.removesuffix('USDT')}</b> съели плотность на {side}: "
                                   f"{_fmt(dd['price'])}, было {_money(dd['notional'])} $",
                        hlines=[(dd["price"], "#f0b429", "съели")], title="плотность съедена"))
        self.dens_prev = {k: v.get("s") for k, v in items.items()}

    def stats_updated(self, coins: dict[str, dict]) -> None:
        """Цена у хая или лоя дня."""
        self.stats = coins
        for sym, st in coins.items():
            p, hi, lo = st.get("price"), st.get("day_hi"), st.get("day_lo")
            if not p or hi is None or lo is None:
                continue
            day = time.strftime("%Y%m%d")
            if (hi - p) / p * 100 <= NEAR_DAY:
                self._send(f"dayhi|{sym}|{day}", lambda sym=sym, p=p, hi=hi: self._pack(
                    sym, "15m", f"⬆️ <b>{sym.removesuffix('USDT')}</b> у хая дня: цена {_fmt(p)}, хай {_fmt(hi)}",
                    hlines=[(hi, "#4c8dff", "хай дня")], title="у хая дня", extra=self._level_age(sym, hi, True)))
            elif (p - lo) / p * 100 <= NEAR_DAY:
                self._send(f"daylo|{sym}|{day}", lambda sym=sym, p=p, lo=lo: self._pack(
                    sym, "15m", f"⬇️ <b>{sym.removesuffix('USDT')}</b> у лоя дня: цена {_fmt(p)}, лой {_fmt(lo)}",
                    hlines=[(lo, "#4c8dff", "лой дня")], title="у лоя дня", extra=self._level_age(sym, lo, False)))

    def setups(self, live_dir: Path) -> None:
        """Новый сетап «можно торговать» — по тем же правилам, что на главной."""
        src, tn = live_dir / "structures.html", live_dir / "trend-now.json"
        if not src.exists() or not tn.exists():
            return
        mt = src.stat().st_mtime
        if mt == self.forms_mtime:
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
                if f.get("direction") != tside or (f.get("rr") is not None and f["rr"] < 1):
                    continue
                key = f"setup|{p['symbol']}|{tf}|{f['kind']}|{f['direction']}|{f.get('ts')}"
                if key in self.forms_seen:
                    continue
                self.forms_seen.add(key)
                if first:                           # на старте старые сетапы не шлём
                    continue
                ms = f.get("measured") or {}
                exp = ms.get("exp_net")
                verdict = ("прошла замер" if ms.get("significant") and exp and exp > 0
                           else f"замер {exp:+.2f}R" if exp is not None else "замера нет")
                side = "ЛОНГ" if f["direction"] == "long" else "ШОРТ"
                ff, sym = dict(f), p["symbol"]
                head = (f"🎯 <b>{sym.removesuffix('USDT')}</b> {side} {tf}: {ff.get('title') or ff['kind']}\n"
                        f"вход {_fmt(ff['entry'])} · стоп {_fmt(ff['stop'])}"
                        + (f" · цель {_fmt(ff['target'])}" if ff.get("target") else "")
                        + (f" · R:R {ff['rr']:.2f}" if ff.get("rr") is not None else ""))
                reasons = "; ".join((ff.get("reasons") or [])[:3])
                self._send(key, lambda sym=sym, tf=tf, ff=ff, head=head, reasons=reasons, verdict=verdict: self._pack(
                    sym, tf, head, title=ff.get("title") or ff["kind"],
                    zone={"entry": ff["entry"], "stop": ff["stop"], "target": ff.get("target"), "t": ff.get("ts")},
                    mark_t=ff.get("ts"),
                    extra=(f"Почему: {reasons}.\n" if reasons else "") + f"{verdict}. Это сигнал скринера, не совет."))
        if len(self.forms_seen) > 5000:
            self.forms_seen = set(list(self.forms_seen)[-2000:])
