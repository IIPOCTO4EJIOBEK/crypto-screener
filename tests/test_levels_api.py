import json,threading,urllib.request,urllib.error
from http.server import ThreadingHTTPServer
from tools.trade.webhook import make_handler


def test_levels_endpoint_requires_token_and_persists(tmp_path,monkeypatch):
 monkeypatch.setenv('MANUAL_LEVELS_DB',str(tmp_path/'levels.db'))
 token='test-token-1234567890';srv=ThreadingHTTPServer(('127.0.0.1',0),make_handler(token,tmp_path));thread=threading.Thread(target=srv.serve_forever,daemon=True);thread.start();url='http://127.0.0.1:'+str(srv.server_port)+'/api/levels'
 try:
  try:urllib.request.urlopen(url);assert False
  except urllib.error.HTTPError as e:assert e.code==403
  req=urllib.request.Request(url,data=json.dumps({'op':'save','item':{'symbol':'USUSDT','price':.012}}).encode(),headers={'X-Token':token,'Content-Type':'application/json'})
  with urllib.request.urlopen(req) as r:assert json.load(r)['ok']
  with urllib.request.urlopen(urllib.request.Request(url,headers={'X-Token':token})) as r:assert json.load(r)['items'][0]['symbol']=='USUSDT'
 finally:srv.shutdown();srv.server_close();thread.join()
