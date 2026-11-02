"""Balanced API-to-ledger projection with explicit source replacement.

Unknown quantities/composition block promotion, leaving previous profit intact.
"""
from collections import Counter,defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import hashlib
import json

from .core import ZERO,money,connect,calculate,import_operations,get_setting

PREFIX='api-ledger:'
OWNED="(id LIKE 'ozon-xlsx:%' OR id LIKE 'api-ledger:%')"


@dataclass
class Projection:
    start: str
    end: str
    rows: list
    issues: list
    net: Decimal
    digest: str


def _value(container,key):
    value=container.get(key)
    return money(value['amount']) if isinstance(value,dict) and value.get('amount') is not None else ZERO


def _fee_kind(amount,label):
    label=label.casefold()
    if 'компенсац' in label:return 'compensation'
    if any(x in label for x in ('реклам','продвижен')):return 'advertising' if amount<=0 else 'advertising_refund'
    if any(x in label for x in ('достав','логист','возвратов','невыкуп')):return 'logistics' if amount<=0 else 'logistics_refund'
    if any(x in label for x in ('комисс','вознагражден')):return 'commission' if amount<=0 else 'service_refund'
    if any(x in label for x in ('выручк','реализац')):raise ValueError('Выручка без данных продажи требует сопоставления')
    return 'service' if amount<=0 else 'service_refund'


