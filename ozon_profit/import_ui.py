"""Native report import UI, separated so the adapter remains independently testable."""
from datetime import date
import pandas as pd
import streamlit as st
from .core import money, rub, get_setting
from .ozon_excel import parse_accruals, parse_costs, parse_sales, reconcile, suggested_costs, apply_report, parse_previous, preview_calculation


def native_import(db):
    st.subheader('Загрузить оригинальные отчёты Ozon')
    st.write('Выберите финансовый отчёт, таблицу себестоимости и отчёт реализации. Столбцы распознаются автоматически. Предыдущая сверка служит сравнением, повторно в доходы не импортируется.')
    accrual_file=st.file_uploader('1. Отчёт по начислениям Ozon',type=['xlsx'],key='native_acc')
    cost_file=st.file_uploader('2. Себестоимость.xlsx, лист «Украшения»',type=['xlsx'],key='native_cost')
    sale_file=st.file_uploader('3. Отчёт о реализации',type=['xlsx'],key='native_sale')
    previous_file=st.file_uploader('4. Предыдущий расчёт (необязательно, только для сравнения)',type=['xlsx'],key='native_previous')
    if not accrual_file or not cost_file or not sale_file:
        st.info('После выбора трёх файлов появится сверка и готовое сопоставление товаров.')
        return
    try:
        content=accrual_file.getvalue()
        report=parse_accruals(content)
        costs=parse_costs(cost_file.getvalue())
        sales=parse_sales(sale_file.getvalue())
        differences=reconcile(report,sales)
        st.write(f'Период: {report.start:%d.%m.%Y}–{report.end:%d.%m.%Y}. Финансовых строк: {report.raw_count}. После объединения одинаковых услуг за день: {len(report.rows)}.')
        st.metric('Начисления после всех удержаний', f'{rub(report.net):,.2f} ₽'.replace(',',' '))
        st.dataframe(pd.DataFrame([{'Категория':k,'Сумма, ₽':float(v)} for k,v in report.categories.items()]),hide_index=True,width='stretch')
        if differences:
            st.error('Реализация, количество или партнёрские выплаты не совпали. Импорт остановлен до разбора расхождений.')
            st.dataframe(pd.DataFrame(differences),width='stretch')
            return
        st.success('Реализация, количество и программы партнёров совпали между двумя отчётами по каждому артикулу. Это сверка отчётов, а не подтверждение окончательной прибыли.')
        st.subheader('Проверьте себестоимость товаров')
        st.caption('Для «Наличника» выбран синий вариант, подтверждённый вами; вариант из смолы на Ozon не продаётся. Обе «Малины» оценены по «Рыбе» и останутся помеченными как примерные. Товар без продаж может остаться без себестоимости.')
        proposed=suggested_costs(report.products,costs)
        edited=st.data_editor(pd.DataFrame(proposed),hide_index=True,width='stretch',
            disabled=['sku','name'],column_config={
                'sku':st.column_config.TextColumn('Артикул'),
                'name':st.column_config.TextColumn('Товар Ozon'),
                'cost_name':st.column_config.SelectboxColumn('Изделие в себестоимости',options=['']+[r['name'] for r in costs]),
                'estimated':st.column_config.CheckboxColumn('Оценка / вариант не подтверждён'),
                'note':st.column_config.TextColumn('Примечание'),
            })
        by_name={r['name']:r['amount'] for r in costs}
        records=[]; unresolved=[]
        sold={r['sku'] for r in report.rows if r['kind']=='sale'}
        for record in edited.to_dict('records'):
            if record['cost_name'] not in by_name:
                if record['sku'] in sold: unresolved.append(record['sku'])
                continue
            # An alias to another product cannot silently become an exact cost.
            estimated=bool(record['estimated']) or ('малина' in record['sku'].lower() and record['cost_name']=='Подвеска Рыба')
            records.append(dict(sku=record['sku'],name=record['name'],amount=by_name[record['cost_name']],estimated=estimated,note=record['note']))
        if unresolved:st.warning('Нет стоимости для проданных товаров: '+', '.join(unresolved)+'. Прибыль будет неполной.')
        since=st.date_input('Эта себестоимость действует с',value=report.start,key='native_since')
        bases={
            'sales':'Только реализация (как в предыдущей сверке)',
            'sales_partner':'Реализация + программы партнёров',
            'sales_points':'Реализация + баллы за скидки',
            'sales_points_partner':'Реализация + баллы + программы партнёров',
        }
        tax_base=st.selectbox('Предварительная база НПД',list(bases),format_func=bases.get,key='native_tax_base')
        st.caption('Налоговая трактовка баллов и партнёрских выплат должна быть проверена отдельно. Начисления учитывают их один раз независимо от выбора налоговой базы. Ставка берётся из настроек (исходно 4%).')
        preview=preview_calculation(content,report,records,since,tax_base,get_setting(db,'tax_rate','4'))
        st.subheader('Результат выбранных допущений до загрузки')
        columns=st.columns(3)
        for col,label,key in zip(columns,['Себестоимость с оценками','Налоговый резерв','Предварительная прибыль'],['costs','tax','profit']):
            col.metric(label,f'{rub(preview[key]):,.2f} ₽'.replace(',',' '))
        if preview['missing'] or preview['estimated']:
            st.warning(f'Неизвестная стоимость: {preview["missing"]} продаж; оценочная / неподтверждённая: {preview["estimated"]}. Это предварительный расчёт.')
        if previous_file:
            try:
                previous=parse_previous(previous_file.getvalue())
                labels={'revenue':'Реализация','net':'Начисления','costs':'Себестоимость','tax':'Налог','profit':'Прибыль'}
                comparison=[{'Показатель':labels[k],'Сейчас, ₽':float(rub(preview[k])),'Предыдущий расчёт, ₽':float(rub(v)),'Разница, ₽':float(rub(preview[k])-rub(v))} for k,v in previous.items()]
                st.dataframe(pd.DataFrame(comparison),hide_index=True,width='stretch')
                st.caption('Предыдущая сверка не добавляется к операциям и не считается эталоном. Проверяем совпадение арифметики при выбранных допущениях.')
            except ValueError as exc:
                st.warning(f'Предыдущая сверка не распознана: {exc}. Три исходных отчёта можно импортировать без неё.')
        st.warning('Загрузите полный финансовый отчёт за указанный период. Повторная выгрузка заменит ранее импортированные этим способом финансовые строки периода, включая исчезнувшие операции. Записи ручного импорта в этом периоде блокируют автоимпорт. История себестоимости сохраняется; запись на ту же дату заменяется выбранным значением.')
        confirmed=st.checkbox('Я проверила период, сопоставление и налоговую базу; финансовый отчёт полный',key='native_confirm')
        if st.button('Загрузить отчёты и пересчитать',disabled=not confirmed,key='native_submit'):
            count=apply_report(db,content,accrual_file.name,report,records,since,tax_base)
            if count:
                st.success(f'Импорт завершён: {count} финансовых записей. Откройте «Обзор» и выберите период отчёта.')
            else:
                st.info('Этот финансовый файл уже загружен. Повторная загрузка не меняет данные и настройки; стоимость можно изменить в справочнике, налоговую базу — в настройках.')
    except Exception as exc:
        st.error(f'Отчёты не загружены: {exc}')
