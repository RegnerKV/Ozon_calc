"""Owner-facing screens: overview, products, planning and settings."""
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal
import json
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from .core import connect,calculate,money,rub,get_setting,set_setting,save_cost,withdrawal,KINDS
from .presentation import cash,details_frame
from .api_ui import active_credentials,run_update
from .runtime import secret,save_api_credentials
from .secure_store import StorageError
from .seller_api import ApiError,init_tables

MOSCOW=timezone(timedelta(hours=3))


def today():return datetime.now(MOSCOW).date()


def latest_period(db):
    from .dashboard import latest_period as choose_period
    return choose_period(db,today())


def ensure_profit(db):
    if not get_setting(db,'api_last_success'):return
    a=get_setting(db,'api_start');b=get_setting(db,'api_end')
    if not a or not b:return
    if get_setting(db,'api_profit_last_success')==get_setting(db,'api_last_success') and get_setting(db,'api_profit_enabled')=='1' and get_setting(db,'ads_classification_version')=='english-ads-v1' and not calculate(db,a,b)['stale_api']:return
    from .api_ledger import promote
    try:promote(db,a,b,tax_base=get_setting(db,'tax_base','sales'),enable=True)
    except ValueError as exc:
        message=str(exc)
        if get_setting(db,'api_profit_error')!=message:set_setting(db,'api_profit_error',message)


def refresh(db):
    schemes=tuple(json.loads(get_setting(db,'api_schemes','["fbo","fbs"]')))
    first=today()-timedelta(days=89)
    last_known=get_setting(db,'api_start')
    # Preserve the initial reconciliation period while first preparing a store.
    if not get_setting(db,'api_profit_enabled') and last_known:first=min(first,date.fromisoformat(last_known))
    result=run_update(db,first,today(),schemes)
    if get_setting(db,'catalog_date')!=str(today()):
        from .seller_api import SellerClient
        from .catalog import apply_library
        pair=active_credentials();client=SellerClient(*pair)
        try:
            catalog=client.full_catalog()
            with db:
                for p in catalog:
                    db.execute('INSERT INTO products VALUES(?,?) ON CONFLICT(sku) DO NOTHING',(p['offer_id'],p['name']))
                    for sku in p['skus']:db.execute('INSERT OR REPLACE INTO product_links VALUES(?,?)',(sku,p['offer_id']))
                db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('catalog_date',str(today())))
            apply_library(db)
        except ApiError:
            result['catalog_warning']='Не все товары каталога обновлены; загруженные продажи сохранены.'
        finally:client.close()
    ensure_profit(db)
    from .performance import sync as sync_performance
    from .credentials import CredentialError
    try:sync_performance(db,first,today(),password=secret('app_password'))
    except (ApiError,StorageError,CredentialError) as exc:set_setting(db,'performance_error',str(exc))
    return result


def _profit(result):
    if result['stale_api']:return 'Нужен пересчёт'
    if result['missing'] or result['net_mode']:return 'Нужна себестоимость'
    if not result['count']:return 'Нет данных'
    return cash(result['profit'])


