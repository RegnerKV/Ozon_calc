"""Read-only Seller API transport and complete-period staging.

Migrated using October 2026 published OpenAPI mirrors (see API_MIGRATION.md).
Live account verification is still required.
No API response changes the validated accounting ledger in this version.
"""
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import time as clock
import requests
import re
from .core import money
from .credentials import validate

BASE='https://api-seller.ozon.ru'
ENDPOINTS={'finance':'/v1/finance/accrual/by-day','types':'/v1/finance/accrual/types','fbo':'/v3/posting/fbo/list','fbs':'/v4/posting/fbs/list'}


class ApiError(Exception):
    """Contains only local, non-secret messages. Never include request/response dumps."""


class ApiHttpError(ApiError):
    def __init__(self,endpoint,status,provider_detail=''):
        self.endpoint=endpoint
        self.status=int(status)
        message=f'POST {endpoint}: HTTP {self.status}. '
        if self.status==404:
            message+='Сервер вернул «не найдено». Нужно проверить ответ Ozon, адрес метода и доступ; причина по одному HTTP-коду не установлена.'
        elif self.status in (401,403):
            message+='Доступ отклонён. Проверьте ключ и разрешения; необходимость Premium не установлена.'
        else:
            message+='Запрос не выполнен. Требуется проверить параметры и актуальность метода.'
        self.provider_detail=provider_detail
        if provider_detail:message+=' Ответ Ozon: '+provider_detail
        super().__init__(message)


def iso(value):
    return value.astimezone(timezone.utc).isoformat(timespec='microseconds' if value.microsecond else 'seconds').replace('+00:00','Z')


def windows(start,end,max_days=28):
    if end<start:raise ApiError('Конечная дата раньше начальной')
    if (end-start).days>1095:raise ApiError('За один запуск выберите не более трёх лет')
    current=start
    while current<=end:
        last=min(end,current+timedelta(days=max_days-1))
        # UI dates are interpreted in Moscow (UTC+3), avoiding host timezone dependence.
        moscow=timezone(timedelta(hours=3))
        a=datetime.combine(current,time.min,moscow)
        b=datetime.combine(last,time.max,moscow)
        yield current,last,iso(a),iso(b)
        current=last+timedelta(days=1)


