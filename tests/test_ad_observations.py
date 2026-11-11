import copy
import json
import sqlite3
import pytest
from ozon_profit import ad_observations as ads
from ozon_profit.seller_api import ApiError


def snapshot():
    return dict(schema_version=3,tool='OzonApiDiagnostics',control_article='synthetic',
        period={'from':'2026-10-01','to':'2026-10-02'},created_at='synthetic',
        owner_reference_not_api_result={'advertising_clicks_approx':99999},
        performance_coverage=dict(eligible_campaigns=3,reports_received=2,target_reports_observed=2,campaign_listing_complete=True),
        checks=[{'sample':{'target_rows':2,'target_daily_statistics':{'state':'parsed','days':[
            {'day':'2026-10-01','clicks':'10','toCart':'2','orders':'1'},
            {'day':'2026-10-02','clicks':None,'toCart':None}]}}},
            {'sample':{'target_rows':1,'target_daily_statistics':{'state':'parsed','days':[
                {'day':'2026-10-01','clicks':'5','toCart':'0','orders':'1'}]}}}])


def test_diagnostic_missing_values_and_orders_not_summed():
    run=ads.diagnostic_snapshot(snapshot());s=ads.summary(run)
    assert s['clicks']==dict(observed='15',known_rows=2,missing_rows=1)
    assert s['toCart']['observed']=='2'
    assert s['expense']['observed'] is None
    assert s['orders']['observed'] is None
    assert s['orders']['known_rows']==2
    assert not s['expense']['cpc_verified'] and not run['complete']
    assert '99999' not in json.dumps(run)


@pytest.mark.parametrize('raw',[True,float('nan'),'-1','1.5','unknown'])
def test_invalid_count(raw):
    if raw is True: assert ads.number(raw,'clicks') is None
    else:
        with pytest.raises(ApiError): ads.number(raw,'clicks')


@pytest.mark.parametrize('row,expected',[
    ({},'unknown'),({'paymentType':'CPC'},'CPC'),
    ({'paymentType':'CPC','payment_type':'CPO'},'excluded'),
    ({'advObjectType':'SKU'},'unknown'),({'advObjectType':'SEARCH_PROMO'},'excluded')])
def test_payment(row,expected): assert ads.payment(row)==expected


def test_raw_report_verifies_campaign_target_and_unique_day():
    row=dict(sku='123',date='01.10.2026',clicks='4',toCart='0')
    result=ads.parse_report({'7':{'report':{'rows':[row]}}},'7',{'123'},'2026-10-01','2026-10-02')
    assert result[0]['clicks']=='4' and result[0]['expense'] is None
    for bad in ({'8':{'rows':[row]}},{'7':{'rows':[row,row]}},{'7':{'rows':[{**row,'date':'2026-09-01'}]}}):
        with pytest.raises(ApiError):ads.parse_report(bad,'7',{'123'},'2026-10-01','2026-10-02')


class Client:
    token='synthetic'
    def __init__(self): self.calls=[];self.ready=False
    def authorize(self): pass
    def request(self,method,path,**kw):
        self.calls.append((method,path))
        if path.endswith('/campaign'): return {'list':[{'id':'7','advObjectType':'SKU'}]}
        if method=='POST': return {'UUID':'00000000-0000-4000-8000-000000000001'}
        if path.endswith('/report'): return {'7':{'rows':[dict(sku='123',date='2026-10-01',clicks='4')]}}
        return {'state':'OK' if self.ready else 'IN_PROGRESS'}


def test_resumed_job_no_duplicate_export_and_finance_unchanged():
    db=sqlite3.connect(':memory:');db.execute('CREATE TABLE operations(value TEXT)');db.execute("INSERT INTO operations VALUES('untouched')");db.commit()
    client=Client()
    run=ads.advance(db,'synthetic',{'123'},'2026-10-01','2026-10-02',client)
    assert run['received']==0 and run['jobs'][0]['state']=='pending'
    with pytest.raises(ApiError):ads.advance(db,'another',{'123'},'2026-10-01','2026-10-02',client)
    client.ready=True
    run=ads.advance(db,'synthetic',{'123'},'2026-10-01','2026-10-02',client)
    assert run['complete'] and run['received']==1
    ads.advance(db,'synthetic',{'123'},'2026-10-01','2026-10-02',client)
    assert sum(m=='POST' for m,p in client.calls)==1
    assert db.execute('SELECT * FROM operations').fetchall()==[('untouched',)]
    assert ads.summary(run)['clicks']['observed']=='4'


def test_snapshot_never_changes_finance_and_persists(tmp_path):
    path=tmp_path/'local.db';db=sqlite3.connect(path)
    run=ads.diagnostic_snapshot(snapshot());ads.save(db,run);db.close()
    db=sqlite3.connect(path)
    assert ads.load(db,'synthetic','2026-10-01','2026-10-02')==run
    assert ads.load(db,'synthetic','2026-10-02','2026-10-02') is None


