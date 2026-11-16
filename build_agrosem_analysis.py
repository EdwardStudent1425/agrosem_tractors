"""АГРОСЕМ, група B: відтворюваний пакет першої командної частини.

Запуск: python build_agrosem_analysis.py --source <xlsx> --case <pdf>
Бібліотеки: pandas, numpy, openpyxl, matplotlib, pypdf, python-docx.
Початкові файли не змінюються. Жодного заповнення NaN нулями.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / '.task_deps'))
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.chart.label import DataLabelList
from openpyxl.workbook.properties import CalcProperties


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def fmt(x, decimals=0):
    return f'{x:,.{decimals}f}'.replace(',', ' ').replace('.', ',')


def md_table(df):
    values = [[str(x).replace('|', '/').replace('\n', ' ') for x in row] for row in df.itertuples(index=False, name=None)]
    return '\n'.join(['| ' + ' | '.join(map(str, df.columns)) + ' |', '| ' + ' | '.join(['---'] * len(df.columns)) + ' |'] + ['| ' + ' | '.join(row) + ' |' for row in values])


def build_data(df, snapshot):
    group_col = 'Номенклатурна гр 3 рівень'
    b = df.loc[df[group_col].astype('string').str.strip().eq('Трактори')].copy()
    b.insert(0, 'source_row', b.index + 2)
    b = b.reset_index(drop=True)
    lag_sales = {int(re.search(r'\d+', c).group()): c for c in b if c.startswith('Продажі\n')}
    lag_hits = {int(re.search(r'\d+', c).group()): c for c in b if c.startswith('Хіти ') and '\n' in c}
    if set(lag_sales) != set(range(1, 37)) or set(lag_hits) != set(range(1, 37)):
        raise ValueError('Очікуються 36 пар місячних стовпців; перевірте структуру джерела.')
    dates = {lag: (snapshot.to_period('M') - lag).to_timestamp() for lag in range(1, 37)}
    s = b[[lag_sales[i] for i in range(1, 37)]].copy()
    h = b[[lag_hits[i] for i in range(1, 37)]].copy()
    s.columns = h.columns = [dates[i].strftime('%Y-%m') for i in range(1, 37)]
    s12, h12 = s.iloc[:, :12], h.iloc[:, :12]
    canonical = {
        'Артикул': 'sku_id', 'GUID': 'guid', 'Назва': 'name',
        'Номенклатурна гр 1 рівень': 'subsystem', 'Номенклатурна гр 2 рівень': 'series_raw',
        group_col: 'group', 'К-сть в упаковці': 'pack_u', 'Кратність продаж': 'sale_multiple_u',
        'Прайс': 'price', 'Індикотор повернення': 'return_raw', 'Critical Code': 'critical_code',
        'Inventory Class Code': 'inventory_class', 'Доступний залишок на складі': 'available_balance_u',
        'В замовленні постачальника': 'ordered_u', 'Зарезервовано на складі': 'reserved_u',
        'Продажі з початку місяця': 'current_month_units', 'Хіти поточного місяця': 'current_month_hits',
    }
    m = b[['source_row'] + list(canonical)].rename(columns=canonical)
    m['series'] = m['series_raw'].str.strip().replace({'Трактрори 6B': 'Трактори 6B'})
    m['return_flag_assumed'] = m['return_raw'].map({'Retun': 'R', 'NO': 'E'})
    m['observed_months_12'] = s12.notna().sum(axis=1)
    m['observed_months_36'] = s.notna().sum(axis=1)
    m['complete_12'] = m['observed_months_12'].eq(12)
    m['complete_36'] = m['observed_months_36'].eq(36)
    m['first_observed_month'] = [next((c for c in reversed(s.columns) if pd.notna(row[c])), None) for _, row in s.iterrows()]
    m['sold_12_observed_u'] = s12.sum(axis=1, min_count=1)
    m['sold_36_observed_u'] = s.sum(axis=1, min_count=1)
    m['hits_12_sum_proxy'] = h12.sum(axis=1, min_count=1)
    m['hits_12_max_month'] = h12.max(axis=1)
    m['hits_segment_proxy'] = pd.cut(m['hits_12_sum_proxy'], [-1, 2, 3, 9, np.inf], labels=['0–2', '3', '4–9', '10+']).astype('string').fillna('Немає історії')
    m['clients_ge3_evidence'] = np.select(
        [m['hits_12_max_month'].ge(3), ~m['complete_12'], m['hits_12_sum_proxy'].lt(3)],
        ['LOWER_BOUND_GE3', 'HISTORY_INCOMPLETE', 'UPPER_BOUND_LT3'],
        default='NEEDS_CLIENT_IDS')
    m['positive_months_12'] = s12.gt(0).sum(axis=1)
    m['avg_month_observed_u'] = s12.mean(axis=1)
    m['std_month_observed_u'] = s12.std(axis=1, ddof=1)
    m['cv_12'] = m['std_month_observed_u'].div(m['avg_month_observed_u'].replace(0, np.nan))
    m['adi_12_observed'] = m['observed_months_12'].div(m['positive_months_12'].replace(0, np.nan))
    m['sold12_price_proxy'] = m['sold_12_observed_u'] * m['price']
    m['balance_price_proxy'] = m['available_balance_u'] * m['price']
    m['ordered_price_proxy'] = m['ordered_u'] * m['price']
    m['reserve_price_proxy'] = m['reserved_u'] * m['price']
    m['ip_case_if_gross_u'] = m['available_balance_u'] + m['ordered_u'] - m['reserved_u']
    m['ip_if_available_is_free_u'] = m['available_balance_u'] + m['ordered_u']
    # ABC only for rows with a complete, comparable annual observation window.
    m['abc_12'] = np.where(m['complete_12'], 'D0', 'N')
    eligible = m.loc[m['complete_12'] & m['sold12_price_proxy'].gt(0)].sort_values(['sold12_price_proxy', 'sku_id'], ascending=[False, True])
    before = (eligible['sold12_price_proxy'].cumsum() - eligible['sold12_price_proxy']) / eligible['sold12_price_proxy'].sum()
    m.loc[eligible.index, 'abc_12'] = np.select([before.lt(.8), before.lt(.95)], ['A', 'B'], default='C')
    m['xyz_12'] = np.select(
        [~m['complete_12'], m['sold_12_observed_u'].eq(0), m['cv_12'].le(.5), m['cv_12'].le(1)],
        ['N', 'D0', 'X', 'Y'], default='Z')
    m['abc_xyz'] = m['abc_12'] + '/' + m['xyz_12']
    m['no_observed_sales_37'] = (m['sold_36_observed_u'] + m['current_month_units']).eq(0)
    m['flag_boundary3_proxy'] = m['hits_12_sum_proxy'].eq(3)
    m['flag_cc_unknown'] = ~m['critical_code'].isin([11, 12, 13, 14, 15, 20, 21, 50, 51, 52, 54, 55, 90, 91, 99])
    m['flag_reserve_gt_balance'] = m['reserved_u'].gt(m['available_balance_u'])
    m['flag_cc11_12_zero_balance_recent_sales'] = m['critical_code'].isin([11, 12]) & m['available_balance_u'].eq(0) & m['sold_12_observed_u'].gt(0)
    m['flag_balance_without_sales12'] = m['available_balance_u'].gt(0) & m['sold_12_observed_u'].eq(0)
    m['flag_balance_without_sales37'] = m['available_balance_u'].gt(0) & m['no_observed_sales_37']
    m['flag_cc13_balance'] = m['critical_code'].eq(13) & m['available_balance_u'].gt(0)
    mismatch_cells = (s.eq(0).to_numpy() & h.gt(0).to_numpy())
    m['flag_hits_without_units'] = mismatch_cells.any(axis=1)
    m['review_flags_count'] = m[[c for c in m if c.startswith('flag_')]].sum(axis=1)
    anomalies = []
    for row, col in zip(*np.where(mismatch_cells)):
        anomalies.append({'sku_id': m.at[row, 'sku_id'], 'source_row': int(m.at[row, 'source_row']), 'month': s.columns[col], 'units': float(s.iat[row, col]), 'hits': float(h.iat[row, col]), 'issue': 'Хіти > 0 за нульових продажів; збережено без виправлення'})
    anomalies = pd.DataFrame(anomalies, columns=['sku_id','source_row','month','units','hits','issue'])
    hist_sales = pd.concat([m[['sku_id']], s.reindex(columns=sorted(s.columns))], axis=1)
    hist_hits = pd.concat([m[['sku_id']], h.reindex(columns=sorted(h.columns))], axis=1)
    monthly = []
    cohort = m['complete_36']
    for lag in range(36, 0, -1):
        month = dates[lag].strftime('%Y-%m')
        sold = s[month]
        monthly.append({'month': month, 'lag': lag, 'fiscal_year_end': dates[lag].year + (dates[lag].month >= 11),
                        'sold_observed_u': sold.sum(min_count=1), 'hits_sum_proxy': h[month].sum(min_count=1),
                        'sku_observed': sold.notna().sum(), 'sku_positive_sales': sold.gt(0).sum(),
                        'sales_missing': sold.isna().sum(), 'coverage_pct': sold.notna().mean() * 100,
                        'sold_price_proxy': (sold * m['price']).sum(min_count=1),
                        'cohort36_sold_u': sold[cohort].sum(min_count=1), 'cohort36_sku_n': cohort.sum()})
    monthly = pd.DataFrame(monthly)
    monthly_series = []
    for series, ix in m.groupby('series').groups.items():
        for month in sorted(s.columns):
            monthly_series.append({'series': series, 'month': month, 'sold_observed_u': s.loc[ix, month].sum(min_count=1),
                                   'sku_observed': s.loc[ix, month].notna().sum(), 'sku_total': len(ix),
                                   'sold_price_proxy': (s.loc[ix, month] * m.loc[ix, 'price']).sum(min_count=1)})
    monthly_series = pd.DataFrame(monthly_series)

    def aggregate(keys):
        return m.groupby(keys, dropna=False, observed=True).agg(
            sku_n=('sku_id', 'size'), sold12_u=('sold_12_observed_u', 'sum'),
            sold12_price_proxy=('sold12_price_proxy', 'sum'), balance_u=('available_balance_u', 'sum'),
            balance_price_proxy=('balance_price_proxy','sum'), ordered_price_proxy=('ordered_price_proxy','sum'),
            complete12_sku_n=('complete_12','sum'), boundary3_sku_n=('flag_boundary3_proxy','sum')).reset_index()

    series = aggregate(['series'])
    series['sku_share_pct'] = series['sku_n'] / len(m) * 100
    segments = aggregate(['hits_segment_proxy'])
    segment_order = {'0–2': 0, '3': 1, '4–9': 2, '10+': 3, 'Немає історії': 4}
    segments = segments.iloc[segments['hits_segment_proxy'].map(segment_order).argsort()].reset_index(drop=True)
    matrix = aggregate(['critical_code','inventory_class','return_raw','hits_segment_proxy'])
    abcxyz = aggregate(['abc_12','xyz_12'])
    coverage = monthly[['month','sku_observed','sales_missing','coverage_pct','sku_positive_sales']].copy()
    outside = df.loc[df['Номенклатурна гр 1 рівень'].str.contains('трактор', case=False, na=False) & df[group_col].ne('Трактори')].copy()
    outside = outside.rename(columns=canonical)[['sku_id','name','subsystem','series_raw','group','price','available_balance_u']]
    return {'b': b, 'm': m, 's': s, 'h': h, 'sales': hist_sales, 'hits': hist_hits,
            'monthly': monthly, 'monthly_series': monthly_series, 'series': series,
            'segments': segments, 'matrix': matrix, 'abcxyz': abcxyz, 'coverage': coverage,
            'anomalies': anomalies, 'outside': outside, 'canonical': canonical,
            'lag_sales': lag_sales, 'lag_hits': lag_hits}


def get_metrics(data, source_rows):
    m, s, h = data['m'], data['s'], data['h']
    return {
        'source_rows': source_rows, 'source_columns': 89, 'sku_n': len(m), 'share_pct': len(m)/source_rows*100,
        'sold12_u': m['sold_12_observed_u'].sum(), 'sold36_u': m['sold_36_observed_u'].sum(),
        'current_u': m['current_month_units'].sum(), 'hits12_proxy': m['hits_12_sum_proxy'].sum(),
        'balance_u': m['available_balance_u'].sum(), 'ordered_u': m['ordered_u'].sum(), 'reserved_u': m['reserved_u'].sum(),
        'balance_value': m['balance_price_proxy'].sum(), 'ordered_value': m['ordered_price_proxy'].sum(),
        'reserved_value': m['reserve_price_proxy'].sum(), 'sold12_value': m['sold12_price_proxy'].sum(),
        'missing_sales': int(s.isna().sum().sum()), 'missing_hits': int(h.isna().sum().sum()),
        'complete12': int(m['complete_12'].sum()), 'complete36': int(m['complete_36'].sum()),
        'incomplete12': int((~m['complete_12']).sum()),
        'segments': m['hits_segment_proxy'].value_counts().to_dict(),
        'abc': m['abc_12'].value_counts().to_dict(), 'xyz': m['xyz_12'].value_counts().to_dict(),
        'client_evidence': m['clients_ge3_evidence'].value_counts().to_dict(),
        'no_sales37': int(m['no_observed_sales_37'].sum()),
        'no_sales37_balance': int(m['flag_balance_without_sales37'].sum()),
        'no_sales12_balance': int(m['flag_balance_without_sales12'].sum()),
        'no_sales12_balance_value': m.loc[m['flag_balance_without_sales12'], 'balance_price_proxy'].sum(),
        'no_sales37_balance_value': m.loc[m['flag_balance_without_sales37'], 'balance_price_proxy'].sum(),
        'zero_balance_critical_recent': int(m['flag_cc11_12_zero_balance_recent_sales'].sum()),
        'reserve_gt_balance': int(m['flag_reserve_gt_balance'].sum()),
        'cc13_balance': int(m['flag_cc13_balance'].sum()), 'unknown_cc': int(m['flag_cc_unknown'].sum()),
        'no_return': int(m['return_raw'].eq('NO').sum()), 'returnable': int(m['return_raw'].eq('Retun').sum()),
        'boundary_complete12': int(m.loc[m['flag_boundary3_proxy'],'complete_12'].sum()),
        'boundary_max3': int((m['flag_boundary3_proxy'] & m['hits_12_max_month'].eq(3)).sum()),
        'outside_tractor_named': len(data['outside']),
        'balance_proxy_ratio': m['sold12_price_proxy'].sum()/m['balance_price_proxy'].sum(),
        'budget_proxy35': m['sold12_price_proxy'].sum()/3.5,
        'budget_proxy25': m['sold12_price_proxy'].sum()/2.5,
        'units_total_37': m['sold_36_observed_u'].sum()+m['current_month_units'].sum(),
    }


def build_checks(data, metrics):
    m, b, s, h = (data[k] for k in ['m','b','s','h'])
    checks = []
    def add(name, actual, expected, kind='контроль', note=''):
        ok = np.isclose(actual, expected, rtol=1e-9, atol=1e-6) if isinstance(actual, (int,float,np.number)) else actual == expected
        checks.append({'check': name, 'actual': actual, 'expected': expected, 'status': 'PASS' if ok else 'REVIEW', 'type': kind, 'note': note})
    add('Кількість відібраних SKU = сума серій', len(m), int(data['series']['sku_n'].sum()))
    add('Сегментація за хітами зберігає всі SKU', len(m), int(data['segments']['sku_n'].sum()))
    add('Матриця CC × IC × RET × хіти зберігає всі SKU', len(m), int(data['matrix']['sku_n'].sum()))
    add('ABC/XYZ зберігає всі SKU', len(m), int(data['abcxyz']['sku_n'].sum()))
    add('Дублікати sku_id', int(m['sku_id'].duplicated().sum()), 0)
    add('Дублікати GUID', int(m['guid'].duplicated().sum()), 0)
    add('Пропуски sku_id', int(m['sku_id'].isna().sum()), 0)
    add('Пропуски GUID', int(m['guid'].isna().sum()), 0)
    add('36 повних місяців історії', len(s.columns), 36)
    add('Розбіжність масок пропусків продажів і хітів', int((s.isna().to_numpy() != h.isna().to_numpy()).sum()), 0)
    for label, hist in [('sales',s), ('hits',h)]:
        present = hist.notna().to_numpy()
        internal = ((~present) & (np.cumsum(present,axis=1)>0) & (np.cumsum(present[:,::-1],axis=1)[:,::-1]>0))
        add(f'Внутрішні прогалини {label}', int(internal.sum()), 0, note='Виявлені NaN розташовані лише перед початком наявної історії; причина невідома.')
    numeric = b.select_dtypes('number')
    add('Від’ємні числові значення', int(numeric.lt(0).sum().sum()), 0)
    add('Невалідна упаковка чи кратність', int((m['pack_u'].le(0) | m['sale_multiple_u'].le(0)).sum()), 0)
    add('Пропуски статичних полів', int(m[list(data['canonical'].values())].isna().sum().sum()), 0)
    add('Баланс за агрегатами серій', float(data['series']['balance_price_proxy'].sum()), metrics['balance_value'])
    add('Сума 36 місяців = сума SKU', float(data['monthly']['sold_observed_u'].sum()), metrics['sold36_u'])
    add('Останні 12 місяців = річна сума SKU', float(data['monthly'].tail(12)['sold_observed_u'].sum()), metrics['sold12_u'])
    add('Невідомі коди CC', metrics['unknown_cc'], 0, 'виняток даних', 'Код 0 збережено; автоматичну політику не призначати.')
    add('Хіти > 0 при продажах = 0 (місячні комірки)', len(data['anomalies']), 0, 'виняток даних', 'Один SKU, три місяці; дані не виправлялися.')
    add('Резерв перевищує доступний залишок', metrics['reserve_gt_balance'], 0, 'семантичне уточнення', 'Може бути коректно, якщо доступний залишок уже очищений від резерву.')
    return pd.DataFrame(checks)


def make_charts(data, out):
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10, 'axes.spines.top':False, 'axes.spines.right':False})
    fig, axes = plt.subplots(2, 1, figsize=(11, 7.5), sharex=True, gridspec_kw={'height_ratios':[2,1]})
    d = data['monthly']
    x = pd.to_datetime(d['month'])
    axes[0].plot(x, d['sold_observed_u'], color='#24764B', lw=2, label='Уся група, спостережені продажі')
    axes[0].plot(x, d['cohort36_sold_u'], color='#C68C24', lw=1.6, label=f"Стала когорта: {fmt(data['m']['complete_36'].sum())} SKU з повною історією")
    axes[0].set_ylabel('Одиниць за місяць')
    axes[0].set_title('Трактори B: історія продажів і зміна покриття даними', loc='left', weight='bold')
    axes[0].legend(frameon=False, fontsize=9)
    axes[0].grid(alpha=.15)
    axes[1].fill_between(x, d['sku_observed'], color='#24764B', alpha=.16)
    axes[1].plot(x, d['sku_observed'], color='#24764B')
    axes[1].set_ylabel('SKU з даними')
    axes[1].set_ylim(0, 14000)
    axes[1].grid(alpha=.15)
    fig.text(.08,.015,'NaN збережено. Листопад 2024 (неповний) не включено. Стала когорта не усуває зміни активності SKU.', fontsize=9, color='#555555')
    fig.tight_layout(rect=[0,.045,1,1])
    fig.savefig(out/'history_and_coverage.png', dpi=180)
    fig.savefig(out/'history_and_coverage.pdf')
    plt.close(fig)
    d = data['series'].sort_values('sku_n')
    fig, ax = plt.subplots(figsize=(9,4.8))
    labels = d['series'].str.replace('Трактори ', '', regex=False)
    bars = ax.barh(labels, d['sku_n'], color='#24764B')
    ax.bar_label(bars, labels=[fmt(v) for v in d['sku_n']], padding=5)
    ax.set_xlim(0, max(d['sku_n'])*1.14)
    ax.set_title('Склад групи B: усі 13 398 SKU збережено', loc='left', weight='bold')
    ax.set_xlabel('Кількість SKU')
    fig.tight_layout()
    fig.savefig(out/'series_structure.png', dpi=180)
    plt.close(fig)


def write_workbook(out, sheets):
    wb = Workbook(write_only=True)
    wb.calculation = CalcProperties(calcId=191029, fullCalcOnLoad=True)
    wb.properties.title = 'АГРОСЕМ — Трактори B — частина 1/5'
    wb.properties.subject = 'Аудит, очищення, сегментація та передача даних команді'
    wb.properties.creator = 'Аналітична команда'
    for name, df in sheets.items():
        ws = wb.create_sheet(name)
        ws.freeze_panes = 'B2'
        ws.sheet_view.showGridLines = False
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.print_options.horizontalCentered = True
        ws.page_setup.orientation = 'landscape'
        ws.page_setup.paperSize = '9'
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        for i, column in enumerate(df.columns, 1):
            width = 16
            if str(column) in ('sku_id','guid','Артикул','GUID'):
                width = 39
            elif any(k in str(column).lower() for k in ['description','note','опис','правило','issue','умова','зміст','результат','завдання']):
                width = 68
            elif str(column) in ['name','Назва','series','subsystem','series_raw','check','Показник','Крок','field','source_column']:
                width = 34
            elif name in ['Продажі_36м','Хіти_36м'] and i > 1:
                width = 12
            ws.column_dimensions[get_column_letter(i)].width = width
        headers=[]
        for col in df.columns:
            cell=WriteOnlyCell(ws,value=str(col))
            cell.font=Font(name='Calibri',bold=True,color='FFFFFF',size=10)
            cell.fill=PatternFill('solid',fgColor='205B3A')
            cell.alignment=Alignment(vertical='center',wrap_text=True)
            headers.append(cell)
        ws.append(headers)
        ws.row_dimensions[1].height = 42
        numeric_cols = {i: col for i,col in enumerate(df.columns) if pd.api.types.is_numeric_dtype(df[col])}
        for values in df.itertuples(index=False, name=None):
            cells=[]
            for i, value in enumerate(values):
                if value is pd.NA or value is None or (not isinstance(value,str) and pd.isna(value)):
                    cells.append(None)
                    continue
                if isinstance(value,np.generic): value=value.item()
                if isinstance(value,(pd.Timestamp,datetime)): value=value.strftime('%Y-%m-%d')
                if i in numeric_cols and isinstance(value,float):
                    cell=WriteOnlyCell(ws,value=round(value,8))
                    col=str(numeric_cols[i]).lower()
                    cell.number_format = '#,##0.00' if any(k in col for k in ['price','value','cv','adi','pct','avg','std']) else '#,##0.########'
                    cells.append(cell)
                else:
                    cell=WriteOnlyCell(ws,value=value)
                    if isinstance(value,str):
                        # All user data is literal text, including names that start with '='.
                        cell.data_type='s'
                    cells.append(cell)
            ws.append(cells)
        ws.auto_filter.ref=f'A1:{get_column_letter(len(df.columns))}{len(df)+1}'
        if name == 'Місяці_група':
            chart = LineChart()
            chart.title='Спостережені продажі: 36 повних місяців'
            chart.y_axis.title='Одиниць'
            chart.x_axis.title='Місяць'
            chart.add_data(Reference(ws,min_col=4,max_col=4,min_row=1,max_row=len(df)+1),titles_from_data=True)
            chart.set_categories(Reference(ws,min_col=1,min_row=2,max_row=len(df)+1))
            chart.height=11
            chart.width=24
            ws.add_chart(chart,'N2')
    wb.save(out/'03_АГРОСЕМ_B_дані_та_аудит.xlsx')


def document_from_markdown(markdown, destination, charts=None):
    from docx import Document
    from docx.shared import Inches, Pt, RGBColor
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    doc = Document()
    sec=doc.sections[0]
    sec.top_margin=Inches(.65)
    sec.bottom_margin=Inches(.65)
    sec.left_margin=Inches(.7)
    sec.right_margin=Inches(.7)
    sec.page_width=Inches(8.27)
    sec.page_height=Inches(11.69)
    normal=doc.styles['Normal']
    normal.font.name='Calibri'
    normal.font.size=Pt(10.5)
    normal.paragraph_format.space_after=Pt(6)
    for sn in ['Title','Heading 1','Heading 2','Heading 3']:
        doc.styles[sn].font.name='Calibri'
        doc.styles[sn].font.color.rgb=RGBColor.from_string('205B3A')
    lines=markdown.splitlines()
    i=0
    def plain(text):
        return text.replace('**','').replace('`','')
    while i<len(lines):
        line=lines[i]
        if line.startswith('|'):
            rows=[]
            while i<len(lines) and lines[i].startswith('|'):
                row=[plain(x.strip()) for x in lines[i].strip('|').split('|')]
                if not all(re.fullmatch(r'[:\- ]+',x) for x in row): rows.append(row)
                i+=1
            table=doc.add_table(rows=1, cols=len(rows[0]))
            table.style='Light Shading Accent 1'
            for j, value in enumerate(rows[0]): table.rows[0].cells[j].text=value
            trPr=table.rows[0]._tr.get_or_add_trPr()
            repeat=OxmlElement('w:tblHeader')
            trPr.append(repeat)
            for row in rows[1:]:
                cells=table.add_row().cells
                for j,value in enumerate(row): cells[j].text=value
            for row in table.rows:
                for cell in row.cells:
                    for p in cell.paragraphs:
                        for run in p.runs: run.font.size=Pt(8.5)
            continue
        if line.startswith('![') and charts:
            match=re.match(r'!\[([^]]*)\]\(([^)]+)\)',line)
            if match and (charts/match.group(2)).exists():
                doc.add_picture(str(charts/match.group(2)),width=Inches(6.75))
                doc.add_paragraph(match.group(1),style='Caption')
        elif line.startswith('# '): doc.add_heading(plain(line[2:]),0)
        elif line.startswith('### '): doc.add_heading(plain(line[4:]),2)
        elif line.startswith('## '): doc.add_heading(plain(line[3:]),1)
        elif line.startswith('- '): doc.add_paragraph(plain(line[2:]),style='List Bullet')
        elif re.match(r'^\d+\. ',line): doc.add_paragraph(plain(re.sub(r'^\d+\. ','',line)),style='List Number')
        elif line.strip(): doc.add_paragraph(plain(line))
        i+=1
    footer=sec.footer.paragraphs[0]
    footer.alignment=2
    footer.add_run('АГРОСЕМ • Трактори B • ')
    field=OxmlElement('w:fldSimple')
    field.set(qn('w:instr'),'PAGE')
    footer._p.append(field)
    doc.core_properties.title=markdown.splitlines()[0].lstrip('# ')
    doc.core_properties.subject='Командний кейс: план і перша частина аналітичної роботи'
    doc.save(destination)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,default=Path(r'C:\Users\user\Downloads\12_11_2024_140029.xlsx'))
    parser.add_argument('--case',type=Path,default=Path(r'C:\Users\user\Downloads\АГРОСЕМ_case.pdf'))
    parser.add_argument('--output',type=Path,default=ROOT/'agrosem_results')
    parser.add_argument('--snapshot',default='2024-11-12',help='Історичний зріз цього кейсу: 2024-11-12.')
    parser.add_argument('--use-cache',action='store_true',help='Лише для повторного складання з локального, власноруч створеного кешу.')
    args=parser.parse_args()
    out=args.output.resolve()
    out.mkdir(parents=True,exist_ok=True)
    (out/'charts').mkdir(exist_ok=True)
    (out/'data').mkdir(exist_ok=True)
    snapshot=pd.Timestamp(args.snapshot)
    if snapshot != pd.Timestamp('2024-11-12'):
        raise ValueError('Шаблони цього звіту підготовлені для зрізу 2024-11-12. Для іншої дати потрібно також адаптувати текст і періоди звіту.')
    if args.use_cache:
        df=pd.read_pickle(out/'source_cache.pkl')
    else:
        df=pd.read_excel(args.source,dtype={'Артикул':str,'GUID':str})
    data=build_data(df,snapshot)
    metrics=get_metrics(data,len(df))
    checks=build_checks(data,metrics)
    (out/'metrics.json').write_text(json.dumps(metrics,ensure_ascii=False,indent=2,default=lambda x:x.item() if isinstance(x,np.generic) else str(x)),encoding='utf-8')
    print(json.dumps(metrics,ensure_ascii=False,indent=2,default=lambda x:x.item() if isinstance(x,np.generic) else str(x)),flush=True)
    make_charts(data,out/'charts')
    # Narrative and workbook definitions are separated for readability.
    from agrosem_report_content import build_content
    plan, report, sheets, dictionary=build_content(data,metrics,checks,snapshot)
    (out/'01_План_і_ролі.md').write_text(plan,encoding='utf-8')
    (out/'02_Частина_1_аудит.md').write_text(report,encoding='utf-8')
    document_from_markdown(plan,out/'01_План_і_ролі.docx')
    document_from_markdown(report,out/'02_Частина_1_аудит.docx',out)
    exports={'sku_master':data['m'],'sales_36m':data['sales'],'hits_36m':data['hits'],
             'monthly_group':data['monthly'],'monthly_series':data['monthly_series'],
             'boundary_3hits':data['m'].loc[data['m']['flag_boundary3_proxy']],
             'cell_anomalies':data['anomalies'],'data_dictionary':dictionary,'quality_checks':checks,
             'series_summary':data['series'],'risk_matrix':data['matrix'],'abc_xyz':data['abcxyz'],
             'coverage':data['coverage'],'assumptions':sheets['Припущення'],'calendar':sheets['Календар']}
    for name, frame in exports.items():
        frame.to_csv(out/'data'/f'{name}.csv',index=False,sep=';',encoding='utf-8-sig',float_format='%.12g')
    saleslong=data['sales'].melt(id_vars='sku_id',var_name='month',value_name='sold_units')
    hitslong=data['hits'].melt(id_vars='sku_id',var_name='month',value_name='hits')
    if not saleslong[['sku_id','month']].equals(hitslong[['sku_id','month']]): raise AssertionError('Розійшлися ключі місячних даних')
    saleslong['hits']=hitslong['hits']
    saleslong['is_observed']=saleslong['sold_units'].notna()
    saleslong.to_csv(out/'data/monthly_long.csv.gz',index=False,encoding='utf-8',float_format='%.12g',compression='gzip')
    write_workbook(out,sheets)
    manifest={'snapshot_assumed':str(snapshot.date()),'source_excel':{'path':str(args.source),'sha256':sha256(args.source)},
              'case_pdf':{'path':str(args.case),'sha256':sha256(args.case)},
              'input_excel_shape':list(df.shape),'scope_rule':'Номенклатурна гр 3 рівень == Трактори',
              'monthly_window':'2021-11..2024-10','annual_window':'2023-11..2024-10',
              'checks_pass':int(checks['status'].eq('PASS').sum()),'checks_review':int(checks['status'].eq('REVIEW').sum()),
              'sheet_shapes':{name:list(frame.shape) for name,frame in sheets.items()},
              'artifacts':{p.name:{'bytes':p.stat().st_size,'sha256':sha256(p)} for p in out.iterdir() if p.suffix in ['.docx','.xlsx','.md']}}
    (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print('DONE',json.dumps({'out':str(out),'sheets':len(sheets),'checks_pass':manifest['checks_pass'],'checks_review':manifest['checks_review']},ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
