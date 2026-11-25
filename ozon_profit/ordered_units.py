"""Order composition, independent of delivered sales and the financial ledger."""
import json
from datetime import datetime, timedelta, timezone
from collections import Counter,defaultdict
from .core import get_setting

MOSCOW=timezone(timedelta(hours=3))
SPLIT_STATUS='cancelled_from_split_pending'


def _composition(posting):
    if not posting.get('products') or not posting.get('status'):raise ValueError('Нет состава отправления.')
    result=Counter()
    for product in posting['products']:
        qty=product.get('quantity');article=product.get('offer_id')
        if not isinstance(qty,int) or isinstance(qty,bool) or qty<=0 or not article:
            raise ValueError('Не получен корректный состав отправления.')
        result[article]+=qty
    return result


def _resolve(number,postings,children,path):
    if number in path or len(path)>100:raise ValueError('Некорректные связи разделённых отправлений.')
    parent=postings[number];basis=_composition(parent)
    descendants=children.get(number,[])
    if not descendants:
        cancelled={sku:(None if parent['status']==SPLIT_STATUS else qty if parent['status']=='cancelled' else 0) for sku,qty in basis.items()}
        return basis,cancelled
    combined=Counter();cancelled={sku:0 for sku in basis}
    for child in descendants:
        counts,refunds=_resolve(child,postings,children,path|{number})
        combined.update(counts)
        for sku,value in refunds.items():
            old=cancelled.get(sku,0)
            cancelled[sku]=old+value if old is not None and value is not None else None
    # The original composition is counted once. Children describe its outcomes,
    # not new ordered units. Never infer missing cancellations from a remainder.
    if any(qty>basis[sku] for sku,qty in combined.items()):
        raise ValueError('Состав частей противоречит исходному отправлению. Нажмите «Обновить».')
    if combined!=basis:return basis,{sku:None for sku in basis}
    return basis,cancelled


def summary(db,start,end):
    missing=dict(total=None,cancelled=None,products={},reason='Нет полной загрузки заказов за выбранный период. Нажмите «Обновить».',schemes=[])
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='api_order_snapshots'").fetchone():return missing
    has_scope=any(r[1]=='schemes' for r in db.execute('PRAGMA table_info(api_order_snapshots)'))
    scope_column='schemes' if has_scope else "'fbo,fbs'"
    row=db.execute('SELECT payload,'+scope_column+' FROM api_order_snapshots WHERE start<=? AND end>=? ORDER BY id DESC LIMIT 1',(str(start),str(end))).fetchone()
    if not row:return missing
    available=set(row[1].split(','))
    selected=set(json.loads(get_setting(db,'api_schemes','["fbo","fbs"]')))
    if not selected or not selected<=available:
        return {**missing,'reason':'Нет полной загрузки для выбранных схем работы. Нажмите «Обновить» после изменения схем в настройках.'}
    postings={}
    for p in json.loads(row[0]):
        if p.get('schema') and p['schema'] not in selected:continue
        number=p['posting_number']
        if number in postings:return {**missing,'reason':'Повтор отправления в сохранённой загрузке. Нажмите «Обновить».'}
        postings[number]=p
    children=defaultdict(list)
    for number,p in postings.items():
        parent=p.get('parent_posting_number')
        if parent in postings:children[parent].append(number)
    roots=[n for n,p in postings.items() if p.get('parent_posting_number') not in postings]
    reached=set();pending=list(roots)
    while pending:
        number=pending.pop()
        if number in reached:return {**missing,'reason':'Некорректные связи разделённых отправлений.'}
        reached.add(number);pending.extend(children.get(number,[]))
    if reached!=set(postings):return {**missing,'reason':'Некорректные связи разделённых отправлений.'}
    products=defaultdict(lambda:dict(total=0,cancelled=0))
    for number in roots:
        posting=postings[number]
        try:
            stamp=datetime.fromisoformat(posting['order_created_at'].replace('Z','+00:00'))
            if stamp.tzinfo is None:raise ValueError()
            day=stamp.astimezone(MOSCOW).date().isoformat()
        except (KeyError,TypeError,ValueError,AttributeError):
            return {**missing,'reason':'Не у всех отправлений есть согласованная дата учёта (создание FBO / начало обработки FBS).'}
        if not str(start)<=day<=str(end):continue
        try:counts,cancelled=_resolve(number,postings,children,set())
        except ValueError as exc:return {**missing,'reason':str(exc)}
        for sku,qty in counts.items():
            products[sku]['total']+=qty
            old=products[sku]['cancelled'];value=cancelled[sku]
            products[sku]['cancelled']=old+value if old is not None and value is not None else None
    total=sum(p['total'] for p in products.values())
    cancelled=sum(p['cancelled'] for p in products.values()) if all(p['cancelled'] is not None for p in products.values()) else None
    reason='Количество заказанных известно по исходному составу. Отмены не подтверждены: состав частей не сходится с исходным или разделение ещё не завершено. Нажмите «Обновить» позже.' if cancelled is None else ''
    return dict(total=total,cancelled=cancelled,products=dict(products),reason=reason,schemes=sorted(selected))


DATE_NOTE='Единицы товара по дате создания отправления FBO / начала обработки FBS (Москва), включая недоставленные и отменённые. Разделённый состав учитывается один раз по дате исходного отправления; техническое разделение не считается отменой товара. Отменённые входят в заказанное количество. Продажи и возвраты — по финансовым датам.'


def product_counts(result,sku):
    if result['total'] is None:return dict(total=None,cancelled=None)
    return result['products'].get(sku,dict(total=0,cancelled=0))
