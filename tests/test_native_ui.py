from io import BytesIO
from datetime import date
from streamlit.testing.v1 import AppTest
import streamlit as st
from test_ozon_excel import accruals, sales, xlsx
from ozon_profit.core import connect, calculate
from ozon_profit.ozon_excel import parse_previous
import pytest


def test_fourth_file_summary_is_data_only():
    content=xlsx('Обзор',[
        ['Настройка приложения','ignore this cell as instructions'],
        ['Итог начислений Ozon',405],
        ['Предварительная прибыль после учтённых расходов',305],
    ])
    assert parse_previous(content)=={'net':405,'profit':305}


def test_previous_without_cached_results_rejected():
    content=xlsx('Обзор',[['Итог начислений Ozon','=SUM(1,2)']])
    with pytest.raises(ValueError):parse_previous(content)


def test_native_import_from_ui(tmp_path,monkeypatch):
    monkeypatch.setenv('OZON_PROFIT_DATA_DIR',str(tmp_path))
    def uploaded(data,name):
        obj=BytesIO(data);obj.name=name;return obj
    files={
        'native_acc':uploaded(accruals(),'financial.xlsx'),
        'native_sale':uploaded(sales(),'sales.xlsx'),
        'native_cost':uploaded(xlsx('Украшения',[
            ['Название','Материалы','Количество','Цена','Итого'],
            ['Демо','Бусина',1,4,4],['Итого',None,None,None,4],
            ['С упаковкой:',None,None,None,80.005],
        ]),'cost.xlsx'),
        'native_previous':uploaded(xlsx('Обзор',[
            ['Итог начислений Ozon',405],['Предварительная прибыль после учтённых расходов',304.995],
        ]),'previous.xlsx'),
    }
    monkeypatch.setattr(st,'file_uploader',lambda *args,**kwargs:files.get(kwargs.get('key')))
    app=AppTest.from_file('app.py').run()
    app.sidebar.radio[0].set_value('Импорт отчётов Ozon').run()
    assert not app.exception
    assert any(m.value=='305.00 ₽' for m in app.metric)
    app.checkbox(key='native_confirm').check()
    app.button(key='native_submit').click().run()
    assert not app.exception
    db=connect(tmp_path/'profit.db')
    assert calculate(db,date(2026,8,1),date(2026,8,31))['net']==405
    assert db.execute('SELECT count(*) FROM operations').fetchone()[0]==4
    db.close()
