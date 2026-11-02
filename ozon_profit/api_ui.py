"""Local credentials and API staging review, pending verification of live contracts."""
from datetime import date, datetime, timedelta, timezone
import json
import os
import pandas as pd
import streamlit as st
from . import credentials
from .core import get_setting, set_setting, money, rub
from .seller_api import SellerClient, ApiError, sync, init_tables


def active_credentials():
    if '_seller_pair' not in st.session_state:
        try:st.session_state['_seller_pair']=credentials.load() if os.name=='nt' else None
        except credentials.CredentialError:st.session_state['_seller_pair']=None
    return st.session_state['_seller_pair']


def run_update(db,start,end,schemes):
    pair=active_credentials()
    if not pair:raise ApiError('Сначала введите Client ID и ключ в разделе «Подключение Ozon».')
    client=SellerClient(*pair)
    try:
        result=sync(db,client,start,end,schemes)
        if get_setting(db,'api_profit_enabled','0')=='1':
            from .api_ledger import promote
            try:
                promote(db,start,end)
                result['profit_status']='updated'
            except ValueError:
                set_setting(db,'api_profit_error','Новые начисления загружены, но не все удалось сопоставить. Прибыль сохранена по предыдущим данным; проверьте раздел прибыли API.')
                result['profit_status']='blocked'
        else:result['profit_status']='disabled'
        return result
    finally:client.close()


