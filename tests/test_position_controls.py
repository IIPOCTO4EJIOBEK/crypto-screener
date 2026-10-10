import pytest
from src.trade.position_controls import enqueue, pending, apply
from tests.test_screener_bot import setup, run, row, T0
from src.trade.intraday import Config


def test_commands_cannot_put_stop_on_wrong_side_of_live_price(tmp_path):
    book, broker, ledger, state, data = setup(tmp_path)
    run(state,broker,ledger,data,[row()],T0)
    position = state.pos()[0]
    enqueue(tmp_path,dict(op='edit',key=position.key,reason='new support',stop=101,target=110))
    apply(pending(tmp_path),state,broker,ledger,T0+1000)
    assert state.pos()[0].stop == 98
    assert ledger.journal()[-1]['kind']=='control_rejected'
    with pytest.raises(ValueError): enqueue(tmp_path,dict(op='close',key=position.key,reason=''))


def test_drawdown_halts_new_entries_without_resetting_peak(tmp_path):
    book,broker,ledger,state,data=setup(tmp_path)
    state.cash=749
    result=run(state,broker,ledger,data,[row()],T0,Config(max_drawdown=.25))
    assert result['opened']==0 and ledger.halted and state.peak==1000

