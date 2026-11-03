"""Fictional finances only; no uploaded store data or credentials."""
from datetime import date
import json
from decimal import Decimal as D
import pytest
from ozon_profit.core import connect,save_cost,set_setting,calculate,import_operations,get_setting
from ozon_profit.seller_api import init_tables,clean_finance
from ozon_profit.api_ledger import project,preview,promote,restore_previous

START=END='2026-08-03'


def m(value):return {'amount':str(value),'currency':'RUB'}


def posting_accrual(identifier=1,sale=500,bonus=100,partner=5,original=-200,price=250,delivery=-10):
    return {'accrual_id':identifier,'date':START,'accrued_category':'POSTING','unit_number':'100-200-1',
        'total_amount':m(sale+bonus+partner+original+delivery),
        'posting':{'delivery_schema':'FBS','products':[{'sku':987,
            'commission':{'sale_amount':m(sale+bonus+partner),'sale_price':m(price),
                'seller_price':m((sale+bonus+partner)/abs(sale/price) if price and sale else 0),'bonus':m(bonus),
                'coinvestment':m(partner),'sale_commission':m(original),'commission':m(original)},
            'delivery':{'total_accrued':m(delivery),'services':[{'type_id':8,'accrued':m(delivery)}]}}]}}


@pytest.fixture
def db():
    db=connect(':memory:');init_tables(db)
    save_cost(db,'demo','Демо',START,'80.005')
    with db:
        db.execute('INSERT INTO product_links VALUES(?,?)',('987','demo'))
        db.execute('INSERT INTO api_postings VALUES(?,?,?,?)',('100-200-1','fbs','delivered',json.dumps({
            'posting_number':'100-200-1','schema':'fbs','status':'delivered',
            'products':[{'sku':987,'offer_id':'demo','name':'Демо','quantity':2}]})))
    for key,value in [('api_start',START),('api_end',END),('api_last_success','2026-08-04T00:00:00Z'),('tax_base','sales')]:set_setting(db,key,value)
    yield db
    db.close()


def stage(db,items):
    with db:
        db.execute('DELETE FROM api_finance')
        for item in items:
            row=clean_finance(item,date.fromisoformat(START),{'8':'Логистика','9':'Продвижение с оплатой за заказ'})
            db.execute('INSERT INTO api_finance VALUES(?,?,?,?)',(row['id'],row['day'],row['amount'],json.dumps(row['payload'])))


def excel(db):
    import_operations(db,b'fictional-xlsx','demo.xlsx',[
        dict(id='ozon-xlsx:sale',day=START,kind='sale',amount=500,qty=2,sku='demo',name='Демо',order_no='100-200-1'),
        dict(id='ozon-xlsx:fee',day=START,kind='service',amount=-105,qty=0,sku='',order_no='')])


def test_projection_balances_original_commission_and_discounts(db):
    stage(db,[posting_accrual()])
    p=project(db,START,END)
    assert not p.issues and p.net==395
    assert sum(D(r['amount']) for r in p.rows)==395
    amounts={r['kind']:D(r['amount']) for r in p.rows}
    assert amounts['commission']==-200 and amounts['points']==100 and amounts['partner']==5
    assert amounts['logistics']==-10
    r=preview(db,p)
    assert r['revenue']==500 and r['costs']==D('160.010') and r['tax']==20
    assert r['profit']==D('214.990')
    assert db.execute('SELECT count(*) FROM operations').fetchone()[0]==0


def test_source_switch_replaces_excel_and_has_reversible_backup(db):
    excel(db);stage(db,[posting_accrual()])
    before=calculate(db,START,END)
    p=promote(db,START,END,enable=True)
    assert calculate(db,START,END)['net']==before['net']==395
    assert db.execute("SELECT count(*) FROM operations WHERE id LIKE 'ozon-xlsx:%'").fetchone()[0]==0
    assert db.execute('SELECT count(*) FROM ledger_backups').fetchone()[0]==1
    assert get_setting(db,'api_profit_enabled')=='1'
    restore_previous(db,START,END)
    assert db.execute("SELECT count(*) FROM operations WHERE id LIKE 'ozon-xlsx:%'").fetchone()[0]==2
    assert calculate(db,START,END)['profit']==before['profit']
    assert get_setting(db,'api_profit_enabled')=='0'