def connection_ui(db):
    init_tables(db)
    st.subheader('Подключение Ozon Seller API')
    st.caption('Версия подключения: начисления v1 • FBO v3 • FBS v4')
    st.warning('Подключение обновлено по опубликованным схемам API за октябрь 2026 года. Проверка реальных ответов вашего магазина ещё не завершена. Доступ без Premium не подтверждён реальным успешным запросом. Начисления можно включить в прибыль после проверки сопоставления ниже. Прибыль остаётся предварительной.')
    st.write('В личном кабинете Ozon Seller откройте настройки → раздел API/ключей Seller API. Названия пунктов могут отличаться. Создайте ключ с минимальными правами чтения финансов и отправлений. Client ID и ключ вводятся только здесь, в локальном приложении. Не отправляйте ключ в чат.')
    pair=active_credentials()
    st.caption('Ключ подключён для текущего сеанса.' if pair else 'Ключ пока не подключён.')
    if os.name!='nt':st.info('В этой системе ключ хранится только в памяти сеанса. Постоянное хранение реализовано через защиту Windows DPAPI и должно быть проверено в Windows-сборке.')
    def remember():
        try:
            values=credentials.validate(st.session_state.get('seller_client_input',''),st.session_state.get('seller_key_input',''))
            if st.session_state.get('seller_persist',False):credentials.save(*values)
            st.session_state['_seller_pair']=values
            st.session_state['_credential_message']='Подключение сохранено. Теперь можно выполнить проверочную загрузку.'
            st.session_state['seller_key_input']=''
            st.session_state['seller_client_input']=''
        except credentials.CredentialError as exc:
            st.session_state['_credential_message']=str(exc)
        except OSError:
            st.session_state['_credential_message']='Не удалось сохранить ключ. Проверьте доступ к личной папке данных Windows.'
    with st.form('seller_credentials'):
        st.text_input('Client ID',key='seller_client_input')
        st.text_input('API-ключ',type='password',key='seller_key_input')
        st.checkbox('Сохранить защищённо для следующих запусков',value=False,disabled=os.name!='nt',key='seller_persist')
        st.form_submit_button('Сохранить подключение',on_click=remember)
    if st.session_state.get('_credential_message'):st.info(st.session_state['_credential_message'])
    if st.button('Забыть ключ на этом компьютере'):
        credentials.delete()
        st.session_state['_seller_pair']=None
        st.session_state.pop('_credential_message',None)
        st.success('Сохранённый ключ удалён. Чтобы отозвать доступ полностью, удалите ключ и в кабинете Ozon.')
    st.subheader('Проверка доступа к методам')
    st.caption('Выполняются четыре небольших запроса: начисления за день, справочник типов, FBO и FBS. Таблица покажет, какой именно адрес возвращает 404. Проверка не меняет базу и не показывает ключ или финансовые строки.')
    if st.button('Проверить методы API',key='api_diagnose'):
        current=active_credentials()
        if not current:
            st.error('Сначала сохраните Client ID и API-ключ.')
        else:
            client=SellerClient(*current)
            try:
                with st.spinner('Проверяем методы Ozon…'):
                    statuses=client.diagnose(datetime.now(timezone(timedelta(hours=3))).date()-timedelta(days=1))
                st.dataframe(pd.DataFrame(statuses),width='stretch',hide_index=True)
                st.session_state['_api_diagnostic_text']='\n\n'.join(row['Метод']+'\n'+row['Результат'] for row in statuses)
            except ApiError as exc:
                st.error(str(exc))
            except Exception:
                st.error('Проверка не завершена. Сохранённые данные не изменены.')
            finally:
                client.close()
    if st.session_state.get('_api_diagnostic_text'):
        st.caption('Полный текст диагностики (без обрезания столбцов):')
        st.code(st.session_state['_api_diagnostic_text'],language=None,wrap_lines=True)
    st.subheader('Проверочная загрузка')
    today=datetime.now(timezone(timedelta(hours=3))).date()
    first=st.date_input('Начало загрузки',today-timedelta(days=89),key='api_since')
    last=st.date_input('Конец загрузки',today,key='api_until')
    schemes=st.multiselect('Схемы работы',options=['fbo','fbs'],default=['fbo','fbs'],format_func=str.upper)
    finance_only=st.checkbox('Загрузить только финансовые начисления, без отправлений',key='api_finance_only')
    if st.button('Загрузить данные Ozon',key='api_download'):
        try:
            with st.spinner('Загружаем финансы и отправления…'):
                if not schemes and not finance_only:
                    raise ApiError('Выберите FBO/FBS или включите загрузку только финансов.')
                stats=run_update(db,first,last,() if finance_only else schemes)
            st.success(f'Получено финансовых операций: {stats["finance"]}; отправлений: {stats["postings"]}.')
            if stats.get('profit_status')=='blocked':st.warning(get_setting(db,'api_profit_error'))
            elif stats.get('profit_status')=='updated':st.success('Прибыль API пересчитана.')
        except ApiError as exc:st.error(str(exc))
        except Exception:st.error('Обновление не завершено. Проверьте соединение и настройки; ключи и ответы API в журнал не выводятся.')
    st.caption('Последнее успешное обновление выбранных разделов: '+get_setting(db,'api_last_success','ещё не было'))
    st.caption('Загруженные разделы: '+get_setting(db,'api_components','ещё не было'))
    st.subheader('Автоматическая загрузка')
    st.caption('По умолчанию отключена. Работает, пока открыто приложение и доступен ключ. Последние 90 дней запрашиваются заново, чтобы получить задержанные начисления. Для более старых корректировок выберите период вручную. После включения прибыли API успешная загрузка также пересчитывает прибыль; при ошибке сопоставления прежний расчёт сохраняется.')
    with st.form('api_auto'):
        enabled=st.checkbox('Обновлять при открытии приложения',value=get_setting(db,'api_on_start','0')=='1')
        minutes=st.selectbox('Повторять загрузку',options=[0,30,60,180],index=[0,30,60,180].index(int(get_setting(db,'api_interval','0'))),format_func=lambda v:'Отключено' if v==0 else f'Каждые {v} минут')
        if st.form_submit_button('Сохранить расписание'):
            set_setting(db,'api_on_start',int(enabled));set_setting(db,'api_interval',minutes)
            st.success('Сохранено. Расписание активно только при открытом приложении.')
    st.subheader('Полученные финансовые операции (для сверки)')
    rows=db.execute('SELECT id,day,amount,payload FROM api_finance WHERE day BETWEEN ? AND ? ORDER BY day,id',(str(first),str(last))).fetchall()
    view=[]
    for r in rows:
        p=json.loads(r['payload'])
        view.append({'ID':r['id'],'Дата':r['day'],'Тип':p.get('operation_type_name') or p.get('operation_type'),'Начисление, ₽':float(money(r['amount'])),'Отправление':p.get('posting',{}).get('posting_number') or ''})
    st.dataframe(pd.DataFrame(view),width='stretch',hide_index=True)
    if rows:st.metric('Итого начислений API за выбранный период',f'{rub(sum((money(r["amount"]) for r in rows),money(0))):,.2f} ₽'.replace(',',' '))
    cost_upload_ui(db,first)
    profit_ui(db,first,last)
    st.subheader('Отправления и состав')
    postings=[]
    for r in db.execute('SELECT payload FROM api_postings ORDER BY posting_number'):
        posting=json.loads(r[0])
        for p in posting['products']:
            postings.append({'Отправление':posting['posting_number'],'Схема':posting['schema'].upper(),'Статус':posting['status'],'Артикул':p.get('offer_id'),'SKU':p.get('sku'),'Товар':p.get('name'),'Количество':p.get('quantity')})
    st.dataframe(pd.DataFrame(postings),width='stretch',hide_index=True)
    st.caption('Список отправлений содержит последние сохранённые статусы по всем загруженным периодам. Для сопоставления дополнительно запрашиваются отправления, созданные за 31 день до начала финансового периода. Более старые отправления могут потребовать расширения периода.')
    st.info('Реклама: суммы, которые вернёт финансовый API, сохраняются в составе операций. Отдельное подключение Performance API, его права, доступность и рекламная статистика пока не реализованы. Не добавляйте рекламные расходы второй раз поверх удержаний.')