def test_ui_keeps_rc3_navigation_and_missing_expenses(tmp_path,monkeypatch):
    from ozon_profit.core import connect
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv('OZON_PROFIT_DATA_DIR',str(tmp_path))
    db=connect();ads.save(db,ads.diagnostic_snapshot(snapshot()));db.close()
    app=AppTest.from_file('app.py',default_timeout=20).run()
    assert app.sidebar.radio[0].options==['Обзор','Товары','План продаж','Настройки']
    app.selectbox(key='overview_period').set_value('Свои даты').run()
    from datetime import date
    app.date_input(key='period_start').set_value(date(2026,10,1))
    app.date_input(key='period_end').set_value(date(2026,10,2)).run()
    assert not app.exception
    assert any(h.value=='Что это говорит об эффективности рекламы' for h in app.subheader)
    assert any('Окупаемость рекламы не установлена' in m.value for m in app.markdown)
    assert next(m for m in app.metric if m.label=='Рекламные клики · известно').value=='15'
    assert next(m for m in app.metric if m.label=='Расход в отчёте, ₽ · известно').value=='Нет данных'


def test_rejected_report_does_not_hide_remaining_campaigns():
    from ozon_profit.performance import PerformanceHttpError
    class Rejected(Client):
        def request(self,method,path,**kw):
            if path.endswith('/campaign'): return {'list':[{'id':'6'},{'id':'7'}]}
            if method=='POST' and kw['json']['campaigns']==['6']: raise PerformanceHttpError(400)
            return super().request(method,path,**kw)
    client=Rejected();client.ready=True;db=sqlite3.connect(':memory:')
    run=ads.advance(db,'synthetic',{'123'},'2026-10-01','2026-10-02',client)
    assert [j['state'] for j in run['jobs']]==['rejected','done']
    assert run['received']==1 and run['eligible']==2 and not run['complete']


def test_uncertain_submission_blocks_duplicate_generation():
    class Uncertain(Client):
        def request(self,method,path,**kw):
            if method=='POST': raise ApiError('synthetic timeout')
            return super().request(method,path,**kw)
    db=sqlite3.connect(':memory:');client=Uncertain()
    with pytest.raises(ApiError):ads.advance(db,'synthetic',{'123'},'2026-10-01','2026-10-02',client)
    assert ads.load(db,'synthetic','2026-10-01','2026-10-02')['jobs'][0]['state']=='submitting'
    with pytest.raises(ApiError,match='не подтверждён'):ads.advance(db,'synthetic',{'123'},'2026-10-01','2026-10-02',client)


def test_effectiveness_uses_paired_rows_not_mismatched_totals():
    run=ads.diagnostic_snapshot(snapshot())
    run['reports'][0]['rows'].append(dict(day='2026-10-02',clicks='100',toCart=None))
    finding=ads.effectiveness(run)
    assert finding['paired_rows']==2
    assert finding['paired_clicks']==15 and finding['paired_carts']==2
    assert finding['cart_events_per_100_clicks']==ads.Decimal(2)/15*100
    assert finding['verdict']=='Окупаемость рекламы не установлена'
    assert any('расходов' in gap for gap in finding['gaps'])


def test_effectiveness_unknown_and_explicit_zero_are_distinct():
    run=ads.diagnostic_snapshot(snapshot())
    for report in run['reports']:
        for row in report['rows']:row['clicks']=None;row['toCart']=None
    finding=ads.effectiveness(run)
    assert finding['cart_events_per_100_clicks'] is None
    assert 'Данных' in finding['interest']
    run['reports'][0]['rows'][0].update(clicks='0',toCart='0')
    finding=ads.effectiveness(run)
    assert finding['cart_events_per_100_clicks'] is None
    assert 'известных строках' in finding['interest']


def test_ui_continues_pending_export_without_another_click(tmp_path,monkeypatch):
    from datetime import date
    from streamlit.testing.v1 import AppTest
    from ozon_profit.core import connect
    from ozon_profit import ad_observations_ui as ui
    monkeypatch.setenv('OZON_PROFIT_DATA_DIR',str(tmp_path))
    db=connect();db.execute("INSERT INTO products VALUES('synthetic','Demo')")
    db.execute("INSERT INTO product_links VALUES('123','synthetic')");db.commit();db.close()
    client=Client();client.close=lambda:None
    monkeypatch.setattr(ui,'Client',lambda *args:client)
    monkeypatch.setattr(ui,'load_credentials',lambda *args:('synthetic','synthetic'))
    app=AppTest.from_file('app.py',default_timeout=20).run()
    app.selectbox(key='overview_period').set_value('Свои даты').run()
    app.date_input(key='period_start').set_value(date(2026,10,1))
    app.date_input(key='period_end').set_value(date(2026,10,2)).run()
    app.button(key='ad_advance').click().run()
    assert not app.exception
    assert app.session_state['_ad_running']==('synthetic','2026-10-01','2026-10-02')
    client.ready=True;app.run()
    assert not app.exception and app.session_state['_ad_running'] is None
    assert sum(method=='POST' for method,path in client.calls)==1
    assert next(m for m in app.metric if m.label=='Рекламные клики · известно').value=='4'
