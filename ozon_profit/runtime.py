"""UI runtime: durable state setup and reusable, non-secret configuration reads."""
from pathlib import Path
import hashlib
import os
import streamlit as st
from .core import data_dir,get_setting
from .remote_storage import StateManager,SupabaseStore
from .secure_store import StorageError,save_pair,load_pair
from . import credentials


def secret(name,default=''):
    try:return st.secrets.get(name,default)
    except (FileNotFoundError,st.errors.StreamlitSecretNotFoundError):return default


@st.cache_resource
def _manager(identity,_path,_password,_url,_key,_bucket):
    return StateManager(_path,_password,SupabaseStore(_url,_key,_bucket) if _url else None)


def state_manager():
    cfg=secret('storage',{})
    url=str(cfg.get('supabase_url','')).strip();key=str(cfg.get('supabase_key','')).strip()
    bucket=str(cfg.get('bucket','ozon-profit')).strip()
    password=secret('app_password')
    if bool(url)!=bool(key):raise StorageError('Заполните одновременно supabase_url и supabase_key в Secrets.')
    path=data_dir()/'profit.db'
    identity=hashlib.sha256((str(path)+url+key+bucket+password).encode()).hexdigest()
    manager=_manager(identity,path,password,url,key,bucket)
    manager.prepare()
    return manager


def save_api_credentials(db,client_id,api_key):
    pair=credentials.validate(client_id,api_key)
    password=secret('app_password')
    if password:
        save_pair(db,pair,password)
    elif os.name=='nt':credentials.save(*pair)
    else:raise StorageError('Для сохранения ключа в веб-версии настройте пароль приложения. В Windows ключ хранится через DPAPI.')
    st.session_state['_seller_pair']=pair
    return pair


def saved_api_credentials(db):
    password=secret('app_password')
    if password:
        pair=load_pair(db,password)
        if pair:return pair
    if os.name=='nt':
        pair=credentials.load()
        if pair:return pair
    if get_setting(db,'ignore_config_credentials','0')!='1':
        client=secret('ozon_client_id');key=secret('ozon_api_key')
        if client and key:return credentials.validate(client,key)
    return None