def test_repeat_promotion_is_idempotent_without_extra_backups(db):
    stage(db,[posting_accrual()])
    promote(db,START,END);promote(db,START,END)
    assert db.execute('SELECT count(*) FROM operations').fetchone()[0]==5
    assert db.execute('SELECT count(*) FROM ledger_backups').fetchone()[0]==0


def test_correction_replaces_previous_projection(db):
    stage(db,[posting_accrual()]);promote(db,START,END)
    stage(db,[posting_accrual(delivery=-15)]);promote(db,START,END)
    assert calculate(db,START,END)['net']==390
    assert db.execute('SELECT count(*) FROM operations').fetchone()[0]==5
    assert db.execute('SELECT count(*) FROM ledger_backups').fetchone()[0]==1


def test_missing_order_uses_exact_unit_price_and_saved_sku_link(db):
    with db:db.execute('DELETE FROM api_postings')
    stage(db,[posting_accrual()])
    p=project(db,START,END)
    assert not p.issues
    assert next(r['qty'] for r in p.rows if r['kind']=='sale')=='2'


def test_missing_unit_price_can_use_single_sale_order_composition(db):
    item=posting_accrual();del item['posting']['products'][0]['commission']['sale_price']
    stage(db,[item]);assert not project(db,START,END).issues


def test_missing_quantity_blocks_source_switch_and_preserves_excel(db):
    excel(db)
    with db:db.execute('DELETE FROM api_postings')
    item=posting_accrual();del item['posting']['products'][0]['commission']['sale_price']
    del item['posting']['products'][0]['commission']['seller_price']
    stage(db,[item]);p=project(db,START,END)
    assert p.issues
    with pytest.raises(ValueError):promote(db,START,END,enable=True)
    assert db.execute("SELECT count(*) FROM operations WHERE id LIKE 'ozon-xlsx:%'").fetchone()[0]==2
    assert get_setting(db,'api_profit_enabled')!='1'


def test_unexplained_total_blocks_projection_without_adjustment_plug(db):
    item=posting_accrual();item['total_amount']=m(999)
    stage(db,[item]);p=project(db,START,END)
    assert p.issues
    with pytest.raises(ValueError):preview(db,p)


def test_missing_original_commission_uses_reported_final_with_exact_balance(db):
    item=posting_accrual();del item['posting']['products'][0]['commission']['sale_commission']
    stage(db,[item]);assert not project(db,START,END).issues
    item['total_amount']=m(999)
    stage(db,[item]);assert project(db,START,END).issues


def test_repeated_sale_quantities_cannot_exceed_order(db):
    stage(db,[posting_accrual(1),posting_accrual(2)])
    assert project(db,START,END).issues


def test_returns_do_not_restore_inventory_or_estimate_partial_quantity(db):
    item=posting_accrual(sale=-250,bonus=-50,partner=0,original=100,price=-250,delivery=-5)
    stage(db,[item]);p=project(db,START,END)
    assert not p.issues
    r=preview(db,p)
    assert r['costs']==0 and r['revenue']==-250 and r['tax']==0
    del item['posting']['products'][0]['commission']['sale_price']
    del item['posting']['products'][0]['commission']['seller_price']
    stage(db,[item]);assert project(db,START,END).issues


def test_unknown_cost_is_visible_and_not_fabricated(db):
    with db:db.execute('DELETE FROM costs')
    stage(db,[posting_accrual()]);r=preview(db,project(db,START,END))
    assert r['missing']==1 and r['incomplete'] and r['costs']==0


