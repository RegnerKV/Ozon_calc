"""Read-only Seller API transport and complete-period staging.

Endpoint contracts require live verification against current official docs.
No API response changes the validated accounting ledger in this version.
"""
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import time as clock
import requests
from .core import money
from .credentials import validate

BASE='https://api-seller.ozon.ru'
ENDPOINTS={'finance':'/v3/finance/transaction/list','fbo':'/v2/posting/fbo/list','fbs':'/v3/posting/fbs/list'}


class ApiError(Exception):
    """Contains only local, non-secret messages. Never include request/response dumps."""


def iso(value):
    return value.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')


def windows(start,end,max_days=28):
    if end<start:raise ApiError('Конечная дата раньше начальной')
    if (end-start).days>1095:raise ApiError('За один запуск выберите не более трёх лет')
    current=start
    while current<=end:
        last=min(end,current+timedelta(days=max_days-1))
        # UI dates are interpreted in Moscow (UTC+3), avoiding host timezone dependence.
        moscow=timezone(timedelta(hours=3))
        a=datetime.combine(current,time.min,moscow)
        b=datetime.combine(last,time.max,moscow).replace(microsecond=0)
        yield current,last,iso(a),iso(b)
        current=last+timedelta(days=1)


class SellerClient:
    def __init__(self,client_id,api_key,session=None,sleep=clock.sleep):
        self.client_id,self._api_key=validate(client_id,api_key)
        self.session=session or requests.Session()
        self.sleep=sleep

    def __repr__(self):return '<SellerClient credentials=hidden>'

    def close(self):
        self.session.close()
        self._api_key=''

    def post(self,endpoint,payload):
        if endpoint not in ENDPOINTS.values():raise ApiError('Недопустимый метод API')
        for attempt in range(4):
            try:
                response=self.session.post(BASE+endpoint,json=payload,
                    headers={'Client-Id':self.client_id,'Api-Key':self._api_key,'Content-Type':'application/json'},
                    timeout=(10,45),allow_redirects=False)
            except requests.RequestException:
                if attempt<3:self.sleep(2**attempt);continue
                raise ApiError('Нет соединения с Ozon. Проверьте интернет и доступ к api-seller.ozon.ru. Сохранённые данные не изменены.') from None
            if response.status_code in (401,403):
                raise ApiError('Ozon отказал в доступе (401/403). Проверьте Client ID, ключ, разрешения чтения и сетевые ограничения. Это само по себе не означает, что нужен Premium.')
            if response.status_code==429 or 500<=response.status_code<600:
                if attempt<3:
                    try:delay=float(response.headers.get('Retry-After',2**attempt))
                    except (ValueError,TypeError):delay=2**attempt
                    self.sleep(min(30,max(0,delay)));continue
                raise ApiError('Ozon временно недоступен или ограничил частоту запросов. Повторите обновление позже.')
            if response.status_code!=200:
                raise ApiError(f'Запрос Ozon не выполнен (HTTP {int(response.status_code)}). Проверьте актуальность метода и права ключа.')
            try:
                data=response.json(parse_float=Decimal)
            except (ValueError,TypeError):raise ApiError('Ozon вернул ответ неизвестного формата') from None
            if not isinstance(data,dict) or 'result' not in data:
                raise ApiError('Изменился формат ответа Ozon. Загрузка остановлена без изменения данных.')
            return data['result']
        raise ApiError('Не удалось выполнить запрос Ozon')

    def finances(self,since,to):
        result=[];seen=set();expected=None
        for page in range(1,10001):
            raw=self.post(ENDPOINTS['finance'],{'filter':{'date':{'from':since,'to':to},'operation_type':[],'posting_number':'','transaction_type':'all'},'page':page,'page_size':1000})
            if not isinstance(raw,dict) or not isinstance(raw.get('operations'),list):raise ApiError('Изменился формат финансовых операций')
            count=raw.get('page_count');total=raw.get('row_count')
            if not isinstance(count,int) or isinstance(count,bool) or count<0 or not isinstance(total,int) or isinstance(total,bool) or total<0:
                raise ApiError('В ответе финансов нет корректного количества страниц/строк')
            if expected is None:expected=(count,total)
            elif expected!=(count,total):raise ApiError('Финансовые данные менялись во время загрузки. Повторите обновление.')
            for item in raw['operations']:
                cleaned=clean_finance(item)
                if cleaned['id'] in seen:raise ApiError('Ozon повторил операцию на нескольких страницах; обновление отменено')
                seen.add(cleaned['id']);result.append(cleaned)
            if page>=count:
                if len(result)!=total:raise ApiError('Получены не все финансовые операции. Данные не сохранены.')
                return result
            if not raw['operations']:raise ApiError('Пустая промежуточная страница финансов')
        raise ApiError('Слишком много страниц финансов')

    def postings(self,kind,since,to):
        if kind not in ('fbo','fbs'):raise ApiError('Неизвестная схема работы')
        result=[];seen=set();limit=1000;offset=0
        for _ in range(10000):
            payload={'dir':'ASC','filter':{'since':since,'to':to,'status':''},'limit':limit,'offset':offset,
                     'with':{'analytics_data':False,'financial_data':False}}
            raw=self.post(ENDPOINTS[kind],payload)
            if kind=='fbo':
                if not isinstance(raw,list):raise ApiError('Изменился формат заказов FBO')
                items=raw;more=len(items)==limit
            else:
                if not isinstance(raw,dict) or not isinstance(raw.get('postings'),list) or not isinstance(raw.get('has_next'),bool):
                    raise ApiError('Изменился формат заказов FBS')
                items=raw['postings'];more=raw['has_next']
            for item in items:
                clean=clean_posting(item,kind)
                if clean['posting_number'] in seen:raise ApiError('Ozon повторил отправление на нескольких страницах')
                seen.add(clean['posting_number']);result.append(clean)
            if not more:return result
            if not items:raise ApiError('Пустая промежуточная страница заказов')
            offset+=len(items)
        raise ApiError('Слишком много страниц заказов; сузьте период')


