"""Synthetic fixtures matching October 2026 published OpenAPI mirrors.
Live authorization and actual Ozon payloads remain a separate verification.
"""
from datetime import date
import json
import requests
import pytest
from ozon_profit.seller_api import SellerClient,ApiError,ApiHttpError,ENDPOINTS,sync,windows,clean_finance
from ozon_profit.core import connect,calculate
from ozon_profit import credentials

TEST_KEY='synthetic-test-key-not-a-real-secret'
SINCE='2026-08-02T21:00:00Z'
TO='2026-08-03T20:59:59Z'
DAY=date(2026,8,3)

class Response:
    def __init__(self,result=None,status=200,headers=None):
        self.status_code=status;self.headers=headers or {};self.result={} if result is None else result
    def json(self,**kwargs):return self.result

class Session:
    def __init__(self,responses):self.responses=iter(responses);self.calls=[];self.closed=False
    def post(self,url,**kwargs):
        self.calls.append((url,kwargs))
        response=next(self.responses)
        if isinstance(response,Exception):raise response
        return response
    def close(self):self.closed=True


def client(responses):
    session=Session(responses);delays=[]
    return SellerClient('123',TEST_KEY,session=session,sleep=delays.append),session,delays


def rub(value):return {'amount':str(value),'currency':'RUB'}

def accrual(identifier=1,amount='20',category='POSTING'):
    return dict(accrual_id=identifier,date='2026-08-03',accrued_category=category,total_amount=rub(amount),
        unit_number='100-200-1',posting={'delivery_schema':'FBS','buyer_name':'PRIVATE',
        'products':[{'sku':987,'commission':{'sale_amount':rub(50),'sale_commission':rub(-30),'commission':rub(-20),'bonus':rub(10)}}]},
        non_item_fee={'type_id':7,'accrued':rub(amount)},customer={'name':'PRIVATE'})


def types_response():return Response({'accrual_types':[{'id':7,'name':'Синтетическая услуга'}]})

def day_response(items,last_id=''):return Response({'accruals':items,'last_id':last_id})

def posting(number='100-200-1'):
    return dict(posting_number=number,status='delivered',in_process_at='2026-08-01T00:00:00Z',
        customer={'name':'PRIVATE'},products=[{'sku':987,'offer_id':'demo','name':'Демо','quantity':2,'price':rub(25)}])

def posting_response(items=None,more=False,cursor=''):
    return Response({'postings':items or [],'has_next':more,'cursor':cursor})


def test_transport_uses_new_paths_and_retains_tls_verification():
    api,session,_=client([Response({})])
    assert api.post(ENDPOINTS['finance'],{'date':str(DAY),'last_id':''})=={}
    url,call=session.calls[0]
    assert url=='https://api-seller.ozon.ru/v1/finance/accrual/by-day'
    assert call['headers']['Api-Key']==TEST_KEY and call['allow_redirects'] is False
    assert 'verify' not in call and TEST_KEY not in repr(api)
    assert ENDPOINTS['fbo']=='/v3/posting/fbo/list'
    assert ENDPOINTS['fbs']=='/v4/posting/fbs/list'


def test_retired_paths_are_not_permitted():
    api,session,_=client([])
    for endpoint in ['/v3/finance/transaction/list','/v2/posting/fbo/list','/v3/posting/fbs/list']:
        with pytest.raises(ApiError):api.post(endpoint,{})
    assert not session.calls


def test_daily_finance_cursor_and_exact_date_reuse():
    api,session,_=client([types_response(),day_response([accrual(1)],'cursor1'),day_response([accrual(2)])])
    rows=api.finances(SINCE,TO)
    assert len(rows)==2
    assert session.calls[1][1]['json']=={'date':str(DAY),'last_id':''}
    assert session.calls[2][1]['json']=={'date':str(DAY),'last_id':'cursor1'}
    assert 'PRIVATE' not in json.dumps(rows)


def test_empty_protobuf_daily_response_is_accepted():
    api,_,_=client([types_response(),Response({})])
    assert api.finances(SINCE,TO)==[]


def test_finance_uses_each_calendar_day():
    api,session,_=client([types_response(),day_response([accrual()]),day_response([])])
    rows=api.finances(SINCE,'2026-08-04T20:59:59Z')
    assert len(rows)==1
    assert [c[1]['json']['date'] for c in session.calls[1:]]==['2026-08-03','2026-08-04']