def test_estimated_cost_and_tax_choice_preserved(db):
    save_cost(db,'demo','Демо',START,'80.005',True)
    stage(db,[posting_accrual()]);p=project(db,START,END)
    assert preview(db,p,'sales_partner')['tax']==D('20.20')
    assert preview(db,p)['estimated']==1
    promote(db,START,END,tax_base='sales_partner')
    assert calculate(db,START,END)['tax']==D('20.20')


def test_manual_finance_cannot_be_silently_replaced(db):
    import_operations(db,b'manual','manual',[dict(id='manual',day=START,kind='service',amount=-5,qty=0)])
    stage(db,[posting_accrual()])
    assert project(db,START,END).issues
    with pytest.raises(ValueError):promote(db,START,END)


def test_unloaded_period_cannot_erase_excel(db):
    excel(db);stage(db,[])
    assert project(db,'2026-08-01',END).issues
    with pytest.raises(ValueError):promote(db,'2026-08-01',END)
    assert calculate(db,START,END)['net']==395


def test_fully_downloaded_empty_period_removes_prior_owned_operations(db):
    excel(db);stage(db,[]);promote(db,START,END)
    assert calculate(db,START,END)['count']==0
    assert db.execute('SELECT count(*) FROM ledger_backups').fetchone()[0]==1


def test_advertising_already_in_total_is_not_deducted_twice(db):
    ad=dict(accrual_id=2,date=START,accrued_category='NON_ITEM',total_amount=m(-25),non_item_fee={'type_id':9,'accrued':m(-25)})
    stage(db,[posting_accrual(),ad]);p=project(db,START,END)
    assert not p.issues
    r=preview(db,p)
    assert r['net']==370 and r['profit']==D('189.990')
    assert sum(D(r['amount']) for r in p.rows if r['kind']=='advertising')==-25


def test_automatic_update_recomputes_profit_after_activation(db,monkeypatch):
    from ozon_profit import api_ui
    stage(db,[posting_accrual()]);promote(db,START,END,enable=True)
    monkeypatch.setattr(api_ui,'active_credentials',lambda:('123','synthetic-key'))
    def download(*args):
        stage(db,[posting_accrual(delivery=-15)])
        set_setting(db,'api_last_success','2026-08-05T00:00:00Z')
        return {'finance':1,'postings':1}
    monkeypatch.setattr(api_ui,'sync',download)
    result=api_ui.run_update(db,date.fromisoformat(START),date.fromisoformat(END),('fbs',))
    assert result['profit_status']=='updated'
    assert calculate(db,START,END)['profit']==D('209.990')
    assert get_setting(db,'api_profit_last_success')=='2026-08-05T00:00:00Z'


def test_automatic_update_keeps_old_profit_when_new_composition_is_unknown(db,monkeypatch):
    from ozon_profit import api_ui
    stage(db,[posting_accrual()]);promote(db,START,END,enable=True)
    monkeypatch.setattr(api_ui,'active_credentials',lambda:('123','synthetic-key'))
    def download(*args):
        item=posting_accrual();item['total_amount']=m(999)
        stage(db,[item]);set_setting(db,'api_last_success','2026-08-05T00:00:00Z')
        return {'finance':1,'postings':1}
    monkeypatch.setattr(api_ui,'sync',download)
    result=api_ui.run_update(db,date.fromisoformat(START),date.fromisoformat(END),('fbs',))
    assert result['profit_status']=='blocked' and get_setting(db,'api_profit_error')
    assert calculate(db,START,END)['profit']==D('214.990')
    assert get_setting(db,'api_profit_last_success')=='2026-08-04T00:00:00Z'


