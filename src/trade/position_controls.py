"""Paper position commands are queued; only the bot worker mutates trading state."""
import json
import math
import uuid
from pathlib import Path


def enqueue(root, msg):
    op = msg.get('op')
    if op not in ('edit', 'close') or not isinstance(msg.get('key'), str):
        raise ValueError('unknown command')
    reason = str(msg.get('reason', '')).strip()
    if not 3 <= len(reason) <= 300: raise ValueError('reason required')
    data = dict(op=op, key=msg['key'], reason=reason)
    if op == 'edit':
        for name in ('stop', 'target'):
            value = float(msg[name])
            if not math.isfinite(value) or value <= 0: raise ValueError('invalid level')
            data[name] = value
    directory = Path(root)/'controls'; directory.mkdir(exist_ok=True)
    path = directory/(uuid.uuid4().hex+'.json')
    tmp = path.with_suffix('.tmp'); tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8'); tmp.replace(path)
    return path.name


def pending(root):
    return [(p, json.loads(p.read_text('utf-8'))) for p in sorted((Path(root)/'controls').glob('*.json'))]


def apply(commands, state, broker, ledger, now_ms):
    if broker.live: raise ValueError('paper only')
    events=[]
    for path, msg in commands:
        position = next((p for p in state.pos() if p.key == msg['key']), None)
        if not position:
            ledger.log('control_rejected', command=path.name, reason='позиция уже закрыта'); events.append('Команда отклонена: позиция уже закрыта'); continue
        if msg['op'] == 'close':
            position.pending_exit_reason = 'manual'; position.manual_reason = msg['reason']; position.expires_ms = 0
        else:
            price = broker.mid(position.symbol)
            stop, target = msg['stop'], msg['target']
            valid = stop < price < target if position.side == 'long' else target < price < stop
            if not valid:
                ledger.log('control_rejected', command=path.name, reason='стоп и цель должны быть по разные стороны текущей цены'); events.append(position.symbol+': изменение уровней отклонено, текущая цена вне новых стопа/тейка'); continue
            position.stop, position.target = stop, target
            position.no_target = False
            position.last_check_ms = now_ms  # new levels do not apply to past candles
        state.put(position)
        ledger.log('control', key=position.key, symbol=position.symbol, operation=msg['op'], reason=msg['reason'], stop=position.stop, target=position.target)

        events.append(f"{position.symbol}: {msg['op']}; причина: {msg['reason']}; стоп {position.stop:.7g}, цель {position.target:.7g}")
    return events
