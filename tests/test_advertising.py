from decimal import Decimal as D
from ozon_profit.advertising import drr
from ozon_profit.core import KINDS


def row(kind,amount,sku=''):
    return dict(kind=KINDS[kind],amount=D(amount),sku=sku)


def test_drr_by_store_and_article_uses_returns_and_ad_refunds():
    r=drr([row('sale','1000','a'),row('return','-200','a'),row('advertising','-100','a'),row('advertising_refund','20','a')])
    assert r['store']==10 and r['products']['a']['drr']==10


def test_unassigned_ads_are_not_invented_as_article_drr():
    r=drr([row('sale','1000','a'),row('advertising','-100')])
    assert r['store']==10 and r['unassigned']==100
    assert r['products']['a']['drr'] is None
    assert drr([row('advertising','-100')])['store'] is None
