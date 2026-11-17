"""Part 5, evidence-only revision.

Re-read original Excel and report what it can and cannot establish.
No invented costs, service targets, demand distributions or purchase quantities.
Run: python inventory/service_inventory.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / '.task_deps'))

UNKNOWN_RESULTS = ['Cu', 'Co', 'target_service_level', 'target_inventory_u', 'COEFF', 'new_order_u']
CASE_CODES = {11,12,13,14,15,20,21,50,51,52,54,55,90,91,99}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def analyze_original(raw):
    """Only direct fields, exact arithmetic and descriptive flags. NaN stays NaN."""
    b = raw.loc[raw['Номенклатурна гр 3 рівень'].eq('Трактори')].copy()
    b.insert(0,'source_row',b.index+2)
    b = b.reset_index(drop=True)
    mapping = {
        'Артикул':'sku_id','GUID':'guid','Назва':'name',
        'Номенклатурна гр 1 рівень':'subsystem','Номенклатурна гр 2 рівень':'series_raw',
        'Номенклатурна гр 3 рівень':'group', 'Critical Code':'critical_code',
        'Inventory Class Code':'inventory_class','Індикотор повернення':'return_raw',
        'Прайс':'price', 'Доступний залишок на складі':'available_balance_u',
        'В замовленні постачальника':'ordered_u','Зарезервовано на складі':'reserved_u',
        'К-сть в упаковці':'pack_u','Кратність продаж':'sale_multiple_u',
        'Продажі з початку місяця':'current_month_units','Хіти поточного місяця':'current_month_hits',
    }
    sku=b[['source_row']+list(mapping)].rename(columns=mapping)
    if sku['sku_id'].isna().any() or not sku['sku_id'].is_unique:
        raise ValueError('Missing or duplicate SKU keys in original Excel')
    sku['series']=sku['series_raw'].replace({'Трактрори 6B':'Трактори 6B'})
    sc={int(re.search(r'\d+',c).group()):c for c in b if c.startswith('Продажі\n')}
    hc={int(re.search(r'\d+',c).group()):c for c in b if c.startswith('Хіти ') and '\n' in c}
    if set(sc)!=set(range(1,37)) or set(hc)!=set(range(1,37)):
        raise ValueError('Expected exactly 36 sales/hits pairs')
    sales=b[[sc[i] for i in range(1,37)]]
    hits=b[[hc[i] for i in range(1,37)]]
    sku['observed_months_12']=sales.iloc[:,:12].notna().sum(axis=1)
    sku['observed_months_36']=sales.notna().sum(axis=1)
    sku['sales12_observed_u']=sales.iloc[:,:12].sum(axis=1,min_count=1)
    sku['sales36_observed_u']=sales.sum(axis=1,min_count=1)
    sku['hits12_sum_proxy']=hits.iloc[:,:12].sum(axis=1,min_count=1)
    sku['hits12_max_observed_month']=hits.iloc[:,:12].max(axis=1)
    sku['available_value_at_price']=sku['available_balance_u']*sku['price']
    sku['ordered_value_at_price']=sku['ordered_u']*sku['price']
    sku['reserved_value_at_price']=sku['reserved_u']*sku['price']
    sku['sales12_value_at_current_price']=sku['sales12_observed_u']*sku['price']
    # Both expressions are arithmetic only; neither is selected as the true IP.
    sku['balance_plus_ordered_u']=sku['available_balance_u']+sku['ordered_u']
    sku['balance_plus_ordered_minus_reserve_u']=sku['balance_plus_ordered_u']-sku['reserved_u']
    total_observed=pd.concat([sales,b[['Продажі з початку місяця']]],axis=1).sum(axis=1,min_count=1)
    sku['flag_boundary3_proxy']=sku['hits12_sum_proxy'].eq(3)
    sku['flag_no_sales12_positive_balance']=sku['sales12_observed_u'].eq(0)&sku['available_balance_u'].gt(0)
    sku['flag_no_observed_sales37_positive_balance']=total_observed.eq(0)&sku['available_balance_u'].gt(0)
    sku['flag_CC11_12_zero_balance_recent_sales']=sku['critical_code'].isin([11,12])&sku['available_balance_u'].eq(0)&sku['sales12_observed_u'].gt(0)
    sku['flag_reserve_exceeds_available']=sku['reserved_u'].gt(sku['available_balance_u'])
    sku['flag_unknown_CC']=~sku['critical_code'].isin(CASE_CODES)
    for name in UNKNOWN_RESULTS:
        sku[name]=np.nan
    sku['calculation_status']='NOT_DETERMINED_MISSING_COSTS_AND_DEMAND_HORIZON_MODEL'
    metrics={
        'source_rows':len(raw),'source_columns':len(raw.columns),'sku_n':len(sku),
        'sales12_observed_u':float(sku['sales12_observed_u'].sum()),
        'sales36_observed_u':float(sku['sales36_observed_u'].sum()),
        'current_month_units':int(sku['current_month_units'].sum()),
        'available_balance_u':int(sku['available_balance_u'].sum()),'ordered_u':int(sku['ordered_u'].sum()),
        'reserved_u':int(sku['reserved_u'].sum()),
        'available_value_at_price':round(float(sku['available_value_at_price'].sum()),2),
        'ordered_value_at_price':round(float(sku['ordered_value_at_price'].sum()),2),
        'reserved_value_at_price':round(float(sku['reserved_value_at_price'].sum()),2),
        'sales12_value_at_current_price':round(float(sku['sales12_value_at_current_price'].sum()),2),
        'complete_12_skus':int(sku['observed_months_12'].eq(12).sum()),
        'complete_36_skus':int(sku['observed_months_36'].eq(36).sum()),
        'no_complete_month_history_skus':int(sku['observed_months_36'].eq(0).sum()),
        'missing_sales_cells_36':int(sales.isna().sum().sum()),
        'boundary3_proxy_skus':int(sku['flag_boundary3_proxy'].sum()),
        'no_sales12_positive_balance_skus':int(sku['flag_no_sales12_positive_balance'].sum()),
        'no_sales12_positive_balance_value_at_price':round(float(sku.loc[sku['flag_no_sales12_positive_balance'],'available_value_at_price'].sum()),2),
        'no_sales12_positive_balance_complete_history_skus':int((sku['flag_no_sales12_positive_balance']&sku['observed_months_12'].eq(12)).sum()),
        'no_sales37_positive_balance_skus':int(sku['flag_no_observed_sales37_positive_balance'].sum()),
        'CC11_12_zero_balance_recent_sales_skus':int(sku['flag_CC11_12_zero_balance_recent_sales'].sum()),
        'reserve_exceeds_available_skus':int(sku['flag_reserve_exceeds_available'].sum()),
        'unknown_CC_skus':int(sku['flag_unknown_CC'].sum()),
    }
    fields=pd.DataFrame({
        'source_field':list(raw.columns),'source_type':[str(raw[c].dtype) for c in raw],
        'missing_in_group_B':[int(b[c].isna().sum()) for c in raw],
    })
    series=sku.groupby('series',dropna=False).agg(
        sku_n=('sku_id','size'),sales12_observed_u=('sales12_observed_u','sum'),
        available_balance_u=('available_balance_u','sum'),ordered_u=('ordered_u','sum'),reserved_u=('reserved_u','sum'),
        available_value_at_price=('available_value_at_price','sum'),
    ).reset_index()
    return sku,metrics,fields,series


def read_forecast(group_path,sku_path,keys):
    """Keep colleague forecasts exactly as published. No rescaling or disaggregation."""
    group=pd.read_csv(group_path,sep=';',decimal=',')
    forecast=pd.read_csv(sku_path,sep=';',decimal=',')
    if len(group)!=12 or not group['month'].is_unique:
        raise ValueError('Expected 12 distinct published forecast months')
    periods=pd.PeriodIndex(group['month'],freq='M')
    if list(periods)!=list(pd.period_range(periods[0],periods=12,freq='M')):
        raise ValueError('Published forecast calendar is not contiguous')
    if not forecast['sku_id'].is_unique or set(forecast['sku_id'])!=set(keys):
        raise ValueError('Published SKU forecast keys do not match original Excel')
    numeric=group['базовий'].to_numpy(float)
    values=forecast.set_index('sku_id').loc[keys,'прогноз_FY2025_шт'].to_numpy(float)
    if not np.isfinite(numeric).all() or not np.isfinite(values).all():
        raise ValueError('Non-finite published forecast')
    rows=[]
    for width in (3,4):
        for start in range(len(group)):
            complete=start+width<=len(group)
            rows.append({
                'start_month':str(periods[start]),'months':width,
                'end_month':str(periods[start]+width-1),
                'published_forecast_sum_u':float(numeric[start:start+width].sum()) if complete else np.nan,
                'status':'SUM_OF_PUBLISHED_MONTHS_ONLY' if complete else 'UNAVAILABLE_OUTSIDE_PUBLISHED_FORECAST',
            })
    facts={'published_group_annual_u':float(numeric.sum()),'published_SKU_annual_sum_u':float(values.sum()),
           'published_difference_u':float(values.sum()-numeric.sum()),
           'group_forecast_source':str(group_path),'sku_forecast_source':str(sku_path),
           'new_inventory_forecast_fitted':False,'forecast_changed':False}
    return group,values,pd.DataFrame(rows),facts


def missing_inputs():
    rows=[
        ('Cu','Економічний вибір сервісу','Втрата компанії на одну незабезпечену одиницю за погодженим періодом; або її підтверджені складові.','Немає сум втраченої вигоди, термінової доставки, компенсацій і ймовірностей наслідків.','Не можна оцінити втрати дефіциту чи економічний α.'),
        ('Co','Економічний вибір сервісу','Додаткова втрата на одну зайву одиницю за тим самим періодом; або підтверджені витрати капіталу, утримання й залишкова вартість.','Немає ставок утримання, фінансування, фактичного знецінення.','Не можна оцінити втрати надлишку чи економічний α.'),
        ('cost_scope','Порівняння витрат','Однакові одиниці, горизонт, правила дефіциту і те, які втрати клієнта реально несе компанія.','Кейс не задає числової функції втрат або періоду їх оцінки.','Cu та Co неможливо коректно порівняти.'),
        ('price_basis','Грошова оцінка','Валюта, тип прайсу, собівартість і база розрахунку втрат.','У Excel лише Прайс без визначення валюти/типу ціни.','Кількість × Прайс є лише оцінкою за цим полем.'),
        ('return_terms','Оцінка Co зі складових','Строк, комісія та допустима частка повернення; транспорт, залишкова вартість для Retun/NO.','Є лише категорії можливості повернення.','Неможливо грошово оцінити відмінність поверненних та неповерненних деталей.'),
        ('demand_horizon_distribution','Цільовий запас','Узгоджена модель/оцінка розподілу попиту SKU за L+R, включно з невизначеністю.','Є історія продажів, груповий місячний та SKU-річний прогноз; готового розподілу на горизонт немає.','Саме середнє не визначає страховий запас; розподіл самостійно не призначено.'),
        ('approved_service_target','Альтернатива економічному вибору','Погоджений рівень сервісу та його визначення, якщо Cu/Co отримати неможливо.','Цільового α для B або її сегментів немає.','95% або іншу ціль не можна призначити самостійно. Задана ціль не розв’язує економічне порівняння втрат.'),
        ('lead_SY_S0','Горизонт для SY/S0','Фактичні строки доставки та їх мінливість.','SY означає щомісячне замовлення; S0 — поточне. Числових lead time немає.','Їм не підставляються 1 чи 0,5 місяця; для S3 відомі 3 місяці.'),
        ('SOLD_basis','Перехід S → COEFF','Точний період і база SOLD, що реально підставляється в операційну формулу.','Є місячні продажі, але операційна база формули не уточнена.','Не можна підмінити SOLD річним прогнозом і видати відповідний коефіцієнт за фактичний.'),
        ('balance_definition','Нове замовлення','Чи доступний залишок уже зменшений на резерв.','Назва поля і формула PDF допускають різне трактування.','Неможливо вибрати один IP; показано лише два арифметичні вирази без вибору правильного.'),
        ('supplier_due_dates','Нове замовлення','Дати надходження та статус попередніх замовлень по SKU.','Є кількість, без дат.','Неможливо визначити, що буде доступно вчасно, і обґрунтувати Q.'),
        ('clients_definition','Правило клієнтів із кейсу','Семантика хітів, період підрахунку та унікальні customer_id.','Є місячні хіти; річну унікальність не встановлено.','519 позицій — сума хітів 3, не доведені три річні клієнти.'),
        ('CC13_components','Спеціальна політика','Можливість виготовлення та специфікація компонентів для CC 13.','Є опис коду, але немає складу компонентів.','Не можна перерахувати готові деталі в потребу компонентів CC 12.'),
        ('purchase_multiple','Виконуване замовлення','Підтвердити, яке поле визначає закупівельну кратність.','Є упаковка і кратність продажів, операційне застосування не уточнено.','Замовлення не округлялося і не формувалося.'),
    ]
    return pd.DataFrame(rows,columns=['parameter','needed_for','request','evidence_gap','consequence'])


def case_facts():
    rows=[
        ('review_months',1,'місяць','PDF, с. 4: замовлення переглядається щомісячно.'),
        ('S3_lead_months',3,'місяці','PDF, с. 4: S3 з терміном постачання 3 місяці.'),
        ('S3_discount',.14,'частка','PDF, с. 4.'),('SY_discount',.10,'частка','PDF, с. 4.'),
        ('SY_lead_months',None,'невідомо','Щомісячне замовлення не визначає тривалості доставки.'),
        ('S0_lead_months',None,'невідомо','Поточне замовлення; числового строку немає.'),
        ('turnover_agrosem',3.5,'оборотів/рік','PDF, с. 3–4. Не є ставкою витрат на капітал або Co.'),
        ('turnover_manufacturer',2.5,'оборотів/рік','PDF, с. 4. Не визначає цільовий сервіс.'),
        ('COEFF_min',0,'межа','PDF, с. 4.'),('COEFF_max',1,'межа','PDF, с. 4.'),
    ]
    return pd.DataFrame(rows,columns=['parameter','value','unit','source_and_limit'])


def make_status():
    return pd.DataFrame([
        ('Cu','Немає оцінки граничних втрат дефіциту.'),
        ('Co','Немає оцінки граничних втрат зайвого запасу.'),
        ('target_service_level','Немає Cu/Co або погодженого цільового рівня сервісу.'),
        ('target_inventory_u','Немає обґрунтованого рівня сервісу та узгодженої оцінки розподілу попиту за горизонт.'),
        ('COEFF','Немає цільового запасу; база SOLD в операційній формулі також потребує уточнення.'),
        ('new_order_u','Немає цільового запасу, визначення доступного залишку і дат раніше замовленого.'),
    ],columns=['result','reason']).assign(value=np.nan,status='NOT_DETERMINED')


def save_excel(out,sheets):
    from build_agrosem_analysis import write_workbook
    # Only reuse the existing spreadsheet writer, not the old analytical model.
    folder=out/'excel_build'
    folder.mkdir(exist_ok=True)
    write_workbook(folder,sheets)
    (folder/'03_АГРОСЕМ_B_дані_та_аудит.xlsx').replace(out/'inventory_data_and_gaps.xlsx')
    folder.rmdir()


def main():
    p=argparse.ArgumentParser(description='Task 5: original-data audit; missing economics stays missing.')
    p.add_argument('--source',type=Path,default=ROOT/'inputs/12_11_2024_140029.xlsx')
    p.add_argument('--case',type=Path,default=ROOT/'inputs/АГРОСЕМ_case.pdf')
    p.add_argument('--out',type=Path,default=ROOT/'inventory/out')
    args=p.parse_args()
    out=args.out.resolve()
    out.mkdir(parents=True,exist_ok=True)
    legacy_names={'scenario_summary.csv','targets_SKU_S3.csv','assumptions_used.json','inventory_service_COEFF.xlsx'}
    if any((out/name).exists() for name in legacy_names):
        raise ValueError('Old assumption-based results exist in output folder; retire them before evidence-only rebuild.')
    files=[args.source,args.case,ROOT/'agrosem_results/data/sku_master.csv',
           ROOT/'forecast/out/forecast_group_FY2025.csv',ROOT/'forecast/out/forecast_SKU_FY2025.csv']
    before={str(path.resolve()):sha256(path) for path in files}
    with pd.ExcelFile(args.source) as xls:
        if len(xls.sheet_names)!=1: raise ValueError('Select the correct source sheet explicitly')
        raw=pd.read_excel(xls,sheet_name=xls.sheet_names[0],dtype={'Артикул':str,'GUID':str})
    sku,metrics,fields,series=analyze_original(raw)
    group,fc_values,windows,fc_facts=read_forecast(files[3],files[4],sku['sku_id'])
    sku['published_forecast_annual_u']=fc_values
    previous=pd.read_csv(files[2],sep=';').set_index('sku_id').reindex(sku['sku_id'])
    for name in ['available_balance_u','ordered_u','reserved_u','price']:
        if not np.array_equal(sku[name].to_numpy(),previous[name].to_numpy()):
            raise AssertionError(f'Original and prepared field differ: {name}')
    if not np.allclose(sku['sales12_observed_u'],previous['sold_12_observed_u'],equal_nan=True):
        raise AssertionError('Original annual sales differ from prepared audit')
    flags=pd.DataFrame([
        (c,int(sku[c].sum()),'Ознака для перевірки; не висновок про дефіцит, неліквід або дію закупівлі.')
        for c in sku if c.startswith('flag_')],columns=['flag','sku_n','interpretation'])
    missing=missing_inputs()
    facts=case_facts()
    status=make_status()
    metric_frame=pd.DataFrame(metrics.items(),columns=['metric','value'])
    summary={
        'revision':'evidence_only','task5_status':'INCOMPLETE_MISSING_INPUTS',
        'metrics':metrics,'published_forecast':fc_facts,
        'economic_results':{name:None for name in UNKNOWN_RESULTS},
        'missing_parameters':missing['parameter'].tolist(),
        'no_new_assumed_costs':True,'no_service_target_selected':True,'no_demand_distribution_selected':True,
        'old_assumption_based_results_withdrawn':True,
    }
    from inventory.report import make_report,make_html
    report,speaker,readme,start,definitions=make_report(summary,series,flags,missing,facts,status,windows)
    sheets={
        'Почати':start,'Результат_завдання_5':status,'Фактичні_показники':metric_frame,
        'SKU_дані':sku,'Серії':series,'Прапорці_перевірки':flags,
        'Чого_бракує':missing,'Умови_PDF':facts,'Поля_джерела':fields,
        'Прогноз_колеги':group,'Суми_прогнозу':windows,'Словник':definitions,
    }
    exports={
        'observed_SKU':sku,'observed_metrics':metric_frame,'series_summary':series,'review_flags':flags,
        'missing_inputs':missing,'case_facts':facts,'input_fields':fields,
        'calculation_status':status,'forecast_window_sums':windows,'field_dictionary':definitions,
    }
    for name,frame in exports.items():
        frame.to_csv(out/f'{name}.csv',sep=';',index=False,encoding='utf-8-sig',float_format='%.12g')
    # Output filenames deliberately differ from the withdrawn recommendation workbook.
    save_excel(out,sheets)
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    manifest={str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path):{
        'sha256':before[str(path.resolve())],'bytes':path.stat().st_size} for path in files}
    for path in files:
        if sha256(path)!=before[str(path.resolve())]: raise AssertionError('Source was modified')
    (out/'input_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/'conclusions/запаси-сервіс-coeff.md').write_text(report,encoding='utf-8')
    (ROOT/'conclusions/запаси-для-виступу.md').write_text(speaker,encoding='utf-8')
    (ROOT/'inventory/README.md').write_text(readme,encoding='utf-8')
    (ROOT/'html/3b_service_coeff.html').write_text(make_html(summary,missing,status),encoding='utf-8')
    print(json.dumps({'task5_status':summary['task5_status'],'metrics':metrics,'economic_results':summary['economic_results']},ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