def test_duplicate_financial_id_aborts():
    api,_,_=client([types_response(),day_response([accrual()],'a'),day_response([accrual()])])
    with pytest.raises(ApiError):api.finances(SINCE,TO)


def test_finance_nonadvancing_cursor_aborts():
    api,_,_=client([types_response(),day_response([accrual(1)],'a'),day_response([accrual(2)],'a')])
    with pytest.raises(ApiError):api.finances(SINCE,TO)


def test_old_wrapped_financial_format_is_rejected():
    api,_,_=client([types_response(),Response({'result':{'operations':[]}})])
    with pytest.raises(ApiError):api.finances(SINCE,TO)


def test_total_is_not_added_to_its_own_components():
    row=clean_finance(accrual(),DAY,{})
    assert row['amount']=='20' and row['id']=='accrual:1'
    assert row['payload']['items'][0]['commission']['sale_amount']['amount']=='50'


def test_nonitem_fee_uses_types_but_contract_id_is_not_an_order():
    row=clean_finance(accrual(category='NON_ITEM'),DAY,{'7':'Услуга'})
    assert row['payload']['operation_type_name']=='Услуга'
    assert row['payload']['posting']['posting_number']==''

@pytest.mark.parametrize('bad',['currency','id','day','amount','category'])
def test_invalid_accrual_rejected(bad):
    item=accrual()
    if bad=='currency':item['total_amount']['currency']='USD'
    if bad=='id':item.pop('accrual_id')
    if bad=='day':item['date']='2026-08-04'
    if bad=='amount':item['total_amount']['amount']='NaN'
    if bad=='category':item['accrued_category']='NEW_UNKNOWN'
    with pytest.raises(ApiError):clean_finance(item,DAY,{})


def test_rate_limit_retries_are_bounded():
    api,_,delays=client([Response(status=429,headers={'Retry-After':'9999'}),Response({})])
    assert api.post(ENDPOINTS['finance'],{})=={}
    assert delays==[30]

@pytest.mark.parametrize('status',[401,403,400,404,302])
def test_http_errors_include_safe_path_without_retries(status):
    api,session,_=client([Response(status=status)])
    with pytest.raises(ApiHttpError) as caught:api.post(ENDPOINTS['finance'],{})
    assert caught.value.status==status and caught.value.endpoint==ENDPOINTS['finance']
    assert TEST_KEY not in str(caught.value) and len(session.calls)==1


def test_network_exception_does_not_expose_key():
    api,_,delays=client([requests.ConnectionError(TEST_KEY)]*4)
    with pytest.raises(ApiError) as caught:api.post(ENDPOINTS['finance'],{})
    assert TEST_KEY not in str(caught.value) and len(delays)==3

@pytest.mark.parametrize('kind',['fbo','fbs'])
def test_postings_use_cursor_and_top_level_response(kind):
    api,session,_=client([posting_response([posting()],True,'next'),posting_response([posting('200-300-1')])])
    rows=api.postings(kind,SINCE,TO)
    assert len(rows)==2 and rows[0]['products'][0]['offer_id']=='demo'
    assert session.calls[1][1]['json']['cursor']=='next'
    for _,call in session.calls:
        assert 'offset' not in call['json'] and 'dir' not in call['json']
        assert call['json']['sort_dir']=='ASC' and 'status' not in call['json']['filter']
    assert 'PRIVATE' not in json.dumps(rows)


def test_posting_nonadvancing_cursor_aborts():
    api,_,_=client([posting_response([posting()],True,'a'),posting_response([posting('200-300-1')],True,'a')])
    with pytest.raises(ApiError):api.postings('fbo',SINCE,TO)


def test_posting_has_next_requires_cursor():
    api,_,_=client([posting_response([posting()],True)])
    with pytest.raises(ApiError):api.postings('fbs',SINCE,TO)


def test_diagnostics_checks_four_new_methods_independently():
    api,session,_=client([Response(status=404),types_response(),posting_response(),Response(status=403)])
    rows=api.diagnose(DAY)
    assert len(rows)==4 and '404' in rows[0]['Результат'] and 'HTTP 200' in rows[1]['Результат'] and '403' in rows[3]['Результат']
    assert [c[0] for c in session.calls]==['https://api-seller.ozon.ru'+v for v in ENDPOINTS.values()]
    assert TEST_KEY not in json.dumps(rows)


