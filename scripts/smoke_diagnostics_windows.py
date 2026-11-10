"""Run the actual packaged diagnostic with synthetic DPAPI credentials; no API calls."""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ozon_profit import credentials
from ozon_profit.api_diagnostics import ARTICLE


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fail(message):
    print('::error title=Packaged diagnostic smoke::' + message, flush=True)
    raise AssertionError(message)


def main():
    if os.name != 'nt':
        raise RuntimeError('This smoke test requires real Windows DPAPI')
    exe = ROOT / 'dist' / 'OzonApiDiagnostics.exe'
    assert exe.is_file()
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        data = root / 'data'
        data.mkdir()
        credentials.save('123456', 'synthetic-seller-secret', data / 'seller.credentials')
        path = data / 'profit.db'
        with sqlite3.connect(path) as db:
            db.executescript('''CREATE TABLE secure_items(name TEXT PRIMARY KEY,value BLOB);
                CREATE TABLE product_links(ozon_sku TEXT,sku TEXT);
                CREATE TABLE operations(private_data TEXT);
                INSERT INTO operations VALUES('private-buyer-and-financial-operation');''')
            db.execute('INSERT INTO secure_items VALUES(?,?)', ('performance', b'DPAPI:' +
                credentials._transform(b'["synthetic-client", "synthetic-performance-secret"]')))
            db.execute('INSERT INTO product_links VALUES(?,?)', ('12345', ARTICLE))
        before = {p.name: digest(p) for p in data.iterdir()}
        report_path = root / 'report.json'
        result = subprocess.run([str(exe), '--offline', '--data-dir', str(data),
                                 '--output', str(report_path)], capture_output=True, timeout=90)
        if result.returncode != 0:
            # Fixed categories/module identifiers only, never raw output or secrets.
            text = (result.stdout + result.stderr).decode('utf-8', errors='replace')
            category = re.findall(r'Error category: ([A-Za-z]+)', text)
            missing = re.findall(r"ModuleNotFoundError: No module named '([A-Za-z_][A-Za-z_0-9.]*)'", text)
            fail('EXE exit=' + str(result.returncode) + '; categories=' + ','.join(category)
                 + '; missing_modules=' + ','.join(missing))
        report = json.loads(report_path.read_text(encoding='utf-8'))
        assert report['execution'] == 'offline_no_API_requests'
        if report['connections'] != {'seller': 'loaded', 'performance': 'loaded',
                                      'database': 'opened_read_only'}:
            fail('Connection states: ' + json.dumps(report['connections']))
        assert report['checks'] == []
        if {p.name: digest(p) for p in data.iterdir()} != before:
            fail('Database or credential files changed')
        exported = report_path.read_bytes() + result.stdout + result.stderr
        for secret in (b'synthetic-seller-secret', b'synthetic-client',
                       b'synthetic-performance-secret', b'private-buyer', b'123456'):
            assert secret not in exported
        # A repeated invocation must refuse to overwrite the existing report.
        again = subprocess.run([str(exe), '--offline', '--data-dir', str(data),
                                '--output', str(report_path)], capture_output=True, timeout=90)
        assert again.returncode == 2
    print('Packaged EXE: DPAPI connections loaded, no network, DB and credentials unchanged, safe JSON.')


if __name__ == '__main__':
    main()