def test_promote_through_app_and_show_result(db,tmp_path,monkeypatch):
    from streamlit.testing.v1 import AppTest
    import sqlite3
    stage(db,[posting_accrual()])
    copy=sqlite3.connect(tmp_path/'profit.db')
    db.backup(copy);copy.close()
    monkeypatch.setenv('OZON_PROFIT_DATA_DIR',str(tmp_path))
    from ui_harness import technical_app
    app=technical_app().run()
    app.date_input(key='api_since').set_value(date.fromisoformat(START))
    app.date_input(key='api_until').set_value(date.fromisoformat(END)).run()
    assert not app.exception
    assert any(metric.value=='214.99 ₽' for metric in app.metric)
    app.checkbox(key='api_profit_approve').check()
    app.button(key='api_profit_enable').click().run()
    assert not app.exception
    saved=connect(tmp_path/'profit.db')
    assert get_setting(saved,'api_profit_enabled')=='1'
    assert calculate(saved,START,END)['profit']==D('214.990')
    saved.close()
    app=AppTest.from_file('app.py').run()
    app.selectbox(key='overview_period').set_value('Свои даты').run()
    assert not app.exception
    assert any(metric.value=='214,99 ₽' for metric in app.metric)


def test_single_cost_file_can_be_imported_for_api_products(db,tmp_path,monkeypatch):
    import sqlite3
    from io import BytesIO
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    from test_ozon_excel import xlsx
    stage(db,[posting_accrual()])
    with db:db.execute('DELETE FROM costs')
    copy=sqlite3.connect(tmp_path/'profit.db');db.backup(copy);copy.close()
    monkeypatch.setenv('OZON_PROFIT_DATA_DIR',str(tmp_path))
    content=xlsx('Украшения',[
        ['Название','Материалы','Количество','Цена','Итого'],['Демо','Материал',1,4,4],
        ['Итого',None,None,None,4],['С упаковкой:',None,None,None,80.005]])
    upload=BytesIO(content);upload.name='synthetic-costs.xlsx'
    monkeypatch.setattr(st,'file_uploader',lambda *args,**kwargs:upload if kwargs.get('key')=='api_cost_file' else None)
    from ui_harness import technical_app
    app=technical_app().run()
    app.date_input(key='api_cost_date').set_value(date.fromisoformat(START))
    app.button(key='api_cost_save').click().run()
    assert not app.exception
    saved=connect(tmp_path/'profit.db')
    cost=saved.execute('SELECT amount FROM costs WHERE sku=?',('demo',)).fetchone()
    assert cost[0]=='80.005'
    saved.close()


def test_reported_final_commission_with_markup_is_not_lost(db):
    item=posting_accrual()
    item['posting']['products'][0]['commission']['commission']=m(-205)
    item['total_amount']=m(390)
    stage(db,[item]);p=project(db,START,END)
    assert not p.issues
    amounts={r['kind']:D(r['amount']) for r in p.rows}
    assert amounts['commission']==-205 and amounts['points']==100 and amounts['partner']==5
    assert sum(D(r['amount']) for r in p.rows)==390
    assert preview(db,p)['tax']==20


def test_reported_delivery_total_replaces_service_breakdown(db):
    item=posting_accrual()
    item['posting']['products'][0]['delivery']['services'][0]['accrued']=m(-8)
    stage(db,[item]);p=project(db,START,END)
    assert not p.issues
    assert sum(D(r['amount']) for r in p.rows if r['kind']=='logistics')==-10
    assert p.net==395


def test_neither_known_decomposition_can_plug_arbitrary_difference(db):
    item=posting_accrual()
    item['posting']['products'][0]['commission']['commission']=m(-205)
    item['total_amount']=m(777)
    stage(db,[item]);p=project(db,START,END)
    assert p.issues
    with pytest.raises(ValueError):promote(db,START,END)
    assert db.execute('SELECT count(*) FROM operations').fetchone()[0]==0