def project(db,start,end):
    start,end=str(start),str(end)
    date.fromisoformat(start);date.fromisoformat(end)
    issues=[];rows=[];net=ZERO
    a=get_setting(db,'api_start');b=get_setting(db,'api_end')
    if not a or not b or not a<=start<=end<=b or not get_setting(db,'api_last_success'):
        issues.append('Сначала полностью загрузите финансовые данные API за выбранный период.')
    if get_setting(db,'ledger_mode','detail')!='detail':issues.append('Режим итоговых начислений нельзя совместить с подробной прибылью API.')
    manual=db.execute(f"SELECT 1 FROM operations WHERE day BETWEEN ? AND ? AND NOT {OWNED} AND kind<>'restock' LIMIT 1",(start,end)).fetchone()
    if manual:issues.append('В периоде есть операции ручного импорта. Нужно отдельно определить их источник, чтобы избежать повторного учёта.')
    links={r[0]:r[1] for r in db.execute('SELECT ozon_sku,sku FROM product_links')}
    names={r[0]:r[1] for r in db.execute('SELECT sku,name FROM products')}
    orders={r[0]:json.loads(r[1]) for r in db.execute('SELECT posting_number,payload FROM api_postings')}
    records=[dict(r) for r in db.execute('SELECT * FROM api_finance WHERE day BETWEEN ? AND ? ORDER BY day,id',(start,end))]
    sale_events=Counter()
    for raw in records:
        payload=json.loads(raw['payload']);posting=payload.get('posting',{}).get('posting_number','')
        for item in payload.get('items',[]):
            if _value(item.get('commission',{}),'sale_amount')>0:sale_events[(posting,str(item.get('sku')))]+=1
    sold=defaultdict(lambda:ZERO);ordered={}
    for raw in records:
        payload=json.loads(raw['payload']);order=payload.get('posting',{}).get('posting_number','')
        category=payload.get('operation_type');actual=money(raw['amount']);net+=actual
        parts=[];final_commissions={};final_deliveries={}
        def emit(suffix,kind,amount,article='',name='',qty=ZERO):
            if amount==0:return
            parts.append(dict(id=PREFIX+raw['id']+':'+suffix,day=raw['day'],order_no=order,
                sku=article,name=names.get(article,name),kind=kind,amount=str(amount),qty=str(qty)))
        def fee(suffix,value,article='',name=''):
            amount=_value(value,'accrued')
            emit(suffix,_fee_kind(amount,value.get('type_name','')),amount,article,name)
        try:
            if category=='POSTING':
                seen=set()
                for index,item in enumerate(payload.get('items',[])):
                    sku=str(item.get('sku') or '')
                    if sku in seen:raise ValueError('Повтор SKU в одном начислении требует проверки количества')
                    seen.add(sku)
                    product_lines=[p for p in orders.get(order,{}).get('products',[]) if str(p.get('sku'))==sku]
                    article=next((str(p['offer_id']) for p in product_lines if p.get('offer_id')),links.get(sku,''))
                    name=next((str(p.get('name') or '') for p in product_lines),'')
                    available=sum((money(p['quantity']) for p in product_lines),ZERO)
                    commission=item.get('commission') or {};sale=_value(commission,'sale_amount')
                    if sale:
                        if not article:raise ValueError('Не найден артикул: загрузите отправления или исходные отчёты с сопоставлением SKU')
                        price=_value(commission,'sale_price');qty=ZERO
                        if price and abs(sale/price)==abs(sale/price).to_integral_value():qty=abs(sale/price)
                        elif sale>0 and available>0 and sale_events[(order,sku)]==1:qty=available
                        if qty<=0:raise ValueError('Не определено количество проданных/возвращённых единиц')
                        if available>0:
                            ordered[(order,article)]=available
                            if sale>0:sold[(order,article)]+=qty
                            elif qty>available:raise ValueError('Возвращено больше количества в отправлении')
                        elif sale_events[(order,sku)]>1:raise ValueError('Повторные продажи без состава отправления требуют проверки')
                        emit(f'{index}:sale','sale' if sale>0 else 'return',sale,article,name,qty)
                    bonus=_value(commission,'bonus');partner=_value(commission,'coinvestment')
                    emit(f'{index}:points','points' if bonus>=0 else 'points_reversal',bonus,article,name)
                    emit(f'{index}:partner','partner',partner,article,name)
                    # Both original and final commission are reported by Ozon.
                    # A final-based candidate retains points/partner tax categories
                    # by splitting them out of that reported final amount.
                    if commission:
                        has_original=commission.get('sale_commission') is not None
                        has_final=commission.get('commission') is not None
                        if not has_original and not has_final:
                            raise ValueError('В начислении отсутствуют исходная и итоговая комиссии')
                        original=_value(commission,'sale_commission') if has_original else _value(commission,'commission')-bonus-partner
                        emit(f'{index}:commission','commission' if original<=0 else 'service_refund',original,article,name)
                        if has_final:final_commissions[f'{index}:commission']=(_value(commission,'commission')-bonus-partner,article,name)
                    delivery=item.get('delivery') or {}
                    if delivery.get('total_accrued') is not None:
                        final_deliveries[str(index)]=(_value(delivery,'total_accrued'),article,name)
                    services=delivery.get('services',[])
                    if services:
                        for j,value in enumerate(services):fee(f'{index}:delivery:{j}',value,article,name)
                    elif delivery.get('total_accrued') is not None:
                        value=_value(delivery,'total_accrued')
                        emit(f'{index}:delivery','logistics' if value<=0 else 'logistics_refund',value,article,name)
            elif category=='NON_ITEM':fee('service',payload.get('non_item_fee') or {})
            elif category=='ITEM':
                for i,item in enumerate(payload.get('items',[])):
                    article=links.get(str(item.get('sku')),'')
                    for j,value in enumerate(item.get('fees',[])):fee(f'{i}:fee:{j}',value,article)
            elif category=='CONTAINER_FEES':
                for i,value in enumerate(payload.get('container_fees',[])):fee(f'container:{i}',value)
            elif actual!=0:raise ValueError('Неизвестная категория начисления')
            original_sum=sum((money(r['amount']) for r in parts),ZERO)
            final_parts=[dict(r) for r in parts]
            for suffix,(amount,article,name) in final_commissions.items():
                identifier=PREFIX+raw['id']+':'+suffix
                final_parts=[r for r in final_parts if r['id']!=identifier]
                if amount:
                    final_parts.append(dict(id=identifier,day=raw['day'],order_no=order,sku=article,
                        name=names.get(article,name),kind='commission' if amount<=0 else 'service_refund',amount=str(amount),qty='0'))
            for index,(amount,article,name) in final_deliveries.items():
                prefix=PREFIX+raw['id']+':'+index+':delivery'
                final_parts=[r for r in final_parts if not (r['id']==prefix or r['id'].startswith(prefix+':'))]
                if amount:
                    final_parts.append(dict(id=prefix+':total',day=raw['day'],order_no=order,sku=article,
                        name=names.get(article,name),kind='logistics' if amount<=0 else 'logistics_refund',amount=str(amount),qty='0'))
            final_sum=sum((money(r['amount']) for r in final_parts),ZERO)
            if final_sum==actual:parts=final_parts
            elif original_sum!=actual:
                raise ValueError(f'Детализация не совпала с итогом: итог API {actual}; по итоговым комиссиям/доставке {final_sum}; по исходным компонентам {original_sum}. Нужна проверка данных начисления.')
            rows.extend(parts)
        except ValueError as exc:
            issues.append(f'{raw["day"]}, {raw["id"]}: {exc}')
    for (order,article),quantity in sold.items():
        earlier=sum((money(r[0]) for r in db.execute("SELECT qty FROM operations WHERE order_no=? AND sku=? AND kind='sale' AND NOT(day BETWEEN ? AND ?)",(order,article,start,end))),ZERO)
        if quantity+earlier>ordered[(order,article)]:issues.append(f'Отправление {order}: количество продаж превышает состав заказа, возможна повторная выручка/корректировка.')
    encoded=json.dumps({'start':start,'end':end,'rows':rows},sort_keys=True,ensure_ascii=False).encode()
    digest='api-ledger:'+hashlib.sha256(encoded).hexdigest()
    return Projection(start,end,rows,issues,net,digest)


