from src.trade.execution_stats import summarize

def test_retries_not_trades_and_pending_funding_not_final_profit():
    rows=[dict(kind='skip',key='a',reason='capital'),dict(kind='skip',key='a',reason='capital'),dict(kind='skip',key='b',reason='capital'),dict(kind='capacity')]
    rows += [dict(kind='close',formation='retest',tf='5m',side='long',pnl=10,fee=1,funding=1,r_net=2,rules={'x':1}),dict(kind='close',formation='retest',tf='5m',side='long',pnl=-3,fee=1,r_net=-1),dict(kind='close',formation='retest',tf='5m',side='long',pnl=100,fee=1,funding_pending=True)]
    stats=summarize(rows);r=stats['results'][0]
    assert r['net']==4 and r['profit_factor']==2 and r['avg_r']==.5 and r['pending']==1 and r['rules_known']==1
    assert stats['reasons'][0]['retries']==3 and stats['reasons'][0]['unique_signals']==2 and stats['capacity_events']==1

def test_recent_cohort_excludes_old_entries_closed_under_new_runtime():
    from src.trade.execution_stats import cohort
    old=dict(kind='close',ts=200,opened_ms=50);new=dict(kind='close',ts=250,opened_ms=150);skip=dict(kind='skip',ts=200)
    assert cohort([old,new,skip],100)==[new,skip]