def test_diagnostic_packet_whitelists_fields_and_does_not_mutate(db):
    from ozon_profit.api_ledger import diagnostics
    item=posting_accrual();item['total_amount']=m(999)
    stage(db,[item])
    with db:
        raw=db.execute('SELECT id,payload FROM api_finance').fetchone()
        p=json.loads(raw['payload']);p['Api-Key']='PRIVATE-TEST-SECRET';p['customer']={'address':'PRIVATE-ADDRESS'}
        db.execute('UPDATE api_finance SET payload=? WHERE id=?',(json.dumps(p),raw['id']))
    projection=project(db,START,END)
    blob=diagnostics(db,projection)
    assert b'PRIVATE-TEST-SECRET' not in blob and b'PRIVATE-ADDRESS' not in blob
    packet=json.loads(blob)
    assert packet['accruals'][0]['total']=='999'
    assert packet['accruals'][0]['products'][0]['commission']['sale_commission']=='-200'
    assert packet['postings'][0]['products'][0]['offer_id']=='demo'
    assert db.execute('SELECT count(*) FROM operations').fetchone()[0]==0


def test_gross_sale_is_split_before_tax_not_added_twice(db):
    stage(db,[posting_accrual()])
    p=project(db,START,END)
    amounts={r['kind']:D(r['amount']) for r in p.rows}
    assert amounts['sale']==500  # raw sale_amount is 605
    assert amounts['points']==100 and amounts['partner']==5
    assert amounts['commission']==-200
    assert sum(D(r['amount']) for r in p.rows)==395
    assert preview(db,p,'sales')['tax']==20
    assert preview(db,p,'sales_partner')['tax']==D('20.20')
    assert preview(db,p,'sales_points')['tax']==24
    assert preview(db,p,'sales_points_partner')['tax']==D('24.20')


def test_unknown_cost_blocks_activation_even_if_structurally_balanced(db):
    stage(db,[posting_accrual()])
    with db:db.execute('DELETE FROM costs')
    assert preview(db,project(db,START,END))['missing']==1
    with pytest.raises(ValueError,match='себестоимость'):promote(db,START,END,enable=True)
    assert get_setting(db,'api_profit_enabled')!='1'
    assert db.execute('SELECT count(*) FROM operations').fetchone()[0]==0


def test_zero_customer_price_still_spends_inventory(db):
    item=posting_accrual(sale=0,bonus=100,partner=5,original=0,price=0,delivery=0)
    item['posting']['products'][0]['commission']['seller_price']=m('52.5')
    stage(db,[item]);p=project(db,START,END)
    assert not p.issues
    r=preview(db,p)
    assert r['revenue']==0 and r['net']==105 and r['costs']==D('160.010')
    assert r['tax']==0


def test_standalone_points_do_not_create_fake_customer_return(db):
    item=posting_accrual(sale=0,bonus=10,partner=0,original=0,price=0,delivery=0)
    item['posting']['products'][0]['commission']['sale_amount']=m(0)
    stage(db,[item]);p=project(db,START,END)
    assert not p.issues
    assert all(row['kind'] not in ('sale','return') for row in p.rows)
    r=preview(db,p)
    assert r['revenue']==0 and r['costs']==0 and r['net']==10


def test_legacy_gross_projection_is_marked_until_recomputed(db):
    import_operations(db,b'old-model','Ozon Seller API',[
        dict(id='api-ledger:old',day=START,kind='sale',amount=605,qty=2,sku='demo',order_no='100-200-1')])
    assert calculate(db,START,END)['stale_api']
    stage(db,[posting_accrual()]);promote(db,START,END)
    r=calculate(db,START,END)
    assert not r['stale_api'] and r['revenue']==500 and r['tax']==20


