from datetime import date,timedelta
import json
import pandas as pd
import streamlit as st
from .core import money,get_setting
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
        st.caption('Средние удержания и возмещения берутся из фактических операций. Разовые компенсации и корректировки в прогноз не переносятся.')
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
            price=st.number_input('Цена покупателю, ₽ · 0 = средняя',min_value=0.0,value=0.0,key='plan_price')
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
            'price':st.column_config.NumberColumn('Цена покупателю, ₽',min_value=0.0,step=1.0),
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
    try:result=forecast(db,state['items'],start,end,hs,he,extra_costs=extra,ad_budget=budget,order_ad_percent=order_rate)
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
    detail=pd.DataFrame([{'Изделие':r['name'],'Штук':int(r['quantity']),'Реализация, ₽':float(r['revenue']),
        'Себестоимость, ₽':float(r['costs']),'Реклама, ₽':float(r['advertising']),'Прогноз прибыли, ₽':float(r['profit']),'Прибыль на штуку, ₽':float(r['profit']/r['quantity'])} for r in result['rows']])
    st.dataframe(detail,hide_index=True,width='stretch')
    st.caption('Бюджет за клики вычитается целиком из прибыли добавленных товаров. Для строк таблицы он распределён пропорционально плановой выручке.')
    if result['estimated']:st.info('Оценочные данные: '+ '; '.join(result['estimated']))
    st.caption('План — сценарий, а не обещание спроса. Срок определяет темп продаж; итог не умножается на число дней. Удержания, баллы и общие услуги оценены по истории; стоимость берётся на начало плана. Реклама вычитается один раз. Дополнительные расходы плана учтены только в общем итоге.')
