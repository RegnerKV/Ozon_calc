from datetime import date
from decimal import Decimal
import json
import requests
import pytest
from ozon_profit.seller_api import SellerClient,ApiError,ENDPOINTS,sync,windows,init_tables
from ozon_profit.core import connect,calculate
from ozon_profit import credentials

# Synthetic protocol fixtures; these do NOT certify current Ozon contracts.
TEST_KEY='synthetic-test-key-not-a-real-secret'

class Response:
    def __init__(self,result=None,status=200,headers=None):
        self.status_code=status;self.headers=headers or {};self.result=result
    def json(self,**kwargs):return {'result':self.result}

class Session:
    def __init__(self,responses):self.responses=iter(responses);self.calls=[];self.closed=False
    def post(self,url,**kwargs):
        self.calls.append((url,kwargs))
        response=next(self.responses)
        if isinstance(response,Exception):raise response
        return response
    def close(self):self.closed=True


def transaction(id=1,amount='20'):
    return dict(operation_id=id,operation_date='2026-08-03 00:00:00',operation_type='DemoOperation',
        operation_type_name='Демо',amount=amount,accruals_for_sale='50',sale_commission='-30',
        posting={'posting_number':'100-200-1','delivery_schema':'FBS','buyer_name':'PRIVATE'},
        items=[{'sku':987,'name':'Демо'}],services=[],customer={'name':'PRIVATE'})


def finance_response(items,pages=1,total=None):
    return Response(dict(operations=items,page_count=pages,row_count=len(items) if total is None else total))


def client(responses):
    session=Session(responses);delays=[]
    return SellerClient('123',TEST_KEY,session=session,sleep=delays.append),session,delays


def test_transport_headers_tls_defaults_and_no_redirects():
    api,session,_=client([finance_response([])])
    assert api.finances('from','to')==[]
    url,call=session.calls[0]
    assert url.startswith('https://api-seller.ozon.ru/')
    assert call['headers']['Api-Key']==TEST_KEY
    assert call['allow_redirects'] is False
    assert 'verify' not in call
    assert TEST_KEY not in repr(api)


def test_finance_pagination_and_whitelist():
    api,session,_=client([finance_response([transaction(1)],2,2),finance_response([transaction(2)],2,2)])
    rows=api.finances('from','to')
    assert len(rows)==2
    assert [c[1]['json']['page'] for c in session.calls]==[1,2]
    assert 'PRIVATE' not in json.dumps(rows)


def test_changed_page_count_aborts():
    api,_,_=client([finance_response([transaction(1)],2,2),finance_response([transaction(2)],3,3)])
    with pytest.raises(ApiError):api.finances('from','to')


def test_duplicate_financial_id_aborts():
    api,_,_=client([finance_response([transaction(1)],2,2),finance_response([transaction(1)],2,2)])
    with pytest.raises(ApiError):api.finances('from','to')


def test_missing_page_rows_aborts():
    api,_,_=client([finance_response([transaction(1)],1,2)])
    with pytest.raises(ApiError):api.finances('from','to')


def test_rate_limit_and_retry_after_are_bounded():
    api,_,delays=client([Response(status=429,headers={'Retry-After':'9999'}),finance_response([])])
    assert api.finances('from','to')==[]
    assert delays==[30]

@pytest.mark.parametrize('status',[401,403,400,302])
def test_http_failure_is_sanitized(status):
    api,session,_=client([Response(status=status)])
    with pytest.raises(ApiError) as exc:api.finances('from','to')
    assert TEST_KEY not in str(exc.value)
    assert len(session.calls)==1


def test_network_exception_does_not_expose_secret():
    api,_,delays=client([requests.ConnectionError(TEST_KEY)]*4)
    with pytest.raises(ApiError) as exc:api.finances('from','to')
    assert TEST_KEY not in str(exc.value)
    assert len(delays)==3


def posting(number='100-200-1',status='delivered'):
    return dict(posting_number=number,status=status,in_process_at='2026-08-01T00:00:00Z',
        customer={'name':'PRIVATE'},products=[{'sku':987,'offer_id':'demo','name':'Демо','quantity':2,'price':'25'}])


def test_fbs_pagination_and_product_offer_id():
    api,session,_=client([Response({'postings':[posting()],'has_next':True}),Response({'postings':[posting('200-300-1')],'has_next':False})])
    rows=api.postings('fbs','from','to')
    assert rows[0]['products'][0]['offer_id']=='demo'
    assert session.calls[1][1]['json']['offset']==1
    assert 'PRIVATE' not in json.dumps(rows)


