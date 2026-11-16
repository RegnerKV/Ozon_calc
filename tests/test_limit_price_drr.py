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


@pytest.mark.parametrize('attributed',[False,True])
def test_overview_shows_both_drr_bases(db,tmp_path,monkeypatch,attributed):
    from datetime import date
    from streamlit.testing.v1 import AppTest
    from ozon_profit.core import connect
    stage(db,[posting_accrual(),dict(accrual_id=2,date=START,accrued_category='NON_ITEM',total_amount={'amount':'-60.5','currency':'RUB'},non_item_fee={'type_id':9,'accrued':{'amount':'-60.5','currency':'RUB'}})])
    promote(db,START,END)
    monkeypatch.setenv('OZON_PROFIT_DATA_DIR',str(tmp_path))
    local=connect();db.backup(local)
    from test_ordered_units import snapshot,posting
    snapshot(local,[posting('one',5),{**posting('two',2,'cancelled'),'products':[dict(offer_id='orders-only',quantity=2)]}])
    local.commit();local.close()
    if attributed:
        from ozon_profit import performance
        monkeypatch.setattr(performance,'report',lambda *args:dict(store=D('12.1'),unassigned=D(0),products={'demo':D('60.5')},total=D('60.5')))
    app=AppTest.from_file('app.py',default_timeout=20).run()
    app.selectbox(key='overview_period').set_value('Свои даты').run()
    app.date_input(key='period_start').set_value(date.fromisoformat(START))
    app.date_input(key='period_end').set_value(date.fromisoformat(END)).run()
    assert not app.exception
    assert next(m for m in app.metric if m.label=='ДРР магазина от реализации').value=='12,10 %'
    assert next(m for m in app.metric if m.label=='ДРР относительно предельной цены').value=='10,00 %'
    assert next(m for m in app.metric if m.label=='ДРР артикула относительно предельной цены').value==('10,00 %' if attributed else 'Нет данных')
    if not attributed:
        assert any('ДРР от предельной цены не определён' in c.value for c in app.caption)
    assert app.selectbox(key='article_dashboard').value=='demo'
    table=next(d.value for d in app.dataframe if 'Заказано, шт.' in d.value.columns).set_index('Артикул')
    assert table.loc['demo','Заказано, шт.']==5
    assert table.loc['orders-only','Отменено, шт.']==2
    import pandas as pd
    assert pd.isna(table.loc['orders-only','Продано, шт.'])
    app.selectbox(key='article_dashboard').set_value('orders-only').run()
    assert not app.exception
    assert next(m for m in app.metric if m.label=='Заказано по артикулу').value=='2 шт.'
    assert next(m for m in app.metric if m.label=='Отменено по артикулу').value=='2 шт.'
    assert next(m for m in app.metric if m.label=='Продано по артикулу').value=='Нет данных'
    assert next(m for m in app.metric if m.label=='ДРР артикула относительно предельной цены').value=='Нет данных'
