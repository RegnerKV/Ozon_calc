"""Synthetic structural fixtures only. No customer spreadsheets or transactions."""
from datetime import datetime, date
from decimal import Decimal as D
from io import BytesIO
from openpyxl import Workbook
import pytest
from ozon_profit.ozon_excel import parse_accruals, parse_costs, parse_sales, reconcile, apply_report
from ozon_profit.core import connect, calculate, set_setting, import_operations

HEADERS=['ID начисления','Дата начисления','Группа услуг','Тип начисления','Артикул','SKU','Название товара','Количество','Сумма итого, руб.']


def xlsx(name,rows):
    book=Workbook();sheet=book.active;sheet.title=name
    for row in rows:sheet.append(row)
    output=BytesIO();book.save(output);return output.getvalue()


def accruals(extra=None,revenue=500):
    rows=[['Период: 01.08.2026-31.08.2026'],HEADERS,
          ['100-200-1',datetime(2026,8,3),'Продажи','Выручка','demo','987','Демо',1,revenue],
          ['100-200-1',datetime(2026,8,3),'Продажи','Баллы за скидки','demo','987','Демо',1,100],
          ['100-200-1',datetime(2026,8,3),'Продажи','Программы партнёров','demo','987','Демо',1,5],
          ['100-200-1',datetime(2026,8,3),'Вознаграждение Ozon','Вознаграждение за продажу','demo','987','Демо',1,-200]]
    return xlsx('Начисления',rows+(extra or []))


def sales(revenue=500):
    return xlsx('Отчет о реализации',[
        ['Реализация товаров за период с 01.08.2026 по 31.08.2026'],
        ['№ п/п','Название товара','Артикул','SKU','Штрих-код','Реализовано',None,None,None,'Возвращено клиентом'],
        [None,None,None,None,None,'Реализовано на сумму, руб.','Выплаты по механикам лояльности партнёров, руб.','Кол-во','Цена реализации, руб.','Возвращено на сумму, руб.','Выплаты по механикам лояльности партнёров, руб.','Кол-во'],
        [None,1,2,3,4,5,6,7,8,9,10,11],
        [1,'Демо','demo','987','CODE',revenue,5,1,revenue,None,None,None],
        [None,None,None,None,None,revenue,5],
    ])


def cost_records():
    return [dict(sku='demo',name='Демо',amount='80.005',estimated=True,note='Synthetic')]


def test_native_report_types_and_shared_ids():
    r=parse_accruals(accruals())
    assert len(r.rows)==4 and len({row['id'] for row in r.rows})==4
    assert r.net==405 and r.raw_count==4
    assert {row['kind'] for row in r.rows}=={'sale','points','partner','commission'}
    assert sum(row['qty'] for row in r.rows)==1


def test_blank_id_fees_same_day_are_summed():
    extra=[[None,datetime(2026,8,4),'Другие услуги и штрафы','Досрочная выплата',None,None,None,0,-3],
           [None,datetime(2026,8,4),'Другие услуги и штрафы','Досрочная выплата',None,None,None,0,-7]]
    r=parse_accruals(accruals(extra))
    assert r.net==395 and len(r.rows)==5 and r.raw_count==6


def test_positive_fee_is_refund_not_sale():
    r=parse_accruals(accruals([['acq',datetime(2026,8,4),'Услуги партнёров','Эквайринг','demo','987','Демо',1,3]]))
    assert r.rows[-1]['kind']=='service_refund'
    db=connect(':memory:')
    apply_report(db,b'a','synthetic',r,cost_records(),date(2026,8,1),'sales')
    result=calculate(db,'2026-08-01','2026-08-31')
    assert result['revenue']==500 and result['expenses']==197
    db.close()


def test_packaging_blocks_ignore_materials_and_subtotals():
    content=xlsx('Украшения',[
        ['Название','Материалы','Кол-во','Цена за шт','Итого'],
        ['Демо','Бусина',1,4,4],['Итого',None,None,None,4],
        ['С упаковкой:',None,None,None,84.005],
    ])
    assert parse_costs(content)==[{'name':'Демо','amount':D('84.005')}]


def test_missing_cached_cost_rejected():
    content=xlsx('Украшения',[
        ['Название'],['Демо'],['С упаковкой:',None,None,None,'=1+2'],
    ])
    with pytest.raises(ValueError):parse_costs(content)


def test_sales_report_and_finance_match():
    r=parse_sales(sales())
    assert r.rows==1 and r.products['demo']['sold']==1
    assert reconcile(parse_accruals(accruals()),r)==[]


def test_reconciliation_difference_visible():
    differences=reconcile(parse_accruals(accruals()),parse_sales(sales(499)))
    assert differences==[dict(sku='demo',field='revenue',accruals='500',sales='499')]


def test_unknown_type_does_not_silently_import():
    with pytest.raises(ValueError,match='Новый тип'):
        parse_accruals(accruals([['x',datetime(2026,8,4),'Новая группа','Неизвестно',None,None,None,0,-3]]))


def test_snapshot_update_deletes_disappeared_rows():
    db=connect(':memory:')
    first=accruals([['x',datetime(2026,8,4),'Другие услуги и штрафы','Досрочная выплата',None,None,None,0,-10]])
    second=accruals(revenue=450)
    assert apply_report(db,first,'first',parse_accruals(first),cost_records(),date(2026,8,1),'sales')==5
    assert apply_report(db,second,'second',parse_accruals(second),[],date(2026,8,1),'sales')==4
    result=calculate(db,'2026-08-01','2026-08-31')
    assert result['count']==4 and result['net']==355 and result['tax']==18
    assert result['profit']==D('256.995')
    assert apply_report(db,second,'copy',parse_accruals(second),[],date(2026,8,1),'sales')==0
    assert db.execute('SELECT count(*) FROM costs').fetchone()[0]==1
    db.close()


def test_manual_overlap_blocked_without_modification():
    db=connect(':memory:')
    import_operations(db,b'manual','manual',[dict(id='man',day='2026-08-04',kind='service',amount=-2,qty=0)])
    content=accruals()
    with pytest.raises(ValueError,match='ручного'):
        apply_report(db,content,'test',parse_accruals(content),cost_records(),date(2026,8,1),'sales')
    assert db.execute('SELECT count(*) FROM operations').fetchone()[0]==1
    assert db.execute('SELECT count(*) FROM costs').fetchone()[0]==0
    db.close()


def test_invalid_cost_does_not_replace_snapshot():
    db=connect(':memory:');content=accruals()
    apply_report(db,content,'one',parse_accruals(content),cost_records(),date(2026,8,1),'sales')
    new=accruals(revenue=200)
    with pytest.raises(ValueError):apply_report(db,new,'two',parse_accruals(new),[dict(sku='demo',name='Test',amount=-1,estimated=False)],date(2026,8,1),'sales')
    assert calculate(db,'2026-08-01','2026-08-31')['net']==405
    db.close()

@pytest.mark.parametrize('base,tax',[('sales','20'),('sales_partner','20.20'),('sales_points','24'),('sales_points_partner','24.20')])
def test_explicit_tax_bases(base,tax):
    db=connect(':memory:');content=accruals()
    apply_report(db,content,'test',parse_accruals(content),cost_records(),date(2026,8,1),base)
    assert calculate(db,'2026-08-01','2026-08-31')['tax']==D(tax)
    db.close()
