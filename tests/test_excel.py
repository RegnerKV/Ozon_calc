from io import BytesIO
from decimal import Decimal
import pandas as pd
import pytest
from ozon_profit.excel import workbook, sheet, operation_rows
from ozon_profit.core import connect, save_cost, import_operations, calculate


def test_real_excel_roundtrip():
    output=BytesIO()
    frame=pd.DataFrame({'ID':['s1','f1'],'Дата':['05.09.2026','06.09.2026'],'Сумма':['1000','-150'],'Артикул':['FISH','FISH'],'Количество':[2,0],'Тип':['sale','commission']})
    with pd.ExcelWriter(output,engine='openpyxl') as writer:
        frame.to_excel(writer,index=False,sheet_name='Начисления')
    content=output.getvalue()
    assert workbook(content).sheet_names==['Начисления']
    data=sheet(content,'Начисления')
    rows=operation_rows(data,{'id':'ID','day':'Дата','amount':'Сумма','sku':'Артикул','qty':'Количество','kind':'Тип'},'sale')
    db=connect(':memory:')
    save_cost(db,'FISH','Рыба','2026-09-01','123.005')
    import_operations(db,content,'generated-test.xlsx',rows)
    result=calculate(db,'2026-09-01','2026-09-30')
    assert result['net']==850
    assert result['profit']==Decimal('563.990')
    db.close()


def test_blank_date_rejected():
    with pytest.raises(ValueError):
        operation_rows(pd.DataFrame({'date':[''],'id':['1']}),{'day':'date','id':'id'},'sale')
