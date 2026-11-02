"""Private encrypted SQLite backup over Supabase Storage REST, single app instance.
No plaintext fallback after a configured restore fails.
"""
from pathlib import Path
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import threading
import requests
from .secure_store import seal,unseal,StorageError

MAX_SIZE=128*1024*1024


class SupabaseStore:
    def __init__(self,url,key,bucket='ozon-profit',session=None):
        if not re.fullmatch(r'https://[a-z0-9-]+\.supabase\.co',url.rstrip('/')):
            raise StorageError('Нужен Project URL вида https://имя-проекта.supabase.co.')
        if not key or any(c.isspace() for c in key):raise StorageError('Проверьте ключ хранилища в Secrets.')
        if not re.fullmatch('[a-z0-9-]{3,50}',bucket):raise StorageError('Некорректное имя закрытого хранилища.')
        self.url=url.rstrip('/');self.key=key;self.bucket=bucket;self.session=session or requests.Session()
        self.identity=hashlib.sha256((self.url+'/'+bucket).encode()).hexdigest()
        self._ready=False

    def _request(self,method,path,**kwargs):
        headers={'apikey':self.key,'Authorization':'Bearer '+self.key}
        headers.update(kwargs.pop('headers',{}))
        try:
            return self.session.request(method,self.url+'/storage/v1/'+path,headers=headers,
                timeout=(10,40),allow_redirects=False,**kwargs)
        except requests.RequestException:
            raise StorageError('Хранилище недоступно. Локальные данные сохранены; проверьте подключение Supabase.') from None

    def _bucket(self):
        if self._ready:return
        response=self._request('GET','bucket/'+self.bucket)
        if response.status_code==404:
            response=self._request('POST','bucket',json={'id':self.bucket,'name':self.bucket,'public':False})
            if response.status_code not in (200,201):raise StorageError('Не удалось создать закрытое хранилище. Нужен service_role ключ Supabase.')
            response=self._request('GET','bucket/'+self.bucket)
        if response.status_code!=200:raise StorageError('Не удалось открыть хранилище. Проверьте Project URL и service_role ключ.')
        try:private=response.json().get('public') is False
        except (ValueError,TypeError,AttributeError):private=False
        if not private:raise StorageError('Хранилище должно быть закрытым (public=false). Сохранение остановлено.')
        self._ready=True

    def read(self):
        self._bucket()
        response=self._request('GET','object/'+self.bucket+'/owner-state.enc')
        if response.status_code==404:return None
        if response.status_code!=200:raise StorageError('Не удалось восстановить данные из облачного хранилища.')
        if len(response.content)>MAX_SIZE:raise StorageError('Резервная копия слишком большая для автоматического восстановления.')
        return response.content

    def write(self,data):
        self._bucket()
        if len(data)>MAX_SIZE:raise StorageError('Резервная копия слишком большая.')
        response=self._request('POST','object/'+self.bucket+'/owner-state.enc',data=data,
            headers={'Content-Type':'application/octet-stream','x-upsert':'true'})
        if response.status_code not in (200,201):raise StorageError('Не удалось сохранить резервную копию Supabase. Локальная база сохранена.')


def snapshot(db,include_credentials=False):
    copy=sqlite3.connect(':memory:')
    try:
        db.backup(copy)
        if not include_credentials and copy.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='secure_items'").fetchone():
            copy.execute('DELETE FROM secure_items');copy.commit()
        return copy.serialize()
    finally:copy.close()


class StateManager:
    def __init__(self,path,password,store=None):
        self.path=Path(path);self.password=password;self.store=store
        self.lock=threading.RLock();self.last_error='';self.last_digest=None
        self.ready=False;self.restored=False;self.can_upload=False
        self.marker=self.path.with_suffix('.remote.json')

    def prepare(self):
        with self.lock:
            if self.ready and (not self.store or self.path.exists()):return
            if not self.store:
                self.ready=True;return
            # On a surviving local disk trust a successful mirror marker, not
            # a download on every browser refresh. Subsequent saves use current DB.
            if self.path.exists() and self.marker.exists():
                try:
                    saved=json.loads(self.marker.read_text())
                    if saved.get('store')!=self.store.identity:raise StorageError('Хранилище изменилось. Сначала сохраните резервную копию и проверьте настройки.')
                    self.last_digest=saved.get('digest');self.can_upload=True;self.ready=True;return
                except (ValueError,OSError):raise StorageError('Не удалось прочитать состояние резервной копии.') from None
            try:remote=self.store.read()
            except StorageError:
                if not self.path.exists():raise
                self.last_error='Облачное хранилище недоступно; используется существующая база на сервере.'
                self.ready=True;return
            if remote is not None:
                if self.path.exists():
                    raise StorageError('Найдены две базы: на сервере и в Supabase. Автоматическая замена остановлена, чтобы сохранить обе. Нужен выбор версии.')
                data=unseal(remote,self.password,'database')
                self._restore(data);self.restored=True
                self.last_digest=hashlib.sha256(data).hexdigest()
                self._mark(self.last_digest)
            self.can_upload=True
            self.ready=True

    def _restore(self,data):
        if not data.startswith(b'SQLite format 3\x00'):raise StorageError('Облачная копия не является базой приложения.')
        self.path.parent.mkdir(parents=True,exist_ok=True)
        descriptor,temporary=tempfile.mkstemp(dir=self.path.parent,suffix='.restore')
        try:
            with os.fdopen(descriptor,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
            check=sqlite3.connect(temporary)
            try:
                if check.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise StorageError('Резервная копия повреждена.')
                if not check.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'").fetchone():raise StorageError('Это не база Ozon Profit.')
            finally:check.close()
            os.replace(temporary,self.path)
        finally:Path(temporary).unlink(missing_ok=True)

    def _mark(self,digest):
        self.marker.write_text(json.dumps({'store':self.store.identity,'digest':digest}))

    def persist(self,db):
        if not self.store:return False
        with self.lock:
            if not self.can_upload:
                try:
                    existing=self.store.read()
                    if existing is not None:
                        self.last_error='Найдена отдельная облачная копия. Автоматическая перезапись остановлена; обе версии сохранены.'
                        return False
                    self.can_upload=True
                except StorageError as exc:
                    self.last_error=str(exc);return False
            data=snapshot(db,include_credentials=True)
            digest=hashlib.sha256(data).hexdigest()
            if digest==self.last_digest:return True
            try:
                self.store.write(seal(data,self.password,'database'))
                self._mark(digest)
                self.last_digest=digest;self.last_error=''
                return True
            except (StorageError,OSError) as exc:
                self.last_error=str(exc) if isinstance(exc,StorageError) else 'Облачная копия записана, но отметку сохранения не удалось обновить.'
                return False
