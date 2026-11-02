from datetime import date, timedelta
from decimal import Decimal
import pandas as pd
import streamlit as st
from ozon_profit.core import (connect, calculate, save_cost, import_operations, KINDS,
                              money, rub, get_setting, set_setting, withdrawal)
from ozon_profit.excel import workbook, sheet, operation_rows, text_cell

st.set_page_config(page_title='Ozon Profit', page_icon='💎', layout='wide')
from ozon_profit.access import require_login, local_mode
require_login()
st.markdown('''<style>.stApp {background: #f6f8fc} [data-testid="stMetric"] {
background:white;padding:18px;border-radius:14px;border:1px solid #e8ecf4}
.block-container {padding-top:2rem} h1 {color:#1542a4}</style>''', unsafe_allow_html=True)
st.title('💎 Ozon Profit')
st.caption('Украшения • учёт прибыли')
if not local_mode():
    st.warning('Веб-версия: загруженные данные хранятся на сервере приложения. Хранилище Community Cloud может очищаться при перезапуске. Сохраняйте резервную копию базы в разделе «Настройки».')
db = connect()


def show_money(value):
    return f'{rub(value):,.2f} ₽'.replace(',', ' ')


def readable(details):
    frame = pd.DataFrame(details)
    if frame.empty:
        return frame
    for col in ['amount', 'cost', 'tax', 'profit']:
        frame[col] = frame[col].map(lambda x: float(rub(x)))
    return frame


page = st.sidebar.radio('Раздел', ['Обзор', 'Импорт отчётов Ozon', 'Подключение Ozon', 'Загрузка Excel', 'Себестоимость', 'Заказы и операции', 'Настройки', 'Как пользоваться'])
today = date.today()
start = st.sidebar.date_input('С даты', date.fromisoformat(get_setting(db,'report_start',str(today.replace(day=1)))),key='period_start')
end = st.sidebar.date_input('По дату', date.fromisoformat(get_setting(db,'report_end',str(today))),key='period_end')
if end < start:
    st.error('Конечная дата раньше начальной'); st.stop()
last = db.execute('SELECT max(imported_at) FROM imports').fetchone()[0]
st.sidebar.caption(f'Последний успешный импорт: {last or "ещё не было"}' + (' (UTC)' if last else ''))
st.sidebar.caption('Последняя полная загрузка API: '+get_setting(db,'api_last_success','ещё не было'))
if st.sidebar.button('↻ Обновить', width='stretch'):
    from ozon_profit.api_ui import active_credentials, run_update
    if active_credentials():
        try:
            with st.spinner('Проверочная загрузка Ozon…'):
                run_update(db,today-timedelta(days=89),today,('fbo','fbs'))
            st.sidebar.success('API-данные обновлены для сверки. Прибыль пока рассчитывается по Excel.')
        except Exception as exc:
            from ozon_profit.seller_api import ApiError
            st.sidebar.error(str(exc) if isinstance(exc,ApiError) else 'Загрузка Ozon не завершена. Локальные данные сохранены.')
    else:
        st.sidebar.info('Расчёт из локальной базы обновлён. Для API откройте «Подключение Ozon».')
result = calculate(db, start, end)

