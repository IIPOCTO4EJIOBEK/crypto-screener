import json,asyncio
from tools.live.klines import Feed

def test_held_coins_stay_subscribed(tmp_path,monkeypatch):
 universe=tmp_path/'universe.json';pin=tmp_path/'held.json'
 universe.write_text('{"symbols":["BTCUSDT"]}');pin.write_text('{"symbols":["1000BONKUSDT"]}')
 monkeypatch.setenv('SCREENER_POSITION_SYMBOLS',str(pin))
 assert Feed(tmp_path,universe,('1m',)).read_symbols()==['1000BONKUSDT','BTCUSDT']

def test_live_timestamp_is_per_timeframe_not_file_write(tmp_path):
 f=Feed(tmp_path,tmp_path/'u',('1m','1h'));f.hist[('AAAUSDT','1m')]=[];f.hist[('AAAUSDT','1h')]=[]
 f.merge('AAAUSDT','1m',[0,100,101,99,100,1],1000)
 f.merge('AAAUSDT','1h',[0,100,101,99,101,1],50000);f.flush()
 assert json.loads((tmp_path/'AAAUSDT.live.json').read_text())['quote_ms']=={'1m':1000,'1h':50000}

def test_subscription_groups_bounded_and_cancelled(tmp_path):
 async def scenario():
  f=Feed(tmp_path,tmp_path/'u',('1m','5m','15m','1h'));f.syms=[f'C{i}USDT' for i in range(300)];groups=[]
  async def group(http,syms,sem):groups.append(len(syms));return
  f.socket_group=group
  await f.session(None)
  assert groups==[128,128,44]
 asyncio.run(scenario())

def test_quotes_publish_while_rest_history_is_loading(tmp_path):
 f=Feed(tmp_path,tmp_path/'u',('1m',))
 f.merge('BONKUSDT','1m',[0,100,101,99,100,1],1000);f.flush()
 data=json.loads((tmp_path/'BONKUSDT.live.json').read_text())
 assert data['k']['1m'][4]==100 and data['quote_ms']['1m']==1000
 assert ('BONKUSDT','1m') not in f.hist
