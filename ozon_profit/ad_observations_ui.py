"""Advertising evidence inside the existing overview, independent of finance."""
import json
import sqlite3
from datetime import timedelta
import pandas as pd
import streamlit as st
from . import ad_observations as ads
from .seller_api import ApiError
from .performance import Client, load_credentials
from .runtime import secret
from .secure_store import StorageError
from .credentials import CredentialError

LABELS = {'views':'Рекламные показы', 'clicks':'Рекламные клики', 'toCart':'Добавления в корзину из рекламы',
          'expense':'Расход в отчёте, ₽', 'orders':'Заказы по атрибуции Performance', 'modelOrders':'Модельные заказы Performance'}


def render(db, start, end):
    path=db.execute('PRAGMA database_list').fetchone()[2]
    _refreshing_view(path,start,end)


@st.fragment(run_every=timedelta(seconds=10))
def _refreshing_view(path,start,end):
    # Fragment reruns can use a new thread; never reuse the parent's connection.
    db=sqlite3.connect(path)
    db.row_factory=sqlite3.Row
    try:_render(db,start,end)
    finally:db.close()


def _render(db,start,end):
    with st.expander('Рекламные показатели товара · отдельно от финансов',expanded=True):
        st.caption('Финансовые показатели рассчитываются по Seller. Эти наблюдения не меняют прибыль, реализацию и расходы. Продажи из всех источников не являются заказами по атрибуции рекламы.')
        with st.expander('Загрузить сохранённый отчёт диагностики v3'):
            upload=st.file_uploader('Локальный JSON диагностики',type=['json'],key='ad_diagnostic_file')
            if st.button('Применить наблюдения',key='ad_import') and upload is not None:
                try:
                    if upload.size>5_000_000: raise ApiError('Размер отчёта превышает 5 МБ.')
                    run=ads.diagnostic_snapshot(json.loads(upload.getvalue()))
                    current=ads.load(db,run['article'],run['start'],run['end'])
                    if current and current['source']=='live':
                        raise ApiError('Для этого периода уже есть загрузка API; диагностический снимок её не заменяет.')
                    ads.save(db,run)
                    st.success('Наблюдения сохранены локально. Выберите артикул и период '+run['start']+' — '+run['end']+'.')
                except (ApiError,ValueError,UnicodeDecodeError) as exc:
                    st.error(str(exc) if isinstance(exc,ApiError) else 'Не распознан JSON отчёта.')
        ads.init(db)
        articles=sorted({r[0] for r in db.execute('SELECT sku FROM products')} |
                        {r[0] for r in db.execute('SELECT DISTINCT article FROM ad_observation_runs')})
        if not articles:
            st.info('Обновите каталог магазина или загрузите сохранённый отчёт.');return
        article=st.selectbox('Артикул для рекламных показателей',articles,key='ad_article')
        scope=(article,str(start),str(end))
        if st.button('Получить / продолжить рекламный отчёт',key='ad_advance'):
            st.session_state['_ad_running']=scope
        running=st.session_state.get('_ad_running')==scope
        if running and st.button('Приостановить загрузку',key='ad_pause'):
            st.session_state['_ad_running']=None;running=False
        if running:
            client=None
            try:
                pair=load_credentials(db,secret('app_password'))
                if not pair: raise ApiError('Подключение Performance не найдено в локальной базе. Сохранённые наблюдения доступны без подключения.')
                skus={str(r[0]) for r in db.execute('SELECT ozon_sku FROM product_links WHERE sku=?',(article,))}
                client=Client(*pair)
                with st.spinner('Получаем очередную часть рекламного отчёта…'):
                    progress=ads.advance(db,article,skus,start,end,client)
                    if all(j['state'] in ('done','rejected') for j in progress['jobs']):
                        st.session_state['_ad_running']=None
                    else:st.info('Загрузка продолжается автоматически, пока открыт «Обзор». После закрытия её можно продолжить с сохранённого места.')
            except (ApiError,StorageError,CredentialError) as exc:
                st.session_state['_ad_running']=None
                st.warning(str(exc))
            finally:
                if client:client.close()
        run=ads.load(db,article,start,end)
        if not run:
            st.info('За выбранные даты рекламных наблюдений нет. Это не нулевые клики или расходы.');return
        st.caption('Источник: '+('сохранённая диагностика v3' if run['source']=='diagnostic_v3' else 'Performance API')+' · '+str(run.get('updated') or 'загрузка начата'))
        st.write(f"Распознано отчётов: {run['received']} из {run['eligible']}. Товар найден в {sum(bool(r['rows']) for r in run['reports'])} отчётах.")
        failed=sum(j['state']=='rejected' for j in run.get('jobs',[]))
        if failed:st.warning(f'Запросов отчёта отклонено: {failed}. Полнота не установлена; требование подписки по HTTP-коду не определено.')
        st.warning('Показаны известные наблюдения. Полнота показателей за период не установлена; пропуски не равны нулю.')
        interpretation=ads.effectiveness(run)
        st.subheader('Что это говорит об эффективности рекламы')
        st.write('**'+interpretation['verdict']+'**')
        st.write(interpretation['interest'])
        ratio=interpretation['cart_events_per_100_clicks']
        if ratio is not None:
            text=f"{ratio:.2f}".replace('.',',')
            st.write(f"В {interpretation['paired_rows']} строках, где известны оба показателя: {interpretation['paired_clicks']} кликов и {interpretation['paired_carts']} добавлений в корзину — {text} добавления на 100 кликов.")
            st.caption('Это соотношение событий в сопоставимых строках, а не доля покупателей и не конверсия в заказ. Оно не учитывает строки с пропусками и неполученные отчёты; оценки «хорошо/плохо» без сопоставимой базы нет.')
        st.write('**Что мешает оценить отдачу и что проверить**')
        for gap in interpretation['gaps']:st.write('• '+gap)
        st.info(interpretation['action'])
        values=ads.summary(run)
        cols=st.columns(3)
        for col,field in zip(cols,('clicks','toCart','expense')):
            value=values[field]['observed']
            col.metric(LABELS[field]+' · известно',value if value is not None else 'Нет данных')
            col.caption(f"Известных строк: {values[field]['known_rows']}; пропусков в полученных строках: {values[field]['missing_rows']}")
        st.caption('Тип оплаты: '+('CPC подтверждён для полученных отчётов.' if values['expense']['cpc_verified'] else 'не подтверждён; расход нельзя считать расходом только за клики.'))
        st.info('Итог рекламных заказов и конверсия не определены. Атрибуция может пересекаться между кампаниями. Заказы ниже показаны отдельно по каждому отчёту; они не равны реализованным изделиям.')
        rows=[]
        for report in run['reports']:
            for row in report['rows']:
                rows.append({'Отчёт / кампания':report['campaign'],'Дата':row['day'],
                    **{label:row.get(k) if row.get(k) is not None else '—' for k,label in LABELS.items()}})
        if rows:st.dataframe(pd.DataFrame(rows),hide_index=True,width='stretch')
        else:st.info('В полученных отчётах строки товара не обнаружены; это не доказывает отсутствие исторической рекламы.')
