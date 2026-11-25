"""Owner-approved whole-business ROMI, including organic sales.

Use the existing financial profit and its advertising cost together. Advertising
has already been deducted from profit; do not deduct it again or mix sources.
"""
from .advertising import drr
from .product_metrics import summarize
from .core import money

NOTE='ROMI = чистая прибыль ÷ рекламные расходы × 100%. Реклама уже вычтена из прибыли. Учитываются все продажи, включая органические; это предварительный показатель магазина, а не отдельная оценка отдачи рекламы. По артикулам используется то же распределение общих расходов, что и в расчёте прибыли.'


def ratio(profit,expense):
    if profit is None or expense is None:return None
    profit,expense=money(profit),money(expense)
    return profit/expense*100 if expense>0 else None


def report(result,performance=None):
    missing=dict(store=None,products={},reason='Для ROMI нужны рассчитанная чистая прибыль и положительные рекламные расходы за выбранный период.')
    if not result['count'] or result['missing'] or result['stale_api'] or result['net_mode']:return missing
    expense=drr(result['details'])['advertising']
    if performance is not None and performance['total']!=expense:
        return {**missing,'reason':'ROMI не определён: рекламные расходы Performance и финансового расчёта требуют сверки.'}
    products=summarize(result,allocate_shared=True)
    return dict(store=ratio(result['profit'],expense),
        products={sku:ratio(p['profit'],p['advertising']) if not p['blocked'] else None for sku,p in products['products'].items()},
        reason='' if expense>0 else missing['reason'])


def percent(value):
    return f'{value:.2f} %'.replace('.',',') if value is not None else 'Нет данных'
