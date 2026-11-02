"""Windows launcher: local browser, per-user data and one running instance."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sys
import threading
import time
import urllib.request
import webbrowser


def _run():
    root=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))
    os.chdir(root)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    url=f'http://127.0.0.1:{port}'
    if os.name=='nt':
        from ozon_profit.core import data_dir
        (data_dir()/'running.json').write_text(json.dumps({'url':url}))
    print(f'Ozon Profit: {url}',flush=True)
    def open_browser():
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(120):
            try:
                with opener.open(url+'/_stcore/health',timeout=1) as response:
                    if response.status==200:webbrowser.open(url);return
            except OSError:pass
            time.sleep(.5)
        print('Не удалось открыть приложение. Закройте окно и попробуйте снова.',flush=True)
    if os.environ.get('OZON_PROFIT_NO_BROWSER')!='1':threading.Thread(target=open_browser,daemon=True).start()
    from streamlit.web import bootstrap
    flags={'server.address':'127.0.0.1','server.port':port,'server.headless':True,
        'browser.gatherUsageStats':False,'global.developmentMode':False,'server.fileWatcherType':'none'}
    bootstrap.load_config_options(flags)
    bootstrap.run(str(root/'app.py'),False,[],flags)


def main():
    os.environ['OZON_PROFIT_LOCAL']='1'
    if os.name=='nt':
        # Verify HTTPS using Windows' native trusted roots, including managed roots.
        # Configure before importing Requests or Streamlit. TLS verification stays on.
        import truststore
        truststore.inject_into_ssl()
    mutex=None;kernel=None
    if os.name=='nt':
        from ctypes import wintypes
        from ozon_profit.core import data_dir
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.CreateMutexW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.LPCWSTR]
        kernel.CreateMutexW.restype=wintypes.HANDLE
        kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle.restype=wintypes.BOOL
        name='Local\\OzonProfit-'+hashlib.sha256(str(data_dir()).encode()).hexdigest()[:24]
        mutex=kernel.CreateMutexW(None,False,name)
        if not mutex:raise OSError('Не удалось запустить приложение для текущего пользователя Windows')
        if ctypes.get_last_error()==183:
            try:
                existing=json.loads((data_dir()/'running.json').read_text())['url']
                if re.fullmatch(r'http://127\.0\.0\.1:\d{1,5}',existing) and os.environ.get('OZON_PROFIT_NO_BROWSER')!='1':webbrowser.open(existing)
            except (OSError,ValueError,KeyError):pass
            kernel.CloseHandle(mutex)
            return
    try:_run()
    finally:
        if mutex:kernel.CloseHandle(mutex)


if __name__=='__main__':main()
