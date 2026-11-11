"""Advertising evidence inside the existing overview, independent of finance."""
import json
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
    with st.expander('Рекламные показатели товара · отдельно от финансов'):
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
        if st.button('Получить / продолжить рекламный отчёт',key='ad_advance'):
            client=None
            try:
                pair=load_credentials(db,secret('app_password'))
                if not pair: raise ApiError('Подключение Performance не найдено в локальной базе. Сохранённые наблюдения доступны без подключения.')
                skus={str(r[0]) for r in db.execute('SELECT ozon_sku FROM product_links WHERE sku=?',(article,))}
                client=Client(*pair)
                with st.spinner('Получаем очередную часть рекламного отчёта…'):
                    ads.advance(db,article,skus,start,end,client)
            except (ApiError,StorageError,CredentialError) as exc:st.warning(str(exc))
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
