"""Состояние бота и журнал: что у него есть и что он делал.

Состояние — один JSON (`state.json`), журнал — построчный JSON
(`journal.jsonl`), по каталогу на режим: бумажная торговля, тестовая сеть и
живой счёт никогда не пишут в один файл. Запись состояния атомарная (через
временный файл и rename): обрыв посреди записи не оставит полфайла.

Файл `HALT` в каталоге режима останавливает торговлю: бот видит его и не
ставит заявок. Его создаёт стоп по просадке, и его можно положить руками.
Снимается он только руками — автоматического возобновления нет.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class State:
    mode: str
    cash: float
    positions: dict[str, float] = field(default_factory=dict)   # монета → количество
    peak: float = 0.0
    start_equity: float = 0.0
    start_ts: int = 0
    last_rebalance_day: int | None = None    # day_ts последнего ребаланса
    last_funding_ts: int | None = None       # до какого момента фандинг уже учтён

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + sum(q * prices.get(s, 0.0) for s, q in self.positions.items())


class Ledger:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "state.json"
        self.journal_path = self.root / "journal.jsonl"
        self.halt_path = self.root / "HALT"

    def load(self, mode: str, capital: float) -> State:
        if self.state_path.exists():
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return State(**data)
        now = int(time.time() * 1000)
        return State(mode=mode, cash=capital, peak=capital,
                     start_equity=capital, start_ts=now)

    def save(self, state: State) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(state), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def log(self, kind: str, **data) -> dict:
        if getattr(self, '_transaction', None):
            return self._transaction.log(kind, **data)
        row = {"ts": int(time.time() * 1000), "kind": kind, **data}
        from src.trade.atomic_store import database, append_journal
        if database(self.root).exists():
            append_journal(self.root, row)
            return row
        with self.journal_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

    def journal(self) -> list[dict]:
        from src.trade.atomic_store import database, read_journal
        if database(self.root).exists():
            tx = getattr(self, '_transaction', None)
            return read_journal(self.root) + (tx.rows if tx and not tx.committed else [])
        if not self.journal_path.exists():
            return []
        return [json.loads(l) for l in self.journal_path.read_text(encoding="utf-8").splitlines() if l]

    @property
    def halted(self) -> str | None:
        tx = getattr(self, '_transaction', None)
        if tx and tx.halt_reason:
            return tx.halt_reason
        return self.halt_path.read_text(encoding="utf-8") if self.halt_path.exists() else None

    def halt(self, reason: str) -> None:
        tx = getattr(self, '_transaction', None)
        if tx:
            tx.halt_reason = reason
            return
        self.halt_path.write_text(reason, encoding="utf-8")