def _replace(db,projection):
    db.execute('INSERT OR IGNORE INTO imports(digest,name) VALUES(?,?)',(projection.digest,'Ozon Seller API'))
    db.execute(f'DELETE FROM operations WHERE day BETWEEN ? AND ? AND {OWNED}',(projection.start,projection.end))
    db.executemany('INSERT INTO operations VALUES(?,?,?,?,?,?,?,?,?)',[
        (r['id'],r['day'],r['order_no'],r['sku'],r['name'],r['kind'],r['amount'],r['qty'],projection.digest) for r in projection.rows])


def preview(db,projection,tax_base=None):
    if projection.issues:raise ValueError('Не все начисления удалось сопоставить')
    snapshot=connect(':memory:')
    try:
        db.backup(snapshot)
        with snapshot:
            _replace(snapshot,projection)
            if tax_base:snapshot.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('tax_base',tax_base))
        return calculate(snapshot,projection.start,projection.end)
    finally:snapshot.close()


def promote(db,start,end,tax_base=None,enable=False):
    if tax_base is not None and tax_base not in ('sales','sales_partner','sales_points','sales_points_partner','net'):raise ValueError('Неизвестная налоговая база')
    with db:
        db.execute('BEGIN IMMEDIATE')
        projection=project(db,start,end)
        if projection.issues:raise ValueError('Не удалось сопоставить все начисления. Предыдущий расчёт сохранён; откройте проверку прибыли API.')
        # Apply exactly the core ledger's validation before replacing any real data.
        if projection.rows:
            validator=connect(':memory:')
            try:import_operations(validator,projection.digest.encode(),'validation',projection.rows)
            finally:validator.close()
        old=[dict(r) for r in db.execute(f'SELECT * FROM operations WHERE day BETWEEN ? AND ? AND {OWNED} ORDER BY day,id',(str(start),str(end)))]
        unchanged=all(r['source']==projection.digest for r in old) and len(old)==len(projection.rows)
        if not unchanged:
            if old:db.execute('INSERT INTO ledger_backups(period_start,period_end,operations) VALUES(?,?,?)',(str(start),str(end),json.dumps(old,ensure_ascii=False)))
            _replace(db,projection)
        db.executemany('INSERT OR REPLACE INTO settings VALUES(?,?)',[
            ('ledger_mode','detail'),('api_profit_last_success',get_setting(db,'api_last_success')),
            ('api_profit_error',''),('report_start',str(start)),('report_end',str(end)),
        ])
        if tax_base:db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('tax_base',tax_base))
        if enable:db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('api_profit_enabled','1'))
    return projection


