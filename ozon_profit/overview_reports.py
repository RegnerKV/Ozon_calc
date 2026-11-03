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
        else:label=kind
        totals[label]+=money(row['amount'])
    totals['Себестоимость']-=result['costs'];totals[tax_label]-=result['tax']
    return pd.DataFrame([{'Статья':k,'Сумма, ₽':float(v)} for k,v in totals.items() if v])


def service_breakdown(db,start,end):
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
            if kind in ('service','service_refund'):totals[label]+=amount
    return [{'Категория услуги':name,'Сумма, ₽':float(value)} for name,value in sorted(totals.items()) if value]
