"""Entry point for the standalone Windows diagnostic (no Streamlit startup)."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys


def main(argv=None):
    # A packaged EXE piped by CI may inherit a non-Cyrillic Windows code page.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='Ozon API: безопасная диагностика сохранённых подключений')
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--offline', action='store_true', help='Не обращаться к API')
    parser.add_argument('--max-campaigns', type=int, default=20)
    args = parser.parse_args(argv)
    if not 1 <= args.max_campaigns <= 100:
        parser.error('--max-campaigns: 1..100')
    output = args.output
    if output is None and os.name == 'nt':
        from tkinter import Tk, filedialog
        root = Tk()
        root.withdraw()
        selected = filedialog.asksaveasfilename(title='Сохранить диагностику API',
            initialfile='Ozon-API-diagnostics.json', defaultextension='.json',
            filetypes=[('JSON', '*.json')])
        root.destroy()
        if not selected:
            return 0
        output = Path(selected)
    output = output or Path('Ozon-API-diagnostics-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.json')
    from ozon_profit.api_diagnostics import default_data_directory, run
    directory = args.data_dir or default_data_directory()
    # A user-selected output must never replace the DB or either credential file.
    target = output.resolve()
    if (target.suffix.lower() != '.json' or target.is_symlink() or
            target == directory.resolve() or target.name.lower().startswith('profit.db') or
            target.name.lower() in ('seller.credentials', 'performance.credentials') or
            target.is_relative_to(directory.resolve())):
        print('Отчёт должен быть JSON-файлом вне папки данных приложения.')
        return 2
    print('База: только чтение. Импорт и пересчёт не запускаются.', flush=True)
    print('Проверки аналитики выполняются с интервалом 61 секунда; дождитесь завершения.', flush=True)
    try:
        if os.name == 'nt':
            import truststore
            truststore.inject_into_ssl()
        report = run(directory, offline=args.offline, max_campaigns=args.max_campaigns,
                     progress=lambda name: print('Проверка: ' + name, flush=True))
        # Exclusive creation prevents overwriting any pre-existing user file.
        with output.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        print('JSON-отчёт сохранён. HTTP-ошибки не устанавливают тип подписки.', flush=True)
        return 0
    except FileExistsError:
        print('Файл уже существует. Выберите новое имя отчёта.')
        return 2
    except Exception as exc:
        # No exception text: libraries may include headers, credentials or data.
        print('Диагностика не завершена. Подробности исключения скрыты для защиты данных.')
        known_errors = {'ImportError', 'ModuleNotFoundError', 'OSError', 'PermissionError',
                        'FileNotFoundError', 'ValueError', 'TypeError', 'RuntimeError',
                        'UnicodeEncodeError', 'UnicodeDecodeError', 'AttributeError'}
        print('Error category: ' + (type(exc).__name__ if type(exc).__name__ in known_errors else 'other'))
        return 1


if __name__ == '__main__':
    result = main()
    if os.name == 'nt' and getattr(sys, 'frozen', False) and len(sys.argv) == 1:
        input('Нажмите Enter, чтобы закрыть окно...')
    raise SystemExit(result)