if page == 'Обзор':
    st.subheader('Прибыль магазина')
    st.warning('Предварительный расчёт. Полнота отчётов Ozon и закрытие периода ещё не подтверждены. Импорт файла сам по себе не подтверждает прибыль.')
    cols = st.columns(3)
    for col, label, a, b in zip(cols, ['Сегодня', 'За 7 дней', 'С начала месяца'], [today, today-timedelta(days=6), today.replace(day=1)], [today]*3):
        calc = calculate(db, a, b)
        col.metric(label, show_money(calc['profit']) if calc['count'] else 'Нет данных')
    st.caption('Карточки выше используют текущую дату компьютера. Данные выбранного периода — ниже.')
    if not result['count']:
        st.info('За выбранный период нет операций. Откройте «Загрузка Excel» и выберите даты отчёта в боковой панели.')
    if result['missing'] or result['estimated']:
        st.error(f'Неполный расчёт: {result["missing"]} операций без точной базы себестоимости; {result["estimated"]} — с оценочной себестоимостью. Показанная прибыль может быть завышена.')
    if result['net_mode']:
        st.error('Загружены только итоговые начисления. В них нет данных для выручки, себестоимости и стандартной налоговой базы. Показан денежный результат начислений, а не прибыль магазина.')
    cols = st.columns(4)
    for col, title, key in zip(cols, ['Начисления после удержаний', 'Выручка продаж за вычетом возвратов', 'Известная себестоимость', 'Расчётный налог'], ['net','revenue','costs','tax']):
        col.metric(title, show_money(result[key]))
    st.metric('Предварительная прибыль / неполный результат' if result['incomplete'] else 'Предварительная прибыль', show_money(result['profit']))
    st.caption(f'Удержания без возврата выручки: {show_money(result["expenses"])}. Налоговая база: {get_setting(db,"tax_base","sales_points")}.')
    if result['revenue'] > 0:
        st.write(f'Рентабельность по выручке: {result["profit"]/result["revenue"]*100:.1f}% • Доля удержаний: {result["expenses"]/result["revenue"]*100:.1f}%')
    frame = readable(result['details'])
    if not frame.empty:
        st.subheader('Динамика результата по дате начисления')
        st.line_chart(frame.groupby('day')['profit'].sum(), y_label='₽')
        st.caption('Даты финансовых операций могут отличаться от дат продаж. График учитывает только известную себестоимость.')
        st.subheader('Результат по товарам')
        st.dataframe(frame.groupby(['sku','name'], dropna=False)[['amount','cost','tax','profit']].sum().rename(columns={'amount':'Начисления','cost':'Себестоимость','tax':'Налог','profit':'Результат'}), width='stretch')
        st.caption('Пустой артикул — общие расходы магазина; они не распределены между товарами и заказами.')
    st.subheader('Сколько можно вывести себе')
    st.caption('Это отдельная оценка по фактическим доступным деньгам, а не по начислениям Ozon. Введите остаток, уже доступный вам, исключив деньги в пути. Налоговый резерв можно уточнить с учётом уже уплаченного налога.')
    with st.form('cash'):
        cash = st.number_input('Фактически доступные деньги, ₽', min_value=0.0, value=float(get_setting(db,'cash','0')))
        tax_reserve = st.number_input('Оставить на налоги, ₽', min_value=0.0, value=float(get_setting(db,'tax_reserve',str(result['tax']))))
        materials = st.number_input('Оставить на материалы, ₽', min_value=0.0, value=float(get_setting(db,'materials','0')))
        obligations = st.number_input('Оставить на обязательные платежи, ₽', min_value=0.0, value=float(get_setting(db,'obligations','0')))
        if st.form_submit_button('Сохранить остаток и резервы'):
            for k,v in [('cash',cash),('tax_reserve',tax_reserve),('materials',materials),('obligations',obligations)]: set_setting(db,k,v)
    st.metric('Можно вывести по указанному остатку', show_money(withdrawal(cash,tax_reserve,materials,obligations)))
    st.caption('Остаток и резервы введены вручную; проверьте их перед выводом.')

elif page == 'Импорт отчётов Ozon':
    from ozon_profit.import_ui import native_import
    native_import(db)

elif page == 'Подключение Ozon':
    from ozon_profit.api_ui import connection_ui
    connection_ui(db)

