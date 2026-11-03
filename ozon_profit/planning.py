"""Sales forecast: commission / buyer revenue; logistics and services / net units."""
from collections import defaultdict
from datetime import date
from .core import ZERO,money,get_setting,calculate

ADS={'advertising','advertising_refund'}
FEE_FIELDS=('commission','logistics','services')


def _fee_field(row):
    kind=row['kind']
    if kind=='commission' or (kind=='service_refund' and row['id'].startswith('api-ledger:') and row['id'].endswith(':commission')):return 'commission'
    if kind in ('logistics','logistics_refund'):return 'logistics'
    if kind in ('service','service_refund'):return 'services'
    return None


def history(db,start,end):
    if str(end)<str(start):raise ValueError('Проверьте даты истории')
    rows=db.execute('SELECT * FROM operations WHERE day BETWEEN ? AND ?',(str(start),str(end))).fetchall()
    issues=[]
    from .seller_api import init_tables
    init_tables(db);staged=False
    if db.execute('SELECT 1 FROM api_finance WHERE day BETWEEN ? AND ? LIMIT 1',(str(start),str(end))).fetchone():
        from .api_ledger import project
        projection=project(db,start,end);issues=projection.issues
        if not issues:rows=projection.rows;staged=True
    products=defaultdict(lambda:dict(units=ZERO,revenue=ZERO,points=ZERO,partner=ZERO,fees=ZERO,commission=ZERO,logistics=ZERO,services=ZERO))
    total_revenue=ZERO;ads=ZERO;common={name:ZERO for name in FEE_FIELDS}
    for r in rows:
        value=money(r['amount']);kind=r['kind'];p=products[r['sku']]
        if kind in ('sale','return'):
            p['units']+=money(r['qty'])*(1 if kind=='sale' else -1)
            p['revenue']+=value;total_revenue+=value
        elif kind in ADS:ads-=value
        elif _fee_field(r):
            field=_fee_field(r)
            if r['sku']:p[field]-=value;p['fees']-=value
            else:common[field]-=value
        elif kind in ('points','points_reversal'):p['points']+=value
        elif kind=='partner':p['partner']+=value
    units=sum((p['units'] for p in products.values()),ZERO)
    assigned={key:sum((p[key] for sku,p in products.items() if sku),ZERO) for key in FEE_FIELDS}
    result=dict(products=dict(products),units=units,revenue=total_revenue,advertising=max(ZERO,ads),issues=issues,
                common=common,assigned=assigned,ad_share=ZERO,common_ratio=ZERO,fees_ratio=ZERO,points_ratio=ZERO,partner_ratio=ZERO)
    if total_revenue>0:
        result['ad_share']=max(ZERO,ads)/total_revenue*100
        result['common_ratio']=sum(common.values(),ZERO)/total_revenue
        result['fees_ratio']=sum(assigned.values(),ZERO)/total_revenue
        for name in ('points','partner'):result[name+'_ratio']=sum((p[name] for p in products.values()),ZERO)/total_revenue
    result['stale']=False if staged else calculate(db,start,end)['stale_api']
    return result


def _allocate(total,weights):
    """Allocate whole-plan sums once, keeping the exact remainder in the last row."""
    denominator=sum(weights,ZERO);result=[];remaining=total
    for index,weight in enumerate(weights):
        amount=(total*weight/denominator if denominator>0 else ZERO) if index<len(weights)-1 else remaining
        result.append(amount);remaining-=amount
    return result


