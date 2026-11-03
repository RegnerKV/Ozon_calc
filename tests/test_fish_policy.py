"""Owner's accepted rule tested with fictional recipe prices."""
from decimal import Decimal
from ozon_profit.core import connect,save_cost,set_setting,import_operations,calculate
from ozon_profit.catalog import apply_library,apply_fish_policy,FISH_POLICY


def store():
    db=connect(':memory:')
    set_setting(db,'fish_cost_policy','1')
    with db:
        db.execute('INSERT INTO cost_library VALUES(?,?,?)',('Подвеска Рыба','2026-09-01','17.005'))
        db.executemany('INSERT INTO products VALUES(?,?)',[('malina','Малина'),('earrings','Серьги Сирень')])
    return db


def test_unknown_articles_get_accepted_cost_and_profit_including_past_sales():
    db=store();apply_library(db)
    import_operations(db,b'demo','fictional.xlsx',[
        dict(id='sale',day='2026-08-01',kind='sale',sku='earrings',name='Сирень',amount=100,qty=2)])
    result=calculate(db,'2026-08-01','2026-08-01')
    assert result['costs']==Decimal('34.010')
    assert result['missing']==result['estimated']==0
    assert result['profit']==Decimal('61.990')
    row=db.execute('SELECT * FROM costs WHERE sku=?',('earrings',)).fetchone()
    assert row['note'].startswith(FISH_POLICY)
    db.execute('INSERT INTO products VALUES(?,?)',('future','Новый артикул'));db.commit()
    apply_library(db)
    assert db.execute('SELECT amount FROM costs WHERE sku=?',('future',)).fetchone()[0]=='17.005'
    db.close()


def test_migrates_previous_fish_estimate_but_preserves_own_cost_and_other_estimates():
    db=store()
    save_cost(db,'malina','Малина','2026-09-01','17.005',True,'Из сохранённой таблицы: Подвеска Рыба; Оценка по «Рыбе»')
    save_cost(db,'own','Своё изделие','2026-08-01','9.7')
    save_cost(db,'estimated','Другая оценка','2026-08-01','4',True,'Ручная оценка')
    apply_library(db);apply_library(db)
    assert db.execute('SELECT max(estimated) FROM costs WHERE sku=?',('malina',)).fetchone()[0]==0
    assert db.execute('SELECT amount FROM costs WHERE sku=?',('own',)).fetchone()[0]=='9.7'
    assert db.execute('SELECT estimated FROM costs WHERE sku=?',('estimated',)).fetchone()[0]==1
    assert db.execute('SELECT count(*) FROM costs WHERE sku=?',('malina',)).fetchone()[0]==2
    db.close()


def test_fish_history_changes_future_cost_without_overriding_later_own_cost():
    db=store();apply_library(db)
    db.execute('INSERT INTO cost_library VALUES(?,?,?)',('Подвеска Рыба','2026-10-01','19.25'));db.commit()
    apply_library(db)
    assert db.execute('SELECT amount FROM costs WHERE sku=? AND since=?',('earrings','2026-09-01')).fetchone()[0]=='17.005'
    assert db.execute('SELECT amount FROM costs WHERE sku=? AND since=?',('earrings','2026-10-01')).fetchone()[0]=='19.25'
    save_cost(db,'earrings','Серьги Сирень','2026-09-15','11')
    apply_library(db)
    latest=db.execute('SELECT amount FROM costs WHERE sku=? ORDER BY since DESC LIMIT 1',('earrings',)).fetchone()[0]
    assert latest=='11'
    db.close()


def test_without_fish_recipe_no_cost_is_invented():
    db=connect(':memory:');set_setting(db,'fish_cost_policy','1')
    db.execute('INSERT INTO products VALUES(?,?)',('unknown','Неизвестный товар'));db.commit()
    apply_fish_policy(db)
    assert not db.execute('SELECT 1 FROM costs').fetchone()
    db.close()