class SellerClient:
    def __init__(self,client_id,api_key,session=None,sleep=clock.sleep):
        self.client_id,self._api_key=validate(client_id,api_key)
        self.session=session or requests.Session()
        self.sleep=sleep
        self._accrual_types=None

    def __repr__(self):return '<SellerClient credentials=hidden>'

    def close(self):
        self.session.close()
        self._api_key=''

    def _error_detail(self,response):
        # Extract only structured error fields; never expose full bodies or headers.
        try:data=response.json()
        except (ValueError,TypeError):return ''
        if not isinstance(data,dict):return ''
        parts=[]
        code=data.get('code')
        if isinstance(code,int) and not isinstance(code,bool):parts.append(f'code={code}')
        elif isinstance(code,str) and re.fullmatch(r'[A-Za-z_][A-Za-z_0-9.-]{0,63}',code):parts.append('code='+code)
        message=data.get('message')
        if isinstance(message,str):parts.append(message)
        # Values of failed fields are not copied; provider text is redacted below.
        text='; '.join(parts)
        for secret in (self._api_key,self.client_id):
            if secret:text=text.replace(secret,'[скрыто]')
        if re.search(r'api[-_ ]?key|client[-_ ]?id|authorization|bearer',text,re.I):
            return 'Пояснение скрыто, поскольку упоминает данные авторизации.'
        return re.sub(r'\s+',' ',text).strip()[:600]

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
                raise ApiHttpError(endpoint,response.status_code,self._error_detail(response))
            if response.status_code==429 or 500<=response.status_code<600:
                if attempt<3:
                    try:delay=float(response.headers.get('Retry-After',2**attempt))
                    except (ValueError,TypeError):delay=2**attempt
                    self.sleep(min(30,max(0,delay)));continue
                raise ApiError('Ozon временно недоступен или ограничил частоту запросов. Повторите обновление позже.')
            if response.status_code!=200:
                raise ApiHttpError(endpoint,response.status_code,self._error_detail(response))
            try:
                data=response.json(parse_float=Decimal)
            except (ValueError,TypeError):raise ApiError('Ozon вернул ответ неизвестного формата') from None
            if not isinstance(data,dict) or 'code' in data or 'error' in data:
                raise ApiError('Изменился формат ответа Ozon. Загрузка остановлена без изменения данных.')
            return data
        raise ApiError('Не удалось выполнить запрос Ozon')

    def diagnose(self,day):
        first,last,since,to=next(windows(day,day))
        payloads={
            'finance':{'date':day.isoformat(),'last_id':''},
            'types':{},
            'fbo':posting_payload(since,to),
            'fbs':posting_payload(since,to),
        }
        statuses=[]
        for name,endpoint in ENDPOINTS.items():
            try:
                self.post(endpoint,payloads[name])
                status='HTTP 200: ответ получен (полная структура ещё не проверена)'
            except ApiError as exc:
                status=str(exc)
            statuses.append({'Раздел':name.upper(),'Метод':'POST '+endpoint,'Результат':status})
        return statuses

    def accrual_types(self):
        if self._accrual_types is None:
            raw=self.post(ENDPOINTS['types'],{})
            items=raw.get('accrual_types')
            if not isinstance(items,list):raise ApiError('Изменился формат справочника начислений Ozon')
            result={}
            for item in items:
                if not isinstance(item,dict) or item.get('id') is None or not isinstance(item.get('name'),str):
                    raise ApiError('Не удалось разобрать справочник начислений')
                identifier=str(item['id'])
                if identifier in result:raise ApiError('Повтор типа начисления в справочнике')
                result[identifier]=item['name']
            self._accrual_types=result
        return self._accrual_types

    def finances(self,since,to):
        moscow=timezone(timedelta(hours=3))
        try:
            first=datetime.fromisoformat(since.replace('Z','+00:00')).astimezone(moscow).date()
            last=datetime.fromisoformat(to.replace('Z','+00:00')).astimezone(moscow).date()
        except ValueError:raise ApiError('Некорректный период финансов') from None
        if first<date(2022,1,1) or last<first:raise ApiError('Начисления доступны с 01.01.2022; проверьте период')
        types=self.accrual_types()
        result=[];seen=set();day=first
        while day<=last:
            cursor='';cursors=set()
            for _ in range(10000):
                raw=self.post(ENDPOINTS['finance'],{'date':day.isoformat(),'last_id':cursor})
                # Empty repeated fields can be omitted by protobuf JSON serialization.
                items=raw.get('accruals',[]);next_cursor=raw.get('last_id','')
                if not isinstance(items,list) or not isinstance(next_cursor,str):raise ApiError('Изменился формат начислений за день')
                if 'result' in raw or (raw and not {'accruals','last_id'}.intersection(raw)):
                    raise ApiError('Ожидался новый формат начислений accruals/last_id')
                for item in items:
                    cleaned=clean_finance(item,day,types)
                    if cleaned['id'] in seen:raise ApiError('Ozon повторил начисление на нескольких страницах; обновление отменено')
                    seen.add(cleaned['id']);result.append(cleaned)
                if not next_cursor:break
                if next_cursor in cursors or not items:raise ApiError('Курсор финансов не продвигается. Обновление отменено.')
                cursors.add(next_cursor);cursor=next_cursor
            else:raise ApiError('Слишком много страниц начислений за день')
            day+=timedelta(days=1)
        return result

    def postings(self,kind,since,to):
        if kind not in ('fbo','fbs'):raise ApiError('Неизвестная схема работы')
        result=[];seen=set();cursor='';cursors=set()
        for _ in range(10000):
            raw=self.post(ENDPOINTS[kind],posting_payload(since,to,cursor,limit=100))
            items=raw.get('postings',[]);more=raw.get('has_next',False);next_cursor=raw.get('cursor','')
            if 'result' in raw or (raw and not {'postings','has_next','cursor'}.intersection(raw)) or not isinstance(items,list) or not isinstance(more,bool) or not isinstance(next_cursor,str):
                raise ApiError('Изменился формат отправлений Ozon')
            for item in items:
                clean=clean_posting(item,kind)
                if clean['posting_number'] in seen:raise ApiError('Ozon повторил отправление на нескольких страницах')
                seen.add(clean['posting_number']);result.append(clean)
            if not more:return result
            if not items or not next_cursor or next_cursor in cursors:raise ApiError('Курсор отправлений не продвигается')
            cursors.add(next_cursor);cursor=next_cursor
        raise ApiError('Слишком много страниц заказов; сузьте период')