def forecast(db,items,start,end,baseline_start,baseline_end,extra_ad_pp=0,extra_costs=0,*,ad_budget=None,order_ad_percent=0,include_coinvest=True):
    start=date.fromisoformat(str(start));end=date.fromisoformat(str(end))
    if end<start:raise ValueError('Окончание плана раньше начала')
    delta=money(extra_ad_pp);extra=money(extra_costs)
    if delta<0 or extra<0:raise ValueError('Дополнительные расходы не могут быть отрицательными')
    h=history(db,baseline_start,baseline_end)
    budget=money(ad_budget) if ad_budget is not None else None;order_rate=money(order_ad_percent)
    if budget is not None and (budget<0 or order_rate not in (0,6,8,10)):raise ValueError('Укажите бюджет от нуля и ставку за заказы: 0, 6, 8 или 10%')
    issues=list(h['issues']);rows=[];rate=money(get_setting(db,'tax_rate','4'))/100;base=get_setting(db,'tax_base','sales')
    if h['stale']:issues.append('История ещё рассчитана прежней версией API. Сначала обновите фактическую прибыль.')
    if h['units']<=0:issues.append('В выбранной истории нет положительного количества продаж для расчёта расходов на штуку.')
    totals={k:ZERO for k in ('revenue','points','partner','fees','common','commission','logistics','services','costs','advertising','tax','profit','baseline_profit','advertising_increase','quantity','click_advertising','order_advertising','extra_costs')}
    estimated=[];seen=set()
    for item in items:
        sku=str(item['sku']);qty=money(item['quantity'])
        if sku in seen:raise ValueError('Один артикул должен быть одной строкой плана')
        seen.add(sku)
        if qty<=0 or qty!=qty.to_integral_value():raise ValueError('Количество должно быть целым положительным числом')
        product=db.execute('SELECT name FROM products WHERE sku=?',(sku,)).fetchone();name=product[0] if product else sku
        own=h['products'].get(sku,{});own_history=own.get('units',ZERO)>0 and own.get('revenue',ZERO)>0
        expected=money(item.get('price')) if item.get('price') not in (None,'') else (own['revenue']/own['units'] if own_history else ZERO)
        cost=db.execute('SELECT * FROM costs WHERE sku=? AND since<=? ORDER BY since DESC LIMIT 1',(sku,str(start))).fetchone()
        if expected<=0:issues.append(f'{name}: укажите ожидаемую фактическую оплату покупателя после скидок.')
        if not cost:issues.append(f'{name}: не задана себестоимость на начало плана.')
        if not own_history:
            if h['revenue']<=0:issues.append(f'{name}: в выбранном периоде нет продаж. Выберите другой период для средних показателей.')
            estimated.append(name+' — средние условия магазина')
        if cost and cost['estimated']:estimated.append(name+' — примерная себестоимость')
        ratios={k:(own[k]/own['revenue'] if own_history else h[k+'_ratio']) for k in ('points','partner')}
        direct_commission_rate=own['commission']/own['revenue'] if own_history else h['assigned']['commission']/h['revenue'] if h['revenue']>0 else ZERO
        common_commission_rate=h['common']['commission']/h['revenue'] if h['revenue']>0 else ZERO
        unit_rates={k:(own[k]/own['units'] if own_history else h['assigned'][k]/h['units'] if h['units']>0 else ZERO) for k in ('logistics','services')}
        shared_unit={k:h['common'][k]/h['units'] if h['units']>0 else ZERO for k in ('logistics','services')}
        revenue=expected*qty;points=revenue*ratios['points'] if include_coinvest else ZERO;partner=revenue*ratios['partner'] if include_coinvest else ZERO
        commission=revenue*(direct_commission_rate+common_commission_rate)
        logistics=qty*(unit_rates['logistics']+shared_unit['logistics']);services=qty*(unit_rates['services']+shared_unit['services'])
        fees=revenue*direct_commission_rate+qty*sum(unit_rates.values(),ZERO)
        common=revenue*common_commission_rate+qty*sum(shared_unit.values(),ZERO)
        rows.append(dict(sku=sku,name=name,quantity=qty,price=expected,revenue=revenue,points=points,partner=partner,
            fees=fees,common=common,commission=commission,logistics=logistics,services=services,
            commission_percent=(direct_commission_rate+common_commission_rate)*100,
            logistics_per_unit=unit_rates['logistics']+shared_unit['logistics'],services_per_unit=unit_rates['services']+shared_unit['services'],
            costs=money(cost['amount'])*qty if cost else ZERO,estimated=not own_history or not cost or bool(cost['estimated']),
            history_source='Артикул' if own_history else 'Среднее по магазину'))
    weights=[r['revenue'] for r in rows]
    clicks=_allocate(budget,weights) if budget is not None else [v*(h['ad_share']+delta)/100 for v in weights]
    extras=_allocate(extra,weights)
    for r,click,extra_part in zip(rows,clicks,extras):
        r['click_advertising']=click;r['order_advertising']=r['revenue']*order_rate/100 if budget is not None else ZERO
        r['advertising']=click+r['order_advertising'];r['extra_costs']=extra_part
        r['baseline_advertising']=r['revenue']*h['ad_share']/100
        net=r['revenue']+r['points']+r['partner']-r['commission']-r['logistics']-r['services']-r['advertising']
        r['pre_tax']=net-r['costs']
        r['baseline_pre_tax']=r['pre_tax']+r['advertising']-r['baseline_advertising']
        taxable=r['revenue']
        if base in ('sales_points','sales_points_partner'):taxable+=r['points']
        if base in ('sales_partner','sales_points_partner'):taxable+=r['partner']
        r['tax_base']=taxable if base!='net' else net
        r['baseline_tax_base']=taxable if base!='net' else net+r['advertising']-r['baseline_advertising']
    tax=max(ZERO,sum((r['tax_base'] for r in rows),ZERO))*rate
    baseline_tax=max(ZERO,sum((r['baseline_tax_base'] for r in rows),ZERO))*rate
    row_taxes=_allocate(tax,[max(ZERO,r['tax_base']) for r in rows])
    baseline_taxes=_allocate(baseline_tax,[max(ZERO,r['baseline_tax_base']) for r in rows])
    for r,t,bt in zip(rows,row_taxes,baseline_taxes):
        r['tax']=t;r['profit']=r['pre_tax']-t-r['extra_costs'];r['baseline_profit']=r['baseline_pre_tax']-bt-r['extra_costs']
        for key in totals:
            if key in r:totals[key]+=r[key]
    no_coinvest_tax_base=sum((r['revenue'] if base!='net' else r['pre_tax']+r['costs']-r['points']-r['partner'] for r in rows),ZERO)
    without_coinvest=sum((r['pre_tax']-r['points']-r['partner'] for r in rows),ZERO)-max(ZERO,no_coinvest_tax_base)*rate-extra
    totals['advertising_increase']=totals['advertising']-sum((r['baseline_advertising'] for r in rows),ZERO)
    return dict(totals=totals,rows=rows,issues=issues,estimated=estimated,base_ad_share=h['ad_share'],include_coinvest=bool(include_coinvest),
        planned_ad_share=totals['advertising']/totals['revenue']*100 if totals['revenue']>0 else ZERO,
        days=(end-start).days+1,daily_quantity=totals['quantity']/((end-start).days+1),extra_costs=extra,tax_rate=rate*100,profit_without_coinvest=without_coinvest)