def profit_ui(db,start,end):
    from .api_ledger import project,preview,promote,restore_previous
    st.subheader('Прибыль по данным API')
    bases={'sales':'Только реализация','sales_partner':'Реализация + программы партнёров',
           'sales_points':'Реализация + баллы','sales_points_partner':'Реализация + баллы + программы партнёров',
           'net':'Начисления после удержаний (пользовательский вариант)'}
    base=get_setting(db,'tax_base','sales')
    selected=st.selectbox('Налоговая база для прибыли API',list(bases),index=list(bases).index(base),format_func=bases.get,key='api_profit_tax')
    st.caption('Ставка берётся из настроек. Налоговый резерв предварительный; выбор базы сам по себе не подтверждает правила налогообложения.')
    projection=project(db,start,end)
    if projection.issues:
        st.warning('Прибыль API пока не включена для этого периода: требуется сопоставление.')
        st.dataframe(pd.DataFrame({'Что проверить':projection.issues}),width='stretch',hide_index=True)
        st.caption('Если не найдены артикулы или количество: снимите флажок «только финансовые начисления», выберите свои схемы FBO/FBS и загрузите данные ещё раз. Стоимость изделий задаётся отдельно в справочнике или через импорт исходных Excel.')
    else:
        result=preview(db,projection,selected)
        labels=[('Начисления','net'),('Реализация','revenue'),('Себестоимость','costs'),('Налоговый резерв','tax'),('Предварительная прибыль','profit')]
        for col,(label,key) in zip(st.columns(len(labels)),labels):col.metric(label,f'{rub(result[key]):,.2f} ₽'.replace(',',' '))
        if result['missing'] or result['estimated']:
            st.warning(f'Неполный расчёт: {result["missing"]} продаж без стоимости, {result["estimated"]} с примерной себестоимостью. Исправьте справочник; показанная прибыль может быть завышена.')
        st.caption('Вычисление по API: исходная комиссия и баллы учитываются один раз; итоговая комиссия не прибавляется повторно. Возврат выручки не означает физический возврат товара в запас.')
        approved=st.checkbox('Использовать API для прибыли: заменить операции Ozon из Excel за выбранный период и пересчитывать обновляемые периоды после следующих загрузок. Предыдущие операции сохранить в резервной копии.',key='api_profit_approve')
        if st.button('Включить прибыль API',disabled=not approved,key='api_profit_enable'):
            try:
                promote(db,start,end,tax_base=selected,enable=True)
                st.success('Прибыль API включена. Откройте «Обзор» и выберите этот период. Следующие успешные обновления будут пересчитывать прибыль автоматически.')
            except ValueError as exc:st.error(str(exc))
    if get_setting(db,'api_profit_error'):st.warning(get_setting(db,'api_profit_error'))
    if db.execute('SELECT 1 FROM ledger_backups WHERE period_start=? AND period_end=?',(str(start),str(end))).fetchone():
        if st.button('Вернуть предыдущие операции периода и отключить автопересчёт API',key='api_profit_restore'):
            try:
                restore_previous(db,start,end)
                st.success('Предыдущие операции восстановлены. Автопересчёт API отключён. Настройки налога и история себестоимости сохранены.')
            except ValueError as exc:st.error(str(exc))


