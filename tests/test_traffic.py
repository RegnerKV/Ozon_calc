from datetime import date
import pandas as pd
import pytest
from ozon_profit import traffic
from ozon_profit.core import connect
from ozon_profit.seller_api import ApiError
from ozon_profit.performance import Client

A=date(2026,10,1);B=date(2026,10,4)


def test_observations_are_idempotent_missing_is_not_zero_and_not_allocated():
    db=connect(':memory:')
    for _ in range(2):
        traffic.replace(db,'traffic',A,B,[('2026-10-01','123',100,2),('2026-10-02','123',0,0),('2026-10-01','456',50,None)])
        traffic.replace(db,'promo',A,B,[('2026-10-01','on'),('2026-10-02','off')])
        traffic.replace(db,'click',A,B,[('2026-10-01','12'),('2026-10-02','0')])
    frame=traffic.daily(db,A,B)
    assert frame.views.iloc[0]==150 and pd.isna(frame.orders.iloc[0])
    assert frame.views.iloc[1]==0 and pd.isna(frame.views.iloc[2])
    assert frame.promo.iloc[2]=='unknown'
    assert db.execute('SELECT COUNT(*) FROM operations').fetchone()[0]==0
    result=traffic.comparison(frame)
    assert result['Дней'].sum()==2
    assert result.loc[result['Оплата за клики']=='Были расходы','Показы / день'].dropna().iloc[0]==150
    assert traffic.daily(db,A,B,'123').click.isna().all()


def test_promo_mixed_and_unknown_are_not_off():
    r=traffic.parse_promo([{'date':str(A),'status':'Включено'},{'date':str(A),'status':'Выключено'}],A,B)
    assert r==[(str(A),'mixed')]
    with pytest.raises(ApiError):traffic.parse_promo([{'date':str(A),'status':'obsolete'}],A,B)
    with pytest.raises(ApiError):traffic.parse_promo([{'date':'2025-01-01','status':'on'}],A,B)


def test_seller_dimensions_and_request():
    class Seller:
        def post(self,path,payload):
            assert path=='/v1/analytics/data'
            assert payload['dimension']==['sku','day']
            assert payload['metrics']==['hits_view','ordered_units']
            return {'result':{'data':[{'dimensions':[{'id':'123'},{'id':str(A)}],'metrics':[100,2]}]}}
    assert traffic.fetch_traffic(Seller(),A,B)==[(str(A),'123',100,2)]


def test_import_preserves_unknown_orders_and_rejects_duplicates():
    frame=pd.DataFrame([{'Дата':'01.10.2026','SKU':'123','Показы (всего)':0}])
    assert traffic.import_views(frame)==[(str(A),'123',0,None)]
    with pytest.raises(ApiError):traffic.import_views(pd.concat([frame,frame]))
    with pytest.raises(ApiError):traffic.import_views(frame.assign(**{'Показы (всего)':-2}))


def test_async_report_only_downloads_from_fixed_path():
    class Perf:
        token='fictional'
        def __init__(self):self.paths=[]
        def authorize(self):pass
        def request(self,method,path,**kwargs):
            self.paths.append(path)
            if path==traffic.PROMO:return {'UUID':'abc-123'}
            if path.endswith('abc-123'):return {'state':'OK','link':'https://attacker.invalid/token'}
            assert path=='/api/client/statistics/report'
            return {'rows':[{'date':str(A),'status':True}]}
    client=Perf()
    assert traffic.fetch_promo(client,A,B)==(None,[(str(A),'on')])
    assert not any('attacker' in p for p in client.paths)
    with pytest.raises(ApiError):traffic.fetch_promo(client,A,B,'../campaign/activate')


def test_clicks_filter_payment_type_not_just_sku_campaign():
    class Perf:
        token='fictional'
        def authorize(self):pass
        def request(self,method,path,**kwargs):
            if path.endswith('/campaign'):
                return {'list':[{'id':'1','paymentType':'CPC'},{'id':'2','paymentType':'CPO'}]}
            assert kwargs['params']['campaignIds']==['1']
            return {'rows':[{'campaignId':'1','date':str(A),'expense':'10.20'}]}
    result=traffic.fetch_clicks(Perf(),A,B)
    assert result==[(str(A),'10.20'),('2026-10-02','0'),('2026-10-03','0'),('2026-10-04','0')]


