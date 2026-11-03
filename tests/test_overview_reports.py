from decimal import Decimal as D
from ozon_profit.core import KINDS
from ozon_profit.overview_reports import cashflow


def test_cashflow_combines_coinvest_and_keeps_refund_sign():
    details=[dict(kind=KINDS[kind],amount=D(value)) for kind,value in [('points','100'),('points_reversal','-30'),('partner','20'),('return','-200'),('commission','-50')]]
    rows=cashflow(dict(details=details,costs=D(10),tax=D(4)),'Налоги (4%)').set_index('Статья')['Сумма, ₽']
    assert rows['Соинвест']==90 and rows['Возврат']==-200 and rows['Комиссия']==-50
    assert rows['Налоги (4%)']==-4


def test_service_breakdown_uses_actual_categories_and_excludes_ads():
    import json
    from ozon_profit.core import connect
    from ozon_profit.seller_api import init_tables
    from ozon_profit.overview_reports import service_breakdown
    db=connect(':memory:');init_tables(db)
    for identifier,label,amount in [('1','Переупаковка','-10'),('2','Продвижение с оплатой за заказ','-100')]:
        payload=dict(non_item_fee=dict(type_name=label,accrued=dict(amount=amount)))
        db.execute('INSERT INTO api_finance VALUES(?,?,?,?)',(identifier,'2026-10-01',amount,json.dumps(payload)))
    db.commit()
    assert service_breakdown(db,'2026-10-01','2026-10-31')==[{'Категория услуги':'Переупаковка','Сумма, ₽':-10.0}]
    db.close()