def overview(db):
    notice=None
    head,action=st.columns([5,1])
    with head:
        st.markdown('## Ваша прибыль — на одном экране')
        stamp=get_setting(db,'api_profit_last_success') or get_setting(db,'api_last_success')
        if stamp:
            try:stamp=datetime.fromisoformat(stamp.replace('Z','+00:00')).astimezone(MOSCOW).strftime('%d.%m.%Y в %H:%M')
            except ValueError:pass
        st.caption('Последнее обновление: '+(stamp or 'ещё не было'))
    with action:
        if st.button('↻ Обновить',type='primary',width='stretch',key='refresh_main'):
            if not active_credentials():notice=('info','Один раз подключите магазин в настройках.')
            else:
                try:
                    with st.spinner('Обновляем данные и прибыль…'):refresh(db)
                    notice=('success','Данные обновлены')
                except ApiError as exc:notice=('error',str(exc))
                except Exception:notice=('error','Обновление не завершено. Предыдущие данные сохранены.')
    if notice:getattr(st,notice[0])(notice[1])
    choice=st.selectbox('Период',['Текущий месяц','Прошлый месяц','За 7 дней','За последние 30 дней','Свои даты'],key='overview_period')
    if choice=='Свои даты':
        a,b=latest_period(db);c1,c2=st.columns(2)
        start=c1.date_input('С',a,key='period_start',format='DD.MM.YYYY');end=c2.date_input('По',b,key='period_end',format='DD.MM.YYYY')
    elif choice=='Текущий месяц':start,end=today().replace(day=1),today()
    elif choice=='Прошлый месяц':
        end=today().replace(day=1)-timedelta(days=1);start=end.replace(day=1)
    elif choice=='За последние 30 дней':start,end=today()-timedelta(days=29),today()
    elif choice=='За 7 дней':start,end=today()-timedelta(days=6),today()
    else:start,end=latest_period(db)
    if end<start:st.error('Проверьте даты');return
    from .dashboard import snapshot
    result,issues,raw_net=snapshot(db,start,end)
    from .kpi import comparison_period,tone,metric
    prior_start,prior_end=comparison_period(start,end)
    prior,prior_issues,_=snapshot(db,prior_start,prior_end)
    prior_known=bool(prior['count']) and not prior_issues and not prior['stale_api']
    current_known=bool(result['count']) and not issues and not result['stale_api']
    comparison_note=f'Сравнение с {prior_start:%d.%m.%Y} — {prior_end:%d.%m.%Y}'
    st.caption(comparison_note+('' if prior_known else ' · нет полных данных для сравнения'))
    st.caption(f'{start:%d.%m.%Y} — {end:%d.%m.%Y} · Предварительный расчёт')
    if issues:
        st.warning('Начисления загружены, но прибыль за этот период пока нельзя рассчитать. Ниже указаны причины.')
        cols=st.columns(3)
        cols[0].metric('Чистая прибыль','Требуется проверка')
        cols[1].metric('Реализация покупателям','Требуется проверка')
        cols[2].metric('Начислено Ozon',cash(raw_net))
        st.dataframe(pd.DataFrame({'Что проверить':issues}),hide_index=True,width='stretch')
        return
    if not result['count']:
        if active_credentials():st.info('За выбранный период нет рассчитанных данных. Нажмите «Обновить». Если загрузка завершится ошибкой, её причина появится выше. Себестоимость загружается один раз в разделе «Товары».')
        else:st.info('Начните с настроек: подключите Ozon и один раз загрузите себестоимость в разделе «Товары».')
    if get_setting(db,'api_profit_error') and not result['count']:st.warning(get_setting(db,'api_profit_error'))
    if result['missing']:
        st.warning('Не заполнена себестоимость на дату продажи. Укажите её в разделе «Товары», включая дату начала действия.')
        missing=[{'Дата продажи':r['day'],'Артикул':r['sku'],'Товар':r['name'],'Причина':r['issue']} for r in result['details'] if r['issue'] and r['issue']!='Примерная себестоимость']
        st.dataframe(pd.DataFrame(missing).drop_duplicates(),hide_index=True,width='stretch')
    elif result['estimated']:st.info(f'В расчёте {result["estimated"]} продаж с примерной себестоимостью. Их можно уточнить в товарах.')
    top=st.columns(4)
    metric(top[0],'profit','Чистая прибыль · предварительно',_profit(result),tone('profit',result['profit'] if current_known and not result['missing'] and not result['stale_api'] else None))
    metric(top[1],'revenue','Реализация покупателям',cash(result['revenue']) if not result['stale_api'] else 'Нужен пересчёт',tone('revenue',result['revenue'] if current_known else None,prior['revenue'] if prior_known else None),help=comparison_note+(' · '+cash(prior['revenue']) if prior_known else ' · нет данных'))
    metric(top[2],'net','Начислено Ozon',cash(result['net']),tone('net',result['net'] if current_known else None,prior['net'] if prior_known else None),help=comparison_note+(' · '+cash(prior['net']) if prior_known else ' · нет данных'))
    from .advertising import drr
    advertising=drr(result['details'])
    from .performance import report as performance_report
    perf=performance_report(db,start,end,result['revenue'])
    if perf:
        advertising['store']=perf['store'];advertising['unassigned']=perf['unassigned']
        for sku,value in advertising['products'].items():
            value['advertising']=perf['products'].get(sku,money(0))
            value['drr']=value['advertising']/value['revenue']*100 if value['revenue']>0 and perf['unassigned']==0 else None
        if perf['total']!=advertising['advertising']:
            st.warning('Реклама требует сверки источников: Performance — '+cash(perf['total'])+', явно выделенные расходы Seller — '+cash(advertising['advertising'])+'. Показанная прибыль пока рассчитана по Seller API.')
        st.caption('Реклама: Performance API. Прибыль: финансовые начисления Seller API; рекламные расходы повторно не вычитаются.')
    else:st.caption('Реклама показана по начислениям Seller API. Для полной сверки подключите Performance в настройках.')
    if get_setting(db,'performance_error'):st.warning('Не удалось обновить рекламу: '+get_setting(db,'performance_error'))
    known_ads=perf is not None or advertising['advertising']!=0
    if not known_ads:
        for value in advertising['products'].values():value['drr']=None
    if advertising['unassigned']>0:st.caption('Общая реклама '+cash(advertising['unassigned'])+' распределена по товарам пропорционально реализации.')
    frame=details_frame(result['details'])
    ads=sum(((-money(r['amount'])) for r in result['details'] if r['kind'] in ('Реклама','Возврат расходов на рекламу')),money(0))
    row=st.columns(4)
    metric(row[3],'drr','ДРР магазина за период',(f'{advertising["store"]:.2f} %'.replace('.',',') if advertising['store'] is not None else 'Нет реализации') if known_ads else 'Нет данных рекламы',tone('drr',advertising['store'] if known_ads else None))
    metric(row[0],'costs','Себестоимость',cash(result['costs']))
    tax_label='Налоги ('+format(money(get_setting(db,'tax_rate','4')).normalize(),'f')+'%)'
    metric(row[1],'tax',tax_label,cash(result['tax']) if not result['stale_api'] else 'Нужен пересчёт')
    metric(row[2],'ads','Реклама',cash(perf['total'] if perf else ads) if known_ads else 'Нет данных рекламы')
    margin_value=result['profit']/result['revenue']*100 if result['revenue']>0 and not result['missing'] and not result['stale_api'] else None
    margin=f'{margin_value:.2f} %'.replace('.',',') if margin_value is not None else '—'
    metric(top[3],'margin','Процент чистой прибыли',margin,tone('margin',margin_value))
    st.caption('Процент чистой прибыли = чистая прибыль ÷ реализация покупателям × 100%.')
    minis=st.columns(3)
    for col,label,first in zip(minis,['Сегодня','За 7 дней','Этот месяц'],[today(),today()-timedelta(days=6),today().replace(day=1)]):
        mini,mini_issues,_=snapshot(db,first,today())
        col.caption(label+' · '+('Требуется проверка' if mini_issues else _profit(mini)))
    if not frame.empty:
        left,right=st.columns([3,2])
        with left:
            chart=st.selectbox('Динамика',['Чистая прибыль','Реализация покупателям'],key='overview_chart')
            if chart=='Чистая прибыль' and (result['missing'] or result['stale_api']):
                st.info('Чистая прибыль пока не рассчитана. На графике показана реализация покупателям.')
                chart='Реализация покупателям'
            plotted=frame if chart=='Чистая прибыль' else frame[frame['kind'].isin([KINDS['sale'],KINDS['return']])]
            daily=plotted.groupby('day')['profit' if chart=='Чистая прибыль' else 'amount'].sum()
            daily=daily.reindex(pd.date_range(start,end).strftime('%Y-%m-%d'),fill_value=0)
            fig=go.Figure(go.Scatter(x=daily.index,y=daily.values,mode='lines+markers',fill='tozeroy',
                line={'color':'#4c7bec','width':3},marker={'size':5},fillcolor='rgba(76,123,236,.08)',hovertemplate='%{x}<br>%{y:,.2f} ₽<extra></extra>'))
            fig.update_layout(height=270,margin=dict(l=0,r=10,t=10,b=0),paper_bgcolor='rgba(0,0,0,0)',plot_bgcolor='rgba(0,0,0,0)',xaxis_title=None,yaxis_title='₽',showlegend=False)
            ticks=list(daily.index)[::max(1,len(daily)//6)]
            fig.update_xaxes(type='category',tickmode='array',tickvals=ticks,ticktext=[date.fromisoformat(value).strftime('%d.%m') for value in ticks])
            st.plotly_chart(fig,width='stretch')
        with right:
            st.subheader('На что уходят деньги')
            from .overview_reports import expenses_html
            st.html(expenses_html(db,start,end,result,tax_label))
            st.caption('Нажмите «Реклама» или «Прочие услуги», чтобы раскрыть состав прямо в таблице. Плюс — поступление, минус — расход. Соинвест учитывает начисления и их отмены.')
        st.subheader('Результат по товарам')
        from .product_metrics import summarize
        overview_products=summarize(result,allocate_shared=True)
        if overview_products['has_shared']:
            st.caption('Общие расходы распределены пропорционально реализации товаров. Сумма товарной прибыли совпадает с итогом магазина; распределение не является прямой рекламной атрибуцией.')
        names={r['sku']:r['name'] for r in db.execute('SELECT sku,name FROM products')}
        lines=[]
        for sku,p in overview_products['products'].items():
            lines.append({'Артикул':sku,'Изделие':names.get(sku) or p['name'] or sku,
                'Реализация, ₽':float(rub(p['revenue'])),'Себестоимость, ₽':float(rub(p['costs'])),'Налоги, ₽':float(rub(p['tax'])),
                'Чистая прибыль по артикулу, ₽':float(rub(p['profit'])) if not p['blocked'] else None,
                'Реклама, ₽':float(rub(p['advertising'])) if overview_products['allocated'] or overview_products['unassigned_advertising']==0 else None,'ДРР, %':float(p['drr']) if p['drr'] is not None else None})
        if lines:
            st.dataframe(pd.DataFrame(lines),hide_index=True,width='stretch')
            selected_sku=st.selectbox('Посмотреть артикул',list(overview_products['products']),format_func=lambda sku:(names.get(sku) or sku)+' · '+sku,key='article_dashboard')
            p=overview_products['products'][selected_sku]
            prior_products=summarize(prior,allocate_shared=True)['products'] if prior_known else {}
            prior_product=prior_products.get(selected_sku)
            prior_revenue=prior_product['revenue'] if prior_product else money(0) if prior_known else None
            product_ads_known=known_ads and (perf is None or perf['total']==advertising['advertising'])
            st.caption('Включены общие расходы, распределённые по выручке. Выбор артикула не меняет общий итог магазина.')
            c1,c2,c3=st.columns(3)
            metric(c1,'article_profit','Чистая прибыль по артикулу',cash(p['profit']) if not p['blocked'] else 'Недостаточно данных',tone('profit',p['profit'] if not p['blocked'] else None))
            metric(c2,'article_revenue','Реализация по артикулу',cash(p['revenue']),tone('revenue',p['revenue'],prior_revenue),help=comparison_note+(' · '+cash(prior_revenue) if prior_revenue is not None else ' · нет данных'))
            metric(c3,'article_drr','ДРР артикула',f'{p["drr"]:.2f} %'.replace('.',',') if p['drr'] is not None and product_ads_known else 'Нет данных рекламы',tone('drr',p['drr'] if product_ads_known else None))
            c1,c2,c3,c4=st.columns(4)
            metric(c1,'article_costs','Себестоимость артикула',cash(p['costs']))
            metric(c2,'article_tax','Налоги по артикулу',cash(p['tax']))
            metric(c3,'article_ads','Реклама по артикулу',cash(p['advertising']) if product_ads_known else 'Нет данных рекламы')
            metric(c4,'article_margin','Процент прибыли артикула',f'{p["margin"]:.2f} %'.replace('.',',') if p['margin'] is not None else '—',tone('margin',p['margin']))
            st.caption('Чистая прибыль с одной проданной штуки: '+(cash(p['unit_profit']) if p['unit_profit'] is not None else 'не определена')+'. ДРР = реклама ÷ реализация покупателям × 100%.')
        with st.expander('Посмотреть заказы и операции'):
            order=st.text_input('Номер отправления',key='order_filter')
            selected=frame[frame.order_no==order.strip()] if order.strip() else frame
            st.dataframe(selected.rename(columns={'day':'Дата','order_no':'Отправление','sku':'Артикул','name':'Изделие','kind':'Тип','amount':'Начисление','cost':'Стоимость','tax':'Налог','profit':'Результат','issue':'Примечание','quantity':'Количество'}),hide_index=True,width='stretch')


def products_page(db):
    from .catalog import import_library
    st.header('Товары и себестоимость')
    st.caption('Для товаров без собственной стоимости применяется себестоимость подвески «Рыба» с упаковкой — принятая вами стоимость для расчёта. Своя стоимость товара имеет приоритет.')
    total=db.execute('SELECT count(DISTINCT sku) FROM costs').fetchone()[0]
    st.caption(f'Стоимость сохранена для {total} товаров. Загружать файл повторно не нужно.')
    with st.expander('Загрузить или обновить себестоимость',expanded=total==0):
        upload=st.file_uploader('Себестоимость.xlsx',type=['xlsx'],key='library_file')
        first=db.execute('SELECT min(day) FROM operations').fetchone()[0] or get_setting(db,'api_start',str(today().replace(day=1)))
        effective=st.date_input('Стоимость действует с',date.fromisoformat(first),key='library_since',format='DD.MM.YYYY')
        if upload and st.button('Сохранить таблицу',key='library_save'):
            try:
                count=import_library(db,upload.getvalue(),effective)
                ensure_profit(db)
                st.success(f'Сохранено изделий: {count}. Таблица будет использоваться для новых товаров автоматически.')
            except ValueError as exc:st.error(str(exc))
    rows=[]
    for p in db.execute('SELECT * FROM products ORDER BY name'):
        c=db.execute('SELECT * FROM costs WHERE sku=? AND since<=? ORDER BY since DESC LIMIT 1',(p['sku'],str(today()))).fetchone()
        rows.append({'sku':p['sku'],'name':p['name'],'amount':c['amount'] if c else '', 'note':c['note'] if c else ''})
    if rows:
        when=st.date_input('Новые значения действуют с',today(),key='edit_cost_since',format='DD.MM.YYYY')
        st.caption('Изменение сохраняется с выбранной даты; прежняя стоимость остаётся в истории.')
        edited=st.data_editor(pd.DataFrame(rows),hide_index=True,width='stretch',disabled=['sku'],key='product_editor',column_config={
            'sku':st.column_config.TextColumn('Артикул'),'name':st.column_config.TextColumn('Изделие'),
            'amount':st.column_config.TextColumn('С упаковкой, ₽'),
            'note':st.column_config.TextColumn('Примечание')})
        if st.button('Сохранить изменения',type='primary'):
            try:
                changes=[]
                for old,new in zip(rows,edited.to_dict('records')):
                    if old==new:continue
                    if not new['name'].strip() or new['amount']=='':raise ValueError('Укажите название и стоимость изменённого товара')
                    value=money(new['amount'])
                    if value<0:raise ValueError('Стоимость не может быть отрицательной')
                    changes.append((new,value))
                with db:
                    for new,value in changes:
                        db.execute('UPDATE products SET name=? WHERE sku=?',(new['name'],new['sku']))
                        db.execute('INSERT OR REPLACE INTO costs VALUES(?,?,?,?,?)',(new['sku'],str(when),str(value),0,new['note']))
                ensure_profit(db);st.success('Изменения сохранены')
            except ValueError as exc:st.error(str(exc))
    with st.expander('Добавить товар вручную'):
        with st.form('cost'):
            sku=st.text_input('Артикул Ozon');name=st.text_input('Название изделия')
            amount=st.text_input('Стоимость с упаковкой, ₽','0')
            effective=st.date_input('Действует с',today(),key='manual_cost_since',format='DD.MM.YYYY');note=st.text_input('Примечание')
            if st.form_submit_button('Сохранить'):
                try:save_cost(db,sku,name,effective,amount,False,note);ensure_profit(db);st.success('Товар сохранён')
                except ValueError as exc:st.error(str(exc))
    with st.expander('История изменений стоимости'):
        st.dataframe(pd.read_sql_query('SELECT p.name AS Изделие,c.sku AS Артикул,c.since AS "С даты",c.amount AS Стоимость FROM costs c JOIN products p USING(sku) ORDER BY c.since DESC',db),hide_index=True,width='stretch')


def settings_page(db,manager):
    st.header('Настройки')
    pair=active_credentials()
    st.subheader('Магазин Ozon')
    st.caption('Подключён · ключ сохранён и подхватывается автоматически' if pair else 'Подключите магазин один раз')
    if pair and st.button('Проверить соединение',key='simple_check_connection'):
        from .seller_api import SellerClient,ENDPOINTS
        client=SellerClient(*pair)
        try:
            with st.spinner('Проверяем доступ к Ozon…'):client.post(ENDPOINTS['types'],{})
            st.success('Ozon ответил. Вернитесь в «Обзор» и нажмите «Обновить» для загрузки данных.')
        except ApiError as exc:st.error(str(exc))
        finally:client.close()
    with st.expander('Изменить подключение' if pair else 'Подключить магазин',expanded=not bool(pair)):
        def remember_connection():
            from .credentials import CredentialError
            target=connect()
            try:
                save_api_credentials(target,st.session_state.get('simple_client',''),st.session_state.get('simple_api_key',''))
                set_setting(target,'api_schemes',json.dumps(st.session_state.get('simple_schemes') or ['fbo','fbs']))
                st.session_state['_auto_attempted']=False
                st.session_state['_simple_connection_status']='Ключ сохранён зашифрованно. Дальше подключение подхватывается автоматически.'
                st.session_state['simple_client']='';st.session_state['simple_api_key']=''
            except (ValueError,StorageError,CredentialError) as exc:
                st.session_state['_simple_connection_status']=str(exc)
            finally:target.close()
        with st.form('simple_credentials'):
            st.text_input('Client ID',key='simple_client')
            st.text_input('API-ключ',type='password',key='simple_api_key')
            st.multiselect('Схемы работы',['fbo','fbs'],default=json.loads(get_setting(db,'api_schemes','["fbo","fbs"]')),format_func=str.upper,key='simple_schemes')
            st.form_submit_button('Сохранить подключение',type='primary',on_click=remember_connection)
        if st.session_state.get('_simple_connection_status'):st.info(st.session_state['_simple_connection_status'])
        st.caption('Ключ не хранится в открытом виде и не попадает в журнал. В веб-версии его обработка выполняется на сервере приложения.')
    from .performance import connection_ui as performance_connection
    performance_connection(db)
    st.subheader('Обновления и налоги')
    with st.form('preferences'):
        auto=st.checkbox('Обновлять данные при открытии',value=get_setting(db,'api_on_start','1')=='1')
        every=int(get_setting(db,'api_interval','60'))
        interval=st.selectbox('Пока приложение открыто',[0,30,60,180],index=[0,30,60,180].index(every),format_func=lambda v:'Обновлять только вручную' if not v else f'Каждые {v} минут')
        rate=st.number_input('Налоговая ставка, %',0.0,100.0,float(get_setting(db,'tax_rate','4')))
        bases={'sales':'Реализация покупателям','sales_partner':'Реализация + партнёрские выплаты','sales_points':'Реализация + баллы','sales_points_partner':'Реализация + все возмещения','net':'Начисления после удержаний (свой вариант)'}
        base=get_setting(db,'tax_base','sales')
        selected=st.selectbox('Налоговая база',list(bases),index=list(bases).index(base),format_func=bases.get)
        if st.form_submit_button('Сохранить настройки'):
            with db:db.executemany('INSERT OR REPLACE INTO settings VALUES(?,?)',[('api_on_start',str(int(auto))),('api_interval',str(interval)),('tax_rate',str(rate)),('tax_base',selected)])
            st.success('Сохранено')
    st.subheader('Хранение данных')
    from .access import local_mode
    if local_mode():
        st.success('Данные хранятся на этом компьютере и сохраняются между запусками. Подключать облачное хранилище не нужно.')
        st.caption('В Windows ключ Ozon защищён DPAPI и доступен только вашей учётной записи.')
    else:
        if manager.store:
            st.success('Постоянное хранилище Supabase подключено' if not manager.last_error else 'Хранилище настроено, но последняя отправка не удалась')
            if manager.last_error:st.warning(manager.last_error)
            st.caption('База, история стоимости, план и зашифрованный ключ сохраняются автоматически. В Supabase уходит зашифрованная копия в закрытый bucket.')
        else:
            st.warning('Данные и ключ уже сохраняются между открытиями, пока существует диск Streamlit. Чтобы пережить полную пересборку сервера, один раз подключите постоянное хранилище ниже.')
        with st.expander('Подключить постоянное хранение · один раз',expanded=not bool(manager.store)):
            st.markdown('''1. Откройте [Supabase](https://supabase.com/dashboard) и войдите через GitHub.
    2. Нажмите **New project**, задайте имя `Ozon Profit` и пароль базы. Выберите бесплатный тариф и дождитесь создания.
    3. В настройках проекта найдите **Project URL** и ключ **service_role** (Legacy API Keys). Ключ держите в секрете.
    4. В Streamlit откройте **Settings → Secrets** и добавьте блок ниже, сохранив существующий `app_password`.
    5. Сохраните Secrets и откройте приложение. Закрытый bucket создастся автоматически; текущие данные перенесутся без повторного импорта.''')
            st.code('[storage]\nsupabase_url = "https://ВАШ-ПРОЕКТ.supabase.co"\nsupabase_key = "ВАШ_SERVICE_ROLE_KEY"',language='toml')
            st.caption('Не присылайте ключи и пароли в чат. Здесь не нужны SQL-команды или создание таблиц. Сервис может приостанавливать бесплатный проект; его потребуется возобновить в Supabase. Проверка реального подключения завершится после ввода ваших реквизитов.')
    with st.expander('Резервная копия'):
        from .remote_storage import snapshot
        st.download_button('Скачать копию данных',snapshot(db),'OzonProfit-backup.db','application/octet-stream')
        st.caption('Файл содержит финансовые данные, стоимость и план. Сохранённый ключ API из пользовательской копии исключён.')
    with st.expander('Диагностика и расширенные инструменты'):
        tool=st.selectbox('Инструмент',['—','Проверить подключение и начисления','Импорт оригинальных Excel','Ручной импорт Excel'],key='advanced_tool')
        if tool=='Проверить подключение и начисления':
            from .api_ui import connection_ui
            connection_ui(db)
        elif tool=='Импорт оригинальных Excel':
            from .import_ui import native_import
            native_import(db)
        elif tool=='Ручной импорт Excel':
            from .advanced_ui import manual_import
            manual_import(db)
