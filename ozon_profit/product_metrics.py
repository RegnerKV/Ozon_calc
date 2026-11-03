"""Article P&L with an explicit, reversible view-only shared-cost allocation."""
from collections import defaultdict
from .core import money,ZERO,KINDS

FIELDS=('net','revenue','costs','tax','profit','advertising')


def summarize(result,allocate_shared=True):
    def empty():return dict(**{k:ZERO for k in FIELDS},quantity=ZERO,name='',missing=False,estimated=False,allocated=ZERO)
    products=defaultdict(empty);shared=empty()
    for row in result['details']:
        sku=row['sku'];p=products[sku] if sku else shared
        if row.get('name'):p['name']=row['name']
        amount=money(row['amount']);cost=money(row['cost']);tax=money(row['tax'])
        p['net']+=amount;p['costs']+=cost;p['tax']+=tax;p['profit']+=amount-cost-tax
        if row['kind'] in (KINDS['sale'],KINDS['return']):
            p['revenue']+=amount;p['quantity']+=money(row.get('quantity',0))*(1 if row['kind']==KINDS['sale'] else -1)
        if row['kind'] in (KINDS['advertising'],KINDS['advertising_refund']):p['advertising']-=amount
        issue=row.get('issue','')
        p['missing']=p['missing'] or bool(issue and issue!='Примерная себестоимость')
        p['estimated']=p['estimated'] or issue=='Примерная себестоимость'
    # Core clamps the total tax reserve, not individual tax reversals.
    tax_adjustment=result['tax']-sum((p['tax'] for p in products.values()),ZERO)-shared['tax']
    shared['tax']+=tax_adjustment;shared['profit']-=tax_adjustment
    eligible=[sku for sku,p in products.items() if p['revenue']>0]
    total=sum((products[sku]['revenue'] for sku in eligible),ZERO)
    has_shared=any(shared[k] for k in FIELDS)
    if allocate_shared and total>0:
        for field in FIELDS:
            remaining=shared[field]
            for index,sku in enumerate(eligible):
                value=remaining if index==len(eligible)-1 else shared[field]*products[sku]['revenue']/total
                products[sku][field]+=value;remaining-=value
                if field=='profit':products[sku]['allocated']=value
    allocation_complete=not has_shared or bool(allocate_shared and total>0)
    for p in products.values():
        p['approximate']=p['estimated'] or bool(allocate_shared and has_shared)
        p['blocked']=p['missing'] or result['stale_api'] or not allocation_complete
        p['drr']=p['advertising']/p['revenue']*100 if p['revenue']>0 and (allocate_shared or shared['advertising']==0) else None
        p['margin']=p['profit']/p['revenue']*100 if p['revenue']>0 and not p['blocked'] else None
        p['unit_profit']=p['profit']/p['quantity'] if p['quantity']>0 and not p['blocked'] else None
    return dict(products=dict(products),has_shared=has_shared,unassigned_advertising=shared['advertising'],allocated=allocate_shared and total>0)
