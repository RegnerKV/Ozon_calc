"""Synthetic Performance responses. Live account response remains owner verification."""
from datetime import date
import pytest
from ozon_profit.core import connect,import_operations,calculate,save_cost
from ozon_profit.performance import Client,parse_expenses,save_credentials,load_credentials,sync,report,init
from ozon_profit.seller_api import ApiError
from ozon_profit.remote_storage import snapshot


class Response:
    def __init__(self,data,status=200):self.data=data;self.status_code=status
    def json(self):return self.data
class Session:
    def __init__(self,responses):self.responses=iter(responses);self.calls=[]
    def request(self,*args,**kw):self.calls.append((args,kw));return next(self.responses)
    def close(self):pass


def test_oauth_and_expense_request_use_fixed_host_tls_and_no_redirects():
    s=Session([Response({'access_token':'synthetic-token','expires_in':1800}),Response({'rows':[{'campaignId':1,'date':'2026-10-01','moneySpent':'50.25'}]})])
    c=Client('fictional@performance','synthetic-secret',session=s)
    assert c.expenses(date(2026,10,1),date(2026,10,1))==[('2026-10-01','1','','50.25')]
    assert s.calls[0][0][1]=='https://api-performance.ozon.ru/api/client/token'
    assert s.calls[1][1]['headers']['Authorization']=='Bearer synthetic-token'
    for _,kw in s.calls:
        assert kw['allow_redirects'] is False and 'verify' not in kw
    assert 'synthetic-secret' not in repr(c)


def test_unknown_report_is_not_treated_as_zero():
    with pytest.raises(ApiError):parse_expenses({'contentType':'unknown'},'2026-10-01','2026-10-31')
    with pytest.raises(ApiError):parse_expenses([{'date':'2026-10-01'}],'2026-10-01','2026-10-31')
    assert parse_expenses({'rows':[]},'2026-10-01','2026-10-31')==[]


def test_encrypted_connection_and_idempotent_statistic_do_not_double_charge_profit(tmp_path):
    db=connect(':memory:');password='fictional-owner-password'
    save_credentials(db,'synthetic@performance','fictional-client-secret',password)
    assert load_credentials(db,password)==('synthetic@performance','fictional-client-secret')
    raw=db.execute('SELECT value FROM secure_items').fetchone()[0]
    assert b'fictional-client-secret' not in raw
    save_cost(db,'demo','Демо','2026-01-01','10')
    import_operations(db,b'fictional','fictional.xlsx',[dict(id='sale',day='2026-10-01',kind='sale',sku='demo',amount=1000,qty=1),dict(id='ad',day='2026-10-01',kind='advertising',sku='',amount=-100,qty=0)])
    before=calculate(db,'2026-10-01','2026-10-31')['net']
    class Fake:
        def __init__(self,*args):pass
        def expenses(self,start,end):return [(str(start),'1','','100')]
        def close(self):pass
    for _ in range(2):sync(db,'2026-10-01','2026-10-31',password,client_factory=Fake)
    assert report(db,'2026-10-01','2026-10-31',1000)['store']==10
    assert calculate(db,'2026-10-01','2026-10-31')['net']==before==900
    assert calculate(db,'2026-10-01','2026-10-31')['profit']==850
    assert db.execute('SELECT count(*) FROM performance_expenses').fetchone()[0]==1
    assert b'fictional-client-secret' not in snapshot(db)
    class Broken(Fake):
        def expenses(self,*args):raise ApiError('synthetic outage')
    with pytest.raises(ApiError):sync(db,'2026-10-01','2026-10-31',password,client_factory=Broken)
    assert report(db,'2026-10-01','2026-10-31',1000)['total']==100