def test_mutating_get_routes_rejected():
    client=Client('fictional','fictional')
    for path in ('/api/client/campaign/all_sku_promo/activate','/api/client/campaign/all_sku_promo/deactivate','/api/client/campaign/all_sku_promo/set_bid'):
        with pytest.raises(ApiError):client.request('GET',path)


def test_page_without_connection(tmp_path,monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv('OZON_PROFIT_DATA_DIR',str(tmp_path))
    app=AppTest.from_file('app.py').run()
    app.sidebar.radio[0].set_value('Реклама').run()
    assert not app.exception
    next(b for b in app.button if b.label=='Обновить аналитику').click().run()
    assert not app.exception and app.warning


def test_real_seller_transport_accepts_read_only_analytics():
    from ozon_profit.seller_api import SellerClient
    class Response:
        status_code=200
        def json(self,**kw):return {'result':{'data':[]}}
    class Session:
        def post(self,url,**kw):
            assert url=='https://api-seller.ozon.ru/v1/analytics/data'
            assert kw['allow_redirects'] is False
            return Response()
    assert traffic.fetch_traffic(SellerClient('123','synthetic',session=Session()),A,B)==[]


def test_chart_contains_status_background_and_does_not_connect_missing_views():
    from ozon_profit.traffic_ui import chart
    frame=pd.DataFrame([{'day':str(A),'views':100,'orders':1,'promo':'on','click':10},
                        {'day':'2026-10-02','views':None,'orders':None,'promo':'unknown','click':None}])
    fig=chart(frame)
    assert len(fig.layout.shapes)==1
    assert fig.layout.shapes[0].fillcolor=='#dff568'
    assert fig.data[-1].connectgaps is False
    assert not chart(frame,False,False).layout.shapes


@pytest.mark.parametrize('value',[None,'',float('nan'),-1,1.5])
def test_missing_or_invalid_count_never_becomes_zero(value):
    with pytest.raises(ApiError):traffic.count(value)


@pytest.mark.parametrize('header,value,expected',[
 ('isEnabled',True,'on'),('is_active',False,'off'),('Статус','Включён','on'),
 ('Статус продвижения ','Выключена','off'),('promotion_status','ACTIVE','on'),
 ('Продвижение всех товаров','Выключено','off'),
])
def test_promo_report_header_variants_and_explicit_display_labels(header,value,expected):
    assert traffic.parse_promo([{'Дата':str(A),header:value,'expense':'0'}],A,B)==[(str(A),expected)]


def test_promo_does_not_infer_status_from_money_or_conflicting_labels():
    with pytest.raises(ApiError) as exc:
        traffic.parse_promo([{'date':str(A),'expense':'0','orders':1,'unknown':'private-value-not-for-errors'}],A,B)
    assert 'expense' in str(exc.value) and 'private-value-not-for-errors' not in str(exc.value)
    with pytest.raises(ApiError):
        traffic.parse_promo([{'date':str(A),'Статус A':'Включено','Статус B':'Выключено'}],A,B)


def test_source_errors_survive_reruns_and_new_sessions(tmp_path,monkeypatch):
    from streamlit.testing.v1 import AppTest
    import ozon_profit.traffic_ui as ui
    from ozon_profit.core import set_setting
    monkeypatch.setenv('OZON_PROFIT_DATA_DIR',str(tmp_path))
    db=connect();set_setting(db,'api_on_start','0');db.close()
    monkeypatch.setattr(ui,'active_credentials',lambda:('123','synthetic'))
    class Denied:
        def __init__(self,*a):pass
        def post(self,*a):raise ApiError('HTTP 403: доступ к показам ограничен')
        def close(self):pass
    monkeypatch.setattr(ui,'SellerClient',Denied)
    app=AppTest.from_file('app.py').run()
    app.sidebar.radio[0].set_value('Реклама').run()
    next(b for b in app.button if b.label=='Обновить аналитику').click().run()
    assert not app.exception
    assert any('HTTP 403' in w.value for w in app.warning)
    app.checkbox[0].uncheck().run()
    assert any('HTTP 403' in w.value for w in app.warning)
    reopened=AppTest.from_file('app.py').run()
    reopened.sidebar.radio[0].set_value('Реклама').run()
    assert not reopened.exception
    assert any('HTTP 403' in w.value for w in reopened.warning)


def test_unrelated_boolean_flag_is_not_promotion_state():
    with pytest.raises(ApiError):traffic.parse_promo([{'date':str(A),'isReturned':False,'expense':0}],A,B)
    assert traffic.parse_promo([{'date':str(A),'allSkuPromoEnabled':True}],A,B)==[(str(A),'on')]
