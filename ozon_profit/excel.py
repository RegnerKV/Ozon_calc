from io import BytesIO
import pandas as pd

MAX_BYTES = 25 * 1024 * 1024


def workbook(content):
    if len(content) > MAX_BYTES:
        raise ValueError('Максимальный размер файла — 25 МБ')
    # Reject oversized decompressed archives before passing the workbook to openpyxl.
    import zipfile
    with zipfile.ZipFile(BytesIO(content)) as archive:
        if sum(item.file_size for item in archive.infolist()) > 150 * 1024 * 1024:
            raise ValueError('Слишком большой распакованный Excel-файл')
    return pd.ExcelFile(BytesIO(content), engine='openpyxl')


def sheet(content, name, header=0):
    frame = pd.read_excel(workbook(content), sheet_name=name, header=header, dtype=object)
    frame = frame.where(frame.notna(), '')
    return frame


def text_cell(value):
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def operation_rows(frame, mapping, default_kind):
    rows = []
    for _, source in frame.iterrows():
        def cell(field, default=''):
            col = mapping.get(field)
            return source[col] if col else default
        raw_day = cell('day')
        if isinstance(raw_day, (int, float)):
            raise ValueError('Дата прочитана как число. Выберите столбец с датами Excel или текстом ДД.ММ.ГГГГ.')
        timestamp = pd.to_datetime(raw_day, dayfirst=True, errors='raise')
        if pd.isna(timestamp):
            raise ValueError('Пустая дата')
        rows.append(dict(id=text_cell(cell('id')), day=timestamp.date().isoformat(),
                         order_no=text_cell(cell('order_no')), sku=text_cell(cell('sku')),
                         name=text_cell(cell('name')), amount=cell('amount'), qty=cell('qty', 0),
                         kind=text_cell(cell('kind', default_kind))))
    return rows
