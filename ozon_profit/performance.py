"""Read-only Performance expense statistics. Never posts these again to the ledger."""
from datetime import date,datetime,timedelta,timezone
import json
import os
import re
import time
import requests
from .core import money,get_setting,set_setting
from .seller_api import ApiError

BASE='https://api-performance.ozon.ru'


def init(db):
    db.execute('CREATE TABLE IF NOT EXISTS performance_expenses(day TEXT,campaign TEXT,sku TEXT,amount TEXT,PRIMARY KEY(day,campaign,sku))')
    db.commit()


def validate(client,secret):
    pair=tuple(str(v).strip() for v in (client,secret))
    if any(not v or len(v)>4096 or any(c.isspace() for c in v) for v in pair):raise ValueError('Укажите Client ID и Client Secret Performance API без пробелов.')
    return pair


def save_credentials(db,client,secret,password=''):
    from .credentials import _transform
    from .secure_store import seal,StorageError
    pair=validate(client,secret);data=json.dumps(pair).encode()
    if os.name=='nt':data=b'DPAPI:'+_transform(data)
    elif password:data=b'WEB:'+seal(data,password,'performance-key')
    else:raise StorageError('Защищённое сохранение доступно в Windows или с паролем веб-приложения.')
    with db:
        db.execute('INSERT OR REPLACE INTO secure_items VALUES(?,?)',('performance',data))
        db.execute("DELETE FROM settings WHERE key IN ('performance_start','performance_end','performance_success','performance_error')")


def load_credentials(db,password=''):
    from .credentials import _transform
    from .secure_store import unseal,StorageError
    row=db.execute('SELECT value FROM secure_items WHERE name=?',('performance',)).fetchone()
    if not row:return None
    raw=bytes(row[0])
    if raw.startswith(b'DPAPI:'):raw=_transform(raw[6:],decrypt=True)
    elif raw.startswith(b'WEB:'):raw=unseal(raw[4:],password,'performance-key')
    else:raise StorageError('Не удалось прочитать подключение Performance API.')
    return validate(*json.loads(raw))


class Client:
    def __init__(self,client,secret,session=None,sleep=time.sleep):
        self.pair=validate(client,secret);self.session=session or requests.Session();self.sleep=sleep;self.token='';self.expires=0
    def __repr__(self):return '<PerformanceClient credentials=hidden>'
    def close(self):self.session.close();self.token='';self.pair=('', '')
    def request(self,method,path,**kwargs):
        allowed={'/api/client/token':'POST','/api/client/statistics/expense/json':'GET',
            '/api/client/campaign':'GET','/api/client/statistics/json':'POST',
            '/api/client/statistics/report':'GET'}
        expected=allowed.get(path)
        if expected is None and re.fullmatch(r'/api/client/statistics/[0-9a-fA-F-]{36}',path):expected='GET'
        if expected!=method:raise ApiError('Недопустимый метод Performance API')
        for attempt in range(4):
            try:r=self.session.request(method,BASE+path,timeout=(10,45),allow_redirects=False,**kwargs)
            except requests.RequestException:
                raise ApiError('Не удалось соединиться с api-performance.ozon.ru. Проверьте интернет и VPN. Сохранённая статистика рекламы не изменена.') from None
            if r.status_code==429 or 500<=r.status_code<600:
                if attempt<3 and path!='/api/client/statistics/json':
                    try:delay=max(2**attempt,min(300,int(getattr(r,'headers',{}).get('Retry-After','0'))))
                    except (ValueError,TypeError):delay=2**attempt
                    self.sleep(delay);continue
            if r.status_code!=200:raise ApiError(f'Performance API: HTTP {r.status_code}. Проверьте реквизиты, доступ и повторите позже.')
            try:return r.json()
            except ValueError:raise ApiError('Performance API вернул неизвестный формат отчёта. Статистика не заменена.') from None
    def authorize(self):
        if self.token and time.monotonic()<self.expires:return
        r=self.request('POST','/api/client/token',json={'client_id':self.pair[0],'client_secret':self.pair[1],'grant_type':'client_credentials'})
        if not isinstance(r,dict) or not isinstance(r.get('access_token'),str) or not r['access_token']:raise ApiError('Performance API не выдал токен.')
        try:lifetime=int(r.get('expires_in',0))
        except (ValueError,TypeError):raise ApiError('Performance API вернул неизвестный срок токена.') from None
        self.token=r['access_token'];self.expires=time.monotonic()+max(0,min(lifetime,3600)-60)
    def expenses(self,start,end):
        self.authorize()
        data=self.request('GET','/api/client/statistics/expense/json',params={'dateFrom':str(start),'dateTo':str(end)},headers={'Authorization':'Bearer '+self.token})
        return parse_expenses(data,start,end)