def test_atomic_snapshot_idempotency_and_corrections():
    db=connect(':memory:')
    for amount in ('20','18'):
        api,_,_=client([types_response(),day_response([accrual(amount=amount)]),posting_response(),posting_response([posting()])])
        stats=sync(db,api,DAY,DAY)
        assert stats['finance']==1
    assert db.execute('SELECT count(*) FROM api_finance').fetchone()[0]==1
    assert db.execute('SELECT amount FROM api_finance').fetchone()[0]=='18'
    assert db.execute('SELECT count(*) FROM api_postings').fetchone()[0]==1
    assert calculate(db,DAY,DAY)['count']==0
    db.close()


def test_failed_later_method_does_not_destroy_finance_snapshot():
    db=connect(':memory:')
    api,_,_=client([types_response(),day_response([accrual()]),posting_response(),posting_response()])
    sync(db,api,DAY,DAY)
    stamp=db.execute("SELECT value FROM settings WHERE key='api_last_success'").fetchone()[0]
    api,_,_=client([types_response(),day_response([accrual(amount='1')]),Response(status=403)])
    with pytest.raises(ApiError):sync(db,api,DAY,DAY)
    assert db.execute('SELECT amount FROM api_finance').fetchone()[0]=='20'
    assert db.execute("SELECT value FROM settings WHERE key='api_last_success'").fetchone()[0]==stamp
    db.close()


def test_finance_only_snapshot_deletes_disappeared_accrual():
    db=connect(':memory:')
    api,session,_=client([types_response(),day_response([accrual()])])
    sync(db,api,DAY,DAY,schemes=())
    assert len(session.calls)==2
    api,_,_=client([types_response(),day_response([])])
    sync(db,api,DAY,DAY,schemes=())
    assert db.execute('SELECT count(*) FROM api_finance').fetchone()[0]==0
    assert db.execute("SELECT value FROM settings WHERE key='api_components'").fetchone()[0]=='finance'
    db.close()


def test_wrong_shop_is_blocked_before_network():
    db=connect(':memory:')
    api,session,_=client([types_response(),day_response([])])
    sync(db,api,DAY,DAY,schemes=())
    api.client_id='456'
    with pytest.raises(ApiError,match='другим магазином'):sync(db,api,DAY,DAY)
    assert len(session.calls)==2
    db.close()


def test_provider_message_redacts_credentials_and_omits_other_fields():
    response=Response({'code':3,'message':f'invalid field limit {TEST_KEY} for 123','other':'PRIVATE'},status=400)
    api,_,_=client([response])
    with pytest.raises(ApiHttpError) as caught:api.post(ENDPOINTS['finance'],{})
    assert 'invalid field limit' in caught.value.provider_detail
    assert TEST_KEY not in str(caught.value) and 'PRIVATE' not in str(caught.value)
    assert '123' not in caught.value.provider_detail


def test_auth_related_error_text_is_hidden():
    api,_,_=client([Response({'message':'Api-Key confidential'},status=403)])
    with pytest.raises(ApiError) as caught:api.post(ENDPOINTS['finance'],{})
    assert 'confidential' not in str(caught.value)
def test_windows_are_contiguous_and_moscow_based():
    periods=list(windows(date(2026,8,1),date(2026,9,1)))
    assert len(periods)==2
    assert periods[0][2]=='2026-07-31T21:00:00Z'
    assert periods[0][3]=='2026-08-28T20:59:59.999999Z'
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



def test_item_and_container_totals_are_not_double_counted():
    item=accrual(category='ITEM')
    item['item_fees']={'fees':[{'sku':987,'fees':[{'type_id':7,'accrued':rub(-4)}]}]}
    result=clean_finance(item,DAY,{})
    assert result['amount']=='20'
    assert result['payload']['items'][0]['fees'][0]['accrued']['amount']=='-4'
    item=accrual(category='CONTAINER_FEES')
    item['container_fees']={'fees':[{'type_id':8,'accrued':rub(20)}]}
    result=clean_finance(item,DAY,{})
    assert result['amount']=='20' and result['payload']['container_fees'][0]['type_id']==8


def test_error_like_200_response_cannot_erase_snapshot():
    api,_,_=client([types_response(),Response({'code':7,'message':'denied'})])
    with pytest.raises(ApiError):api.finances(SINCE,TO)


def test_unknown_finance_envelope_not_treated_as_empty_day():
    api,_,_=client([types_response(),Response({'unexpected':[]})])
    with pytest.raises(ApiError):api.finances(SINCE,TO)
