import json
import sqlite3
import pytest
from ozon_profit.core import connect,save_cost,get_setting,set_setting
from ozon_profit.secure_store import seal,unseal,save_pair,load_pair,StorageError
from ozon_profit.remote_storage import StateManager,snapshot,SupabaseStore

PASSWORD='synthetic-owner-password-123'
TEST_PAIR=('123','synthetic-seller-key-not-real')

class Remote:
    identity='test-private-store'
    def __init__(self):self.data=None;self.down=False;self.writes=0
    def read(self):
        if self.down:raise StorageError('offline')
        return self.data
    def write(self,value):
        if self.down:raise StorageError('offline')
        self.data=value;self.writes+=1


def test_authenticated_cipher_does_not_expose_plaintext():
    encrypted=seal(b'private-finances',PASSWORD,'database')
    assert b'private-finances' not in encrypted
    assert unseal(encrypted,PASSWORD,'database')==b'private-finances'
    with pytest.raises(StorageError):unseal(encrypted,'another-password-123','database')
    with pytest.raises(StorageError):unseal(encrypted,PASSWORD,'seller-key')


def test_credentials_persist_across_connections_encrypted(tmp_path):
    path=tmp_path/'profit.db';db=connect(path)
    save_pair(db,TEST_PAIR,PASSWORD);db.close()
    assert TEST_PAIR[1].encode() not in path.read_bytes()
    reopened=connect(path)
    assert load_pair(reopened,PASSWORD)==TEST_PAIR
    reopened.close()


def test_cloud_restore_after_disk_reset_retains_cost_key_and_plan(tmp_path):
    store=Remote();first=tmp_path/'first'/'profit.db'
    manager=StateManager(first,PASSWORD,store);manager.prepare()
    first.parent.mkdir(exist_ok=True);db=connect(first)
    save_cost(db,'demo','Demo','2026-01-01','80.005',True)
    save_pair(db,TEST_PAIR,PASSWORD)
    with db:db.execute('INSERT INTO sales_plans VALUES(1,?)',('{"quantity":50}',))
    assert manager.persist(db)
    assert TEST_PAIR[1].encode() not in store.data and b'Demo' not in store.data
    assert manager.persist(db) and store.writes==1
    db.close()
    second=tmp_path/'new-machine'/'profit.db'
    restored=StateManager(second,PASSWORD,store);restored.prepare()
    assert restored.restored
    db=connect(second)
    assert db.execute('SELECT amount FROM costs').fetchone()[0]=='80.005'
    assert load_pair(db,PASSWORD)==TEST_PAIR
    assert json.loads(db.execute('SELECT payload FROM sales_plans').fetchone()[0])['quantity']==50
    db.close()


def test_configured_outage_never_replaces_cloud_state_with_empty_database(tmp_path):
    store=Remote();store.down=True
    manager=StateManager(tmp_path/'missing.db',PASSWORD,store)
    with pytest.raises(StorageError):manager.prepare()
    assert not manager.path.exists() and store.writes==0


def test_existing_local_database_survives_outage_and_unknown_cloud_copy(tmp_path):
    store=Remote();store.down=True;path=tmp_path/'profit.db';db=connect(path)
    save_cost(db,'demo','Demo','2026-01-01','100')
    manager=StateManager(path,PASSWORD,store);manager.prepare()
    assert not manager.persist(db) and store.writes==0
    store.down=False;store.data=seal(snapshot(db,True),PASSWORD,'database')
    assert not manager.persist(db) and store.writes==0
    assert db.execute('SELECT amount FROM costs').fetchone()[0]=='100'
    db.close()


def test_two_existing_databases_are_not_silently_overwritten(tmp_path):
    store=Remote();cloud=connect(':memory:');set_setting(cloud,'demo','cloud')
    store.data=seal(snapshot(cloud,True),PASSWORD,'database');cloud.close()
    local=tmp_path/'profit.db';db=connect(local);set_setting(db,'demo','local');db.close()
    with pytest.raises(StorageError):StateManager(local,PASSWORD,store).prepare()
    db=connect(local);assert get_setting(db,'demo')=='local';db.close()
    assert store.writes==0


def test_wrong_password_does_not_create_empty_restored_database(tmp_path):
    store=Remote();db=connect(':memory:');store.data=seal(snapshot(db,True),PASSWORD,'database');db.close()
    path=tmp_path/'profit.db'
    with pytest.raises(StorageError):StateManager(path,'wrong-password-456',store).prepare()
    assert not path.exists()


def test_user_backup_excludes_even_encrypted_credentials():
    db=connect(':memory:');save_pair(db,TEST_PAIR,PASSWORD)
    data=snapshot(db)
    copy=sqlite3.connect(':memory:');copy.deserialize(data)
    assert copy.execute('SELECT count(*) FROM secure_items').fetchone()[0]==0
    assert load_pair(db,PASSWORD)==TEST_PAIR
    copy.close();db.close()


def test_cloud_bucket_must_be_private():
    class Response:
        status_code=200
        def json(self):return {'public':True}
    class Session:
        def request(self,*args,**kwargs):return Response()
    store=SupabaseStore('https://demo-project.supabase.co','synthetic-role-key',session=Session())
    with pytest.raises(StorageError,match='закрытым'):store.read()


def test_storage_transport_keeps_tls_and_does_not_redirect_or_print_keys():
    calls=[]
    class Response:
        status_code=200
        content=b'encrypted-placeholder'
        def json(self):return {'public':False}
    class Session:
        def request(self,*args,**kwargs):calls.append((args,kwargs));return Response()
    store=SupabaseStore('https://demo-project.supabase.co','synthetic-role-key',session=Session())
    assert store.read()==b'encrypted-placeholder'
    assert all(c[1]['allow_redirects'] is False and 'verify' not in c[1] for c in calls)
    assert calls[0][1]['headers']['apikey']=='synthetic-role-key'


def test_storage_refuses_arbitrary_hosts():
    for url in ['http://demo.supabase.co','https://127.0.0.1','https://demo.supabase.co.attacker.test']:
        with pytest.raises(StorageError):SupabaseStore(url,'synthetic-role-key')
