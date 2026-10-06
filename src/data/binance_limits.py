"""Shared public Binance request pacing for processes on one host.

Opt in with SCREENER_RATE_DB. SQLite transactions serialize reservations;
sleep happens outside the transaction. No credentials are stored here.
"""
import os
import sqlite3
import time
from contextlib import closing
from urllib.parse import urlsplit

def weight(url, params=None):
    params=params or {}
    path=urlsplit(url).path
    limit=int(params.get('limit',100))
    if path.endswith('/depth'):
        return 2 if limit<=50 else 5 if limit<=100 else 10 if limit<=500 else 20
    if path.endswith('/klines'):
        return 1 if limit<100 else 2 if limit<500 else 5 if limit<=1000 else 10
    if path.endswith('/ticker/24hr'):
        return 1 if params.get('symbol') else 40
    if path.endswith('/premiumIndex'):
        return 1 if params.get('symbol') else 10
    return 1

def _connection(path):
    c=sqlite3.connect(path,timeout=30,isolation_level=None)
    c.execute('CREATE TABLE IF NOT EXISTS rate (id INTEGER PRIMARY KEY, next_at REAL, blocked_until REAL)')
    c.execute('INSERT OR IGNORE INTO rate VALUES (1,0,0)')
    return c

def acquire(url,params=None):
    path=os.environ.get('SCREENER_RATE_DB')
    if not path or urlsplit(url).hostname!='fapi.binance.com':return
    pace=float(os.environ.get('SCREENER_RATE_WEIGHT_PER_MIN','1200'))/60
    if pace<=0:raise ValueError('Request pace must be positive')
    while True:
        with closing(_connection(path)) as c:
            c.execute('BEGIN IMMEDIATE')
            next_at,blocked=c.execute('SELECT next_at,blocked_until FROM rate WHERE id=1').fetchone()
            now=time.time()
            reserved=max(now,next_at,blocked)
            c.execute('UPDATE rate SET next_at=? WHERE id=1',(reserved+weight(url,params)/pace,))
            c.commit()
        while reserved>time.time():
            time.sleep(max(0,min(reserved-time.time(),1)))
        with closing(_connection(path)) as c:
            blocked=c.execute('SELECT blocked_until FROM rate WHERE id=1').fetchone()[0]
        if blocked<=time.time():return
        # A response from another process established a new cooldown while
        # this request waited. Reserve another slot after that cooldown.

def observe(url,status,headers):
    path=os.environ.get('SCREENER_RATE_DB')
    if not path or urlsplit(url).hostname!='fapi.binance.com':return
    headers={k.lower():v for k,v in headers.items()}
    now=time.time();until=0
    if status in (418,429):
        try:wait=float(headers.get('retry-after','600'))
        except (TypeError,ValueError):wait=600
        until=now+max(wait,120)
    else:
        used=float(headers.get('x-mbx-used-weight-1m',0))
        budget=float(os.environ.get('SCREENER_RATE_WEIGHT_PER_MIN','1200'))
        if used>=budget:until=(int(now)//60+1)*60+2
    if until:
        with closing(_connection(path)) as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('UPDATE rate SET blocked_until=max(blocked_until,?) WHERE id=1',(until,))
            c.commit()

