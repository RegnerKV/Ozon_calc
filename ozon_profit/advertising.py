"""Actual DRR with explicit gaps in product attribution; no invented allocation."""
from collections import defaultdict
from .core import money,KINDS,ZERO


def drr(details):
    products=defaultdict(lambda:dict(revenue=ZERO,advertising=ZERO))
    revenue=ZERO;advertising=ZERO;unassigned=ZERO
    for row in details:
        sku=row['sku'];kind=row['kind'];amount=money(row['amount'])
        if kind in (KINDS['sale'],KINDS['return']):
            revenue+=amount;products[sku]['revenue']+=amount
        if kind in (KINDS['advertising'],KINDS['advertising_refund']):
            advertising-=amount
            if sku:products[sku]['advertising']-=amount
            else:unassigned-=amount
    return dict(advertising=max(ZERO,advertising),unassigned=max(ZERO,unassigned),
                store=max(ZERO,advertising)/revenue*100 if revenue>0 else None,
                products={sku:dict(**value,drr=max(ZERO,value['advertising'])/value['revenue']*100 if value['revenue']>0 and unassigned<=0 else None) for sku,value in products.items() if sku})
