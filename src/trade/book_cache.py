"""Validated full top-20 snapshots; never reconstruct diff depth as a book."""
import json
import math
import time
from pathlib import Path
from src.data.market import OrderBook, Level

def decode(data, symbol, now):
    if data.get('s') != symbol: raise ValueError('book symbol mismatch')
    event = int(data['E']); received = float(data['received'])
    if not 0 <= now-received <= 5 or not 0 <= now-event/1000 <= 5:
        raise ValueError('stale or future book')
    def levels(key, reverse):
        values = [Level(float(p), float(q)) for p,q in data[key]]
        if not values or any(not math.isfinite(x.price*x.size) or x.price<=0 or x.size<=0 for x in values):
            raise ValueError('invalid depth')
        if values != sorted(values,key=lambda x:x.price,reverse=reverse) or len({x.price for x in values})!=len(values):
            raise ValueError('unordered depth')
        return values
    bids, asks = levels('b',True), levels('a',False)
    if bids[0].price >= asks[0].price: raise ValueError('crossed depth')
    return OrderBook('binance',symbol,event,bids,asks)

class BookCache:
    def __init__(self, root, clock=time.time): self.root=Path(root);self.clock=clock
    def get(self, symbol):
        if not symbol or any(not (c.isalnum() or c=='_') for c in symbol):
            raise ValueError('invalid symbol')
        return decode(json.loads((self.root/(symbol+'.json')).read_text()),symbol,self.clock())
