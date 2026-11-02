"""Windows DPAPI credential storage, bound to the current Windows user.

No plaintext fallback. On other operating systems credentials remain session-only.
"""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import tempfile

from .core import data_dir


class CredentialError(Exception):
    pass


def _transform(data: bytes, decrypt=False):
    if os.name != 'nt':
        raise CredentialError('Постоянное защищённое хранение доступно в Windows. Здесь ключ можно использовать только до закрытия приложения.')
    class Blob(ctypes.Structure):
        _fields_=[('cbData',wintypes.DWORD),('pbData',ctypes.POINTER(ctypes.c_ubyte))]
    buffer=ctypes.create_string_buffer(data)
    source=Blob(len(data),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)))
    output=Blob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.LocalFree.argtypes=[ctypes.c_void_p]
    kernel.LocalFree.restype=ctypes.c_void_p
    if decrypt:
        fn=crypt.CryptUnprotectData
        fn.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
        args=(ctypes.byref(source),None,None,None,None,1,ctypes.byref(output))
    else:
        fn=crypt.CryptProtectData
        fn.argtypes=[ctypes.POINTER(Blob),wintypes.LPCWSTR,ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
        args=(ctypes.byref(source),'Ozon Profit Seller API',None,None,None,1,ctypes.byref(output))
    fn.restype=wintypes.BOOL
    if not fn(*args):
        raise CredentialError('Windows не смогла обработать ключ. Используйте ту же учётную запись Windows, в которой он сохранён.')
    try:
        return ctypes.string_at(output.pbData,output.cbData)
    finally:
        # Clear the decrypted temporary output before releasing DPAPI memory.
        if decrypt:ctypes.memset(output.pbData,0,output.cbData)
        kernel.LocalFree(output.pbData)


def validate(client_id,api_key):
    client_id,api_key=str(client_id).strip(),str(api_key).strip()
    if not client_id.isascii() or not client_id.isdigit() or not api_key or any(ch.isspace() for ch in api_key):
        raise CredentialError('Укажите числовой Client ID и непустой API-ключ без пробелов.')
    if len(api_key)>4096:raise CredentialError('Проверьте API-ключ')
    return client_id,api_key


def save(client_id,api_key,path=None):
    client_id,api_key=validate(client_id,api_key)
    encrypted=_transform(json.dumps({'client_id':client_id,'api_key':api_key}).encode())
    target=Path(path) if path else data_dir()/'seller.credentials'
    target.parent.mkdir(parents=True,exist_ok=True)
    # Encrypt before creating any file. Atomic replace avoids truncated secrets.
    descriptor,temporary=tempfile.mkstemp(prefix='.credential-',dir=target.parent)
    try:
        with os.fdopen(descriptor,'wb') as stream:
            stream.write(encrypted);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def load(path=None):
    target=Path(path) if path else data_dir()/'seller.credentials'
    if not target.exists():return None
    try:
        data=json.loads(_transform(target.read_bytes(),decrypt=True))
        return validate(data['client_id'],data['api_key'])
    except CredentialError:
        raise
    except Exception:
        raise CredentialError('Не удалось прочитать сохранённый ключ. Сохраните его заново в приложении.') from None


def delete(path=None):
    (Path(path) if path else data_dir()/'seller.credentials').unlink(missing_ok=True)
