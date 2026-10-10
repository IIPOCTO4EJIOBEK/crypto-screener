import json
import pytest
from src.trade.thesis import invalidation
from src.trade.reversal import confirmed,load_thesis
from src.data.entry_safety import stop_guard,prior_atr
from src.trade.confirmation import annotate,entry_confirmation
from tests.test_fast_monitor import position

D=300000

def pattern(short=False):
    # Pivot high at index2 is confirmed by index4 BEFORE the sweep at index6.
    prices=[(99,101,98,100),(100,103,99,102),(102,110,101,108),(108,109,104,105),
            (105,107,102,104),(104,108,103,106),(106,112,105,108),(108,109,102,103),(103,104,100,101)]
    rows=[[i*D,*p,1] for i,p in enumerate(prices)]
    if short:rows=[[b[0],200-b[1],200-b[3],200-b[2],200-b[4],1] for b in rows]
    return rows

@pytest.mark.parametrize('short',[False,True])
def test_sweep_confirmation_is_causal_and_symmetric(short):
    p=position();p.side='short' if short else 'long';p.opened_ms=D
    rows=pattern(short);now=9*D
    signal=invalidation(p,rows,now)
    assert signal and signal['ready_ms']==8*D
    assert confirmed(p,signal,99 if not short else 101,now)
    assert not confirmed(p,signal,106 if not short else 94,now)
    assert invalidation(p,rows,7*D) is None  # confirming candle not closed
    assert invalidation(p,rows,10*D) is None # latest history stale
    assert invalidation(p,rows[:4]+rows[5:],now) is None # history gap
    p.opened_ms=7*D
    assert invalidation(p,rows,now) is None # sweep predates this position

def test_persistent_rule_is_bound_to_position_and_fresh_closed_candle():
    p=position();p.opened_ms=D;r=invalidation(p,pattern(),9*D)
    assert not confirmed(p,dict(r,_position_key='other'),99,9*D)
    assert not confirmed(p,dict(r,_opened_ms=2*D),99,9*D)
    assert not confirmed(p,r,99,10*D)
    p.tf='1h';assert not confirmed(p,r,99,9*D)

def test_reclaimed_sweep_needs_two_new_closed_candles():
    p=position();p.opened_ms=D;rows=pattern()+[[9*D,101,108,100,106,1],[10*D,106,107,102,103,1]]
    assert invalidation(p,rows,11*D) is None
    rows.append([11*D,103,104,100,101,1]);r=invalidation(p,rows,12*D)
    assert r and r['ready_ms']==12*D

def test_original_level_needs_two_post_entry_closures():
    p=position();p.opened_ms=7*D;p.entry_rules={'trigger_level':105}
    rows=pattern();r=invalidation(p,rows,9*D)
    assert r and r['trigger_level']==105 and r['ready_ms']==9*D
    p.opened_ms=8*D;assert invalidation(p,rows,9*D) is None

def test_published_thesis_expires(tmp_path):
    p=tmp_path/'thesis.json';p.write_text(json.dumps({'updated_ms':100000,'signals':[{'x':1}]}))
    assert load_thesis(p,110000)==[{'x':1}]
    assert load_thesis(p,131000)==[] and load_thesis(p,99000)==[]

def test_broad_stop_is_rejected_not_tightened_and_impulse_excluded_from_atr():
    assert stop_guard(2.207,1.857,'1h')
    assert stop_guard(100,96,'5m')
    assert not stop_guard(100,98,'5m',1)
    assert stop_guard(100,98,'5m',.5)
    rows=[[i*D,100,101,99,100,1] for i in range(15)]+[[15*D,100,180,50,160,1]]
    assert prior_atr(rows,15*D)==2

@pytest.mark.parametrize('kind',['breakout','structure_break','retest'])
def test_cancelled_level_blocks_actual_fill(kind,tmp_path):
    row=dict(kind=kind,symbol='AAAUSDT',tf='5m',ts=300000,direction='long',entry=100,stop=98,trigger_level=100)
    (tmp_path/'AAAUSDT_5m.json').write_text('[[300000,100,102,99,101,1],[600000,101,102,99,99.5,1]]')
    annotate([row],tmp_path,950000)
    assert entry_confirmation(row,101) and entry_confirmation(row,99)

def test_volume_alone_is_not_a_trade():
    assert entry_confirmation(dict(kind='volume_splash'),100)
    assert not entry_confirmation(dict(kind='volume_splash',manual=True),100)

def test_worker_does_not_acquire_writer_lock_or_change_position(tmp_path):
    from src.trade.atomic_store import transaction,profile_lock,read_state
    from src.trade.ledger import Ledger
    from tests.test_fast_monitor import initial
    from tools.trade.thesis_worker import round_once
    root=tmp_path/'trade';folder=root/'screener-test';market=tmp_path/'kl';market.mkdir();p=position();p.opened_ms=D
    st=initial();st.put(p);ledger=Ledger(folder)
    with transaction(ledger) as tx:tx.commit(st)
    before=read_state(folder)
    (market/'AAAUSDT_5m.json').write_text(json.dumps(pattern()))
    with profile_lock(folder):result=round_once(root,market,9*D)
    assert len(result['signals'])==1 and read_state(folder)==before

def test_reclaimed_or_stale_pending_invalidation_cannot_execute(tmp_path):
    from tests.test_screener_bot import setup,run,row,c,T0,MIN
    from src.trade.intraday import Config
    book,broker,ledger,state,data=setup(tmp_path);run(state,broker,ledger,data,[row()],T0)
    p=state.pos()[0];p.pending_exit_reason='invalidation';p.expires_ms=0;state.put(p)
    data['c']=[c(T0,100,101,99,100)]
    result=run(state,broker,ledger,data,[],T0+MIN,Config(exit_on_opposite=True,no_timeout=True))
    assert result['closed']==0 and state.pos()[0].pending_exit_reason==''

def test_anchor_is_persisted_with_real_entry(tmp_path):
    from tests.test_screener_bot import setup,run,row,T0
    book,broker,ledger,state,data=setup(tmp_path)
    run(state,broker,ledger,data,[row(trigger_level=99.5,_latest_closed=100)],T0)
    assert state.pos()[0].entry_rules['trigger_level']==99.5
