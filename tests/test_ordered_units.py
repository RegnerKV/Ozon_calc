import json
from datetime import date
from ozon_profit.core import connect
from ozon_profit.seller_api import init_tables,clean_posting
from ozon_profit.ordered_units import summary

START=END=date(2026,8,3)


def snapshot(db,postings):
    db.execute('INSERT INTO api_order_snapshots(start,end,payload) VALUES(?,?,?)',(str(START),str(END),json.dumps(postings)))


def posting(number,quantity,status='awaiting_packaging',stamp='2026-08-02T21:00:00Z'):
    return dict(posting_number=number,status=status,order_created_at=stamp,products=[dict(offer_id='demo',quantity=quantity)])


def test_units_not_postings_include_undelivered_and_cancelled():
    db=connect(':memory:');init_tables(db)
    snapshot(db,[posting('one',3),posting('two',2,'cancelled'),posting('old',8,stamp='2026-08-02T20:59:59Z'),posting('next',9,stamp='2026-08-03T21:00:00Z')])
    result=summary(db,START,END)
    assert result['total']==5 and result['cancelled']==2
    assert result['products']=={'demo':dict(total=5,cancelled=2)}
    assert db.execute('SELECT count(*) FROM operations').fetchone()[0]==0


def test_missing_coverage_is_not_zero_but_verified_empty_is_zero():
    db=connect(':memory:');init_tables(db)
    assert summary(db,START,END)['total'] is None
    snapshot(db,[])
    assert summary(db,START,END)['total']==0
    assert summary(db,date(2026,8,2),END)['total'] is None


def test_invalid_date_or_quantity_blocks_total_instead_of_partial_count():
    db=connect(':memory:');init_tables(db)
    for bad in [posting('bad',1,stamp=None),posting('bad',None),posting('bad',True),posting('bad',1,stamp='2026-08-03')]:
        snapshot(db,[posting('ok',3),bad])
        assert summary(db,START,END)['total'] is None


def test_cleaner_preserves_creation_date_independently_of_finance_date():
    raw=dict(posting_number='one',status='delivered',created_at='2026-08-02T22:00:00Z',in_process_at='2026-08-04T00:00:00Z',products=[dict(quantity=3,offer_id='demo')])
    clean=clean_posting(raw,'fbo')
    assert clean['order_created_at']==raw['created_at']
    assert clean['created_at']==raw['in_process_at']


def test_order_metrics_render_without_financial_sales(tmp_path,monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv('OZON_PROFIT_DATA_DIR',str(tmp_path))
    db=connect();init_tables(db)
    snapshot(db,[posting('one',3),posting('cancelled',2,'cancelled')]);db.commit();db.close()
    app=AppTest.from_file('app.py',default_timeout=20).run()
    app.selectbox(key='overview_period').set_value('Свои даты').run()
    app.date_input(key='period_start').set_value(START)
    app.date_input(key='period_end').set_value(END).run()
    assert not app.exception
    assert next(m for m in app.metric if m.label=='Заказано, шт.').value=='5'
    assert next(m for m in app.metric if m.label=='Из них отменено, шт.').value=='2'


def test_fbs_uses_agreed_processing_date_without_changing_finance_date():
    raw=dict(posting_number='one',status='delivering',created_at='2026-08-02T22:00:00Z',in_process_at='2026-08-04T00:00:00Z',products=[dict(quantity=3,offer_id='demo')])
    clean=clean_posting(raw,'fbs')
    assert clean['order_created_at']==raw['in_process_at']==clean['created_at']


def test_parent_and_split_compositions_are_not_double_counted():
    db=connect(':memory:');init_tables(db)
    snapshot(db,[posting('original',3),{**posting('child',3),'parent_posting_number':'original'}])
    assert summary(db,START,END)['total'] is None
