from streamlit.testing.v1 import AppTest


def technical_app(kind='api'):
    module,function=('ozon_profit.api_ui','connection_ui') if kind=='api' else ('ozon_profit.import_ui','native_import')
    return AppTest.from_string(f'''import streamlit as st
from ozon_profit.core import connect
from ozon_profit.seller_api import init_tables
from {module} import {function}
st.set_page_config(layout='wide')
db=connect()
init_tables(db)
{function}(db)
db.close()
''',default_timeout=20)