elif page == 'Себестоимость':
    st.subheader('Справочник и история себестоимости')
    st.info('Используйте полную себестоимость с упаковкой. Новая дата сохраняет историю; запись для той же даты заменяет её значение. Себестоимость «Малины» по «Рыбе» — только оценка, её нужно отметить флажком.')
    with st.form('cost'):
        sku = st.text_input('Артикул Ozon')
        name = st.text_input('Название изделия')
        since = st.date_input('Действует с', value=date(2026,9,1))
        amount = st.text_input('Себестоимость за штуку, ₽ (до 3 и более знаков)', '0')
        estimated = st.checkbox('Примерная себестоимость')
        note = st.text_input('Источник / пояснение')
        if st.form_submit_button('Сохранить'):
            try: save_cost(db,sku,name,since,amount,estimated,note); st.success('Сохранено')
            except ValueError as exc: st.error(str(exc))
    st.dataframe(pd.read_sql_query('SELECT p.sku AS Артикул,p.name AS Изделие,c.since AS "Действует с",c.amount AS Себестоимость,c.estimated AS Оценка,c.note AS Примечание FROM products p JOIN costs c USING(sku) ORDER BY p.sku,c.since', db), width='stretch')
    st.subheader('Артикулы без стоимости (включая товары только с расходами)')
    unknown=pd.read_sql_query('''SELECT DISTINCT o.sku AS Артикул, o.name AS Товар
        FROM operations o WHERE o.sku<>'' AND o.day<=? AND NOT EXISTS
        (SELECT 1 FROM costs c WHERE c.sku=o.sku AND c.since<=?)''',db,params=(str(end),str(end)))
    st.dataframe(unknown,width='stretch',hide_index=True)
    st.subheader('Товары с неопределённой себестоимостью за период')
    issues = [r for r in result['details'] if r['issue']]
    st.dataframe(readable(issues).rename(columns={'issue':'Причина'}), width='stretch')

