import json
import pytest
from src.trade.book_cache import decode
from src.trade.entry_snapshot import prepare
from src.trade.intraday import Config, BotState

def payload():return dict(s='AAAUSDT',E=100000,received=100,b=[['99','2'],['98','3']],a=[['101','2'],['102','3']])

@pytest.mark.parametrize('change',[dict(E=94000),dict(E=101000),dict(received=94),dict(b=[['102','1']]),dict(a=[]),dict(s='WRONG'),dict(b=[['98','1'],['99','1']]),dict(b=[['nan','1']])])
def test_rejects_unsafe_books(change):
 d=payload();d.update(change)
 with pytest.raises(ValueError):decode(d,'AAAUSDT',100)

def test_execution_rereads_book_and_never_calls_rest(tmp_path):
 def forbidden(*args):raise AssertionError('REST called')
 p=tmp_path/'AAAUSDT.json';p.write_text(json.dumps(payload()));now=[100]
 broker=prepare([],BotState(cash=1000,peak=1000,start_equity=1000),Config(),fetch=forbidden,clock=lambda:now[0],book_root=tmp_path)
 assert broker.live is False and broker.mid('AAAUSDT')==100
 d=payload();d['a']=[['103','2']];p.write_text(json.dumps(d))
 assert broker.execute('AAAUSDT','buy',1).price==103
 assert broker.execute('AAAUSDT','buy',10) is None
 now[0]=106;assert broker.execute('AAAUSDT','buy',1) is None
 p.unlink();assert broker.execute('AAAUSDT','buy',1) is None
