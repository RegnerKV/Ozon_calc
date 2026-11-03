from io import BytesIO
import sqlite3
from streamlit.testing.v1 import AppTest
from ozon_profit.core import connect,save_cost,set_setting,import_operations,calculate,get_setting
from ozon_profit.catalog import import_library,apply_library
from ozon_profit.secure_store import load_pair
from test_ozon_excel import xlsx


def setup_store(tmp_path):
    db=connect(tmp_path/'profit.db')
    save_cost(db,'demo','Демо','2026-01-01','20')
    save_cost(db,'new','Новое изделие','2026-01-01','30',True)
    import_operations(db,b'fictional','fictional.xlsx',[
        dict(id='s',day='2026-08-01',kind='sale',amount=1000,qty=10,sku='demo',name='Демо'),
        dict(id='c',day='2026-08-01',kind='commission',amount=-200,qty=0,sku='demo'),
        dict(id='a',day='2026-08-01',kind='advertising',amount=-100,qty=0,sku=''),
        dict(id='x',day='2026-08-01',kind='service',amount=-50,qty=0,sku=''),
    ])
    set_setting(db,'tax_base','sales')
    db.close()


def test_plan_ui_persists_after_new_session_and_extra_ads_reduce_profit(tmp_path,monkeypatch):
    monkeypatch.setenv('OZON_PROFIT_DATA_DIR',str(tmp_path));setup_store(tmp_path)
    app=AppTest.from_file('app.py').run()
    assert app.sidebar.radio[0].options==['Обзор','Товары','План продаж','Настройки']
    app.sidebar.radio[0].set_value('План продаж').run()
    app.selectbox(key='plan_product').set_value('demo')
    next(b for b in app.button if b.label=='Добавить в план').click().run()
    assert not app.exception
    assert any(m.value=='2 250,00 ₽' for m in app.metric)
    app.selectbox(key='plan_product').set_value('new')
    app.number_input(key='plan_price').set_value(100)
    next(b for b in app.button if b.label=='Добавить в план').click().run()
    app.number_input(key='plan_ad_budget').set_value(500).run()
    assert not app.exception
    assert any(m.value=='3 500,00 ₽' for m in app.metric)
    reopened=AppTest.from_file('app.py').run()
    reopened.sidebar.radio[0].set_value('План продаж').run()
    assert not reopened.exception
    assert reopened.number_input(key='plan_ad_budget').value==500
    assert any(m.value=='3 500,00 ₽' for m in reopened.metric)
    db=connect(tmp_path/'profit.db')
    assert calculate(db,'2026-08-01','2026-08-31')['net']==650
    db.close()


def test_web_key_saved_once_is_reloaded_after_session_reset(tmp_path,monkeypatch):
    monkeypatch.setenv('OZON_PROFIT_DATA_DIR',str(tmp_path))
    from ozon_profit import ui_pages
    monkeypatch.setattr(ui_pages,'refresh',lambda db:None)
    password='synthetic-owner-password-123'
    app=AppTest.from_file('app.py');app.secrets['app_password']=password
    app.run();app.sidebar.radio[0].set_value('Настройки').run()
    app.text_input(key='simple_client').set_value('123')
    app.text_input(key='simple_api_key').set_value('synthetic-key-only')
    next(b for b in app.button if b.label=='Сохранить подключение').click().run()
    assert not app.exception
    assert app.text_input(key='simple_api_key').value==''
    db=connect(tmp_path/'profit.db')
    assert load_pair(db,password)==('123','synthetic-key-only');db.close()
    reopened=AppTest.from_file('app.py');reopened.secrets['app_password']=password
    reopened.run();reopened.sidebar.radio[0].set_value('Настройки').run()
    assert not reopened.exception
    assert reopened.session_state['_seller_pair']==('123','synthetic-key-only')
    assert any('ключ сохранён' in c.value for c in reopened.caption)


def test_cost_library_loaded_once_applies_to_future_catalog_without_overwriting_manual():
    db=connect(':memory:')
    content=xlsx('Украшения',[
        ['Название','Материалы','Количество','Цена','Итого'],['Демо','Материал',1,4,4],
        ['С упаковкой:',None,None,None,20]])
    assert import_library(db,content,'2026-01-01')==1
    with db:db.execute('INSERT INTO products VALUES(?,?)',('demo','Демо'))
    apply_library(db)
    assert db.execute('SELECT amount FROM costs').fetchone()[0]=='20'
    save_cost(db,'demo','Демо','2026-02-01',25,False,'Ручная стоимость')
    updated=xlsx('Украшения',[
        ['Название','Материалы','Количество','Цена','Итого'],['Демо','Материал',1,4,4],
        ['С упаковкой:',None,None,None,40]])
    import_library(db,updated,'2026-03-01')
    assert db.execute("SELECT amount FROM costs WHERE sku='demo' ORDER BY since DESC LIMIT 1").fetchone()[0]=='25'
    db.close()
