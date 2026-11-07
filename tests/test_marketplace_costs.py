"""Fictional amounts: net marketplace charges, not advertising or profit margin."""
from decimal import Decimal as D
import pytest
from ozon_profit.core import KINDS,connect,save_cost,import_operations,calculate
from ozon_profit.marketplace_costs import summarize
from ozon_profit.product_metrics import summarize as product_summary


def row(kind,value):return dict(kind=KINDS[kind],amount=D(value))


def test_marketplace_costs_net_coinvest_and_refunds_excluding_ads_and_tax():
    details=[row('sale','1000'),row('commission','-500'),row('logistics','-100'),row('service','-50'),
             row('service_refund','20'),row('logistics_refund','10'),row('points','200'),row('points_reversal','-50'),row('partner','100'),
             row('advertising','-900'),row('advertising_refund','100'),row('compensation','300'),row('adjustment','-70')]
    r=summarize(details)
    assert r['amount']==370 and r['percent']==37 and r['revenue']==1000
    # No mutation; input still contains the separate, actual ad charge.
    assert details[9]['amount']==-900


@pytest.mark.parametrize('revenue',['0','-100'])
def test_nonpositive_realization_does_not_produce_a_percent(revenue):
    assert summarize([row('sale' if revenue=='0' else 'return',revenue),row('commission','-20')])['percent'] is None


def test_net_realization_after_returns_is_the_denominator_and_negative_cost_not_clipped():
    r=summarize([row('sale','1000'),row('return','-200'),row('commission','-100'),row('points','300')])
    assert r['revenue']==800 and r['amount']==-200 and r['percent']==-25


def test_article_allocation_preserves_total_and_does_not_change_profit_or_costs():
    db=connect(':memory:');save_cost(db,'a','A','2026-01-01',10);save_cost(db,'b','B','2026-01-01',20)
    import_operations(db,b'marketplace-fictional','fictional.xlsx',[
        dict(id='a',day='2026-10-01',kind='sale',sku='a',amount=1000,qty=10),
        dict(id='b',day='2026-10-01',kind='sale',sku='b',amount=500,qty=10),
        dict(id='coinvest',day='2026-10-01',kind='points',sku='a',amount=100,qty=0),
        dict(id='fees',day='2026-10-01',kind='service',sku='',amount=-150,qty=0),
        dict(id='ad',day='2026-10-01',kind='advertising',sku='',amount=-90,qty=0)])
    result=calculate(db,'2026-10-01','2026-10-31');before=result['profit']
    products=product_summary(result)['products'];marketplace=summarize(result['details'])
    assert marketplace['amount']==50 and sum(p['marketplace_costs'] for p in products.values())==50
    assert products['a']['marketplace_costs']==0 and products['a']['marketplace_percent']==0
    assert products['b']['marketplace_costs']==50 and products['b']['marketplace_percent']==10
    assert sum(p['profit'] for p in products.values())==before
    assert calculate(db,'2026-10-01','2026-10-31')['profit']==before
    assert product_summary(result,False)['products']['a']['marketplace_percent'] is None
    db.close()
