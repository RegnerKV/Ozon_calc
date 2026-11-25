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
    snapshot(db,[posting('root',5,'cancelled_from_split_pending'),
        {**posting('one',3),'parent_posting_number':'root'},
        {**posting('cancelled',2,'cancelled'),'parent_posting_number':'root'}]);db.commit();db.close()
    app=AppTest.from_file('app.py',default_timeout=20).run()
    app.selectbox(key='overview_period').set_value('Свои даты').run()
    app.date_input(key='period_start').set_value(START)
    app.date_input(key='period_end').set_value(END).run()
    assert not app.exception
    assert next(m for m in app.metric if m.label=='Заказано, шт.').value=='5 шт.'
    assert next(m for m in app.metric if m.label=='Отменено, шт.').value=='2 шт.'
    table=next(d.value for d in app.dataframe if 'Заказано, шт.' in d.value.columns)
    assert list(table.columns[:6])==['Артикул','Изделие','Заказано, шт.','Продано, шт.','Отменено, шт.','Возвраты, шт.']
    assert len(table)==1 and table.iloc[0]['Заказано, шт.']==5
    assert table.iloc[0]['Продано, шт.'] is None
    assert next(m for m in app.metric if m.label=='Заказано по артикулу').value=='5 шт.'
    assert next(m for m in app.metric if m.label=='Отменено по артикулу').value=='2 шт.'
    assert next(m for m in app.metric if m.label=='Продано по артикулу').value=='Нет данных'
    assert next(m for m in app.metric if m.label=='ДРР артикула относительно предельной цены').value=='Нет данных'


def test_fbs_uses_agreed_processing_date_without_changing_finance_date():
    raw=dict(posting_number='one',status='delivering',created_at='2026-08-02T22:00:00Z',in_process_at='2026-08-04T00:00:00Z',products=[dict(quantity=3,offer_id='demo')])
    clean=clean_posting(raw,'fbs')
    assert clean['order_created_at']==raw['in_process_at']==clean['created_at']


def test_parent_and_split_compositions_are_not_double_counted():
    db=connect(':memory:');init_tables(db)
    snapshot(db,[posting('original',3),{**posting('child',3),'parent_posting_number':'original'}])
    assert summary(db,START,END)['total']==3
    assert summary(db,START,END)['cancelled']==0


def test_product_counts_distinguish_no_orders_from_unknown_coverage():
    from ozon_profit.ordered_units import product_counts
    db=connect(':memory:');init_tables(db)
    assert product_counts(summary(db,START,END),'missing')==dict(total=None,cancelled=None)
    snapshot(db,[])
    assert product_counts(summary(db,START,END),'missing')==dict(total=0,cancelled=0)


def test_split_parent_is_not_counted_as_cancelled_and_children_supply_outcomes():
    db=connect(':memory:');init_tables(db)
    snapshot(db,[posting('original',3,'cancelled_from_split_pending'),
        {**posting('child-one',2,'delivered'),'parent_posting_number':'original'},
        {**posting('child-two',1,'cancelled'),'parent_posting_number':'original'}])
    result=summary(db,START,END)
    assert result['total']==3 and result['cancelled']==1
    assert result['products']=={'demo':dict(total=3,cancelled=1)}


def test_nested_split_uses_root_date_and_counts_each_unit_once():
    db=connect(':memory:');init_tables(db)
    snapshot(db,[posting('root',3,'cancelled_from_split_pending'),
        {**posting('middle',2,'cancelled_from_split_pending',stamp='2026-08-04T00:00:00Z'),'parent_posting_number':'root'},
        {**posting('other',1,'delivered',stamp='2026-08-04T00:00:00Z'),'parent_posting_number':'root'},
        {**posting('leaf-one',1,'cancelled',stamp='2026-08-04T00:00:00Z'),'parent_posting_number':'middle'},
        {**posting('leaf-two',1,'delivered',stamp='2026-08-04T00:00:00Z'),'parent_posting_number':'middle'}])
    result=summary(db,START,END)
    assert result['total']==3 and result['cancelled']==1


def test_split_outside_period_does_not_create_orders_in_child_period():
    db=connect(':memory:');init_tables(db)
    snapshot(db,[posting('root',2,'cancelled_from_split_pending',stamp='2026-08-01T00:00:00Z'),
        {**posting('child',2),'parent_posting_number':'root'}])
    assert summary(db,START,END)['total']==0


def test_partial_split_keeps_known_ordered_units_but_not_invented_cancellations():
    db=connect(':memory:');init_tables(db)
    snapshot(db,[posting('root',3,'cancelled_from_split_pending'),
        {**posting('child',1,'cancelled'),'parent_posting_number':'root'},posting('other',2,'cancelled')])
    result=summary(db,START,END)
    assert result['total']==5 and result['cancelled'] is None
    assert result['products']['demo']['total']==5
    assert 'Отмены не подтверждены' in result['reason']


def test_split_reconciliation_matches_articles_not_just_total_units():
    db=connect(':memory:');init_tables(db)
    snapshot(db,[posting('root',3),
        {**posting('child',3,'cancelled'),'parent_posting_number':'root','products':[dict(offer_id='different',quantity=3)]}])
    result=summary(db,START,END)
    assert result['total'] is None and result['cancelled'] is None
    assert 'противоречит' in result['reason']


def test_technical_split_without_parts_keeps_order_quantity_only():
    db=connect(':memory:');init_tables(db)
    snapshot(db,[posting('root',3,'cancelled_from_split_pending')])
    assert summary(db,START,END)['total']==3
    assert summary(db,START,END)['cancelled'] is None


def test_cycles_do_not_recurse_or_double_count():
    db=connect(':memory:');init_tables(db)
    snapshot(db,[{**posting('one',2),'parent_posting_number':'two'},
        {**posting('two',2),'parent_posting_number':'one'}])
    assert summary(db,START,END)['total'] is None


def test_previously_saved_snapshot_is_counted_without_new_api_requests():
    db=connect(':memory:')
    db.execute('DROP TABLE IF EXISTS api_order_snapshots')
    db.execute('CREATE TABLE api_order_snapshots(id INTEGER PRIMARY KEY,start TEXT,end TEXT,payload TEXT)')
    snapshot(db,[posting('root',3,'cancelled_from_split_pending'),
        {**posting('child',3,'cancelled'),'parent_posting_number':'root'}]);db.commit()
    assert summary(db,START,END)['total']==3
    assert summary(db,START,END)['cancelled']==3
    init_tables(db)
    assert summary(db,START,END)['total']==3
    assert db.execute('SELECT schemes FROM api_order_snapshots').fetchone()[0]=='fbo,fbs'


def test_split_quantities_and_cancellations_remain_separate_per_article():
    db=connect(':memory:');init_tables(db)
    snapshot(db,[{**posting('root',2,'cancelled_from_split_pending'),
        'products':[dict(offer_id='a',quantity=2),dict(offer_id='b',quantity=1)]},
        {**posting('one',2,'delivered'),'parent_posting_number':'root','products':[dict(offer_id='a',quantity=2)]},
        {**posting('two',1,'cancelled'),'parent_posting_number':'root','products':[dict(offer_id='b',quantity=1)]}])
    result=summary(db,START,END)
    assert result['total']==3 and result['cancelled']==1
    assert result['products']=={'a':dict(total=2,cancelled=0),'b':dict(total=1,cancelled=1)}
