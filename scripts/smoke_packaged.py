"""Exercise the real bundled app on Windows using only fictional demo data."""
from pathlib import Path
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import traceback
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from ozon_profit.core import connect,save_cost,import_operations,set_setting
from ozon_profit import credentials
from playwright.sync_api import sync_playwright,expect


def launch(exe,env):
    process=subprocess.Popen([str(exe)],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',env=env)
    output=queue.Queue();lines=[]
    def read():
        for line in process.stdout:
            output.put(line);lines.append(line)
    threading.Thread(target=read,daemon=True).start()
    deadline=time.monotonic()+120;url=None
    try:
        while time.monotonic()<deadline:
            if process.poll() is not None:raise RuntimeError('Bundled executable exited: '+''.join(lines)[-3000:])
            try:line=output.get(timeout=.5)
            except queue.Empty:continue
            match=re.search(r'Ozon Profit: (http://127\.0\.0\.1:\d+)',line)
            if match:url=match.group(1);break
        if not url:raise RuntimeError('Executable did not report local URL')
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while time.monotonic()<deadline:
            try:
                with opener.open(url+'/_stcore/health',timeout=2) as response:
                    if response.read()==b'ok':return process,url
            except OSError:time.sleep(.5)
        raise RuntimeError('Executable health endpoint failed')
    except BaseException:
        process.kill();process.wait();raise


def stop(process):
    if process.poll() is not None:return
    if os.name=='nt':
        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
    else:process.terminate()
    try:process.wait(timeout=15)
    except subprocess.TimeoutExpired:process.kill();process.wait()


def main():
    exe=ROOT/'dist'/'OzonProfit'/'OzonProfit.exe'
    temp=Path(tempfile.mkdtemp(prefix='ozon-packaged-'))
    path=temp/'profit.db';db=connect(path)
    save_cost(db,'demo','Демо изделие','2026-01-01','12.345')
    import_operations(db,b'fictional-fixture','synthetic.xlsx',[
        dict(id='sale',day='2026-08-01',kind='sale',amount=1000,qty=2,sku='demo',name='Демо изделие'),
        dict(id='fee',day='2026-08-01',kind='commission',amount=-100,qty=0,sku='demo'),
        dict(id='ad',day='2026-08-01',kind='advertising',amount=-50,qty=0,sku=''),
        dict(id='other',day='2026-08-01',kind='service',amount=-20,qty=0,sku=''),
    ])
    set_setting(db,'tax_base','sales');set_setting(db,'api_on_start','0');set_setting(db,'api_interval','0');db.close()
    env=dict(os.environ,OZON_PROFIT_DATA_DIR=str(temp),OZON_PROFIT_NO_BROWSER='1',PYTHONIOENCODING='utf-8')
    # Windows DPAPI in the same runner identity; no real key or API request.
    credentials.save('123','synthetic-key-not-real',temp/'seller.credentials')
    process,url=launch(exe,env)
    try:
        second=subprocess.run([str(exe)],env=env,timeout=30,capture_output=True)
        if second.returncode:raise RuntimeError('Second launch failed')
        with sync_playwright() as p:
            browser=p.chromium.launch()
            page=browser.new_page(viewport={'width':1440,'height':1100})
            page.goto(url)
            expect(page.get_by_role('heading',name='Ваша прибыль — на одном экране')).to_be_visible(timeout=90000)
            page.get_by_role('combobox',name=re.compile(r'Период$')).click()
            page.get_by_text('Свои даты',exact=True).click()
            expect(page.get_by_text('765,31 ₽',exact=True).first).to_be_visible()
            page.get_by_test_id('stSelectbox').filter(has_text='Динамика').locator('[data-baseweb="select"]').click()
            page.get_by_role('option',name='Реализация покупателям',exact=True).click()
            assert page.get_by_test_id('stException').count()==0
            expect(page.locator('table.ozon-expenses')).to_be_visible()
            page.locator('table.ozon-expenses summary').filter(has_text='Реклама').click()
            expect(page.locator('table.ozon-expenses .child').first).to_be_visible()
            page.get_by_role('combobox',name=re.compile(r'Общие расходы по товарам$')).click()
            page.get_by_role('option',name='Только прямая привязка',exact=True).click()
            expect(page.get_by_text('Недостаточно данных',exact=True)).to_be_visible()
            expect(page.get_by_text('765,31 ₽',exact=True).first).to_be_visible()
            sidebar=page.get_by_test_id('stSidebar')
            sidebar.get_by_text('Товары',exact=True).click()
            expect(page.get_by_role('heading',name='Товары и себестоимость')).to_be_visible()
            sidebar.get_by_text('План продаж',exact=True).click()
            page.get_by_role('button',name='Добавить в план',exact=True).click()
            expect(page.get_by_text('Прогноз чистой прибыли',exact=True)).to_be_visible()
            page.get_by_label('Бюджет рекламы с оплатой за клик на весь период, ₽',exact=True).fill('500')
            page.get_by_label('Бюджет рекламы с оплатой за клик на весь период, ₽',exact=True).press('Enter')
            expect(page.get_by_text('18 382,75 ₽',exact=True)).to_be_visible()
            expect(page.get_by_text('Продвижение за заказы',exact=True)).to_be_visible()
            sidebar.get_by_text('Настройки',exact=True).click()
            expect(page.get_by_text('Подключён · ключ сохранён и подхватывается автоматически',exact=True)).to_be_visible()
            page.screenshot(path=str(ROOT/'windows-smoke.png'),full_page=True)
            assert page.get_by_test_id('stException').count()==0
            browser.close()
    finally:stop(process)
    db=connect(path)
    assert db.execute('SELECT count(*) FROM costs').fetchone()[0]==1
    assert db.execute('SELECT payload FROM sales_plans WHERE id=1').fetchone()
    db.close()
    process,url=launch(exe,env)
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch();page=browser.new_page()
            page.goto(url)
            expect(page.get_by_role('heading',name='Ваша прибыль — на одном экране')).to_be_visible(timeout=90000)
            page.get_by_test_id('stSidebar').get_by_text('План продаж',exact=True).click()
            expect(page.get_by_text('Прогноз чистой прибыли',exact=True)).to_be_visible()
            expect(page.get_by_label('Бюджет рекламы с оплатой за клик на весь период, ₽',exact=True)).to_have_value(re.compile(r'^500(?:\.0+)?$'))
            assert page.get_by_test_id('stException').count()==0
            browser.close()
    finally:stop(process)
    print('Bundled app: all four screens, DPAPI credential reload, single instance and plan persistence verified.')

if __name__=='__main__':
    try:
        main()
        summary=os.environ.get('GITHUB_STEP_SUMMARY')
        if summary:Path(summary).write_text('## Windows packaged app verified\n\nAll four screens, DPAPI reload, single instance, and persisted sales plan passed. Synthetic data only.\n',encoding='utf-8')
    except Exception:
        summary=os.environ.get('GITHUB_STEP_SUMMARY')
        if summary:Path(summary).write_text('## Windows packaged app failed\n\n```text\n'+traceback.format_exc()+'\n```\n',encoding='utf-8')
        raise
