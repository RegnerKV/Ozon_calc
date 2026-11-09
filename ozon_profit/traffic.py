"""Read-only advertising/traffic observations, never used to debit the ledger."""
from datetime import date, timedelta, datetime, timezone
from decimal import Decimal
import re
import pandas as pd
from .seller_api import ApiError
from .core import money

PROMO='/api/client/statistics/all_sku_promo/products/generate/json'


def init(db):
    db.execute('CREATE TABLE IF NOT EXISTS traffic_daily(day TEXT,sku TEXT,views INTEGER,orders INTEGER,PRIMARY KEY(day,sku))')
    db.execute('CREATE TABLE IF NOT EXISTS promo_daily(day TEXT PRIMARY KEY,status TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS click_daily(day TEXT PRIMARY KEY,amount TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS traffic_updates(source TEXT PRIMARY KEY,stamp TEXT)')
    db.commit()


def day(value):
    text=str(value).strip()
    for fmt in ('%Y-%m-%d','%d.%m.%Y'):
        try:return datetime.strptime(text,fmt).date().isoformat()
        except ValueError:pass
    raise ApiError('Не распознана дата в статистике. Ожидается ГГГГ-ММ-ДД или ДД.ММ.ГГГГ.')


def count(value):
    try:
        n=money(value)
        if n<0 or n!=n.to_integral_value():raise ValueError
        return int(n)
    except (ValueError,TypeError):raise ApiError('Количество показов или заказов должно быть целым неотрицательным числом.') from None


def rows(data):
    for _ in range(4):
        if isinstance(data,list):
            if not all(isinstance(r,dict) for r in data):break
            return data
        if not isinstance(data,dict):break
        key=next((k for k in ('rows','list','data','report') if k in data),None)
        if key is None:break
        data=data[key]
    raise ApiError('Не распознан формат статистики Ozon. Сохранённые данные не заменены.')


def field(row,*names):
    for name in names:
        if name in row:return row[name]
    raise ApiError('В отчёте не найдена колонка «'+names[-1]+'». Сохранённые данные не заменены.')


def parse_promo(data,start,end):
    result={}
    for r in rows(data):
        d=day(field(r,'date','day','Дата'))
        if not str(start)<=d<=str(end):raise ApiError('Дата продвижения вне запрошенного периода.')
        raw=field(r,'status','promotionStatus','Статус продвижения')
        text=str(raw).strip().lower()
        if text in ('true','1','on','enabled','включено'):status='on'
        elif text in ('false','0','off','disabled','выключено'):status='off'
        elif text in ('mixed','смешанный'):status='mixed'
        else:raise ApiError('Не распознан статус продвижения. Неизвестный статус не считается выключенным.')
        if d in result and result[d]!=status:status='mixed'
        result[d]=status
    return [(d,s) for d,s in result.items()]


def replace(db,source,start,end,data):
    init(db)
    tables={'traffic':'traffic_daily','promo':'promo_daily','click':'click_daily'}
    table=tables[source]
    with db:
        db.execute(f'DELETE FROM {table} WHERE day BETWEEN ? AND ?',(str(start),str(end)))
        n={'traffic':4,'promo':2,'click':2}[source]
        db.executemany(f'INSERT INTO {table} VALUES('+','.join('?'*n)+')',data)
        db.execute('INSERT OR REPLACE INTO traffic_updates VALUES(?,?)',(source,datetime.now(timezone.utc).isoformat()))


def fetch_traffic(client,start,end):
    result=[];offset=0
    # Ozon limits this method to one request per minute (including pagination).
    while True:
        data=client.post('/v1/analytics/data',{'date_from':str(start),'date_to':str(end),
            'metrics':['hits_view','ordered_units'],'dimension':['sku','day'],
            'filters':[],'sort':[],'limit':1000,'offset':offset})
        payload=data.get('result',{})
        batch=payload.get('data') if isinstance(payload,dict) else None
        if not isinstance(batch,list):raise ApiError('Не распознан ответ аналитики Seller API.')
        for r in batch:
            dims=r.get('dimensions',[]);metrics=r.get('metrics',[])
            if len(dims)!=2 or len(metrics)!=2:raise ApiError('Изменился формат показов Seller API.')
            sku=str(dims[0].get('id',''));d=day(dims[1].get('id',''))
            if not sku or not str(start)<=d<=str(end):raise ApiError('Некорректный SKU или дата аналитики.')
            result.append((d,sku,count(metrics[0]),count(metrics[1])))
        if len(batch)<1000:break
        offset+=1000
        if offset>=50000:raise ApiError('Слишком большой отчёт. Выберите более короткий период.')
        client.sleep(60)
    if len({(r[0],r[1]) for r in result})!=len(result):raise ApiError('Повторяющиеся строки в аналитике Seller API.')
    return result


def auth_get(client,path,params=None):
    client.authorize()
    return client.request('GET',path,params=params or {},headers={'Authorization':'Bearer '+client.token})


