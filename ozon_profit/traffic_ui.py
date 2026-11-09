"""Traffic and advertising comparison. Stored observations survive application restarts."""
from datetime import timedelta,datetime,timezone
import io
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from . import traffic,performance
from .seller_api import SellerClient,ApiError
from .api_ui import active_credentials
from .runtime import secret
from .core import get_setting,set_setting


def chart(frame,show_clicks=True,show_promo=True):
    fig=make_subplots(specs=[[{'secondary_y':True}]])
    if show_promo:
        for r in frame.to_dict('records'):
            if r['promo'] in ('on','mixed'):
                d=pd.Timestamp(r['day'])
                fig.add_vrect(x0=d-timedelta(hours=12),x1=d+timedelta(hours=12),
                    fillcolor='#dff568' if r['promo']=='on' else '#e8e8e8',opacity=.45,line_width=0,layer='below',exclude_empty_subplots=False)
    if show_clicks:
        fig.add_trace(go.Bar(x=frame.day,y=frame.click,name='Расходы за клики, ₽',marker_color='#8acaff',opacity=.65),secondary_y=True)
    fig.add_trace(go.Scatter(x=frame.day,y=frame.views,name='Показы (всего)',mode='lines+markers',connectgaps=False,line={'color':'#0956ff','width':3}),secondary_y=False)
    fig.update_yaxes(title_text='Показы',secondary_y=False,rangemode='tozero')
    fig.update_yaxes(title_text='Клики, ₽',secondary_y=True,rangemode='tozero',showgrid=False)
    fig.update_layout(height=430,template='plotly_white',hovermode='x unified',legend={'orientation':'h','y':1.15},margin={'t':45,'b':30})
    return fig


