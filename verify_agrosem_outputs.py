"""Незалежна перевірка експорту: ключі, NaN, історія, суми і структура файлів."""
from pathlib import Path
import json
import sys
import zipfile
import xml.etree.ElementTree as ET
import argparse
import re
import numpy as np
import pandas as pd
from openpyxl import load_workbook

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'.task_deps'))
sys.stdout.reconfigure(encoding='utf-8')
parser=argparse.ArgumentParser()
parser.add_argument('--output',type=Path,default=ROOT.parent if (ROOT.parent/'data').exists() else ROOT/'agrosem_results')
parser.add_argument('--source',type=Path,help='За наявності: незалежно перечитати оригінальний Excel і порівняти всі історичні комірки.')
args=parser.parse_args()
OUT=args.output.resolve()
data_path=OUT/'data'
results=[]


def check(name,condition,detail=''):
    results.append({'check':name,'status':'PASS' if condition else 'FAIL','detail':detail})
    if not condition: raise AssertionError(f'{name}: {detail}')


master=pd.read_csv(data_path/'sku_master.csv',sep=';',dtype={'sku_id':str,'guid':str})
sales=pd.read_csv(data_path/'sales_36m.csv',sep=';',index_col='sku_id')
hits=pd.read_csv(data_path/'hits_36m.csv',sep=';',index_col='sku_id')
check('Кількість і унікальність ключів',len(master)==13398 and master['sku_id'].is_unique)
check('Історія: 36 місяців у хронологічному порядку',list(sales.columns)==list(pd.period_range('2021-11','2024-10',freq='M').astype(str)))
check('Порожні комірки продажів не стали нулями',int(sales.isna().sum().sum())==67948)
check('Порожні комірки хітів не стали нулями',int(hits.isna().sum().sum())==67948)
check('Повністю порожні ряди мають невідому річну суму',master.loc[master['observed_months_12'].eq(0),'hits_12_sum_proxy'].isna().all() and master['observed_months_12'].eq(0).sum()==2)
check('Річні продажі',sales.iloc[:,-12:].sum().sum()==71162)
check('Продажі за 36 місяців',sales.sum().sum()==170758)
check('Поточний місяць відокремлено',master['current_month_units'].sum()==1782)
check('Пряма річна оцінка збігається з похідним полем',np.isclose((master['sold_12_observed_u']*master['price']).sum(),master['sold12_price_proxy'].sum(),atol=1e-6))
check('Вартість залишку',np.isclose((master['available_balance_u']*master['price']).sum(),1437595.09,atol=1e-6))
check('Граничний список',len(pd.read_csv(data_path/'boundary_3hits.csv',sep=';'))==519)
check('Аномалії комірок',len(pd.read_csv(data_path/'cell_anomalies.csv',sep=';'))==3)
check('Сегменти повністю покривають SKU',master['hits_segment_proxy'].notna().all() and master['hits_segment_proxy'].eq('Немає історії').sum()==2)
check('Матриця ризиків не втрачає SKU',pd.read_csv(data_path/'risk_matrix.csv',sep=';')['sku_n'].sum()==13398)
check('ABC/XYZ не втрачає SKU',pd.read_csv(data_path/'abc_xyz.csv',sep=';')['sku_n'].sum()==13398)
if args.source:
    original=pd.read_excel(args.source,dtype={'Артикул':str,'GUID':str})
    b=original.loc[original['Номенклатурна гр 3 рівень'].eq('Трактори')].set_index('Артикул')
    check('Оригінал: порядок і всі ключі',list(b.index)==master['sku_id'].tolist())
    for label,prefix,export in [('Продажі','Продажі\n',sales),('Хіти','Хіти ',hits)]:
        source_cols={int(re.search(r'\d+',c).group()):c for c in b if c.startswith(prefix) and '\n' in c}
        source_values=b[[source_cols[i] for i in range(36,0,-1)]].to_numpy()
        check(f'Оригінал: усі 482 328 комірок {label}',np.array_equal(source_values,export.to_numpy(),equal_nan=True))
check('Попередні замовлення не підмінено новим ORDER','order_raw' not in master.columns and master['ordered_u'].sum()==42262)
long=pd.read_csv(data_path/'monthly_long.csv.gz')
check('Довгий формат має унікальний ключ SKU-місяць',len(long)==482328 and not long.duplicated(['sku_id','month']).any())
check('Довгий формат зберігає маску відсутності',long['sold_units'].isna().sum()==67948 and long['is_observed'].eq(long['sold_units'].notna()).all())
manifest=json.loads((OUT/'manifest.json').read_text(encoding='utf-8'))
book=OUT/'03_АГРОСЕМ_B_дані_та_аудит.xlsx'
with zipfile.ZipFile(book) as z:
    check('XLSX: цілісність ZIP-контейнера',z.testzip() is None)
    check('XLSX: 26 аркушів',len([x for x in z.namelist() if x.startswith('xl/worksheets/sheet') and x.endswith('.xml')])==26)
    check('XLSX: графік включено','xl/charts/chart1.xml' in z.namelist())
wb=load_workbook(book,read_only=True,data_only=True)
ws=wb['SKU']
rows=ws.iter_rows(values_only=True)
cols=next(rows)
sku_index=cols.index('sku_id')
balance_index=cols.index('balance_price_proxy')
identifiers=[]
balance=0
for row in rows:
    identifiers.append(row[sku_index])
    balance+=row[balance_index]
check('XLSX: SKU-довідник відповідає CSV',identifiers==master['sku_id'].tolist())
check('XLSX: прайсова оцінка залишку',np.isclose(balance,1437595.09,atol=1e-6))
history_rows=wb['Продажі_36м'].iter_rows(min_col=1,max_col=37,values_only=True)
header=next(history_rows)
blank_count=0
annual=0
history_n=0
for row in history_rows:
    history_n+=1
    blank_count+=sum(x is None for x in row[1:])
    annual+=sum(x or 0 for x in row[-12:])
check('XLSX: 13 398 історій і всі NaN збережено',history_n==13398 and blank_count==67948)
check('XLSX: річні продажі',annual==71162)
wb.close()
for filename,expected_pictures in [('01_План_і_ролі.docx',0),('02_Частина_1_аудит.docx',2)]:
    with zipfile.ZipFile(OUT/filename) as z:
        check(f'{filename}: цілісність',z.testzip() is None)
        pictures=[p for p in z.namelist() if p.startswith('word/media/')]
        check(f'{filename}: очікувані ілюстрації',len(pictures)==expected_pictures)
        xml=ET.fromstring(z.read('word/document.xml'))
        ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        text=' '.join(e.text or '' for e in xml.findall('.//w:t',ns))
        check(f'{filename}: український зміст і група B','Трактори' in text and 'COEFF' in text and len(text)>10000)
for filename in ['01_План_і_ролі.md','02_Частина_1_аудит.md']:
    import re
    content=(OUT/filename).read_text(encoding='utf-8')
    for image in re.findall(r'!\[[^]]*\]\(([^)]+)\)',content):
        check(f'{filename}: зображення {image}',(OUT/image).is_file())
report={'status':'PASS','checks_n':len(results),'checks':results,'source_data_review_items':3,
        'note':'Перевірка експорту успішна. Три позначені питання первинних даних залишені на уточнення; це не збої експорту.'}
(OUT/'validation_summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'status':'PASS','checks_n':len(results),'source_review_items':3},ensure_ascii=False))