def test_fbo_array_response():
    api,_,_=client([Response([posting()])])
    assert len(api.postings('fbo','from','to'))==1


def test_empty_next_page_aborts():
    api,_,_=client([Response({'postings':[],'has_next':True})])
    with pytest.raises(ApiError):api.postings('fbs','from','to')


def test_atomic_sync_and_idempotency():
    db=connect(':memory:')
    for amount in ('20','18'):
        api,_,_=client([finance_response([transaction(amount=amount)]),Response([]),Response({'postings':[posting()],'has_next':False})])
        stats=sync(db,api,date(2026,8,1),date(2026,8,10))
        assert stats['finance']==1
    assert db.execute('SELECT count(*) FROM api_finance').fetchone()[0]==1
    assert db.execute('SELECT amount FROM api_finance').fetchone()[0]=='18'
    assert db.execute('SELECT count(*) FROM api_postings').fetchone()[0]==1
    assert calculate(db,'2026-08-01','2026-08-31')['count']==0
    db.close()


def test_late_failure_preserves_snapshot_and_success_timestamp():
    db=connect(':memory:')
    api,_,_=client([finance_response([transaction()]),Response([]),Response({'postings':[],'has_next':False})])
    sync(db,api,date(2026,8,1),date(2026,8,10))
    stamp=db.execute("SELECT value FROM settings WHERE key='api_last_success'").fetchone()[0]
    broken,_,_=client([finance_response([transaction(amount='1')]),Response(status=403)])
    with pytest.raises(ApiError):sync(db,broken,date(2026,8,1),date(2026,8,10))
    assert db.execute('SELECT amount FROM api_finance').fetchone()[0]=='20'
    assert db.execute("SELECT value FROM settings WHERE key='api_last_success'").fetchone()[0]==stamp
    db.close()


def test_missing_operation_removed_only_in_refreshed_period():
    db=connect(':memory:')
    api,_,_=client([finance_response([transaction()]),Response([]),Response({'postings':[],'has_next':False})])
    sync(db,api,date(2026,8,1),date(2026,8,10))
    api,_,_=client([finance_response([]),Response([]),Response({'postings':[],'has_next':False})])
    sync(db,api,date(2026,8,1),date(2026,8,10))
    assert db.execute('SELECT count(*) FROM api_finance').fetchone()[0]==0
    db.close()


def test_wrong_shop_blocked_before_network():
    db=connect(':memory:')
    api,_,_=client([finance_response([]),Response([]),Response({'postings':[],'has_next':False})])
    sync(db,api,date(2026,8,1),date(2026,8,10))
    api.client_id='456'
    with pytest.raises(ApiError,match='другим магазином'):sync(db,api,date(2026,8,1),date(2026,8,10))
    db.close()


def test_windows_are_contiguous_and_moscow_based():
    periods=list(windows(date(2026,8,1),date(2026,9,1)))
    assert len(periods)==2
    assert periods[0][2]=='2026-07-31T21:00:00Z'
    assert periods[0][3]=='2026-08-28T20:59:59Z'
    assert periods[1][2]=='2026-08-28T21:00:00Z'


def test_credential_no_plaintext_fallback(tmp_path,monkeypatch):
    def refuse(*args,**kwargs):raise credentials.CredentialError('Unsupported')
    monkeypatch.setattr(credentials,'_transform',refuse)
    with pytest.raises(credentials.CredentialError):credentials.save('123',TEST_KEY,tmp_path/'key.credentials')
    assert list(tmp_path.iterdir())==[]


def test_credential_atomic_ciphertext_roundtrip(tmp_path,monkeypatch):
    # Storage mechanics only; native DPAPI is verified by the Windows-only test.
    def cipher(value,decrypt=False):
        return bytes(v^0x9f for v in value)
    monkeypatch.setattr(credentials,'_transform',cipher)
    target=tmp_path/'key.credentials'
    credentials.save('123',TEST_KEY,target)
    assert TEST_KEY.encode() not in target.read_bytes()
    assert credentials.load(target)==('123',TEST_KEY)
    credentials.delete(target)
    assert credentials.load(target) is None

@pytest.mark.skipif(__import__('os').name!='nt',reason='Native Windows DPAPI')
def test_native_windows_dpapi_roundtrip(tmp_path):
    target=tmp_path/'key.credentials'
    credentials.save('123',TEST_KEY,target)
    assert TEST_KEY.encode() not in target.read_bytes()
    assert credentials.load(target)==('123',TEST_KEY)
