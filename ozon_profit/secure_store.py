"""Authenticated encryption for web credentials and remote database snapshots."""
import base64
import hashlib
import json
import os
from cryptography.fernet import Fernet,InvalidToken


class StorageError(Exception):
    pass


def seal(data,password,purpose):
    if not isinstance(password,str) or len(password)<12:raise StorageError('Для шифрования нужен настроенный пароль приложения.')
    salt=os.urandom(16)
    key=hashlib.scrypt(password.encode(),salt=salt+purpose.encode(),n=16384,r=8,p=1,dklen=32)
    token=Fernet(base64.urlsafe_b64encode(key)).encrypt(data)
    return json.dumps({'version':1,'salt':base64.b64encode(salt).decode(),'token':token.decode()}).encode()


def unseal(data,password,purpose):
    try:
        envelope=json.loads(data)
        if envelope['version']!=1:raise ValueError()
        salt=base64.b64decode(envelope['salt'],validate=True)
        if len(salt)!=16:raise ValueError()
        key=hashlib.scrypt(password.encode(),salt=salt+purpose.encode(),n=16384,r=8,p=1,dklen=32)
        return Fernet(base64.urlsafe_b64encode(key)).decrypt(envelope['token'].encode())
    except (KeyError,TypeError,ValueError,InvalidToken):
        raise StorageError('Не удалось расшифровать сохранённые данные. Нужен пароль приложения, с которым они были сохранены.') from None


def save_pair(db,pair,password):
    from .credentials import validate
    pair=validate(*pair)
    ciphertext=seal(json.dumps(pair).encode(),password,'seller-key')
    with db:
        db.execute('INSERT OR REPLACE INTO secure_items VALUES(?,?)',('seller',ciphertext))
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('ignore_config_credentials','0'))


def load_pair(db,password):
    from .credentials import validate
    row=db.execute('SELECT value FROM secure_items WHERE name=?',('seller',)).fetchone()
    return validate(*json.loads(unseal(row[0],password,'seller-key'))) if row else None