def restore_previous(db,start,end):
    with db:
        db.execute('BEGIN IMMEDIATE')
        saved=db.execute('SELECT operations FROM ledger_backups WHERE period_start=? AND period_end=? ORDER BY id DESC LIMIT 1',(str(start),str(end))).fetchone()
        if not saved:raise ValueError('Резервная версия этого периода не найдена')
        original=json.loads(saved[0])
        current=[dict(r) for r in db.execute(f'SELECT * FROM operations WHERE day BETWEEN ? AND ? AND {OWNED}',(str(start),str(end)))]
        db.execute('INSERT INTO ledger_backups(period_start,period_end,operations) VALUES(?,?,?)',(str(start),str(end),json.dumps(current,ensure_ascii=False)))
        db.execute(f'DELETE FROM operations WHERE day BETWEEN ? AND ? AND {OWNED}',(str(start),str(end)))
        db.executemany('INSERT INTO operations VALUES(?,?,?,?,?,?,?,?,?)',[
            tuple(r[k] for k in ('id','day','order_no','sku','name','kind','amount','qty','source')) for r in original])
        db.executemany('INSERT OR REPLACE INTO settings VALUES(?,?)',[
            ('api_profit_enabled','0'),('api_profit_error','Автопересчёт API отключён после восстановления предыдущих операций.')])


def diagnostics(db,projection):
    """Minimal user-downloadable financial packet. No keys, buyers or raw payloads."""
    entries=[];order_ids=set();skus=set()
    for raw in db.execute('SELECT * FROM api_finance WHERE day BETWEEN ? AND ? ORDER BY day,id',(projection.start,projection.end)):
        marker=f'{raw["day"]}, {raw["id"]}:'
        if not any(marker in issue for issue in projection.issues):continue
        p=json.loads(raw['payload']);number=p.get('posting',{}).get('posting_number','')
        record={'id':raw['id'],'date':raw['day'],'total':str(money(raw['amount'])),
                'category':p.get('operation_type'),'posting_number':number,'products':[]}
        order_ids.add(number)
        def amount(v):
            try:return str(money(v['amount'])) if isinstance(v,dict) and v.get('amount') is not None else None
            except ValueError:return None
        for item in p.get('items',[]):
            sku=str(item.get('sku') or '');skus.add(sku)
            commission=item.get('commission') or {};delivery=item.get('delivery') or {}
            record['products'].append({'sku':sku,
                'commission':{key:amount(commission.get(key)) for key in ('sale_amount','sale_price','seller_price','bonus','coinvestment','sale_commission','commission')},
                'delivery_total':amount(delivery.get('total_accrued')),
                'delivery_services':[{'type_id':v.get('type_id'),'amount':amount(v.get('accrued'))} for v in delivery.get('services',[])]})
        entries.append(record)
        if len(entries)>=100:break
    orders=[]
    for r in db.execute('SELECT posting_number,payload FROM api_postings'):
        if r[0] not in order_ids:continue
        p=json.loads(r[1])
        orders.append({'posting_number':r[0],'products':[
            {'sku':str(item.get('sku') or ''),'offer_id':item.get('offer_id'),'quantity':item.get('quantity')}
            for item in p.get('products',[])]})
    packet={'format':'ozon-profit-diagnostics-v1','period':[projection.start,projection.end],
            'issues':projection.issues[:100],'accruals':entries,'postings':orders,
            'sku_links':[dict(ozon_sku=r[0],offer_id=r[1]) for r in db.execute('SELECT ozon_sku,sku FROM product_links') if r[0] in skus]}
    return json.dumps(packet,ensure_ascii=False,indent=2).encode('utf-8')