def test_missing_cost_ui_hides_profit_and_blocks_activation(db,tmp_path,monkeypatch):
    import sqlite3
    from streamlit.testing.v1 import AppTest
    stage(db,[posting_accrual()])
    with db:db.execute('DELETE FROM costs')
    copy=sqlite3.connect(tmp_path/'profit.db');db.backup(copy);copy.close()
    monkeypatch.setenv('OZON_PROFIT_DATA_DIR',str(tmp_path))
    from ui_harness import technical_app
    app=technical_app().run()
    app.date_input(key='api_since').set_value(date.fromisoformat(START))
    app.date_input(key='api_until').set_value(date.fromisoformat(END)).run()
    assert not app.exception
    assert any(m.label=='Предварительная прибыль' and m.value=='Нужна себестоимость' for m in app.metric)
    app.checkbox(key='api_profit_approve').check().run()
    assert app.button(key='api_profit_enable').disabled


def test_dashboard_shows_staged_period_when_other_period_blocks_promotion(db):
    from ozon_profit.dashboard import snapshot,latest_period
    stage(db,[posting_accrual()])
    set_setting(db,'api_end','2026-10-31')
    set_setting(db,'api_profit_error','Другой период требует проверки')
    result,issues,raw=snapshot(db,START,END)
    assert not issues and raw is None
    assert result['net']==395 and result['profit']==D('214.990')
    assert latest_period(db,date(2026,11,2))==(date(2026,8,3),date(2026,8,3))
    assert db.execute('SELECT count(*) FROM operations').fetchone()[0]==0


def test_dashboard_missing_historical_cost_still_shows_receipts(db):
    from ozon_profit.dashboard import snapshot
    stage(db,[posting_accrual()])
    db.execute('UPDATE costs SET since=?',('2026-11-01',));db.commit()
    result,issues,raw=snapshot(db,START,END)
    assert not issues and raw is None
    assert result['net']==395 and result['revenue']==500
    assert result['missing']==1
    assert any(r['issue']=='Не задана себестоимость на дату продажи' for r in result['details'])
    assert db.execute('SELECT count(*) FROM operations').fetchone()[0]==0


def test_dashboard_unmatched_sku_shows_raw_receipts_and_blocks_profit(db):
    from ozon_profit.dashboard import snapshot
    stage(db,[posting_accrual()])
    db.execute('DELETE FROM product_links');db.execute('DELETE FROM api_postings');db.commit()
    result,issues,raw=snapshot(db,START,END)
    assert issues and raw==395
    assert result['count']==0


def test_dashboard_ui_explains_missing_cost_instead_of_zero_receipts(db,tmp_path,monkeypatch):
    from streamlit.testing.v1 import AppTest
    import sqlite3
    stage(db,[posting_accrual()])
    db.execute('DELETE FROM costs');db.commit()
    set_setting(db,'fish_cost_policy','0')
    target=sqlite3.connect(tmp_path/'profit.db');db.backup(target);target.close()
    monkeypatch.setenv('OZON_PROFIT_DATA_DIR',str(tmp_path))
    app=AppTest.from_file('app.py').run()
    assert not app.exception
    app.selectbox(key='overview_period').set_value('Свои даты').run()
    metrics={metric.label:metric.value for metric in app.metric}
    assert metrics['Начислено Ozon']=='395,00 ₽'
    assert metrics['Реализация покупателям']=='500,00 ₽'
    assert metrics['Чистая прибыль · предварительно']=='Нужна себестоимость'
    assert any('на дату продажи' in warning.value for warning in app.warning)
    assert any('Дата продажи' in frame.value.columns for frame in app.dataframe)


def test_sales_plan_reads_staged_history_even_without_promoted_operations(db):
    from ozon_profit.planning import forecast
    stage(db,[posting_accrual()])
    assert db.execute('SELECT count(*) FROM operations').fetchone()[0]==0
    r=forecast(db,[{'sku':'demo','quantity':50}], '2026-12-01','2026-12-31',START,END,
               ad_budget=500,order_ad_percent=6)
    assert not r['issues']
    assert r['totals']['profit']==D('4124.750')
    assert db.execute('SELECT count(*) FROM operations').fetchone()[0]==0
