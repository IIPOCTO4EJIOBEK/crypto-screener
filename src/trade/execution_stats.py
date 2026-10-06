"""Diagnostics from authoritative records; retry counts are not unique setups."""
from collections import defaultdict

def cohort(rows,since_ms):
    # Older positions closed today still belong to their earlier entry rules.
    return [r for r in rows if (r.get('opened_ms',0) if r['kind']=='close' else r.get('ts',0))>=since_ms]

def summarize(rows):
    groups=defaultdict(list);skips=defaultdict(list)
    for r in rows:
        if r['kind']=='close':groups[(r.get('formation','?'),r.get('tf','?'),r.get('side','?'))].append(r)
        if r['kind']=='skip':skips[r.get('reason','?')].append(r)
    results=[]
    for (formation,tf,side),items in sorted(groups.items()):
        dust=[r for r in items if 'qty' in r and 'entry' in r and abs(r['qty']*r['entry'])<=1e-6]
        settled=[r for r in items if not r.get('funding_pending') and r not in dust];net=[r['pnl']-r['fee']-(r.get('funding') or 0) for r in settled]
        gains=sum(x for x in net if x>0);loss=-sum(x for x in net if x<0)
        results.append(dict(formation=formation,tf=tf,side=side,closed=len(items),settled=len(settled),pending=sum(bool(r.get('funding_pending')) for r in items),dust=len(dust),net=sum(net),wins=sum(x>1e-8 for x in net),profit_factor=gains/loss if loss else None,avg_r=sum(r.get('r_net',0) for r in settled)/len(settled) if settled else None,rules_known=sum(bool(r.get('rules')) for r in settled)))
    reasons=[dict(reason=reason,retries=len(items),unique_signals=len({r.get('key') for r in items if r.get('key')})) for reason,items in skips.items()]
    contexts=defaultdict(list)
    for r in rows:
        if r['kind']!='close' or r.get('funding_pending'):continue
        if 'qty' in r and 'entry' in r and abs(r['qty']*r['entry'])<=1e-6:continue
        for label in r.get('market_context') or ['контекст не записан']:contexts[label].append(r)
    context_results=[dict(context=label,n=len(items),net=sum(r['pnl']-r['fee']-(r.get('funding') or 0) for r in items)) for label,items in sorted(contexts.items())]
    return dict(results=results,contexts=context_results,reasons=sorted(reasons,key=lambda r:-r['retries']),capacity_events=sum(r['kind']=='capacity' for r in rows))
