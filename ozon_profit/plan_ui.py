from datetime import date,timedelta
import json
import pandas as pd
import streamlit as st
from .core import money,get_setting,rub
from .planning import forecast,history
from .presentation import cash
from .ui_pages import today


def plan_page(db):
    st.header('План продаж')
    st.caption('Выберите товары и количество. План сохраняется автоматически и не меняет фактическую прибыль.')
    stored=db.execute('SELECT payload FROM sales_plans WHERE id=1').fetchone()
    from .dashboard import latest_period
    initial_start,baseline_end=latest_period(db,today())
    state=json.loads(stored[0]) if stored else {'start':str(today()),'end':str(today()+timedelta(days=29)),
        'baseline_start':str(initial_start),'baseline_end':str(baseline_end),'ad_budget':'0','order_ad_percent':6,'extra_costs':'0','items':[]}
    def save():
        data=json.dumps(state,ensure_ascii=False,sort_keys=True)
        current=db.execute('SELECT payload FROM sales_plans WHERE id=1').fetchone()
        if not current or current[0]!=data:
            with db:db.execute('INSERT OR REPLACE INTO sales_plans VALUES(1,?)',(data,))
    st.subheader('Срок плана')
    c1,c2=st.columns(2)
    start=c1.date_input('Начало плана',date.fromisoformat(state['start']),key='plan_start',format='DD.MM.YYYY')
    end=c2.date_input('Окончание плана',date.fromisoformat(state['end']),key='plan_end',format='DD.MM.YYYY')
    state['start']=str(start);state['end']=str(end)
    with st.expander('Откуда брать средние цены и удержания',expanded=True):
        a,b=st.columns(2)
        hs=a.date_input('История с',date.fromisoformat(state['baseline_start']),key='plan_history_start',format='DD.MM.YYYY')
        he=b.date_input('История по',date.fromisoformat(state['baseline_end']),key='plan_history_end',format='DD.MM.YYYY')
        state['baseline_start']=str(hs);state['baseline_end']=str(he)
        st.caption('Комиссия берётся в процентах от фактической оплаты покупателей; логистика и остальные услуги — в среднем на проданную штуку. Рекламные расходы из этих средних исключены.')
        coinvest=st.checkbox('Учитывать средний соинвест Ozon из истории',value=bool(state.get('include_coinvest',True)),key='plan_coinvest')
        state['include_coinvest']=coinvest
        st.caption('Цена в плане — сколько покупатель платит после скидок. Баллы и партнёрские выплаты Ozon прибавляются отдельно, только если вы ожидаете сохранения этих условий. Разовые компенсации в план не переносятся.')
    if he<hs or end<start:st.error('Проверьте порядок дат');return
    hist=history(db,hs,he)
    if hist['issues']:
        st.warning('Не удалось получить средние показатели за выбранные даты. Выберите полностью загруженный период или проверьте операции ниже.')
        st.dataframe(pd.DataFrame({'Что проверить':hist['issues']}),hide_index=True,width='stretch')
        save();return
    if hist['revenue']<=0:st.info('В выбранной истории нет продаж. Выберите другой период в блоке выше.')
    catalog={r['sku']:r['name'] for r in db.execute('SELECT sku,name FROM products ORDER BY name')}
    if not catalog:
        st.info('Сначала подключите магазин или добавьте изделия в разделе «Товары».');save();return
    with st.form('add_plan_item'):
        c1,c2=st.columns([3,1])
        sku=c1.selectbox('Товар',list(catalog),format_func=lambda v:catalog[v],key='plan_product')
        qty=c2.number_input('Количество, шт.',1,100000,50,step=1,key='plan_quantity')
        with st.expander('Цена для товара без истории · необязательно'):
            price=st.number_input('Оплата покупателя после скидок, ₽ · 0 = средняя',min_value=0.0,value=0.0,key='plan_price')
            st.caption('При наличии продаж цена подставится автоматически. Для товара без продаж укажите ожидаемую цену.')
        if st.form_submit_button('Добавить в план',type='primary'):
            existing=next((r for r in state['items'] if r['sku']==sku),None)
            if existing:
                existing['quantity']+=int(qty)
                if price:existing['price']=str(price)
            else:state['items'].append({'sku':sku,'quantity':int(qty),'price':str(price) if price else None})
            save();st.rerun()
    if state['items']:
        lines=[]
        for item in state['items']:
            own=hist['products'].get(item['sku'],{})
            average=own.get('revenue',money(0))/own['units'] if own.get('units',0)>0 else money(0)
            lines.append({'sku':item['sku'],'name':catalog.get(item['sku'],item['sku']),'quantity':item['quantity'],
                          'price':float(money(item['price'])) if item.get('price') else float(average),'remove':False})
        edited=st.data_editor(pd.DataFrame(lines),hide_index=True,width='stretch',key='plan_lines',disabled=['sku','name'],column_config={
            'sku':st.column_config.TextColumn('Артикул'),'name':st.column_config.TextColumn('Изделие'),
            'quantity':st.column_config.NumberColumn('Штук',min_value=1,max_value=100000,step=1),
            'price':st.column_config.NumberColumn('Оплата после скидок, ₽',min_value=0.0,step=1.0),
            'remove':st.column_config.CheckboxColumn('Убрать')})
        updated=[]
        for old,new in zip(state['items'],edited.to_dict('records')):
            if new['remove']:continue
            old_price=next(r['price'] for r in lines if r['sku']==old['sku'])
            effective=old.get('price') if new['price']==old_price else str(new['price'])
            try:
                quantity=int(new['quantity'])
                if quantity<=0:raise ValueError()
            except (ValueError,TypeError):
                st.warning('Укажите положительное целое количество в каждой строке плана.');return
            updated.append({'sku':old['sku'],'quantity':quantity,'price':effective})
        state['items']=updated
    st.subheader('Реклама')
    st.caption(f'В выбранной истории всего рекламы: {cash(hist["advertising"])}; ДРР магазина: {hist["ad_share"]:.1f}%. Это справка: в план эти расходы повторно не добавляются.'.replace('.',','))
    budget=st.number_input('Бюджет рекламы с оплатой за клик на весь период, ₽',min_value=0.0,value=float(state.get('ad_budget','0')),step=500.0,key='plan_ad_budget')
    order_rate=st.selectbox('Продвижение с оплатой за заказ',[0,6,8,10],index=[0,6,8,10].index(int(state.get('order_ad_percent',6))),format_func=lambda value:f'{value}% от продаж' if value else 'Отключено — 0%',key='plan_order_rate')
    state['ad_budget']=str(budget);state['order_ad_percent']=order_rate
    st.caption('Бюджет за клики учитывается один раз на весь план. Оплата за заказы добавляется отдельно: выбранный процент от плановой реализации покупателям. Историческая реклама уже исключена из средних удержаний.')
    with st.expander('Другие разовые расходы · необязательно'):
        st.caption('Например, новая фотосъёмка или платное оформление магазина. Оставьте 0, если таких расходов нет. Не включайте сюда рекламу, себестоимость и удержания Ozon: они уже учтены.')
        extra=st.number_input('Расходы сверх учтённых средних, ₽',min_value=0.0,value=float(state['extra_costs']),key='plan_extra_costs')
        state['extra_costs']=str(extra)
    save()
    if not state['items']:st.info('Добавьте первый товар, чтобы увидеть прогноз.');return
    try:result=forecast(db,state['items'],start,end,hs,he,extra_costs=extra,ad_budget=budget,order_ad_percent=order_rate,include_coinvest=coinvest)
    except ValueError as exc:st.error(str(exc));return
    if result['issues']:
        for issue in result['issues']:st.warning(issue)
        st.metric('Прогноз чистой прибыли','Заполните данные')
        return
    total=result['totals']
    c1,c2,c3=st.columns([2,1,1])
    c1.metric('Прогноз чистой прибыли',cash(total['profit']))
    c2.metric('План продаж',f'{int(total["quantity"])} шт.')
    c3.metric('Нужно продавать в день',f'{result["daily_quantity"]:.1f} шт.'.replace('.',','))
    c1,c2,c3=st.columns(3)
    c1.metric('Реклама за клики',cash(total['click_advertising']))
    c2.metric('Продвижение за заказы',cash(total['order_advertising']))
    c3.metric('Всего рекламы · ДРР '+f'{result["planned_ad_share"]:.1f}%',cash(total['advertising']))
    st.subheader('Полный расчёт прибыли')
    bridge=[('Реализация покупателям',total['revenue']),('Соинвест Ozon',total['points']+total['partner']),
        ('Комиссия Ozon',-total['commission']),('Логистика',-total['logistics']),('Прочие услуги',-total['services']),
        ('Себестоимость',-total['costs']),('Реклама за клики',-total['click_advertising']),('Продвижение за заказы',-total['order_advertising']),
        ('Налоги ('+format(result['tax_rate'].normalize(),'f')+'%)',-total['tax']),('Другие расходы',-total['extra_costs']),('Чистая прибыль',total['profit'])]
    st.dataframe(pd.DataFrame([{'Статья':name,'Сумма, ₽':float(rub(value))} for name,value in bridge]),hide_index=True,width='stretch',column_config={'Сумма, ₽':st.column_config.NumberColumn(format='%.2f')})
    detail=pd.DataFrame([{'Изделие':r['name'],'Штук':int(r['quantity']),'Реализация, ₽':float(rub(r['revenue'])),
        'Соинвест, ₽':float(rub(r['points']+r['partner'])),'Удержания Ozon, ₽':float(rub(r['commission']+r['logistics']+r['services'])),
        'Себестоимость, ₽':float(rub(r['costs'])),'Реклама, ₽':float(rub(r['advertising'])),'Налоги, ₽':float(rub(r['tax'])),
        'Другие расходы, ₽':float(rub(r['extra_costs'])),'Прогноз прибыли, ₽':float(rub(r['profit'])),'Прибыль на штуку, ₽':float(rub(r['profit']/r['quantity']))} for r in result['rows']])
    st.dataframe(detail,hide_index=True,width='stretch',column_config={name:st.column_config.NumberColumn(format='%.2f') for name in detail.columns if '₽' in name})
    with st.expander('Из чего состоят удержания по каждому товару',expanded=True):
        st.caption('Доля комиссии — фактически удержанная комиссия ÷ реализация покупателям за выбранную историю. Это эффективная доля, а не опубликованный тариф Ozon.')
        rates=pd.DataFrame([{'Изделие':r['name'],'Источник средних':r['history_source'],
            'Комиссия от реализации, %':float(r['commission_percent']),'Комиссия, ₽':float(rub(r['commission'])),
            'Логистика на штуку, ₽':float(rub(r['logistics_per_unit'])),'Логистика всего, ₽':float(rub(r['logistics'])),
            'Прочие услуги на штуку, ₽':float(rub(r['services_per_unit'])),'Прочие услуги всего, ₽':float(rub(r['services']))} for r in result['rows']])
        st.dataframe(rates,hide_index=True,width='stretch',column_config={name:st.column_config.NumberColumn(format='%.2f') for name in rates.columns if '₽' in name or '%' in name})
    st.caption('Комиссия = плановая реализация × историческая доля комиссии. Логистика и услуги = средний расход на штуку × количество. Общие услуги магазина уже включены; реклама и налоги вычтены отдельно один раз.')
    if coinvest and total['points']+total['partner']!=0:
        st.info('В прогнозе предполагается '+cash(total['points']+total['partner'])+' соинвеста. Без этих выплат при тех же удержаниях прибыль составила бы '+cash(result['profit_without_coinvest'])+'.')
    if result['planned_ad_share']+money('0.5')<result['base_ad_share']:
        st.warning(f'Плановый ДРР {result["planned_ad_share"]:.2f}% ниже исторического {result["base_ad_share"]:.2f}%. Прогноз предполагает те же продажи при меньшей рекламной нагрузке; заданный бюджет сам по себе не гарантирует такой объём.')
    if order_rate==0:st.caption('В плане выбрано 0% за заказы: этот расход не вычитается. Если продвижение остаётся включённым, выберите его ставку выше.')
    with st.expander('Сравнить с прежним ДРР'):
        st.caption(f'Исторический ДРР: {result["base_ad_share"]:.2f}%. Это сравнение при тех же ценах, количестве и остальных расходах, но с прежней рекламной долей.')
        a,b=st.columns(2);a.metric('Прибыль при историческом ДРР',cash(total['baseline_profit']))
        b.metric('На штуку при историческом ДРР',cash(total['baseline_profit']/total['quantity']))
    st.caption('Бюджет за клики и разовые расходы распределены по плановой выручке. Сумма прибыли строк совпадает с итогом плана до округления отображения.')
    if result['estimated']:st.info('Оценочные данные: '+ '; '.join(result['estimated']))
    st.caption('План — сценарий, а не обещание спроса. Срок определяет темп продаж; итог не умножается на число дней. Ставки и средние расходы оценены по выбранной истории; стоимость берётся на начало плана. Соинвест — условие сценария, а не обещание Ozon. Все расходы учтены в строках и общем итоге без повторного вычитания.')
