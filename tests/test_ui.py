from streamlit.testing.v1 import AppTest
import pytest

@pytest.mark.parametrize('page',['Обзор','Товары','План продаж','Настройки'])
def test_page_starts(tmp_path,monkeypatch,page):
    monkeypatch.setenv('OZON_PROFIT_DATA_DIR',str(tmp_path))
    app=AppTest.from_file('app.py',default_timeout=20).run()
    app.sidebar.radio[0].set_value(page).run()
    assert not app.exception


def test_add_cost_through_ui(tmp_path,monkeypatch):
    monkeypatch.setenv('OZON_PROFIT_DATA_DIR',str(tmp_path))
    app=AppTest.from_file('app.py').run()
    app.sidebar.radio[0].set_value('Товары').run()
    app.text_input[0].set_value('MALINA')
    app.text_input[1].set_value('Малина')
    app.text_input[2].set_value('123,005')
    app.checkbox[0].check()
    next(b for b in app.button if b.label == 'Сохранить').click().run()
    assert not app.exception
    from ozon_profit.core import connect
    db=connect(tmp_path/'profit.db')
    record=db.execute('SELECT * FROM costs').fetchone()
    assert record['amount']=='123.005' and record['estimated']==1


def test_dashboard_with_imported_data(tmp_path,monkeypatch):
    from datetime import date
    from ozon_profit.core import connect, save_cost, import_operations
    monkeypatch.setenv('OZON_PROFIT_DATA_DIR',str(tmp_path))
    db=connect(tmp_path/'profit.db')
    save_cost(db,'FISH','Рыба','2026-09-01','123.005')
    import_operations(db,b'fixture','synthetic.xlsx',[
        dict(id='s',day='2026-09-05',kind='sale',amount='1000',qty='2',sku='FISH',name='Рыба',order_no='001'),
        dict(id='f',day='2026-09-05',kind='commission',amount='-150',qty='0',sku='',name='',order_no=''),
    ])
    db.close()
    app=AppTest.from_file('app.py').run()
    assert not app.exception
    assert any(metric.value=='563,99 ₽' for metric in app.metric)
    app.text_input(key='order_filter').set_value('001').run()
    assert len(app.dataframe[-1].value)==1


def test_api_key_stays_in_session_and_input_is_cleared(tmp_path,monkeypatch):
    monkeypatch.setenv('OZON_PROFIT_DATA_DIR',str(tmp_path))
    from ui_harness import technical_app
    app=technical_app().run()
    app.text_input(key='seller_client_input').set_value('123')
    app.text_input(key='seller_key_input').set_value('synthetic-test-key')
    next(b for b in app.button if b.label=='Сохранить подключение').click().run()
    assert not app.exception
    assert app.text_input(key='seller_key_input').value==''
    assert app.session_state['_seller_pair']==('123','synthetic-test-key')
    assert not list(tmp_path.glob('*.credentials'))
    next(b for b in app.button if b.label=='Забыть ключ на этом компьютере').click().run()
    assert app.session_state['_seller_pair'] is None