def clean_finance(item):
    try:
        identifier=str(item['operation_id'])
        if not identifier or identifier=='None':raise ValueError()
        day=datetime.fromisoformat(str(item['operation_date']).replace('Z','+00:00')).date().isoformat()
        amount=money(item['amount'])
        if item['amount'] is None:raise ValueError()
        # Whitelist fields: no customer names, addresses, headers or API credentials.
        payload={k:item.get(k) for k in ['operation_id','operation_type','operation_type_name','operation_date','type','accruals_for_sale','sale_commission','amount']}
        posting=item.get('posting') or {}
        payload['posting']={k:posting.get(k) for k in ['posting_number','delivery_schema','order_date']}
        payload['items']=[{k:p.get(k) for k in ['sku','name']} for p in item.get('items',[])]
        payload['services']=[{k:s.get(k) for k in ['name','price']} for s in item.get('services',[])]
        return {'id':identifier,'day':day,'amount':str(amount),'payload':payload}
    except (KeyError,TypeError,ValueError,AttributeError):
        raise ApiError('Не удалось разобрать финансовую операцию. Данные не сохранены.') from None


def clean_posting(item,kind):
    try:
        number=str(item['posting_number']);status=str(item['status'])
        if not number or not status or not isinstance(item['products'],list):raise ValueError()
        products=[]
        for p in item['products']:
            quantity=p['quantity']
            if not isinstance(quantity,int) or isinstance(quantity,bool) or quantity<=0:raise ValueError()
            products.append({k:p.get(k) for k in ['sku','offer_id','name','quantity','price']})
        return dict(posting_number=number,status=status,schema=kind,products=products,
                    created_at=item.get('in_process_at') or item.get('created_at'))
    except (KeyError,TypeError,ValueError):raise ApiError('Не удалось разобрать отправление Ozon') from None


def init_tables(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS api_finance(id TEXT PRIMARY KEY,day TEXT NOT NULL,amount TEXT NOT NULL,payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS api_postings(posting_number TEXT PRIMARY KEY,schema TEXT NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL);
    ''')


def sync(db,client,start,end,schemes=('fbo','fbs')):
    """Download all responses before touching persisted snapshots. Single-shop DB.

    Staged API data intentionally doesn't share the Excel accounting ledger.
    """
    init_tables(db)
    account=hashlib.sha256(client.client_id.encode()).hexdigest()
    existing=db.execute("SELECT value FROM settings WHERE key='api_account'").fetchone()
    if existing and existing[0]!=account:raise ApiError('Эта база связана с другим магазином. Для другого Client ID нужна отдельная база.')
    finances={};postings={}
    for first,last,since,to in windows(start,end):
        for item in client.finances(since,to):
            if item['id'] in finances:raise ApiError('Повтор ID между периодами обновления')
            if not start.isoformat()<=item['day']<=end.isoformat():raise ApiError('Ozon вернул финансовую операцию вне выбранного периода')
            finances[item['id']]=item
        for scheme in schemes:
            for posting in client.postings(scheme,since,to):
                key=posting['posting_number']
                if key in postings and postings[key]!=posting:raise ApiError('Статус отправления изменился во время загрузки. Повторите обновление.')
                postings[key]=posting
    now=datetime.now(timezone.utc).isoformat(timespec='seconds')
    with db:
        db.execute('BEGIN IMMEDIATE')
        bound=db.execute("SELECT value FROM settings WHERE key='api_account'").fetchone()
        if bound and bound[0]!=account:
            raise ApiError('Магазин был изменён во время загрузки. Данные не сохранены.')
        db.execute('DELETE FROM api_finance WHERE day BETWEEN ? AND ?',(str(start),str(end)))
        db.executemany('INSERT INTO api_finance VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET day=excluded.day,amount=excluded.amount,payload=excluded.payload',
            [(i['id'],i['day'],i['amount'],json.dumps(i['payload'],ensure_ascii=False,default=str)) for i in finances.values()])
        db.executemany('INSERT INTO api_postings VALUES(?,?,?,?) ON CONFLICT(posting_number) DO UPDATE SET schema=excluded.schema,status=excluded.status,payload=excluded.payload',
            [(p['posting_number'],p['schema'],p['status'],json.dumps(p,ensure_ascii=False,default=str)) for p in postings.values()])
        db.executemany('INSERT OR REPLACE INTO settings VALUES(?,?)',[
            ('api_account',account),('api_last_success',now),('api_start',str(start)),('api_end',str(end)),
        ])
    return {'finance':len(finances),'postings':len(postings),'last_success':now}
