"""Windows entry point. Bind to loopback only; no external hosting."""
import os
from pathlib import Path
import socket
import sys
import threading
import time
import urllib.request
import webbrowser


def main():
    os.environ['OZON_PROFIT_LOCAL']='1'
    root=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))
    os.chdir(root)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        port=sock.getsockname()[1]
    url=f'http://127.0.0.1:{port}'
    print(f'Ozon Profit: {url}', flush=True)
    def open_browser():
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(120):
            try:
                with opener.open(url+'/_stcore/health',timeout=1) as response:
                    if response.status==200:
                        webbrowser.open(url)
                        return
            except OSError:
                pass
            time.sleep(.5)
        print('Не удалось открыть приложение. Закройте окно и попробуйте снова.')
    threading.Thread(target=open_browser,daemon=True).start()
    from streamlit.web import bootstrap
    flags = {
        'server.address':'127.0.0.1','server.port':port,'server.headless':True,
        'browser.gatherUsageStats':False,'global.developmentMode':False,
        'server.fileWatcherType':'none',
    }
    bootstrap.load_config_options(flags)
    bootstrap.run(str(root/'app.py'),False,[],flags)


if __name__=='__main__':
    main()
