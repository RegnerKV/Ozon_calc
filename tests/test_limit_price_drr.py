import json
from decimal import Decimal as D
import pytest
from test_api_ledger import db,stage,posting_accrual,START,END
from ozon_profit.api_ledger import promote
from ozon_profit.limit_price_drr import denominator,ratio
from ozon_profit.core import calculate


def test_historical_seller_prices_and_returns_leave_financials_unchanged(db):
    stage(db,[posting_accrual(),posting_accrual(2,sale=-250,bonus=-50,partner=-2.5,price=250,original=100,delivery=0)])
    promote(db,START,END)
    before=calculate(db,START,END)
    base=denominator(db,START,END)
    assert base['total']==D('302.5')
    assert base['products']=={'demo':D('302.5')}
    assert ratio(D('30.25'),base['total'])==10
    assert calculate(db,START,END)==before


@pytest.mark.parametrize('bad',['missing','null','currency','mismatch'])
def test_unknown_or_unreconciled_price_is_not_rebuilt_from_realization(db,bad):
    item=posting_accrual();c=item['posting']['products'][0]['commission']
    if bad=='missing':del c['seller_price']
    if bad=='null':c['seller_price']=None
    if bad=='currency':c['seller_price']['currency']='USD'
    if bad=='mismatch':c['seller_price']['amount']='300'
    # Stage valid finance first, then emulate an old/malformed stored price.
    stage(db,[posting_accrual()]);promote(db,START,END)
    raw=json.loads(db.execute('SELECT payload FROM api_finance').fetchone()[0])
    clean=raw['items'][0]['commission']
    if 'seller_price' not in c:clean.pop('seller_price')
    else:clean['seller_price']=c['seller_price']
    db.execute('UPDATE api_finance SET payload=?',(json.dumps(raw),));db.commit()
    assert denominator(db,START,END)['total'] is None


def test_unpromoted_or_changed_snapshot_is_not_combined_with_old_finance(db):
    stage(db,[posting_accrual()])
    assert denominator(db,START,END)['total'] is None
    promote(db,START,END)
    stage(db,[posting_accrual(sale=600,price=300)])
    assert denominator(db,START,END)['total'] is None


@pytest.mark.parametrize('expense,base',[(None,D(10)),(D(10),None),(D(10),D(0)),(D(10),D(-10))])
def test_no_ratio_when_data_missing_or_base_nonpositive(expense,base):
    assert ratio(expense,base) is None


def test_overview_shows_both_drr_bases(db,tmp_path,monkeypatch):
    from datetime import date
    from streamlit.testing.v1 import AppTest
    from ozon_profit.core import connect
    stage(db,[posting_accrual(),dict(accrual_id=2,date=START,accrued_category='NON_ITEM',total_amount={'amount':'-60.5','currency':'RUB'},non_item_fee={'type_id':9,'accrued':{'amount':'-60.5','currency':'RUB'}})])
    promote(db,START,END)
    monkeypatch.setenv('OZON_PROFIT_DATA_DIR',str(tmp_path))
    local=connect();db.backup(local);local.close()
    app=AppTest.from_file('app.py',default_timeout=20).run()
    app.selectbox(key='overview_period').set_value('Свои даты').run()
    app.date_input(key='period_start').set_value(date.fromisoformat(START))
    app.date_input(key='period_end').set_value(date.fromisoformat(END)).run()
    assert not app.exception
    assert next(m for m in app.metric if m.label=='ДРР магазина от реализации').value=='12,10 %'
    assert next(m for m in app.metric if m.label=='ДРР относительно предельной цены').value=='10,00 %'