def fetch_promo(client,start,end,pending=None):
    """Persist UUID between clicks; no long polling and no arbitrary download URLs."""
    if not pending:
        data=auth_get(client,PROMO,{'timeBounds.from':str(start)+'T00:00:00+03:00',
            'timeBounds.to':str(end)+'T23:59:59+03:00'})
        pending=data.get('UUID') if isinstance(data,dict) else None
    if not isinstance(pending,str) or not re.fullmatch(r'[a-zA-Z0-9-]{1,100}',pending):raise ApiError('Performance API не выдал корректный номер отчёта.')
    status=auth_get(client,'/api/client/statistics/'+pending)
    if not isinstance(status,dict):raise ApiError('Не распознан статус готовности отчёта Ozon.')
    if status.get('state') in ('NOT_STARTED','IN_PROGRESS'):return pending,None
    if status.get('state')!='OK':raise ApiError('Ozon не сформировал отчёт продвижения. Повторите запрос позже.')
    data=auth_get(client,'/api/client/statistics/report',{'UUID':pending})
    return None,parse_promo(data,start,end)


def fetch_clicks(client,start,end):
    ids=set();page=1
    while True:
        data=auth_get(client,'/api/client/campaign',{'page':page,'pageSize':100})
        batch=rows(data)
        for r in batch:
            if r.get('paymentType')=='CPC':ids.add(str(r['id']))
        if len(batch)<100:break
        page+=1
        if page>100:raise ApiError('Слишком много рекламных кампаний.')
    # No current CPC campaigns does not establish historical absence of expenses.
    if not ids:raise ApiError('Не найдены кампании с типом оплаты CPC. Исторические расходы не заменены нулями.')
    totals={};d=start
    while d<=end:totals[str(d)]=Decimal(0);d+=timedelta(days=1)
    campaigns=sorted(ids)
    for i in range(0,len(campaigns),10):
        data=auth_get(client,'/api/client/statistics/daily/json',{'dateFrom':str(start),'dateTo':str(end),'campaignIds':campaigns[i:i+10]})
        for r in rows(data):
            campaign=str(field(r,'campaignId','id','Идентификатор кампании'))
            if campaign not in ids:raise ApiError('Отчёт содержит кампанию другого типа. Расходы не заменены.')
            d=day(field(r,'date','Дата'))
            if d not in totals:raise ApiError('Дата кликовой рекламы вне запрошенного периода.')
            try:amount=money(field(r,'expense','moneySpent','spent','Расход, ₽'))
            except ValueError:raise ApiError('Не распознан расход в отчёте кликовой рекламы.') from None
            if amount<0:raise ApiError('Отрицательный расход требует отдельной проверки отчёта.')
            totals[d]+=amount
    return [(d,str(v)) for d,v in totals.items()]


def import_views(frame):
    aliases={'Дата':'day','date':'day','SKU':'sku','Показы (всего)':'views','hits_view':'views','Заказано товаров':'orders','ordered_units':'orders'}
    frame=frame.rename(columns={c:aliases.get(str(c).strip(),str(c).strip()) for c in frame.columns})
    if not {'day','sku','views'}<=set(frame.columns):raise ApiError('Нужны колонки: Дата, SKU, Показы (всего). Не сводный итог, а отдельная строка на товар и день.')
    result=[]
    for r in frame.to_dict('records'):
        raw=r['day'];d=raw.date().isoformat() if isinstance(raw,datetime) else day(raw)
        sku=str(r['sku']).strip()
        if sku.endswith('.0'):sku=sku[:-2]
        if not sku or sku.lower()=='nan':raise ApiError('Заполните SKU во всех строках.')
        orders=None if 'orders' not in r or pd.isna(r['orders']) else count(r['orders'])
        result.append((d,sku,count(r['views']),orders))
    if not result:raise ApiError('В файле нет строк.')
    if len({(r[0],r[1]) for r in result})!=len(result):raise ApiError('В файле повторяются пары дата + SKU.')
    return result


def daily(db,start,end,sku=None):
    init(db);traffic={}
    query='SELECT * FROM traffic_daily WHERE day BETWEEN ? AND ?';params=[str(start),str(end)]
    if sku:query+=' AND sku=?';params.append(sku)
    for r in db.execute(query,params):
        t=traffic.setdefault(r['day'],{'views':0,'orders':0})
        t['views']+=r['views']
        t['orders']=None if r['orders'] is None or t['orders'] is None else t['orders']+r['orders']
    promo=dict(db.execute('SELECT day,status FROM promo_daily WHERE day BETWEEN ? AND ?',(str(start),str(end))))
    clicks=dict(db.execute('SELECT day,amount FROM click_daily WHERE day BETWEEN ? AND ?',(str(start),str(end))))
    result=[];d=start
    while d<=end:
        k=str(d);t=traffic.get(k,{})
        # CPC report is campaign-level. Never allocate it to a SKU for this analysis.
        click=float(clicks[k]) if k in clicks and not sku else None
        result.append({'day':k,'views':t.get('views'),'orders':t.get('orders'),
            'promo':promo.get(k,'unknown'),'click':click})
        d+=timedelta(days=1)
    return pd.DataFrame(result)


def comparison(frame):
    labels={'on':'Включена оплата за заказ','off':'Выключена оплата за заказ'}
    out=[]
    for state in ('off','on'):
        for paid in (False,True):
            part=frame[(frame.promo==state)&frame.views.notna()&frame.click.notna()&((frame.click>0)==paid)]
            out.append({'Оплата за заказ':labels[state],'Оплата за клики':'Были расходы' if paid else 'Без расходов',
                'Дней':len(part),'Показы / день':part.views.mean(),
                'Заказано шт. / день':part.orders.mean(),'Клики, ₽ / день':part.click.mean()})
    return pd.DataFrame(out)
