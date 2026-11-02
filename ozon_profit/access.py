"""Single-owner web access. Default is locked; only the desktop launcher opts out."""
from collections import deque
import hashlib
import hmac
import os
import threading
import time
import streamlit as st


def local_mode():
    return os.environ.get('OZON_PROFIT_LOCAL')=='1'


@st.cache_resource
def _attempts():
    return threading.Lock(),deque()


def _allowed():
    lock,attempts=_attempts()
    with lock:
        now=time.monotonic()
        while attempts and now-attempts[0]>60:attempts.popleft()
        if len(attempts)>=10:return False
        attempts.append(now)
        return True


def require_login():
    if local_mode():return
    try:password=st.secrets.get('app_password','')
    except (FileNotFoundError,st.errors.StreamlitSecretNotFoundError):password=''
    if not isinstance(password,str) or len(password)<12:
        st.title('💎 Ozon Profit')
        st.info('Доступ закрыт. Владелец приложения должен задать пароль длиной не менее 12 символов в настройке Secrets: app_password. Финансовые данные до входа недоступны.')
        st.stop()
    tag=hashlib.sha256(password.encode()).hexdigest()
    if hmac.compare_digest(st.session_state.get('_owner_login',''),tag):
        if st.sidebar.button('Выйти из приложения',key='owner_logout'):
            # Drop financial widget contents and session-only API credentials as well.
            st.session_state.clear()
            st.rerun()
        return
    st.title('💎 Ozon Profit')
    with st.form('owner_login'):
        candidate=st.text_input('Пароль приложения',type='password',key='_owner_password')
        submit=st.form_submit_button('Войти')
    if submit:
        if not _allowed():
            st.error('Слишком много попыток входа. Подождите минуту.')
        elif len(candidate)<=4096 and hmac.compare_digest(candidate.encode(),password.encode()):
            st.session_state['_owner_login']=tag
            st.session_state.pop('_owner_password',None)
            st.rerun()
        else:st.error('Неверный пароль')
    st.stop()
