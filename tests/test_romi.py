import copy
from decimal import Decimal as D
import pytest
from test_product_metrics import sample
from ozon_profit.romi import ratio,report
from ozon_profit.core import calculate


@pytest.mark.parametrize('profit,expense,expected',[
    ('200','100',D(200)),('-50','100',D(-50)),('0','100',D(0)),
    ('200','0',None),('200','-10',None),(None,'100',None),('200',None,None)])
def test_ratio_does_not_deduct_advertising_twice(profit,expense,expected):
    assert ratio(profit,expense)==expected


@pytest.mark.parametrize('expense,expected', [('90',D(1000)),('990',D(0)),('1500',D(-34))])
def test_financial_profit_and_existing_shared_allocation_are_preserved(expense,expected):
    db,_=sample()
    with db:db.execute("UPDATE operations SET amount=? WHERE id='ads'",('-'+expense,))
    result=calculate(db,'2026-10-01','2026-10-31');before=copy.deepcopy(result)
    values=report(result)
    assert values['store']==expected
    assert result==before
    assert calculate(db,'2026-10-01','2026-10-31')==before
    if expense=='90':
        assert values['products']['a']==D(700)/60*100
        assert values['products']['b']==D(200)/30*100
    db.close()


@pytest.mark.parametrize('field,value',[('missing',1),('stale_api',True),('net_mode',True),('count',0)])
def test_incomplete_financials_do_not_produce_percent(field,value):
    db,result=sample();result[field]=value
    assert report(result)['store'] is None and not report(result)['products']
    db.close()


def test_zero_advertising_is_unknown_ratio_not_zero_percent():
    db,_=sample()
    with db:db.execute("DELETE FROM operations WHERE id='ads'")
    values=report(calculate(db,'2026-10-01','2026-10-31'))
    assert values['store'] is None
    assert all(value is None for value in values['products'].values())
    db.close()


def test_conflicting_performance_total_blocks_mixed_source_ratio():
    db,result=sample()
    assert report(result,dict(total=D(91)))['store'] is None
    assert 'сверки' in report(result,dict(total=D(91)))['reason']
    assert report(result,dict(total=D(90)))['store']==1000
    db.close()


def test_dashboard_table_article_switch_and_period_use_same_romi(tmp_path,monkeypatch):
    import sqlite3
    from datetime import date
    from streamlit.testing.v1 import AppTest
    db,_=sample();target=sqlite3.connect(tmp_path/'profit.db');db.backup(target);target.close();db.close()
    monkeypatch.setenv('OZON_PROFIT_DATA_DIR',str(tmp_path))
    app=AppTest.from_file('app.py',default_timeout=20).run()
    app.selectbox(key='overview_period').set_value('Свои даты').run()
    def metric(label):return next(m.value for m in app.metric if m.label==label)
    assert not app.exception
    assert metric('ROMI магазина')=='1000,00 %'
    assert metric('ROMI артикула')=='1166,67 %'
    assert metric('Чистая прибыль · предварительно')=='900,00 ₽'
    table=next(d.value for d in app.dataframe if 'ROMI, %' in d.value.columns).set_index('Артикул')
    assert table.loc['a','ROMI, %']==pytest.approx(1166.6666667)
    app.selectbox(key='article_dashboard').set_value('b').run()
    assert not app.exception and metric('ROMI артикула')=='666,67 %'
    assert metric('ROMI магазина')=='1000,00 %'
    app.date_input(key='period_start').set_value(date(2026,9,1))
    app.date_input(key='period_end').set_value(date(2026,9,30)).run()
    assert not app.exception and metric('ROMI магазина')=='Нет данных'