elif page == 'Загрузка Excel':
    st.subheader('Загрузка и проверка Excel')
    st.info('Для оригинальных отчётов используйте раздел «Импорт отчётов Ozon» — столбцы определяются автоматически. Здесь находится ручной импорт произвольных таблиц. Предыдущий расчёт используйте для сверки, не импортируйте его как новые операции.')
    purpose = st.radio('Что загружаем?', ['Просмотр / сверка исходного файла', 'Себестоимость', 'Финансовые операции'])
    uploaded = st.file_uploader('Excel-файл (.xlsx)', type=['xlsx'])
    if uploaded:
        content = uploaded.getvalue()
        try:
            book = workbook(content)
            name = st.selectbox('Лист', book.sheet_names)
            raw = sheet(content,name,None)
            st.caption('Первые строки листа: найдите строку с названиями столбцов. Для сложного листа «Украшения» изучите блок каждого изделия и внесите итог с упаковкой через справочник.')
            st.dataframe(raw.head(40), width='stretch')
            header = st.number_input('Номер строки заголовков в Excel', min_value=1, value=1, step=1)
            frame = sheet(content,name,int(header)-1)
            st.caption('Выберите только нужные строки. Исключите итоги, подытоги, заголовки и примечания.')
            first = st.number_input('Первая строка данных после заголовка (1 = первая)', min_value=1, value=1)
            last_row = st.number_input('Последняя строка данных после заголовка', min_value=1, value=max(1,len(frame)))
            selected = frame.iloc[int(first)-1:int(last_row)].copy()
            st.dataframe(selected.head(100), width='stretch')
            if purpose == 'Просмотр / сверка исходного файла':
                st.caption('Просмотр ничего не меняет в базе. Отчёт реализации и предыдущий расчёт нельзя складывать с финансовым отчётом: это может задвоить выручку.')
            else:
                options = ['—'] + list(frame.columns)
                def column(label, key, required=False):
                    value = st.selectbox(label + (' *' if required else ''), options, key=key)
                    return None if value == '—' else value
                if purpose == 'Себестоимость':
                    sku_col = column('Артикул','csku',True)
                    name_col = column('Название','cname',True)
                    cost_col = column('Полная себестоимость с упаковкой','ccost',True)
                    since = st.date_input('Действует с', date(2026,9,1))
                    estimated = st.checkbox('Все загружаемые значения примерные')
                    st.warning('Импорт таблицы рассчитан на одну строку на товар. Для блоков с формулами выберите итог вручную в разделе «Себестоимость». Лист «Закуп» не является себестоимостью готовых изделий.')
                    if st.button('Импортировать себестоимость'):
                        if not all([sku_col,name_col,cost_col]): raise ValueError('Выберите обязательные столбцы')
                        records = [(text_cell(r[sku_col]), text_cell(r[name_col]), money(r[cost_col])) for _,r in selected.iterrows()]
                        if not records: raise ValueError('Нет выбранных строк')
                        if len({r[0] for r in records}) != len(records): raise ValueError('Повтор артикулов: выберите одну итоговую строку на товар')
                        if any(not a or not b or c < 0 for a,b,c in records): raise ValueError('Проверьте артикулы, названия и суммы')
                        # A savepoint keeps the workbook import atomic.
                        db.execute('SAVEPOINT cost_import')
                        try:
                            for a,b,c in records:
                                db.execute('INSERT INTO products VALUES(?,?) ON CONFLICT(sku) DO UPDATE SET name=excluded.name',(a,b))
                                db.execute('INSERT OR REPLACE INTO costs VALUES(?,?,?,?,?)',(a,str(since),str(c),int(estimated),'Импорт Excel'))
                            db.execute('RELEASE cost_import'); db.commit()
                        except Exception:
                            db.execute('ROLLBACK TO cost_import'); db.execute('RELEASE cost_import'); raise
                        st.success(f'Сохранено товаров: {len(records)}')
                else:
                    mode_label = st.radio('Способ учёта', ['Детализация: выручка и каждое удержание отдельной строкой', 'Только итоговые начисления — для сверки, неполный расчёт'])
                    mode = 'net' if mode_label.startswith('Только') else 'detail'
                    st.warning('В детализации сумма продажи — ДО удержаний. Удержания и возврат выручки — отрицательные. Если в строке уже итог после услуг, не импортируйте услуги дополнительно. ID должен быть постоянным уникальным ID строки во всех повторных отчётах; одинаковый ID обновляет ранее загруженную запись.')
                    mapping = {}
                    for field,label,required in [('id','ID операции',True),('day','Дата начисления',True),('amount','Сумма со знаком',True),('order_no','Номер отправления',False),('sku','Артикул Ozon',False),('name','Название изделия',False),('qty','Количество (положительное)',False),('kind','Код типа операции (если есть)',False)]:
                        mapping[field] = column(label,field,required)
                    kind = 'net' if mode == 'net' else st.selectbox('Тип для всех строк без столбца типа', list(k for k in KINDS if k!='net'), format_func=KINDS.get)
                    st.caption('Коды типов: ' + ' • '.join(f'{k}: {v}' for k,v in KINDS.items()))
                    if all(mapping[k] for k in ['id','day','amount']):
                        rows = operation_rows(selected,mapping,kind)
                        preview = pd.DataFrame(rows)
                        allowed = ['net'] if mode == 'net' else [k for k in KINDS if k != 'net']
                        preview['kind'] = preview['kind'].map(lambda value: KINDS.get(value, value))
                        # Editable review lets a non-programmer classify Russian report labels.
                        st.caption('Проверьте подготовленные строки. В столбце «Тип операции» выберите назначение каждой строки. Можно исправить ID, сумму, количество и артикул до сохранения. Неизвестные типы обязательно замените.')
                        for field in ['amount','qty']:
                            preview[field] = preview[field].map(str)
                        edited = st.data_editor(preview, hide_index=True, width='stretch',
                            column_config={
                                'id':st.column_config.TextColumn('Уникальный ID'),
                                'day':st.column_config.TextColumn('Дата ГГГГ-ММ-ДД'),
                                'order_no':st.column_config.TextColumn('Отправление'),
                                'sku':st.column_config.TextColumn('Артикул'),
                                'name':st.column_config.TextColumn('Изделие'),
                                'amount':st.column_config.TextColumn('Сумма со знаком'),
                                'qty':st.column_config.TextColumn('Количество'),
                                'kind':st.column_config.SelectboxColumn('Тип операции',options=[KINDS[k] for k in allowed],required=True),
                            })
                        confirmed = st.checkbox('Проверила строки, знаки сумм, типы и отсутствие итогов/повторных удержаний')
                        if st.button('Проверить и импортировать', disabled=not confirmed):
                            reverse = {v:k for k,v in KINDS.items()}
                            rows = edited.to_dict('records')
                            for row in rows:
                                row['kind'] = reverse.get(row['kind'],row['kind'])
                            count = import_operations(db,content,uploaded.name,rows,mode)
                            st.success(f'Сохранено операций: {count}' if count else 'Этот файл уже загружен. Повторный импорт пропущен.')
                    else:
                        st.caption('Выберите ID, дату и сумму, чтобы подготовить строки для проверки.')
        except Exception as exc:
            st.error(f'Импорт не завершён: {exc}')

