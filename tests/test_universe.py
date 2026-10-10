from tools.live import universe


def _c(sym, kind="COIN"):
    return {"symbol": sym, "underlyingType": kind, "contractType": "PERPETUAL",
            "status": "TRADING", "quoteAsset": "USDT"}


def test_watchlist_keeps_coin_below_threshold(tmp_path):
    wl = tmp_path / "watchlist.txt"
    wl.write_text("# комментарий\ngtc\nNOPEUSDT\nBTCUSDT  # и так в составе\n", encoding="utf-8")
    contracts = [_c("BTCUSDT"), _c("GTCUSDT"), _c("ETHUSDT")]
    volumes = {"BTCUSDT": 5e9, "GTCUSDT": 3e6, "ETHUSDT": 2e9}
    d = universe.pick("binance_futures", 20e6, contracts=contracts, volumes=volumes,
                      watchlist_path=wl)
    assert d["symbols"] == ["BTCUSDT", "ETHUSDT", "GTCUSDT"]
    assert d["watch"] == ["GTCUSDT", "BTCUSDT"]          # NOPE не торгуется — отброшена
    assert d["volumes"]["GTCUSDT"] == 3e6
    assert "GTC" in universe.note_of(d)


def test_no_watchlist_file(tmp_path):
    assert universe.load_watchlist(tmp_path / "нет.txt") == []