def posting_payload(since,to,cursor='',limit=1):
    return {'cursor':cursor,'sort_dir':'ASC','filter':{'since':since,'to':to},'limit':limit,
            'with':{'analytics_data':False,'financial_data':False,'legal_info':False}}


def _rub_money(value):
    if not isinstance(value,dict) or value.get('amount') is None or value.get('currency')!='RUB':
        raise ValueError('Ожидалась сумма в RUB')
    return {'amount':str(money(value['amount'])),'currency':'RUB'}


def _fee(value):
    if not isinstance(value,dict):raise ValueError('Некорректная услуга')
    return {'type_id':value.get('type_id'),'accrued':_rub_money(value.get('accrued'))}


def clean_finance(item,expected_day,types):
    try:
        identifier=str(item['accrual_id'])
        if not identifier.isascii() or not identifier.isdigit():raise ValueError('Нет ID начисления')
        day=date.fromisoformat(item['date'])
        if day!=expected_day:raise ValueError('Дата начисления отличается от запрошенной')
        total=_rub_money(item['total_amount'])
        category=item['accrued_category']
        labels={'POSTING':'Начисление по отправлению','ITEM':'Начисление по товару','NON_ITEM':'Общее начисление магазина','CONTAINER_FEES':'Контейнерные услуги','UNSPECIFIED':'Неопределённое начисление'}
        if category not in labels:raise ValueError('Новая категория начисления')
        payload={'accrual_id':identifier,'operation_date':str(day),'operation_type':category,
                 'operation_type_name':labels[category],'total_amount':total,'items':[],
                 'posting':{'posting_number':str(item.get('unit_number') or '') if category=='POSTING' else ''}}
        posting=item.get('posting') or {}
        if category=='POSTING':
            payload['posting']['delivery_schema']=posting.get('delivery_schema')
            for product in posting.get('products',[]):
                clean={'sku':product.get('sku')}
                commission=product.get('commission')
                if commission:
                    # These are explanatory components; do NOT sum them with total_amount.
                    clean['commission']={k:_rub_money(commission[k]) for k in
                        ('bonus','coinvestment','commission','sale_amount','sale_commission','sale_price','seller_price') if commission.get(k) is not None}
                delivery=product.get('delivery')
                if delivery:
                    clean['delivery']={'services':[_fee(f) for f in delivery.get('services',[])]}
                    if delivery.get('total_accrued') is not None:clean['delivery']['total_accrued']=_rub_money(delivery['total_accrued'])
                payload['items'].append(clean)
        elif category=='NON_ITEM':
            fee=_fee(item.get('non_item_fee'));payload['non_item_fee']=fee
            payload['operation_type_name']=types.get(str(fee['type_id']),labels[category])
        elif category=='ITEM':
            source=item.get('item_fees') or {}
            for p in source.get('fees',[]):payload['items'].append({'sku':p.get('sku'),'fees':[_fee(f) for f in p.get('fees',[])]})
        elif category=='CONTAINER_FEES':
            source=item.get('container_fees') or {}
            payload['container_fees']=[_fee(f) for f in source.get('fees',[])]
        return {'id':'accrual:'+identifier,'day':str(day),'amount':total['amount'],'payload':payload}
    except (KeyError,TypeError,ValueError,AttributeError):
        raise ApiError('Не удалось разобрать начисление: проверьте новый формат, дату и валюту RUB. Данные не сохранены.') from None


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
            ('api_account',account),('api_last_success',now),('api_start',str(start)),('api_end',str(end)),('api_components',','.join(['finance']+list(schemes))),
        ])
    return {'finance':len(finances),'postings':len(postings),'last_success':now}
