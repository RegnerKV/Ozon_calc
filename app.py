from datetime import timedelta
import time
from pathlib import Path
import streamlit as st
from ozon_profit.access import require_login,local_mode
from ozon_profit.core import connect,get_setting,set_setting
from ozon_profit.runtime import state_manager
from ozon_profit.secure_store import StorageError
from ozon_profit.seller_api import init_tables,ApiError
from ozon_profit.ui_pages import overview,products_page,settings_page,ensure_profit,refresh,today
from ozon_profit.api_ui import active_credentials

st.set_page_config(page_title='Ozon Profit',page_icon=str(Path(__file__).parent/'static'/'shop-logo.png'),layout='wide')
require_login()
st.markdown('''<style>
@font-face{font-family:Manrope;src:url('/app/static/fonts/Manrope.ttf') format('truetype');font-weight:200 800;font-display:swap}
@font-face{font-family:Unbounded;src:url('/app/static/fonts/Unbounded.ttf') format('truetype');font-weight:200 900;font-display:swap}
.stApp{background:#fff;color:#141414;font-family:Manrope,"Segoe UI",sans-serif}
.brand{font-family:Unbounded,Manrope,sans-serif;font-size:1.48rem;font-weight:700;letter-spacing:-.055em;padding:4px 0 6px;color:#0956ff}
.shop-name{font-family:Manrope,sans-serif;font-size:1.15rem;font-weight:700;color:#141414;margin-bottom:8px}
[data-testid="stSidebar"] [role="radiogroup"]{gap:12px}
[data-testid="stSidebar"] [role="radiogroup"]>label{border:1px solid #0956ff;border-radius:12px;background:#fff;padding:13px 16px!important;transition:background .15s;min-height:54px;width:100%}
[data-testid="stSidebar"] [role="radiogroup"]>label>div:first-child{position:absolute;opacity:0;width:1px;height:1px;overflow:hidden}
[data-testid="stSidebar"] [role="radiogroup"]>label p{font-family:Manrope,sans-serif;font-size:1.08rem;font-weight:700;color:#141414}
[data-testid="stSidebar"] [role="radiogroup"]>label:has(input:checked){background:#dff568;border-color:#dff568;box-shadow:none}
[data-testid="stSidebar"] [role="radiogroup"]>label:hover{background:#f3f6ff}
[data-testid="stSidebar"] [role="radiogroup"]>label:has(input:checked):hover{background:#dff568}
[data-testid="stSidebar"] [role="radiogroup"]>label:has(input:checked)::before{filter:grayscale(1) brightness(.25)}
[data-testid="stSidebar"] [role="radiogroup"]>label:focus-within{outline:2px solid #6e9be4;outline-offset:3px}
[data-testid="stSidebar"] [role="radiogroup"]>label::before{content:"";display:inline-block;width:20px;height:20px;flex-shrink:0;background-size:20px 20px;background-repeat:no-repeat;margin-right:12px}
[data-testid="stSidebar"] [role="radiogroup"]>label:nth-child(1)::before{background-image:url("data:image/svg+xml,%3Csvg%20xmlns%3D%27http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%27%20width%3D%2724%27%20height%3D%2724%27%20viewBox%3D%270%200%2024%2024%27%20fill%3D%27none%27%20stroke%3D%27%234a73ba%27%20stroke-width%3D%271.7%27%20stroke-linecap%3D%27round%27%20stroke-linejoin%3D%27round%27%3E%3Crect%20x%3D%273%27%20y%3D%273%27%20width%3D%277%27%20height%3D%277%27%20rx%3D%271.5%27%2F%3E%3Crect%20x%3D%2714%27%20y%3D%273%27%20width%3D%277%27%20height%3D%277%27%20rx%3D%271.5%27%2F%3E%3Crect%20x%3D%273%27%20y%3D%2714%27%20width%3D%277%27%20height%3D%277%27%20rx%3D%271.5%27%2F%3E%3Crect%20x%3D%2714%27%20y%3D%2714%27%20width%3D%277%27%20height%3D%277%27%20rx%3D%271.5%27%2F%3E%3C%2Fsvg%3E");}
[data-testid="stSidebar"] [role="radiogroup"]>label:nth-child(2)::before{background-image:url("data:image/svg+xml,%3Csvg%20xmlns%3D%27http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%27%20width%3D%2724%27%20height%3D%2724%27%20viewBox%3D%270%200%2024%2024%27%20fill%3D%27none%27%20stroke%3D%27%234a73ba%27%20stroke-width%3D%271.7%27%20stroke-linecap%3D%27round%27%20stroke-linejoin%3D%27round%27%3E%3Cpath%20d%3D%27M5%208h14l1%2013H4L5%208zM8%208V6a4%204%200%20018%200v2%27%2F%3E%3C%2Fsvg%3E");}
[data-testid="stSidebar"] [role="radiogroup"]>label:nth-child(3)::before{background-image:url("data:image/svg+xml,%3Csvg%20xmlns%3D%27http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%27%20width%3D%2724%27%20height%3D%2724%27%20viewBox%3D%270%200%2024%2024%27%20fill%3D%27none%27%20stroke%3D%27%234a73ba%27%20stroke-width%3D%271.7%27%20stroke-linecap%3D%27round%27%20stroke-linejoin%3D%27round%27%3E%3Cpath%20d%3D%27M3%204v16h18M6%2015l5-5%204%203%206-8%27%2F%3E%3C%2Fsvg%3E");}
[data-testid="stSidebar"] [role="radiogroup"]>label:nth-child(4)::before{background-image:url("data:image/svg+xml,%3Csvg%20xmlns%3D%27http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%27%20width%3D%2724%27%20height%3D%2724%27%20viewBox%3D%270%200%2024%2024%27%20fill%3D%27none%27%20stroke%3D%27%234a73ba%27%20stroke-width%3D%271.7%27%20stroke-linecap%3D%27round%27%20stroke-linejoin%3D%27round%27%3E%3Cpath%20d%3D%27M4%207h16M4%2017h16%27%2F%3E%3Ccircle%20cx%3D%279%27%20cy%3D%277%27%20r%3D%273%27%20fill%3D%27white%27%2F%3E%3Ccircle%20cx%3D%2715%27%20cy%3D%2717%27%20r%3D%273%27%20fill%3D%27white%27%2F%3E%3C%2Fsvg%3E");}
.ozon-expenses{width:100%;table-layout:fixed;border-collapse:separate;border-spacing:0;border:1px solid #e1e7f0;border-radius:16px;overflow:hidden;background:white;font-size:15px}
.ozon-expenses th{text-align:left;padding:13px 16px;color:#67758a;font-weight:500;background:#f9fbfe}
.ozon-expenses td{padding:13px 16px;border-top:1px solid #edf0f5}
.ozon-expenses td:last-child,.ozon-expenses th:last-child{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.ozon-expenses td.tree{padding:0;text-align:left;white-space:normal}
.ozon-expenses summary{display:flex;justify-content:space-between;align-items:center;padding:13px 16px;cursor:pointer;list-style:none;font-weight:600}
.ozon-expenses summary::-webkit-details-marker{display:none}
.ozon-expenses .tree-title::before{content:"›";display:inline-block;margin-right:10px;color:#577eb7}
.ozon-expenses details[open] .tree-title::before{transform:rotate(90deg)}
.ozon-expenses .child{display:flex;justify-content:space-between;gap:12px;padding:10px 16px 10px 35px;border-top:1px solid #f0f3f7;background:#f8faff;color:#506078;font-size:14px}
.ozon-expenses .child span:first-child{white-space:normal;overflow-wrap:anywhere}
.ozon-expenses .child span:last-child{flex-shrink:0;font-variant-numeric:tabular-nums;white-space:nowrap}
.block-container{max-width:1320px;padding-top:4rem;padding-bottom:3rem}
h1,h2,h3{font-family:Unbounded,Manrope,sans-serif!important;letter-spacing:-.045em;color:#141414;font-weight:600}
h2{font-size:1.65rem!important}
h3{font-size:1.25rem!important}
button,input,textarea,select,[data-testid="stMetric"]{font-family:Manrope,sans-serif!important}
[data-testid="stSidebar"]{background:#fff;border-right:1px solid #e8e7e3}
[data-testid="stMetric"]{background:#fff;border:1px solid #ebe9e3;border-radius:12px;padding:18px 20px;box-shadow:none}
[data-testid="stMetricValue"]{font-variant-numeric:tabular-nums;font-family:Manrope,sans-serif;font-weight:750;font-size:1.85rem;letter-spacing:-.045em;color:#141414}
[data-testid="stMetricLabel"]{color:#242424;font-size:.9rem;font-weight:500}
.stButton>button[kind="primary"]{background:#0956ff;border-color:#0956ff;border-radius:24px;color:#fff;font-weight:700}
[data-testid="stExpander"]{background:rgba(255,255,255,.7);border-radius:14px;border-color:#e8e7e3}
[data-testid="stAlert"]{border-radius:12px}
</style>''',unsafe_allow_html=True)
try:manager=state_manager()
except StorageError as exc:
    st.error(str(exc));st.info('Данные не сбрасывались. Проверьте настройки постоянного хранилища в Secrets Streamlit.');st.stop()
db=connect();init_tables(db)
st.sidebar.markdown('<div class="brand">Ozon Profit</div>',unsafe_allow_html=True)
st.sidebar.markdown('<div class="shop-name">ХОЧУ! Цацки</div>',unsafe_allow_html=True)
st.sidebar.image(str(Path(__file__).parent/'static'/'shop-logo.png'),width=120)
storage_status=st.sidebar.empty()
page=st.sidebar.radio('Раздел',['Обзор','Товары','План продаж','Настройки'],label_visibility='collapsed')
# Retain an already entered session key during the first migration to encrypted storage.
if st.session_state.get('_seller_pair') and not db.execute('SELECT 1 FROM secure_items WHERE name=?',('seller',)).fetchone():
    from ozon_profit.runtime import secret,save_api_credentials
    if secret('app_password'):
        try:save_api_credentials(db,*st.session_state['_seller_pair'])
        except StorageError:pass
try:
    from ozon_profit.catalog import apply_library,confirm_existing_costs
    if get_setting(db,'fish_cost_policy',None) is None:set_setting(db,'fish_cost_policy','1')
    apply_library(db)
    confirm_existing_costs(db)
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
