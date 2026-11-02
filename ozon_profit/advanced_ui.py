"""Manual legacy import, available only in advanced settings."""
from datetime import date
import pandas as pd
import streamlit as st
from .core import money,save_cost,import_operations,KINDS
from .excel import workbook,sheet,operation_rows,text_cell

def manual_import(db):
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
