from decimal import Decimal
import pandas as pd
import pytest
from app.engine import normalize, infer_mapping, run_tool
from app.scenarios import scenario_rows, PERIODS


def analyze(rows):
    raw=pd.DataFrame(rows,columns=['日期','商品','数量','单价','订单编号'])
    frame,_=normalize(raw,'orders',infer_mapping(raw,'orders'))
    return run_tool(frame,'orders',{'operation':'compare','metric':'amount',**PERIODS,'end':frame.date.max()})


def test_coffee_breakdown_hand_control():
    _,rows=scenario_rows('coffee_orders')
    r=analyze(rows);b=r['change_breakdown']
    assert b['change']=={'positive_cents':-5600,'negative_cents':-2600,'net_cents':-8200}
    latte=next(c for c in b['categories'] if c['dimension']=='拿铁咖啡')
    assert latte['change']=={'positive_cents':-18200,'negative_cents':-2600,'net_cents':-20800}
    assert latte['previous']['positive_quantity']=='21' and latte['current']['positive_quantity']=='14'
    assert latte['previous']['negative_quantity']=='1' and latte['current']['negative_quantity']=='2'
    assert latte['previous']['weighted_positive_price']==latte['current']['weighted_positive_price']=='26.00'
    assert latte['evidence_id']==next(x['evidence_id'] for x in r['rows'] if x['dimension']=='拿铁咖啡')


def test_mixed_price_and_fractional_quantity_uses_weighted_price():
    b=analyze([['2026-09-14','A','0.5','20','A1'],['2026-09-15','A','1.5','40','A2'],['2026-09-21','A',1,10,'A3']])['change_breakdown']
    c=b['categories'][0]
    assert c['previous']['weighted_positive_price']=='35.00' # 70 / 2, not mean price 30.
    assert Decimal(c['previous']['positive_quantity'])==2
    assert c['change']['positive_cents']==-6000


def test_adjustments_only_have_no_positive_weighted_price():
    b=analyze([['2026-09-14','A',-1,10,'R1'],['2026-09-21','A',-2,12,'R2']])['change_breakdown']
    c=b['categories'][0]
    assert c['current']['weighted_positive_price'] is None
    assert c['current']['positive_quantity']=='0'
    assert c['change']=={'positive_cents':0,'negative_cents':-1400,'net_cents':-1400}


def test_zero_rounded_amount_still_keeps_signed_quantity():
    b=analyze([['2026-09-14','A',1,'0.001','A1'],['2026-09-21','A',-1,'0.001','R1']])['change_breakdown']
    c=b['categories'][0]
    assert c['previous']['positive_quantity']=='1' and c['previous']['weighted_positive_price']=='0.00'
    assert c['current']['negative_quantity']=='1' and c['current']['weighted_positive_price'] is None
    assert b['change']['net_cents']==0


def test_top_categories_do_not_truncate_overall_composition():
    rows=[]
    for i in range(8):
        rows.extend([['2026-09-14',str(i),1,10,'A'+str(i)],['2026-09-21',str(i),2,10,'B'+str(i)]])
    b=analyze(rows)['change_breakdown']
    assert b['shown_categories']==6 and b['all_categories']==8
    assert b['change']['net_cents']==8000
    assert sum(c['change']['net_cents'] for c in b['categories'])==6000


def test_new_and_disappearing_categories_show_zero_amount_and_null_price():
    b=analyze([['2026-09-14','旧品',1,10,'A1'],['2026-09-21','新品',1,20,'B1']])['change_breakdown']
    new=next(c for c in b['categories'] if c['dimension']=='新品')
    assert new['previous']['net_cents']==0 and new['previous']['weighted_positive_price'] is None
    assert new['previous']['positive_quantity']=='0'


@pytest.mark.parametrize('metric',['orders','positive_amount','negative_amount'])
def test_other_metrics_do_not_gain_net_accounting_explanation(metric):
    header,rows=scenario_rows('coffee_orders');raw=pd.DataFrame(rows,columns=header)
    f,_=normalize(raw,'orders',infer_mapping(raw,'orders'))
    r=run_tool(f,'orders',{'operation':'compare','metric':metric,**PERIODS})
    assert r['change_breakdown'] is None
