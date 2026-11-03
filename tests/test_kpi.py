from datetime import date
from decimal import Decimal
import pytest
from ozon_profit.kpi import tone,comparison_period


@pytest.mark.parametrize('kind,value,previous,expected',[
    ('profit',1,None,'good'),('profit',-1,None,'bad'),('profit',0,None,'neutral'),
    ('revenue',100,100,'good'),('revenue',101,100,'good'),('revenue',99,100,'bad'),('net',100,None,'neutral'),
    ('drr',10,None,'good'),('drr',Decimal('10.01'),None,'bad'),('drr',0,None,'good'),('drr',None,None,'neutral'),
    ('margin',25,None,'good'),('margin',Decimal('24.99'),None,'bad'),
    ('costs',100,None,'neutral'),('tax',100,None,'neutral'),('ads',100,None,'neutral')])
def test_owner_threshold_boundaries(kind,value,previous,expected):
    assert tone(kind,value,previous)==expected


@pytest.mark.parametrize('start,end,a,b',[
    ('2026-11-01','2026-11-03','2026-10-01','2026-10-03'),
    ('2026-10-01','2026-10-31','2026-09-01','2026-09-30'),
    ('2026-02-01','2026-02-28','2026-01-01','2026-01-31'),
    ('2024-03-01','2024-03-31','2024-02-01','2024-02-29'),
    ('2026-01-01','2026-01-03','2025-12-01','2025-12-03'),
    ('2026-10-28','2026-11-03','2026-09-27','2026-10-03')])
def test_comparison_calendar_and_equal_rolling_windows(start,end,a,b):
    assert comparison_period(start,end)==(date.fromisoformat(a),date.fromisoformat(b))


def test_confirmed_costs_keep_all_amounts_and_history():
    from ozon_profit.core import connect,save_cost
    from ozon_profit.catalog import confirm_existing_costs
    db=connect(':memory:');save_cost(db,'demo','Демо','2026-01-01','12.345',True)
    save_cost(db,'demo','Демо','2026-02-01','14.5',True)
    confirm_existing_costs(db);confirm_existing_costs(db)
    assert [(r['since'],r['amount'],r['estimated']) for r in db.execute('SELECT * FROM costs ORDER BY since')]==[
        ('2026-01-01','12.345',0),('2026-02-01','14.5',0)]
    db.close()