elif page == 'Заказы и операции':
    st.subheader('Операции выбранного периода')
    frame = readable(result['details'])
    order = st.text_input('Номер отправления (точное совпадение)')
    if not frame.empty:
        if order: frame = frame[frame.order_no == order.strip()]
        st.dataframe(frame.rename(columns={'day':'Дата начисления','order_no':'Отправление','sku':'Артикул','name':'Изделие','kind':'Тип','amount':'Начисление','cost':'Себестоимость','tax':'Налог','profit':'Предварительный результат','issue':'Требует внимания'}), width='stretch')
        st.caption('Результат отправления включает только операции, связанные с ним. Общие услуги магазина остаются нераспределёнными. Налог округляется по итогу периода; при отрицательной базе резерв ограничен нулём.')
        st.download_button('Скачать операции CSV', frame.map(lambda value: "'" + value if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@')) else value).to_csv(index=False).encode('utf-8-sig'), 'operations.csv', 'text/csv')
    else: st.info('Операций пока нет')

elif page == 'Настройки':
    st.subheader('Налоги')
    st.warning('НПД 4% — исходная настройка, а не проверка вашего налогового статуса. База обычно не равна выплате после комиссий. Проверьте правила своего режима, компенсаций и баллов с бухгалтером.')
    with st.form('settings'):
        rate = st.number_input('Ставка, %',0.0,100.0,float(get_setting(db,'tax_rate','4')))
        bases = {'sales_points':'Продажи минус возвраты + баллы за скидки','sales':'Продажи минус возвраты','sales_partner':'Продажи минус возвраты + программы партнёров','sales_points_partner':'Продажи минус возвраты + баллы + программы партнёров','net':'Начисления после удержаний (пользовательский вариант)'}
        current = get_setting(db,'tax_base','sales_points')
        base = st.selectbox('Налоговая база', list(bases), index=list(bases).index(current), format_func=bases.get)
        if st.form_submit_button('Сохранить настройки'):
            set_setting(db,'tax_rate',rate); set_setting(db,'tax_base',base); st.success('Сохранено. Периоды будут пересчитаны.')
    st.subheader('Подключение Ozon')
    st.info('Раздел «Подключение Ozon» содержит экспериментальный клиент API для проверочной загрузки. Актуальные методы и доступ без Premium пока не проверены на реальном соединении. API-данные пока не участвуют в расчёте прибыли. Не присылайте ключ в переписку.')
    st.subheader('Резервная копия')
    st.caption('Копия содержит финансовые данные и историю стоимости. Храните её у себя; не загружайте на GitHub. API-ключи в копию не включаются.')
    if st.button('Подготовить резервную копию базы'):
        import sqlite3
        snapshot=sqlite3.connect(':memory:')
        try:
            db.backup(snapshot)
            st.download_button('Скачать резервную копию',snapshot.serialize(),'OzonProfit-backup.db','application/octet-stream')
        finally:
            snapshot.close()
    st.subheader('Локальные данные')
    st.code(str(__import__('ozon_profit.core',fromlist=['data_dir']).data_dir()))
    st.caption('Для резервной копии закройте приложение и скопируйте profit.db из этой папки. База содержит финансовые данные: храните её в личной папке, защищённой учётной записью Windows и шифрованием диска. Не загружайте её на GitHub.')

else:
    st.markdown('''
### Первый запуск
1. Откройте «Импорт отчётов Ozon»: выберите три оригинальных файла, проверьте предложенную себестоимость и налоговую базу. Для других форматов в «Себестоимость» добавьте артикулы, полную себестоимость с упаковкой и дату начала действия. Стоимость «Малины» по «Рыбе» — примерная оценка, отметьте её как примерную.
2. В «Загрузка Excel» сначала просмотрите каждый файл. Выберите нужный лист и строку заголовков.
3. Загрузите финансовые операции. Отчёт реализации помогает проверить количество и артикулы; предыдущая сверка служит ориентиром, а не дополнительной выручкой.
4. Выберите период отчёта. Сравните показатели с предыдущей сверкой; совпадение арифметики не подтверждает окончательную прибыль.
5. Проверьте неизвестную себестоимость, налоги и нераспределённые расходы. Введите фактический остаток денег и резервы.

### Как считается результат
Начисления — сумма финансовых операций со знаком. Предварительная прибыль = начисления − себестоимость отгруженных проданных изделий − расчётный налог. Реклама, уже включённая в удержания Ozon, повторно не вычитается. Возврат выручки уменьшает начисления и налоговую базу; себестоимость возвращается в запас только отдельной нулевой операцией `restock`, после фактического получения пригодного изделия. Для неё нужны исходные номер отправления и артикул.

Расчёт относится к датам начислений, а не к когортам заказов. Задержанные операции и исправления с прежними ID пересчитывают прошлые даты. Импорт добавляет/обновляет строки, но не удаляет отсутствующие: отменённые операции нужно отражать явной корректировкой.

Подтверждённая прибыль пока не рассчитывается: для неё нужна проверка полноты отчётов, закрытия начислений и сопоставления заказов. При неизвестной себестоимости показанный результат неполный. Прибыль по заказу без общих расходов не равна доле чистой прибыли магазина.

### Ограничения первой версии
В разделе «Импорт отчётов Ozon» распознаются оригинальные отчёты и итоги «С упаковкой» листа «Украшения». Для других форматов доступен ручной импорт. Экспериментальная загрузка API находится в разделе «Подключение Ozon»; её данные пока не участвуют в прибыли. Нет сверки банковских выплат и готовой проверенной Windows-сборки. Файлы исходных отчётов в базу не копируются; сохраняются выбранные операции и история себестоимости.
''')
db.close()


@st.fragment(run_every=60)
def scheduled_download():
    from ozon_profit.api_ui import active_credentials, run_update
    from ozon_profit.seller_api import ApiError
    from ozon_profit.core import connect
    import time
    from datetime import datetime, timezone
    timer_db=connect()
    try:
        interval=int(get_setting(timer_db,'api_interval','0'))
        on_start=get_setting(timer_db,'api_on_start','0')=='1'
        pair=active_credentials()
        last_attempt=st.session_state.get('_api_last_attempt')
        due=(last_attempt is None and (on_start or interval>0)) or (last_attempt is not None and interval>0 and time.monotonic()-last_attempt>=interval*60)
        if due and pair:
            st.session_state['_api_last_attempt']=time.monotonic()
            now=datetime.now(timezone(timedelta(hours=3))).date()
            try:
                run_update(timer_db,now-timedelta(days=89),now,('fbo','fbs'))
                st.caption('Проверочные API-данные обновлены автоматически. Прибыль из Excel не изменена.')
            except ApiError as exc:
                st.caption('Автообновление: '+str(exc))
            except Exception:
                st.caption('Автообновление не завершено; сохранённые данные доступны.')
    finally:
        timer_db.close()

scheduled_download()
