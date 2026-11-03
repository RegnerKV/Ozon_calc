"""Human-readable signed cashflow; original categories remain intact in accounting."""
from collections import defaultdict
import json
import pandas as pd
from .core import money,KINDS,ZERO


def cashflow(result,tax_label):
    totals=defaultdict(lambda:ZERO)
    for row in result['details']:
        kind=row['kind']
        if kind in (KINDS['points'],KINDS['points_reversal'],KINDS['partner']):label='Соинвест'
        elif kind==KINDS['return']:label='Возврат'
        elif kind==KINDS['sale']:continue
        elif kind in (KINDS['advertising'],KINDS['advertising_refund']):label='Реклама'
        elif kind==KINDS['service_refund']:label='Прочие услуги'
        else:label=kind
        totals[label]+=money(row['amount'])
    totals['Себестоимость']-=result['costs'];totals[tax_label]-=result['tax']
    return pd.DataFrame([{'Статья':k,'Сумма, ₽':float(v)} for k,v in totals.items() if v])


def service_breakdown(db,start,end,category='service'):
    totals=defaultdict(lambda:ZERO)
    exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='api_finance'").fetchone()
    if not exists:return []
    from .api_ledger import _fee_kind
    for raw in db.execute('SELECT payload FROM api_finance WHERE day BETWEEN ? AND ?',(str(start),str(end))):
        data=json.loads(raw[0]);fees=[]
        if data.get('non_item_fee'):fees.append(data['non_item_fee'])
        fees.extend(data.get('container_fees',[]))
        for item in data.get('items',[]):fees.extend(item.get('fees',[]))
        for fee in fees:
            amount=money((fee.get('accrued') or {}).get('amount'))
            label=fee.get('type_name') or 'Услуга без названия'
            try:kind=_fee_kind(amount,label)
            except ValueError:continue
            if (category=='service' and kind in ('service','service_refund')) or (category=='advertising' and kind in ('advertising','advertising_refund')):totals[translate_service(label)]+=amount
    return [{'Категория услуги':name,'Сумма, ₽':float(value)} for name,value in sorted(totals.items()) if value]


TRANSLATIONS={
    'acquiring':'Эквайринг',
    'crossdock':'Кросс-докинг',
    'defectfineerrors':'Штрафы и корректировки за дефекты/ошибки (DefectFineErrors)',
    'earlypayment':'Досрочная выплата',
    'packagecost':'Упаковочные материалы',
    'packingfee':'Упаковка / доупаковка',
    'payperclick':'Оплата за клик',
    'promotion':'Продвижение — оплата за заказ',
}


def translate_service(label):
    return TRANSLATIONS.get(label.casefold().replace('_','').replace(' ',''),label)


def expenses_html(db,start,end,result,tax_label):
    from html import escape
    from .presentation import cash
    rows=[]
    for row in cashflow(result,tax_label).to_dict('records'):
        name=row['Статья'];value=cash(row['Сумма, ₽'])
        if name in ('Прочие услуги','Реклама'):
            children=service_breakdown(db,start,end,'advertising' if name=='Реклама' else 'service')
            subtotal=sum((money(r['Сумма, ₽']) for r in children),ZERO)
            difference=money(row['Сумма, ₽'])-subtotal
            if difference:children.append({'Категория услуги':'Без подробной категории','Сумма, ₽':difference})
            content=''.join('<div class="child"><span>'+escape(r['Категория услуги'])+'</span><span>'+escape(cash(r['Сумма, ₽']))+'</span></div>' for r in children)
            rows.append('<tr><td colspan="2" class="tree"><details><summary><span class="tree-title">'+escape(name)+'</span><span>'+escape(value)+'</span></summary>'+content+'</details></td></tr>')
        else:rows.append('<tr><td>'+escape(name)+'</td><td>'+escape(value)+'</td></tr>')
    return '<table class="ozon-expenses"><thead><tr><th>Статья</th><th>Сумма, ₽</th></tr></thead><tbody>'+''.join(rows)+'</tbody></table>'
