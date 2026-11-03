"""Deterministic sales scenarios, separated from real ledger and cash balances."""
from collections import defaultdict
from datetime import date
from decimal import Decimal
from .core import ZERO,money,get_setting,calculate

ADS={'advertising','advertising_refund'}
FEES={'commission','logistics','service','service_refund','logistics_refund'}
CREDITS={'points','points_reversal','partner'}


def history(db,start,end):
    if str(end)<str(start):raise ValueError('Проверьте даты истории')
    rows=db.execute('SELECT * FROM operations WHERE day BETWEEN ? AND ?',(str(start),str(end))).fetchall()
    issues=[]
    from .seller_api import init_tables
    init_tables(db)
    staged=False
    if db.execute('SELECT 1 FROM api_finance WHERE day BETWEEN ? AND ? LIMIT 1',(str(start),str(end))).fetchone():
        from .api_ledger import project
        projection=project(db,start,end)
        issues=projection.issues
        if not issues:rows=projection.rows;staged=True
    products=defaultdict(lambda:dict(units=ZERO,revenue=ZERO,points=ZERO,partner=ZERO,fees=ZERO))
    total_revenue=ZERO;ads=ZERO;common=ZERO
    for r in rows:
        value=money(r['amount']);kind=r['kind'];p=products[r['sku']]
        if kind in ('sale','return'):
            p['units']+=money(r['qty'])*(1 if kind=='sale' else -1)
            p['revenue']+=value;total_revenue+=value
        elif kind in ADS:ads-=value
        elif kind in FEES:
            if r['sku']:p['fees']-=value
            else:common-=value
        elif kind in ('points','points_reversal'):p['points']+=value
        elif kind=='partner':p['partner']+=value
    result=dict(products=dict(products),revenue=total_revenue,advertising=max(ZERO,ads),issues=issues,ad_share=ZERO,common_ratio=ZERO,
                fees_ratio=ZERO,points_ratio=ZERO,partner_ratio=ZERO)
    if total_revenue>0:
        result['ad_share']=max(ZERO,ads)/total_revenue*100
        result['common_ratio']=common/total_revenue
        for name in ('fees','points','partner'):
            result[name+'_ratio']=sum((p[name] for p in products.values()),ZERO)/total_revenue
    result['stale']=False if staged else calculate(db,start,end)['stale_api']
    return result


