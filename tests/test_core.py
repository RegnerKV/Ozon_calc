from datetime import date
from decimal import Decimal as D
import pytest
from ozon_profit.core import connect, save_cost, import_operations, calculate, money, withdrawal, set_setting

@pytest.fixture
def db():
    connection = connect(':memory:')
    yield connection
    connection.close()


def row(id='sale1', kind='sale', amount='1000', qty='2', day='2026-09-05', sku='fish', order_no='order1'):
    return dict(id=id,kind=kind,amount=amount,qty=qty,day=day,sku=sku,order_no=order_no,name='Рыба')


def test_ledger_no_double_deduction_and_precise_cost(db):
    save_cost(db,'fish','Рыба','2026-09-01','123.005')
    import_operations(db,b'one','report.xlsx',[row(),row('fee','commission','-100','0'),row('ship','logistics','-50','0'),row('ad','advertising','-20','0')])
    r=calculate(db,'2026-09-01','2026-09-30')
    assert r['net']==D('830')
    assert r['costs']==D('246.010')
    assert r['tax']==D('40')
    assert r['profit']==D('543.990')


def test_same_file_is_idempotent(db):
    rows=[row()]
    assert import_operations(db,b'one','a.xlsx',rows)==1
    assert import_operations(db,b'one','copy.xlsx',rows)==0
    assert db.execute('SELECT count(*) FROM operations').fetchone()[0]==1


def test_correction_updates_past_period(db):
    import_operations(db,b'one','a',[row(amount='1000')])
    import_operations(db,b'two','b',[row(amount='900')])
    assert calculate(db,'2026-09-01','2026-09-30')['net']==D('900')
    assert db.execute('SELECT count(*) FROM operations').fetchone()[0]==1


def test_cost_history_and_estimate(db):
    save_cost(db,'fish','Рыба','2026-09-01','123.005',True)
    save_cost(db,'fish','Рыба','2026-10-01','300')
    import_operations(db,b'a','a',[row(qty=1),row('oct',day='2026-10-05',qty=1)])
    sep=calculate(db,'2026-09-01','2026-09-30')
    assert sep['costs']==D('123.005') and sep['estimated']==1
    assert calculate(db,'2026-10-01','2026-10-31')['costs']==D('300')


def test_missing_cost_is_incomplete(db):
    import_operations(db,b'a','a',[row()])
    r=calculate(db,'2026-09-01','2026-09-30')
    assert r['missing']==1 and r['incomplete']


def test_returns_do_not_automatically_restock(db):
    save_cost(db,'fish','Рыба','2026-09-01','100')
    import_operations(db,b'a','a',[row(qty=1),row('ret','return','-1000','1')])
    r=calculate(db,'2026-09-01','2026-09-30')
    assert r['costs']==100 and r['tax']==0 and r['profit']==-100


def test_restock_uses_original_cost(db):
    save_cost(db,'fish','Рыба','2026-09-01','100')
    save_cost(db,'fish','Рыба','2026-10-01','200')
    import_operations(db,b'a','a',[row(qty=1),row('ret','return','-1000','1',day='2026-10-03'),row('stock','restock','0','1',day='2026-10-05')])
    assert calculate(db,'2026-10-01','2026-10-31')['costs']==-100


def test_orphan_restock_does_not_create_profit(db):
    save_cost(db,'fish','Рыба','2026-09-01','100')
    import_operations(db,b'a','a',[row('stock','restock','0','1')])
    r=calculate(db,'2026-09-01','2026-09-30')
    assert r['costs']==0 and r['missing']==1


def test_points_and_tax_settings(db):
    import_operations(db,b'a','a',[row('p','points','50','0')])
    assert calculate(db,'2026-09-01','2026-09-30')['tax']==2
    set_setting(db,'tax_base','sales')
    assert calculate(db,'2026-09-01','2026-09-30')['tax']==0


def test_no_negative_tax_reserve(db):
    import_operations(db,b'a','a',[row('r','return','-1000','1')])
    assert calculate(db,'2026-09-01','2026-09-30')['tax']==0


def test_net_and_detail_cannot_mix(db):
    import_operations(db,b'a','a',[row('n','net','100','0')],'net')
    assert calculate(db,'2026-09-01','2026-09-30')['incomplete']
    with pytest.raises(ValueError): import_operations(db,b'b','b',[row()])
    assert db.execute('SELECT count(*) FROM imports').fetchone()[0]==1

@pytest.mark.parametrize('bad',[row(amount='NaN'),row(qty='-1'),row(sku=''),row(id=''),row('f','commission','100','0'),row(day='invalid')])
def test_invalid_import_is_atomic(db,bad):
    with pytest.raises(ValueError): import_operations(db,b'a','a',[row('valid'),bad])
    assert db.execute('SELECT count(*) FROM imports').fetchone()[0]==0
    assert db.execute('SELECT count(*) FROM operations').fetchone()[0]==0


def test_duplicate_ids_rejected(db):
    with pytest.raises(ValueError): import_operations(db,b'a','a',[row(),row()])


def test_money_and_cash():
    assert money('1\u00a0234,005 ₽')==D('1234.005')
    assert withdrawal('10000','400','3000','500')==6100
    assert withdrawal('100','400','0','0')==0


def test_database_survives_restart(tmp_path):
    path=tmp_path/'test.db'
    db=connect(path)
    save_cost(db,'fish','Рыба','2026-09-01','100')
    import_operations(db,b'a','a',[row(qty=1)])
    db.close()
    db=connect(path)
    assert calculate(db,'2026-09-01','2026-09-30')['profit']==860
    db.close()


def test_restock_cannot_exceed_sold_units(db):
    save_cost(db,'fish','Рыба','2026-09-01','100')
    import_operations(db,b'a','a',[row(qty=1),row('stock1','restock','0','1',day='2026-09-10'),row('stock2','restock','0','1',day='2026-09-11')])
    r=calculate(db,'2026-09-01','2026-09-30')
    assert r['costs']==0 and r['missing']==1


def test_restock_multiple_sale_lots_uses_historical_costs(db):
    save_cost(db,'fish','Рыба','2026-09-01','100')
    save_cost(db,'fish','Рыба','2026-09-10','200')
    import_operations(db,b'a','a',[row(qty=1),row('sale2',qty=1,day='2026-09-12'),row('stock','restock','0','2',day='2026-09-20')])
    r=calculate(db,'2026-09-01','2026-09-30')
    assert r['costs']==0 and r['missing']==0
