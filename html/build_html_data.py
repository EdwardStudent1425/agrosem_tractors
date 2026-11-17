"""Збирає компактні дані для HTML-сторінок із сирого Excel.

Запуск з кореня репозиторію:
    python html/build_html_data.py --source inputs/12_11_2024_140029.xlsx

Використовує build_data() з build_agrosem_analysis.py (код колеги), але НЕ читає його
готових файлів з agrosem_results/. Результат: html/data/*.js у вигляді
`window.AGRO.<ключ> = {...}`, щоб сторінки відкривались подвійним кліком (file://) без fetch().
Кеш розпарсеного Excel (повільний) лежить поза репозиторієм: параметр --cache.
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding='utf-8')
import build_agrosem_analysis as base  # noqa: E402  (код колеги, тільки build_data)

OUT = Path(__file__).resolve().parent / 'data'
SNAPSHOT = pd.Timestamp('2024-11-12')

# Контрольні числа, які ми перевірили вручну за сирим Excel (див. розмову).
EXPECT = {
    'sku_n': 13398,
    'sold12_u': 71162,
    'boundary3': 519,
    'monthly_2021_11_to_2024_10': [3471, 3790, 4345, 5998, 3886, 2705, 2697, 4021, 3108, 3776, 3289, 2451,
                                    4466, 2658, 4545, 8493, 8746, 3011, 3708, 4269, 3922, 3394, 4609, 4238,
                                    4735, 3252, 8033, 11253, 9066, 5047, 5796, 4313, 5473, 5284, 4379, 4531],
}


def load(source: Path, cache: Path | None):
    if cache and cache.exists():
        with open(cache, 'rb') as f:
            return pickle.load(f)
    df = pd.read_excel(source)
    data = base.build_data(df, SNAPSHOT)
    data['source_rows'] = len(df)
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        with open(cache, 'wb') as f:
            pickle.dump(data, f)
    return data


def r(x, n=2):
    return None if pd.isna(x) else round(float(x), n)


def write_js(name: str, payload) -> int:
    text = 'window.AGRO = window.AGRO || {};\nwindow.AGRO.%s = %s;\n' % (
        name, json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
    path = OUT / f'{name}.js'
    path.write_text(text, encoding='utf-8')
    return path.stat().st_size


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', type=Path, default=ROOT / 'inputs' / '12_11_2024_140029.xlsx')
    ap.add_argument('--cache', type=Path, default=None, help='pickle-кеш поза репозиторієм')
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    d = load(args.source, args.cache)
    m, s = d['m'], d['s']
    months = sorted(s.columns)                       # 2021-11 ... 2024-10
    assert len(months) == 36 and months[0] == '2021-11' and months[-1] == '2024-10'

    # ---------- перевірка за числами, які ми вже підтвердили вручну ----------
    group_month = [int(s[c].sum()) for c in months]
    checks = {
        'sku_n': len(m) == EXPECT['sku_n'],
        'sold12_u': int(m['sold_12_observed_u'].sum()) == EXPECT['sold12_u'],
        'boundary3': int(m['flag_boundary3_proxy'].sum()) == EXPECT['boundary3'],
        'monthly_36': group_month == EXPECT['monthly_2021_11_to_2024_10'],
    }
    print('CHECKS', checks)
    assert all(checks.values()), checks

    # ---------- щомісячні ряди ----------
    cohort = m['complete_36'].to_numpy()
    def series_of(mask):
        return [int(s.loc[mask, c].sum()) for c in months]
    def observed_of(mask):
        return [int(s.loc[mask, c].notna().sum()) for c in months]

    all_mask = np.ones(len(m), bool)
    monthly = {
        'months': months,
        'all': series_of(all_mask),
        'all_observed_sku': observed_of(all_mask),
        'cohort': series_of(cohort),
        'cohort_sku_n': int(cohort.sum()),
        'by_series': {k: series_of(m['series'].eq(k).to_numpy()) for k in sorted(m['series'].unique())},
        'by_cc': {str(int(k)): series_of(m['critical_code'].eq(k).to_numpy()) for k in sorted(m['critical_code'].unique())},
        'series_sku_n': m['series'].value_counts().to_dict(),
        'cc_sku_n': {str(int(k)): int(v) for k, v in m['critical_code'].value_counts().items()},
        'incomplete_month': {'month': '2024-11', 'units_to_12_nov': int(m['current_month_units'].sum())},
    }

    # Сезонні індекси: місяць / середнє місяців того ж бюджетного року (листопад-жовтень).
    fy = [int(c[:4]) + (1 if int(c[5:]) >= 11 else 0) for c in months]
    def seasonal(values):
        arr = np.array(values, float)
        out = {}
        for y in sorted(set(fy)):
            idx = [i for i, v in enumerate(fy) if v == y]
            out[y] = (arr[idx] / arr[idx].mean())
        return out
    order = [11, 12, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]  # порядок місяців бюджетного року
    seas = {}
    for name, vals in (('all', monthly['all']), ('cohort', monthly['cohort'])):
        per_fy = seasonal(vals)
        seas[name] = {'FY%d' % y: [r(v, 3) for v in arr] for y, arr in per_fy.items()}
        seas[name]['mean'] = [r(np.mean([per_fy[y][i] for y in per_fy]), 3) for i in range(12)]
        seas[name]['fy_totals'] = {'FY%d' % y: int(np.array(vals)[[i for i, v in enumerate(fy) if v == y]].sum()) for y in per_fy}
    seas['fy_month_order'] = order
    monthly['seasonal'] = seas
    write_js('monthly', monthly)

    # ---------- довідник SKU для пошуку/симулятора ----------
    sk = m.copy()
    sk['cc'] = sk['critical_code'].astype(int)
    cols = ['sku_id', 'name', 'series', 'subsystem', 'cc', 'inventory_class', 'return_raw', 'price',
            'available_balance_u', 'ordered_u', 'reserved_u', 'sold_12_observed_u', 'hits_12_sum_proxy',
            'hits_12_max_month', 'observed_months_12', 'avg_month_observed_u', 'std_month_observed_u',
            'pack_u', 'sale_multiple_u']
    rows = []
    for t in sk[cols].itertuples(index=False):
        rows.append([t.sku_id[:8], str(t.name).strip(), t.series.replace('Трактори ', ''), str(t.subsystem).strip(), t.cc,
                     t.inventory_class, 1 if t.return_raw == 'Retun' else 0, r(t.price),
                     r(t.available_balance_u, 0), r(t.ordered_u, 0), r(t.reserved_u, 0),
                     r(t.sold_12_observed_u, 0), r(t.hits_12_sum_proxy, 0), r(t.hits_12_max_month, 0),
                     int(t.observed_months_12), r(t.avg_month_observed_u, 3), r(t.std_month_observed_u, 3),
                     r(t.pack_u, 0), r(t.sale_multiple_u, 0)])
    skus = {
        'cols': ['id8', 'name', 'series', 'subsystem', 'cc', 'ic', 'ret', 'price', 'stock', 'ordered', 'reserved',
                 'sold12', 'hits12', 'hitsMax', 'obs12', 'mean12', 'std12', 'pack', 'saleMult'],
        'ret_note': 'ret: 1 = «Retun» (повернення можливе, робоче припущення R), 0 = «NO» (E)',
        'rows': rows,
    }
    size_sku = write_js('skus', skus)

    # ---------- зведення, які цитують сторінки ----------
    sold12_value = float(m['sold12_price_proxy'].sum())
    summary = {
        'snapshot': '2024-11-12',
        'sku_n': len(m), 'catalog_rows': d['source_rows'],
        'sold12_u': int(m['sold_12_observed_u'].sum()), 'sold36_u': int(m['sold_36_observed_u'].sum()),
        'sold12_value': r(sold12_value), 'stock_u': int(m['available_balance_u'].sum()),
        'stock_value': r(m['balance_price_proxy'].sum()), 'ordered_u': int(m['ordered_u'].sum()),
        'ordered_value': r(m['ordered_price_proxy'].sum()), 'reserved_u': int(m['reserved_u'].sum()),
        'reserved_value': r(m['reserve_price_proxy'].sum()),
        'budget_proxy_3_5': r(sold12_value / 3.5), 'budget_proxy_2_5': r(sold12_value / 2.5),
        'complete12': int(m['complete_12'].sum()), 'complete36': int(m['complete_36'].sum()),
        'missing_cells': int(s.isna().sum().sum()),
        'boundary3': int(m['flag_boundary3_proxy'].sum()),
        'boundary3_max3': int((m['flag_boundary3_proxy'] & m['hits_12_max_month'].eq(3)).sum()),
        'hits_segments': m['hits_segment_proxy'].value_counts().to_dict(),
        'abc': m['abc_12'].value_counts().to_dict(), 'xyz': m['xyz_12'].value_counts().to_dict(),
        'cc_counts': {str(int(k)): int(v) for k, v in m['critical_code'].value_counts().sort_index().items()},
        'series_table': [
            {'series': k, 'sku_n': int(g.shape[0]), 'sold12_u': int(g['sold_12_observed_u'].sum()),
             'stock_value': r(g['balance_price_proxy'].sum())}
            for k, g in m.groupby('series')],
        'checks': {k: bool(v) for k, v in checks.items()},
    }
    write_js('summary', summary)
    print('sizes (KB): skus %.0f' % (size_sku / 1024))
    for p in sorted(OUT.glob('*.js')):
        print(p.name, '%.0f KB' % (p.stat().st_size / 1024))


if __name__ == '__main__':
    main()