def forecast(db,items,start,end,baseline_start,baseline_end,extra_ad_pp=0,extra_costs=0,*,ad_budget=None,order_ad_percent=0):
    start=date.fromisoformat(str(start));end=date.fromisoformat(str(end))
    if end<start:raise ValueError('Окончание плана раньше начала')
    delta=money(extra_ad_pp);extra=money(extra_costs)
    if delta<0 or extra<0:raise ValueError('Дополнительные расходы не могут быть отрицательными')
    h=history(db,baseline_start,baseline_end)
    budget=money(ad_budget) if ad_budget is not None else None
    order_rate=money(order_ad_percent)
    if budget is not None and (budget<0 or order_rate not in (0,6,8,10)):raise ValueError('Укажите бюджет от нуля и ставку за заказы: 0, 6, 8 или 10%')
    issues=list(h['issues']);rows=[];rate=money(get_setting(db,'tax_rate','4'))/100
    base=get_setting(db,'tax_base','sales')
    if h['stale']:issues.append('История ещё рассчитана прежней версией API. Сначала обновите фактическую прибыль.')
    totals={k:ZERO for k in ('revenue','points','partner','fees','common','costs','advertising','tax','profit','baseline_profit','advertising_increase','quantity','click_advertising','order_advertising')}
    tax_base=ZERO;baseline_tax_base=ZERO;pre_tax=ZERO;baseline_pre_tax=ZERO;estimated=[]
    budget_base=ZERO
    for item in items:
        own=h['products'].get(str(item['sku']),{})
        price=money(item['price']) if item.get('price') not in (None,'') else (own['revenue']/own['units'] if own.get('units',ZERO)>0 else ZERO)
        budget_base+=price*money(item['quantity'])
    seen=set();remaining_budget=budget
    for index,item in enumerate(items):
        sku=str(item['sku']);qty=money(item['quantity'])
        if sku in seen:raise ValueError('Один артикул должен быть одной строкой плана')
        seen.add(sku)
        if qty<=0 or qty!=qty.to_integral_value():raise ValueError('Количество должно быть целым положительным числом')
        product=db.execute('SELECT name FROM products WHERE sku=?',(sku,)).fetchone()
        name=product[0] if product else sku
        own=h['products'].get(sku,{})
        own_history=own.get('units',ZERO)>0 and own.get('revenue',ZERO)>0
        expected=money(item.get('price')) if item.get('price') not in (None,'') else (own['revenue']/own['units'] if own_history else ZERO)
        cost=db.execute('SELECT * FROM costs WHERE sku=? AND since<=? ORDER BY since DESC LIMIT 1',(sku,str(start))).fetchone()
        if expected<=0:issues.append(f'{name}: укажите ожидаемую цену покупателю — собственной истории продаж нет.')
        if not cost:issues.append(f'{name}: не задана себестоимость на начало плана.')
        if not own_history:
            if h['revenue']<=0:issues.append(f'{name}: в выбранном периоде нет продаж. Выберите другой период для средних показателей.')
            estimated.append(name+' — средние условия магазина')
        elif own.get('revenue',ZERO)>0:
            pass
        if cost and cost['estimated']:estimated.append(name+' — примерная себестоимость')
        source=own if own_history else None
        ratios={k:(source[k]/source['revenue'] if source else h[k+'_ratio']) for k in ('fees','points','partner')}
        revenue=expected*qty
        points=revenue*ratios['points'];partner=revenue*ratios['partner'];fees=revenue*ratios['fees']
        common=revenue*h['common_ratio'];cogs=(money(cost['amount'])*qty if cost else ZERO)
        baseline_ad=revenue*h['ad_share']/100
        clicks=(budget*revenue/budget_base if budget_base>0 else ZERO) if budget is not None else revenue*(h['ad_share']+delta)/100
        if budget is not None:
            if index==len(items)-1:clicks=remaining_budget
            remaining_budget-=clicks
        orders=revenue*order_rate/100 if budget is not None else ZERO
        ad=clicks+orders
        net=revenue+points+partner-fees-common-ad
        before_tax=net-cogs;before_baseline=revenue+points+partner-fees-common-baseline_ad-cogs
        taxable=revenue
        if base in ('sales_points','sales_points_partner'):taxable+=points
        if base in ('sales_partner','sales_points_partner'):taxable+=partner
        tb=taxable if base!='net' else net
        tbb=taxable if base!='net' else net+ad-baseline_ad
        tax_base+=tb;baseline_tax_base+=tbb;pre_tax+=before_tax;baseline_pre_tax+=before_baseline
        for key,val in [('revenue',revenue),('points',points),('partner',partner),('fees',fees),('common',common),('costs',cogs),('advertising',ad),('click_advertising',clicks),('order_advertising',orders),('advertising_increase',ad-baseline_ad),('quantity',qty)]:totals[key]+=val
        rows.append(dict(sku=sku,name=name,quantity=qty,price=expected,revenue=revenue,costs=cogs,
                         advertising=ad,profit=before_tax-max(ZERO,tb)*rate,estimated=not own_history or not cost or bool(cost['estimated'])))
    totals['tax']=max(ZERO,tax_base)*rate
    totals['profit']=pre_tax-totals['tax']-extra
    totals['baseline_profit']=baseline_pre_tax-max(ZERO,baseline_tax_base)*rate-extra
    return dict(totals=totals,rows=rows,issues=issues,estimated=estimated,base_ad_share=h['ad_share'],
                planned_ad_share=totals['advertising']/totals['revenue']*100 if totals['revenue']>0 else ZERO,days=(end-start).days+1,
                daily_quantity=totals['quantity']/((end-start).days+1),extra_costs=extra)
