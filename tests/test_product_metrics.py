from decimal import Decimal as D
from ozon_profit.core import connect,save_cost,import_operations,calculate
from ozon_profit.product_metrics import summarize


def sample():
    db=connect(':memory:')
    save_cost(db,'a','А','2026-01-01',10);save_cost(db,'b','Б','2026-01-01',20)
    import_operations(db,b'fictional-products','fictional.xlsx',[
        dict(id='a',day='2026-10-01',kind='sale',sku='a',qty=10,amount=1000),
        dict(id='b',day='2026-10-01',kind='sale',sku='b',qty=10,amount=500),
        dict(id='ads',day='2026-10-01',kind='advertising',sku='',qty=0,amount=-90),
        dict(id='service',day='2026-10-01',kind='service',sku='',qty=0,amount=-150)])
    return db,calculate(db,'2026-10-01','2026-10-31')


def test_allocated_products_add_up_to_store_and_drr_uses_buyer_revenue():
    db,result=sample();summary=summarize(result)
    a=summary['products']['a'];b=summary['products']['b']
    assert sum(p['profit'] for p in summary['products'].values())==result['profit']
    assert a['advertising']==60 and b['advertising']==30
    assert a['drr']==b['drr']==6
    assert a['approximate'] and not a['blocked']
    assert a['quantity']==10 and a['unit_profit']==a['profit']/10
    assert a['margin']==a['profit']/1000*100
    assert db.execute('SELECT amount FROM operations WHERE id=?',('ads',)).fetchone()[0]=='-90'
    db.close()


def test_direct_view_never_claims_complete_profit_without_shared_costs():
    db,result=sample();summary=summarize(result,False)
    assert summary['products']['a']['blocked']
    assert summary['products']['a']['drr'] is None
    db.close()


def test_article_dashboard_switch_does_not_filter_store_totals(tmp_path,monkeypatch):
    import sqlite3
    from streamlit.testing.v1 import AppTest
    db,_=sample();target=sqlite3.connect(tmp_path/'profit.db');db.backup(target);target.close();db.close()
    monkeypatch.setenv('OZON_PROFIT_DATA_DIR',str(tmp_path))
    app=AppTest.from_file('app.py').run()
    app.selectbox(key='overview_period').set_value('Свои даты').run()
    main=lambda:next(m.value for m in app.metric if m.label=='Чистая прибыль · предварительно')
    article=lambda:next(m.value for m in app.metric if m.label=='Чистая прибыль по артикулу')
    assert main()=='900,00 ₽' and article()=='700,00 ₽'
    assert next(m.value for m in app.metric if m.label=='Продано за период')=='20 шт.'
    assert next(m.value for m in app.metric if m.label=='Продано по артикулу')=='10 шт.'
    assert 'Продано, шт.' in app.dataframe[0].value.columns
    app.selectbox(key='article_dashboard').set_value('b').run()
    assert not app.exception
    assert main()=='900,00 ₽' and article()=='200,00 ₽'


def test_sold_and_refunded_quantities_exclude_fee_and_stock_movements():
    db,result=sample()
    import_operations(db,b'returns-units','fictional.xlsx',[
        dict(id='return',day='2026-10-02',kind='return',sku='a',qty=2,amount=-200,order_no='demo'),
        dict(id='stock',day='2026-10-03',kind='restock',sku='a',qty=1,amount=0,order_no='demo')])
    result=calculate(db,'2026-10-01','2026-10-31')
    assert result['sold_units']==20 and result['returned_units']==2 and result['net_units']==18
    a=summarize(result)['products']['a']
    assert a['sold_quantity']==10 and a['returned_quantity']==2 and a['quantity']==8
    db.close()
