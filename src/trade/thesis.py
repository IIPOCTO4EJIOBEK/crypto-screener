"""Causal position invalidation, calculated outside the execution writer lock."""
import math
from src.data.market import INTERVALS

def invalidation(position,bars,now):
    duration=INTERVALS.get(position.tf,0)*1000
    closed=[b for b in bars if b[0]+duration<=now] if duration else []
    if len(closed)<5 or closed[-1][0]+duration!=now//duration*duration:return None
    closed=closed[-96:]
    if any(b[0]-a[0]!=duration for a,b in zip(closed,closed[1:])):return None
    if any(not all(isinstance(v,(int,float)) and math.isfinite(v) and v>0 for v in b[1:5]) for b in closed):return None
    sign=1 if position.side=='long' else -1;last=closed[-1]
    def row(level,ready,why):
        return dict(symbol=position.symbol,tf=position.tf,direction='short' if sign>0 else 'long',kind='structure_break',triggered=True,
                    ts=ready-duration,ready_ms=ready,trigger_level=level,_latest_closed=last[4],_latest_closed_ts=last[0],
                    _persistent_invalidation=True,_position_key=position.key,_opened_ms=position.opened_ms,_why=why)
    anchor=(position.entry_rules or {}).get('trigger_level')
    # Two CLOSED candles cancel a held level; a transient intrabar wick does not.
    if anchor and closed[-2][0]>=position.opened_ms and all(sign*(b[4]-anchor)<0 for b in closed[-2:]):
        return row(anchor,last[0]+duration,'два закрытия за исходным уровнем с обратной стороны')
    # Legacy positions have no precise anchor. A known pivot, sweep/rejection,
    # and the NEXT candle breaking its opposite extreme form a causal failure.
    candidates=[];col=2 if sign>0 else 3
    for i in range(5,len(closed)-1):
        sweep=closed[i]
        if sweep[0]<position.opened_ms:continue
        pivots=[j for j in range(2,i-2) if all(sign*(closed[j][col]-closed[k][col])>0 for k in range(j-2,j+3) if k!=j)]
        if not pivots:continue
        level=closed[max(pivots,key=lambda j:sign*closed[j][col])][col]
        previous=closed[max(0,i-14):i];buffer=sum(b[2]-b[3] for b in previous)/len(previous)*.1
        if not (sign*(sweep[col]-level)>buffer and sign*(sweep[4]-level)<-buffer):continue
        invalid=sweep[3] if sign>0 else sweep[2];next_bar=closed[i+1]
        if sign*(next_bar[4]-invalid)>=-buffer or sign*(last[4]-invalid)>=-buffer:continue
        reclaimed=[b for b in closed[i+2:] if sign*(b[4]-invalid)>=0]
        ready=next_bar[0]+duration;why='ложный вынос известного экстремума; следующая свеча сломала границу свечи выноса'
        if reclaimed:
            # The initial failure expired. Require a NEW confirmed loss of the
            # same established boundary after its most recent reclaim.
            if closed[-2][0]<=reclaimed[-1][0] or any(sign*(b[4]-invalid)>=-buffer for b in closed[-2:]):continue
            ready=last[0]+duration;why='повторная потеря границы подтверждённого ложного выноса: два закрытия за уровнем'
        candidates.append(row(invalid,ready,why))
    return candidates[-1] if candidates else None
