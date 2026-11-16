"""Separate DRR using historical Seller unit prices, never buyer realization."""
import json
from .core import ZERO,money
from .api_ledger import project,PREFIX


def denominator(db,start,end):
    missing=dict(total=None,products={},reason='Нет сопоставленных исторических цен продавца за период.')
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='api_finance'").fetchone():return missing
    projection=project(db,start,end)
    if projection.issues:return missing
    sales={r['id']:r for r in projection.rows if r['kind'] in ('sale','return')}
    actual={r['id']:r for r in db.execute("SELECT id,sku,kind,amount,qty FROM operations WHERE day BETWEEN ? AND ? AND kind IN ('sale','return')",(str(start),str(end)))}
    if not sales or set(sales)!=set(actual):return missing
    for identifier,row in sales.items():
        saved=actual[identifier]
        if saved['sku']!=row['sku'] or saved['kind']!=row['kind'] or money(saved['amount'])!=money(row['amount']) or money(saved['qty'])!=money(row['qty']):return missing
    total=ZERO;products={};seen=set()
    for raw in db.execute('SELECT id,payload FROM api_finance WHERE day BETWEEN ? AND ?',(str(start),str(end))):
        payload=json.loads(raw['payload'])
        if payload.get('operation_type')!='POSTING':continue
        for index,item in enumerate(payload.get('items',[])):
            identifier=PREFIX+raw['id']+f':{index}:sale'
            if identifier not in sales:continue
            row=sales[identifier];commission=item.get('commission') or {}
            try:
                price=commission['seller_price'];gross=commission['sale_amount']
                if price['currency']!='RUB' or gross['currency']!='RUB':return missing
                unit=abs(money(price['amount']))
                value=unit*money(row['qty'])*(1 if row['kind']=='sale' else -1)
                # The source price must reconcile to the historical gross amount.
                # Never reconstruct it from buyer realization or reimbursements.
                if unit<=0 or value!=money(gross['amount']):return missing
            except (KeyError,TypeError,ValueError):return missing
            total+=value;products[row['sku']]=products.get(row['sku'],ZERO)+value;seen.add(identifier)
    if seen!=set(sales):return missing
    return dict(total=total,products=products,reason='')


def ratio(expense,base):
    return expense/base*100 if expense is not None and base is not None and base>0 else None
