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
    assert next(m for m in app.metric if m.label=='Рекламные клики · известно').value=='15'
    assert next(m for m in app.metric if m.label=='Расход в отчёте, ₽ · известно').value=='Нет данных'
