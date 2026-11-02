from decimal import Decimal as D
import pytest
from ozon_profit.core import connect,save_cost,import_operations,set_setting
from ozon_profit.planning import forecast

@pytest.fixture
def db():
    db=connect(':memory:')
    save_cost(db,'demo','Демо','2026-01-01','20')
    save_cost(db,'new','Без истории','2026-01-01','30',True)
    import_operations(db,b'fictional','fictional.xlsx',[
        dict(id='s',day='2026-08-01',kind='sale',amount=1000,qty=10,sku='demo'),
        dict(id='fee',day='2026-08-01',kind='commission',amount=-200,qty=0,sku='demo'),
        dict(id='ad',day='2026-08-01',kind='advertising',amount=-100,qty=0,sku=''),
        dict(id='common',day='2026-08-01',kind='service',amount=-50,qty=0,sku=''),
    ])
    set_setting(db,'tax_base','sales')
    yield db
    db.close()


def scenario(db,quantity=50,delta=0,**kwargs):
    return forecast(db,[{'sku':'demo','quantity':quantity,'price':None}],
        '2026-09-01','2026-09-30','2026-08-01','2026-08-31',delta,**kwargs)


def test_forecast_uses_averages_with_ads_and_common_expenses_once(db):
    r=scenario(db)
    assert not r['issues']
    t=r['totals']
    assert t['revenue']==5000 and t['costs']==1000 and t['fees']==1000
    assert t['advertising']==500 and t['common']==250 and t['tax']==200
    assert t['profit']==2050 and t['baseline_profit']==2050


def test_ad_increase_is_percentage_points_and_changes_profit(db):
    r=scenario(db,delta=5);t=r['totals']
    assert r['base_ad_share']==10 and r['planned_ad_share']==15
    assert t['advertising']==750 and t['advertising_increase']==250
    assert t['profit']==1800 and t['baseline_profit']==2050


def test_period_sets_daily_pace_not_a_profit_multiplier(db):
    a=scenario(db)
    b=forecast(db,[{'sku':'demo','quantity':50}], '2026-09-01','2026-10-30','2026-08-01','2026-08-31')
    assert a['totals']['profit']==b['totals']['profit']
    assert a['daily_quantity']==D(50)/30 and b['daily_quantity']==D(50)/60


def test_missing_history_requires_explicit_price_and_marks_estimate(db):
    r=forecast(db,[{'sku':'new','quantity':50}], '2026-09-01','2026-09-30','2026-08-01','2026-08-31')
    assert r['issues']
    r=forecast(db,[{'sku':'new','quantity':50,'price':'100'}], '2026-09-01','2026-09-30','2026-08-01','2026-08-31')
    assert not r['issues'] and r['estimated']
    assert r['totals']['profit']==1550


def test_missing_cost_blocks_numeric_result_status(db):
    with db:db.execute("DELETE FROM costs WHERE sku='demo'")
    assert scenario(db)['issues']


def test_current_cost_history_is_used_for_planned_period(db):
    save_cost(db,'demo','Демо','2026-09-01','30')
    assert scenario(db)['totals']['costs']==1500


def test_extra_cost_and_net_tax_scenario(db):
    assert scenario(db,extra_costs=100)['totals']['profit']==1950
    set_setting(db,'tax_base','net')
    t=scenario(db,delta=5)['totals']
    assert t['tax']==120 and t['profit']==1880


def test_quantity_and_date_validation(db):
    with pytest.raises(ValueError):scenario(db,quantity=-1)
    with pytest.raises(ValueError):scenario(db,quantity=D('1.5'))
    with pytest.raises(ValueError):scenario(db,delta=-1)


def test_multiple_products_and_loyalty_tax_are_separate(db):
    import_operations(db,b'points','fictional.xlsx',[dict(id='p',day='2026-08-01',kind='points',amount=100,qty=0,sku='demo')])
    set_setting(db,'tax_base','sales_points')
    r=forecast(db,[{'sku':'demo','quantity':10},{'sku':'new','quantity':5,'price':100}],
        '2026-09-01','2026-09-30','2026-08-01','2026-08-31')
    assert not r['issues']
    assert r['totals']['revenue']==1500 and r['totals']['points']==150
    assert r['totals']['tax']==66