def cost_upload_ui(db,since):
    from .ozon_excel import parse_costs,suggested_costs
    products={}
    for row in db.execute('SELECT payload FROM api_postings'):
        for p in json.loads(row[0]).get('products',[]):
            if p.get('offer_id'):
                article=str(p['offer_id'])
                products[article]={'name':str(p.get('name') or article),'sku':article}
    if not products:return
    with st.expander('Загрузить себестоимость для товаров API'):
        st.caption('Достаточно одного файла «Себестоимость.xlsx». Итоги с упаковкой читаются из листа «Украшения». Уже сохранённая история остаётся; запись на ту же дату заменяется. Оценки «Малин» по «Рыбе» останутся примерными.')
        uploaded=st.file_uploader('Файл себестоимости',type=['xlsx'],key='api_cost_file')
        if not uploaded:return
        try:
            costs=parse_costs(uploaded.getvalue())
            values={r['name']:r['amount'] for r in costs}
            table=st.data_editor(pd.DataFrame(suggested_costs(products,costs)),hide_index=True,width='stretch',
                disabled=['sku','name'],column_config={
                    'sku':st.column_config.TextColumn('Артикул'),
                    'name':st.column_config.TextColumn('Товар'),
                    'cost_name':st.column_config.SelectboxColumn('Изделие в себестоимости',options=['']+list(values)),
                    'estimated':st.column_config.CheckboxColumn('Примерная стоимость'),
                    'note':st.column_config.TextColumn('Примечание'),
                },key='api_cost_mapping')
            effective=st.date_input('Эта стоимость действует с',since,key='api_cost_date')
            if st.button('Сохранить себестоимость товаров API',key='api_cost_save'):
                selected=[]
                for row in table.to_dict('records'):
                    if row['cost_name'] not in values:continue
                    estimate=bool(row['estimated']) or ('малина' in row['sku'].lower() and row['cost_name']=='Подвеска Рыба')
                    selected.append((row['sku'],row['name'],values[row['cost_name']],estimate,row['note']))
                if not selected:raise ValueError('Выберите хотя бы одно изделие для сопоставления')
                with db:
                    for sku,name,amount,estimated,note in selected:
                        db.execute('INSERT INTO products VALUES(?,?) ON CONFLICT(sku) DO UPDATE SET name=excluded.name',(sku,name))
                        db.execute('INSERT OR REPLACE INTO costs VALUES(?,?,?,?,?)',(sku,str(effective),str(amount),int(estimated),note))
                st.success(f'Себестоимость сохранена для {len(selected)} товаров. Проверка прибыли ниже пересчитана.')
        except ValueError as exc:st.error(str(exc))
        except Exception:st.error('Не удалось прочитать файл себестоимости. Проверьте формат и лист «Украшения».')