def parse_expenses(data,start,end):
    # JSON report schemas are not detailed in the published OpenAPI. Fail closed
    # on unknown containers/columns; never interpret an unknown response as zero.
    if isinstance(data,dict):
        for name in ('rows','list','data','report'):
            if name in data:
                data=data[name]
                if isinstance(data,dict) and 'rows' in data:data=data['rows']
                break
    if not isinstance(data,list):raise ApiError('Не распознан JSON-отчёт расходов Performance API. Предыдущая статистика сохранена.')
    totals={}
    for row in data:
        if not isinstance(row,dict):raise ApiError('Некорректная строка расходов Performance API.')
        day=row.get('date');campaign=row.get('campaignId',row.get('id'))
        amount=row.get('moneySpent',row.get('expense',row.get('spent')))
        if day is None or campaign is None or amount is None:raise ApiError('В отчёте Performance API не найдены дата, кампания или расход. Нужна проверка формата.')
        try:
            day=date.fromisoformat(str(day)).isoformat();value=money(amount)
        except ValueError:raise ApiError('Некорректная дата или сумма в Performance API.') from None
        if not str(start)<=day<=str(end):raise ApiError('Performance API вернул дату вне запрошенного периода.')
        key=(day,str(campaign),str(row.get('sku') or ''))
        totals[key]=totals.get(key,money(0))+value
    return [(*key,str(value)) for key,value in totals.items()]


def sync(db,start,end,password='',client_factory=Client):
    init(db);pair=load_credentials(db,password)
    if not pair:return False
    start=date.fromisoformat(str(start));end=date.fromisoformat(str(end))
    if end<start or (end-start).days>1095:raise ApiError('Выберите корректный период рекламы не длиннее трёх лет.')
    client=client_factory(*pair);rows=[];day=start
    try:
        while day<=end:
            last=min(day+timedelta(days=30),end)
            rows.extend(client.expenses(day,last));day=last+timedelta(days=1)
    finally:client.close()
    with db:
        db.execute('DELETE FROM performance_expenses WHERE day BETWEEN ? AND ?',(str(start),str(end)))
        db.executemany('INSERT INTO performance_expenses VALUES(?,?,?,?)',rows)
        db.executemany('INSERT OR REPLACE INTO settings VALUES(?,?)',[
            ('performance_start',str(start)),('performance_end',str(end)),('performance_success',datetime.now(timezone.utc).isoformat()),('performance_error','')])
    return True


def report(db,start,end,revenue):
    init(db)
    a=get_setting(db,'performance_start');b=get_setting(db,'performance_end')
    if not a or not a<=str(start)<=str(end)<=b:return None
    total=money(0);unassigned=money(0);products={}
    links={r[0]:r[1] for r in db.execute('SELECT ozon_sku,sku FROM product_links')}
    for row in db.execute('SELECT sku,amount FROM performance_expenses WHERE day BETWEEN ? AND ?',(str(start),str(end))):
        value=money(row['amount']);total+=value;sku=links.get(row['sku'],'')
        if sku:products[sku]=products.get(sku,money(0))+value
        else:unassigned+=value
    return dict(total=total,store=total/revenue*100 if revenue>0 else None,unassigned=unassigned,products=products)


def connection_ui(db):
    import streamlit as st
    from .runtime import secret
    from .secure_store import StorageError
    from .credentials import CredentialError
    st.subheader('Реклама — Ozon Performance API')
    try:connected=bool(load_credentials(db,secret('app_password')))
    except (StorageError,CredentialError):connected=False
    st.caption('Реквизиты сохранены' if connected else 'Отдельное подключение для расходов на продвижение')
    with st.expander('Настроить рекламный API',expanded=not connected):
        st.markdown('[Открыть Ozon → Настройки → API-ключи → Performance API](https://seller.ozon.ru/app/settings/api-keys?currentTab=performanceApi). Создайте сервисный аккаунт, откройте его и добавьте ключ. Скопируйте Client ID и Client Secret сюда.')
        def remember():
            from .core import connect
            target=connect()
            try:
                save_credentials(target,st.session_state.get('perf_client',''),st.session_state.get('perf_secret',''),secret('app_password'))
                st.session_state['perf_client']='';st.session_state['perf_secret']=''
                st.session_state['_performance_note']='Реквизиты сохранены зашифрованно. Нажмите «Проверить рекламный API», затем «Обновить» в обзоре.'
            except (ValueError,StorageError,CredentialError) as exc:st.session_state['_performance_note']=str(exc)
            finally:target.close()
        with st.form('performance_connection'):
            st.text_input('Client ID Performance API',key='perf_client')
            st.text_input('Client Secret Performance API',type='password',key='perf_secret')
            st.form_submit_button('Сохранить рекламный API',on_click=remember)
        if st.session_state.get('_performance_note'):st.info(st.session_state['_performance_note'])
    if connected and st.button('Проверить рекламный API'):
        client=None
        try:
            client=Client(*load_credentials(db,secret('app_password')));client.authorize()
            st.success('Performance API выдал токен. Для загрузки расходов нажмите «Обновить» в обзоре.')
        except ApiError as exc:st.error(str(exc))
        finally:
            if client:client.close()
