"""Marketplace service charges net of coinvest, without ads, COGS or taxes."""
from .core import KINDS,ZERO,money


CHARGES={KINDS[k] for k in ('commission','logistics','service','service_refund','logistics_refund')}
COINVEST={KINDS[k] for k in ('points','points_reversal','partner')}


def contribution(row):
    """Refunds reduce expense; a reversal of coinvest increases it again."""
    return -money(row['amount']) if row['kind'] in CHARGES or row['kind'] in COINVEST else ZERO


def percentage(amount,revenue):
    return amount/revenue*100 if revenue>0 else None


def summarize(details):
    revenue=sum((money(r['amount']) for r in details if r['kind'] in (KINDS['sale'],KINDS['return'])),ZERO)
    amount=sum((contribution(r) for r in details),ZERO)
    return dict(amount=amount,revenue=revenue,percent=percentage(amount,revenue))
