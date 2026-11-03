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
    next(b for b in app.button if b.label == 'Сохранить').click().run()
    assert not app.exception
    from ozon_profit.core import connect
    db=connect(tmp_path/'profit.db')
    record=db.execute('SELECT * FROM costs').fetchone()
    assert record['amount']=='123.005' and record['estimated']==0


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
    app.selectbox(key='overview_period').set_value('Свои даты').run()
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


def test_overview_periods_current_month_and_previous_month(tmp_path,monkeypatch):
    from datetime import date
    from ozon_profit import ui_pages
    monkeypatch.setenv('OZON_PROFIT_DATA_DIR',str(tmp_path))
    monkeypatch.setattr(ui_pages,'today',lambda:date(2026,11,3))
    app=AppTest.from_file('app.py').run()
    select=app.selectbox(key='overview_period')
    assert select.options==['Текущий месяц','Прошлый месяц','За 7 дней','За последние 30 дней','Свои даты']
    assert any('01.11.2026 — 03.11.2026' in c.value for c in app.caption)
    select.set_value('Прошлый месяц').run()
    assert any('01.10.2026 — 31.10.2026' in c.value for c in app.caption)
    assert not any(h.value=='Личные деньги' for h in app.subheader)
    assert any(m.label=='Налоги (4%)' for m in app.metric)
    assert any(m.label=='Процент чистой прибыли' for m in app.metric)
    app.selectbox(key='overview_period').set_value('За последние 30 дней').run()
    assert any('05.10.2026 — 03.11.2026' in c.value for c in app.caption)


def test_final_preview_hides_estimated_costs_and_allocation_picker(tmp_path,monkeypatch):
    from ozon_profit.core import connect,save_cost,import_operations
    monkeypatch.setenv('OZON_PROFIT_DATA_DIR',str(tmp_path))
    db=connect(tmp_path/'profit.db');save_cost(db,'demo','Демо','2026-01-01','10',True)
    import_operations(db,b'preview-fixture','fictional.xlsx',[dict(id='s',day='2026-10-01',kind='sale',sku='demo',qty=1,amount=100)])
    db.close()
    app=AppTest.from_file('app.py').run()
    app.selectbox(key='overview_period').set_value('Свои даты').run()
    assert not app.exception
    assert not any(s.key=='article_allocation' for s in app.selectbox)
    app.sidebar.radio[0].set_value('Товары').run()
    assert not app.exception
    assert not any(c.label=='Примерная себестоимость' for c in app.checkbox)
    for frame in app.dataframe:
        assert 'Оценка' not in frame.value.columns and 'estimated' not in frame.value.columns
