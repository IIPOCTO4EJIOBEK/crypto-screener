from src.data.manual_levels import Store
import pytest

def test_moving_level_resets_baseline_without_rearming_fired_level(tmp_path):
 s=Store(tmp_path/'levels.db');item=s.save(dict(symbol='USUSDT',price=1,kind='level'))
 s.observe({'USUSDT':.9},1)
 moved=s.save(dict(item,price=.8))
 assert not s.observe({'USUSDT':.9},2)
 assert len(s.observe({'USUSDT':.7},3))==1
 s.save(dict(moved,price=.6))
 assert not s.snapshot()['items'][0]['active']
 assert not s.observe({'USUSDT':.5},4)
 s.change('rearm',item['id'])
 assert not s.observe({'USUSDT':.5},5)
 assert len(s.observe({'USUSDT':.7},6))==1


def test_level_is_one_shot_and_survives_restart(tmp_path):
 p=tmp_path/'levels.db';s=Store(p);x=s.save(dict(symbol='USUSDT',price=.012,kind='level',direction='up'))
 assert not s.observe({'USUSDT':.011},1)
 s=Store(p);hit=s.observe({'USUSDT':.013},2)
 assert len(hit)==1 and hit[0]['symbol']=='USUSDT'
 assert not s.observe({'USUSDT':.014},3)
 assert not s.snapshot()['items'][0]['active']
 s.change('rearm',x['id']);assert not s.observe({'USUSDT':.014},4)
 assert not s.observe({'USUSDT':.011},5)
 assert len(s.observe({'USUSDT':.012},6))==1


def test_first_price_does_not_trigger_existing_level(tmp_path):
 s=Store(tmp_path/'levels.db');s.save(dict(symbol='USUSDT',price=10))
 assert not s.observe({'USUSDT':11},1)
 assert len(s.observe({'USUSDT':9},2))==1


def test_drawings_do_not_trigger_and_bad_coordinates_rejected(tmp_path):
 s=Store(tmp_path/'levels.db');s.save(dict(symbol='USUSDT',price=10,kind='horizontal'))
 assert not s.observe({'USUSDT':9},1) and not s.observe({'USUSDT':11},2)
 for p in [0,-1,float('nan'),float('inf')]:
  with pytest.raises(ValueError):s.save(dict(symbol='USUSDT',price=p))


def test_delivery_and_ui_ack_are_independent(tmp_path):
 s=Store(tmp_path/'levels.db');s.save(dict(symbol='USUSDT',price=10));s.observe({'USUSDT':9},1);e=s.observe({'USUSDT':11},2)[0]
 s.change('ack',e['id']);assert not s.snapshot()['events'][0]['delivered']
 s.change('delivered',e['id']);assert s.snapshot()['events'][0]['delivered']

