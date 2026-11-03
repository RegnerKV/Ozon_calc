from datetime import timedelta
import time
import streamlit as st
from ozon_profit.access import require_login,local_mode
from ozon_profit.core import connect,get_setting,set_setting
from ozon_profit.runtime import state_manager
from ozon_profit.secure_store import StorageError
from ozon_profit.seller_api import init_tables,ApiError
from ozon_profit.ui_pages import overview,products_page,settings_page,ensure_profit,refresh,today
from ozon_profit.api_ui import active_credentials

st.set_page_config(page_title='Ozon Profit',page_icon='💎',layout='wide')
require_login()
st.markdown('''<style>
.stApp{background:#f8f7f4;color:#182336}
.block-container{max-width:1320px;padding-top:4rem;padding-bottom:3rem}
h1,h2,h3{letter-spacing:-.035em;color:#182336}
[data-testid="stSidebar"]{background:#fff;border-right:1px solid #e8e7e3}
[data-testid="stMetric"]{background:#fff;border:1px solid #ebe9e3;border-radius:18px;padding:20px 22px;box-shadow:0 4px 22px rgba(30,35,55,.025)}
[data-testid="stMetricValue"]{font-variant-numeric:tabular-nums;font-size:1.85rem;letter-spacing:-.04em}
[data-testid="stMetricLabel"]{color:#6a7283;font-size:.85rem}
.stButton>button[kind="primary"]{background:#5369d8;border-color:#5369d8;border-radius:10px}
[data-testid="stExpander"]{background:rgba(255,255,255,.7);border-radius:14px;border-color:#e8e7e3}
[data-testid="stAlert"]{border-radius:12px}
</style>''',unsafe_allow_html=True)
try:manager=state_manager()
except StorageError as exc:
    st.error(str(exc));st.info('Данные не сбрасывались. Проверьте настройки постоянного хранилища в Secrets Streamlit.');st.stop()
db=connect();init_tables(db)
st.sidebar.markdown('## 💎 Ozon Profit')
st.sidebar.caption('Ваш магазин в цифрах')
storage_status=st.sidebar.empty()
page=st.sidebar.radio('Раздел',['Обзор','Товары','План продаж','Настройки'],label_visibility='collapsed')
# Retain an already entered session key during the first migration to encrypted storage.
if st.session_state.get('_seller_pair') and not db.execute('SELECT 1 FROM secure_items WHERE name=?',('seller',)).fetchone():
    from ozon_profit.runtime import secret,save_api_credentials
    if secret('app_password'):
        try:save_api_credentials(db,*st.session_state['_seller_pair'])
        except StorageError:pass
try:
    from ozon_profit.catalog import apply_library
    if get_setting(db,'fish_cost_policy',None) is None:set_setting(db,'fish_cost_policy','1')
    apply_library(db)
    ensure_profit(db)
    if not st.session_state.get('_auto_attempted'):
        st.session_state['_auto_attempted']=True
        if get_setting(db,'api_on_start','1')=='1' and active_credentials():
            stamp=get_setting(db,'api_last_success')
            from datetime import datetime,timezone
            try:fresh=bool(stamp) and (datetime.now(timezone.utc)-datetime.fromisoformat(stamp.replace('Z','+00:00'))).total_seconds()<900
            except ValueError:fresh=False
            if not fresh:
                try:
                    with st.spinner('Обновляем магазин…'):refresh(db)
                except ApiError as exc:st.warning(str(exc))
                except Exception:st.warning('Автообновление не завершено. Сохранённые данные доступны.')
    if page=='Обзор':overview(db)
    elif page=='Товары':products_page(db)
    elif page=='План продаж':
        from ozon_profit.plan_ui import plan_page
        plan_page(db)
    else:settings_page(db,manager)
finally:
    manager.persist(db)
    db.close()
if manager.store:storage_status.caption('● Постоянное хранение настроено' if not manager.last_error else '● Есть несохранённые изменения — проверьте настройки')
elif local_mode():storage_status.caption('● Данные на вашем компьютере')
else:storage_status.caption('Постоянное хранение · подключите в настройках')


@st.fragment(run_every=60)
def automatic_updates():
    periodic=connect()
    try:
        interval=int(get_setting(periodic,'api_interval','60'))
        last=st.session_state.get('_auto_timer',time.monotonic())
        st.session_state.setdefault('_auto_timer',last)
        if interval>0 and time.monotonic()-last>=interval*60 and active_credentials():
            st.session_state['_auto_timer']=time.monotonic()
            try:refresh(periodic)
            except ApiError as exc:st.caption('Автообновление: '+str(exc))
            except Exception:st.caption('Не удалось обновить магазин; сохранённые данные доступны.')
    finally:
        manager.persist(periodic)
        periodic.close()

automatic_updates()
