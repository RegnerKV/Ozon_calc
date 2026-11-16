"""Order composition, independent of delivered sales and the financial ledger."""
import json
from datetime import datetime, timedelta, timezone
from collections import defaultdict

MOSCOW=timezone(timedelta(hours=3))


def summary(db,start,end):
    missing=dict(total=None,cancelled=None,products={},reason='Нет полной загрузки заказов за выбранный период.')
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='api_order_snapshots'").fetchone():return missing
    row=db.execute('SELECT payload FROM api_order_snapshots WHERE start<=? AND end>=? ORDER BY id DESC LIMIT 1',(str(start),str(end))).fetchone()
    if not row:return missing
    total=cancelled=0;products=defaultdict(lambda:dict(total=0,cancelled=0))
    postings=json.loads(row[0])
    numbers={p['posting_number'] for p in postings}
    if any(p.get('parent_posting_number') in numbers for p in postings):
        return {**missing,'reason':'В загрузке есть исходные и разделённые отправления. Нужно сверить состав, чтобы не задвоить заказанные единицы.'}
    for posting in postings:
        try:
            stamp=datetime.fromisoformat(posting['order_created_at'].replace('Z','+00:00'))
            if stamp.tzinfo is None:raise ValueError()
            day=stamp.astimezone(MOSCOW).date().isoformat()
        except (KeyError,TypeError,ValueError,AttributeError):
            return {**missing,'reason':'Не у всех отправлений есть согласованная дата учёта (создание FBO / начало обработки FBS).'}
        if not str(start)<=day<=str(end):continue
        if not posting.get('products') or not posting.get('status'):return missing
        for product in posting['products']:
            qty=product.get('quantity');article=product.get('offer_id')
            if not isinstance(qty,int) or isinstance(qty,bool) or qty<=0 or not article:return missing
            total+=qty;products[article]['total']+=qty
            if posting['status']=='cancelled':
                cancelled+=qty;products[article]['cancelled']+=qty
    return dict(total=total,cancelled=cancelled,products=dict(products),reason='')


def render(db,start,end):
    import streamlit as st
    result=summary(db,start,end)
    a,b=st.columns(2)
    a.metric('Заказано, шт.',result['total'] if result['total'] is not None else 'Нет данных')
    b.metric('Из них отменено, шт.',result['cancelled'] if result['cancelled'] is not None else 'Нет данных')
    st.caption('Единицы товара по дате создания отправления FBO / начала обработки FBS (Москва), включая недоставленные и отменённые. Отменённые показаны отдельно и входят в общее количество. Продажи и возвраты считаются отдельно по финансовым датам.')
    if result['reason']:st.info(result['reason'])
    if result['products']:
        st.dataframe([{'Артикул':sku,'Заказано, шт.':p['total'],'Из них отменено, шт.':p['cancelled']} for sku,p in sorted(result['products'].items())],hide_index=True,width='stretch')