def traffic_page(db):
    traffic.init(db)
    st.title('Реклама и показы')
    st.caption('Как меняются общие показы товаров при продвижении за заказ и расходах за клики')
    from .ui_pages import today
    yesterday=today()-timedelta(days=1)
    cols=st.columns(2)
    start=cols[0].date_input('С',yesterday-timedelta(days=27),key='traffic_start')
    end=cols[1].date_input('По',yesterday,max_value=yesterday,key='traffic_end')
    if end<start or (end-start).days>61:
        st.info('Выберите период до 62 дней. Сегодня исключено: день ещё не завершён.');return
    links={str(r['ozon_sku']):r['sku'] for r in db.execute('SELECT ozon_sku,sku FROM product_links')}
    skus=[r[0] for r in db.execute('SELECT DISTINCT sku FROM traffic_daily ORDER BY sku')]
    sku=st.selectbox('Товар',['']+skus,format_func=lambda x:links.get(x,x) if x else 'Весь магазин',key='traffic_sku')
    if sku:st.caption('Статус оплаты за заказ относится ко всему магазину. Расходы за клики здесь не распределяются по выручке: отчёт пока доступен только по кампаниям.')
    pending=get_setting(db,'traffic_pending','')
    if st.button('Проверить готовность отчёта' if pending else 'Обновить аналитику',type='primary'):
        errors=[];notes=[]
        pair=active_credentials()
        # A persisted minute guard also protects a second app session/repeated clicks.
        stamp=get_setting(db,'traffic_seller_attempt','')
        try:cooldown=bool(stamp) and (datetime.now(timezone.utc)-datetime.fromisoformat(stamp)).total_seconds()<61
        except ValueError:cooldown=False
        if not pending:
            if not pair:errors.append('Seller API не подключён. Добавьте ключ в настройках.')
            elif cooldown:notes.append('Показы: предыдущий запрос был менее минуты назад. Повторите позже.')
            else:
                set_setting(db,'traffic_seller_attempt',datetime.now(timezone.utc).isoformat())
                client=SellerClient(*pair)
                try:
                    with st.spinner('Получаем общие показы по товарам…'):
                        data=traffic.fetch_traffic(client,start,end)
                        traffic.replace(db,'traffic',start,end,data)
                    notes.append(f'Показы: загружено {len(data)} строк.')
                except ApiError as exc:errors.append('Показы: '+str(exc)+' В кабинете и API могут действовать разные ограничения. При необходимости используйте импорт ниже.')
                finally:client.close()
        client=None
        try:
            pair=performance.load_credentials(db,secret('app_password'))
            if not pair:raise ApiError('Добавьте реквизиты Performance API в настройках.')
            client=performance.Client(*pair)
            if pending:
                import json
                saved=json.loads(pending)
                a,b=saved['start'],saved['end']
                uuid,data=traffic.fetch_promo(client,a,b,saved['uuid'])
            else:
                a,b=str(start),str(end)
                uuid,data=traffic.fetch_promo(client,start,end)
            if uuid:
                import json
                set_setting(db,'traffic_pending',json.dumps({'uuid':uuid,'start':a,'end':b}))
                notes.append('Ozon готовит историю продвижения. Нажмите «Проверить готовность отчёта» через несколько секунд.')
            else:
                traffic.replace(db,'promo',a,b,data);set_setting(db,'traffic_pending','')
                notes.append('История продвижения за заказ сохранена.')
        except (ApiError,ValueError) as exc:
            errors.append('Продвижение за заказ: '+str(exc));set_setting(db,'traffic_pending','')
        except Exception:
            errors.append('Не удалось открыть защищённое подключение Performance API. Проверьте настройки.')
        finally:
            if client:client.close()
        if not pending:
            client=None
            try:
                pair=performance.load_credentials(db,secret('app_password'))
                if pair:
                    client=performance.Client(*pair)
                    with st.spinner('Получаем расходы за клики…'):
                        data=traffic.fetch_clicks(client,start,end)
                        traffic.replace(db,'click',start,end,data)
                    notes.append('Расходы за клики сохранены.')
            except ApiError as exc:errors.append('Оплата за клики: '+str(exc))
            except Exception:errors.append('Не удалось открыть подключение Performance API.')
            finally:
                if client:client.close()
        for note in notes:st.info(note)
        for error in errors:st.warning(error)
    stamps=list(db.execute('SELECT source,stamp FROM traffic_updates ORDER BY source'))
    if stamps:
        names={'traffic':'Показы','promo':'Статусы','click':'Клики'}
        st.caption('Последняя загрузка: '+' · '.join(names[r['source']]+': '+r['stamp'][:16].replace('T',' ')+' UTC' for r in stamps))
    frame=traffic.daily(db,start,end,sku or None)
    if frame.views.notna().any():
        a,b=st.columns(2)
        a.metric('Показы за дни с данными',f'{int(frame.views.sum()):,}'.replace(',',' '))
        b.metric('Дней с показами в отчёте',f'{int(frame.views.notna().sum())} из {len(frame)}')
    else:st.info('Показов пока нет. Нажмите «Обновить аналитику». Если API ограничит доступ, загрузите выгрузку из кабинета ниже.')
    a,b=st.columns(2)
    show_clicks=a.checkbox('Расходы за клики',value=not bool(sku),disabled=bool(sku))
    show_promo=b.checkbox('Фон: продвижение за заказ',value=True)
    st.plotly_chart(chart(frame,show_clicks,show_promo),width='stretch')
    st.caption('Лимонный фон — оплата за заказ включена; серый — в течение дня получены разные статусы. Белый фон может означать выключенное продвижение или отсутствие статуса — смотрите таблицу. Пропуск данных не считается нулём.')
    st.subheader('Сравнение условий')
    if sku:
        usable=frame[frame.views.notna() & frame.promo.isin(['on','off'])]
        groups=usable.groupby('promo').agg(Дней=('day','size'),**{'Показы / день':('views','mean'),'Заказано шт. / день':('orders','mean')}).reset_index()
        groups['promo']=groups.promo.map({'on':'Включено','off':'Выключено'})
        st.dataframe(groups.rename(columns={'promo':'Оплата за заказ'}),hide_index=True,width='stretch')
    else:
        comp=traffic.comparison(frame)
        st.dataframe(comp,hide_index=True,width='stretch')
        excluded=len(frame)-int(comp['Дней'].sum())
        if excluded:st.caption(f'Не включено в сравнение {excluded} дней: нет показов, расходов за клики или однозначного статуса продвижения.')
    eligible=frame[frame.views.notna() & frame.promo.isin(['on','off'])]
    off=eligible.loc[eligible.promo=='off','views']
    on=eligible.loc[eligible.promo=='on','views']
    if len(off) and len(on):
        a,b,c=st.columns(3)
        a.metric('Показы / день: без оплаты за заказ',f'{off.mean():,.0f}'.replace(',',' '))
        b.metric('Показы / день: с оплатой за заказ',f'{on.mean():,.0f}'.replace(',',' '))
        c.metric('Разница средних показов',f'{(on.mean()/off.mean()-1)*100:+.1f} %' if off.mean()>0 else 'Нет базы сравнения')
        st.caption(f'Основа сравнения: {len(off)} дней без оплаты за заказ и {len(on)} дней с ней. Разница не скорректирована на расходы за клики или сезонность.')
    states=set(frame.loc[frame.views.notna(),'promo'])
    if not {'on','off'}<=states:st.info('Для сравнения нужны дни с показами и обоими статусами: продвижение за заказ включено и выключено.')
    st.caption('«Без расходов за клики» не означает, что кампания была выключена. Заказы — заказанные товары, не выкупы и не атрибутированные рекламные заказы. Сравнение показывает связь, а не доказанную причину: учитывайте цены, остатки, дни недели и сезонность.')
    with st.expander('Данные по дням'):
        shown=frame.rename(columns={'day':'Дата','views':'Показы (всего)','orders':'Заказано, шт.','promo':'Оплата за заказ','click':'Расходы за клики, ₽'}).copy()
        shown['Оплата за заказ']=shown['Оплата за заказ'].map({'on':'Включено','off':'Выключено','mixed':'Менялось в течение дня','unknown':'Нет данных'})
        st.dataframe(shown,hide_index=True,width='stretch')
        st.download_button('Скачать таблицу CSV',shown.to_csv(index=False).encode('utf-8-sig'),'reklama-po-dnyam.csv','text/csv')
    with st.expander('Загрузить показы из Excel или CSV'):
        st.write('Загрузите данные по товарам и дням. Нужны колонки «Дата», «SKU», «Показы (всего)». Необязательная колонка «Заказано товаров». SKU — числовой идентификатор Ozon, а не ваш артикул. Если названия или структура выгрузки отличаются, их нужно привести к шаблону.')
        st.download_button('Скачать шаблон CSV','Дата;SKU;Показы (всего);Заказано товаров\n'.encode('utf-8-sig'),'pokazy-shablon.csv','text/csv')
        upload=st.file_uploader('Файл с показами',type=['xlsx','csv'],key='traffic_upload')
        if upload and st.button('Сохранить показы'):
            try:
                raw=upload.getvalue()
                source=pd.read_excel(io.BytesIO(raw),dtype={'SKU':str}) if upload.name.lower().endswith('.xlsx') else pd.read_csv(io.BytesIO(raw),sep=None,engine='python',dtype={'SKU':str})
                data=traffic.import_views(source)
                # Import replaces only supplied SKU/day pairs, preserving unrelated products.
                with db:
                    db.executemany('INSERT OR REPLACE INTO traffic_daily VALUES(?,?,?,?)',data)
                    db.execute('INSERT OR REPLACE INTO traffic_updates VALUES(?,?)',('traffic',datetime.now(timezone.utc).isoformat()))
                st.success(f'Сохранено строк: {len(data)}.');st.rerun()
            except Exception:st.error('Не удалось загрузить файл. Проверьте названия колонок, даты, целые неотрицательные количества и отсутствие повторов дата + SKU. Прежние данные сохранены.')
