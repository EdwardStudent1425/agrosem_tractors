"""Evidence-only regression tests. Run: python -m inventory.test_service_inventory."""
from pathlib import Path
import hashlib
from html.parser import HTMLParser
import json
import re
import unittest
import zipfile

import numpy as np
import pandas as pd
from openpyxl import load_workbook

from inventory.service_inventory import analyze_original,UNKNOWN_RESULTS

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'inventory/out'


class MissingValueTest(unittest.TestCase):
    def test_unknown_history_is_not_zero_or_inventory_decision(self):
        # Synthetic fixture tests missing-value handling, not company cost assumptions.
        common={'Артикул':'a','GUID':'a','Назва':'test','Номенклатурна гр 1 рівень':'test',
                'Номенклатурна гр 2 рівень':'Трактори 6-ї серії','Номенклатурна гр 3 рівень':'Трактори',
                'Critical Code':12,'Inventory Class Code':'A','Індикотор повернення':'Retun','Прайс':2,
                'Доступний залишок на складі':2,'В замовленні постачальника':0,'Зарезервовано на складі':0,
                'К-сть в упаковці':1,'Кратність продаж':1,'Продажі з початку місяця':0,'Хіти поточного місяця':0}
        a,b=common.copy(),common.copy()
        b.update({'Артикул':'b','GUID':'b','Доступний залишок на складі':0,'Продажі з початку місяця':1})
        for lag in range(1,37):
            for prefix in ['Продажі','Хіти       ']:
                c=f'{prefix}\n{lag} місяць тому'
                a[c]=0
                b[c]=np.nan
        sku,metrics,_,_=analyze_original(pd.DataFrame([a,b]))
        self.assertEqual(sku.loc[0,'sales12_observed_u'],0)
        self.assertTrue(pd.isna(sku.loc[1,'sales12_observed_u']))
        self.assertEqual(metrics['no_complete_month_history_skus'],1)
        self.assertTrue(sku[UNKNOWN_RESULTS].isna().all().all())


class EvidenceOutputTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s=json.loads((OUT/'summary.json').read_text(encoding='utf-8'))
        cls.p=pd.read_csv(OUT/'observed_SKU.csv',sep=';')

    def test_original_control_totals(self):
        m=self.s['metrics']
        self.assertEqual((m['source_rows'],m['source_columns'],m['sku_n']),(32864,89,13398))
        self.assertEqual(m['sales12_observed_u'],71162)
        self.assertEqual(m['sales36_observed_u'],170758)
        self.assertEqual((m['available_balance_u'],m['ordered_u'],m['reserved_u']),(45237,42262,1157))
        self.assertAlmostEqual(m['available_value_at_price'],1437595.09,places=2)
        self.assertEqual(m['boundary3_proxy_skus'],519)
        self.assertEqual(m['CC11_12_zero_balance_recent_sales_skus'],327)

    def test_no_economic_inputs_or_targets_filled(self):
        self.assertEqual(self.s['revision'],'evidence_only')
        self.assertEqual(self.s['task5_status'],'INCOMPLETE_MISSING_INPUTS')
        self.assertEqual(self.s['economic_results'],dict.fromkeys(UNKNOWN_RESULTS))
        self.assertTrue(self.p[UNKNOWN_RESULTS].isna().all().all())
        status=pd.read_csv(OUT/'calculation_status.csv',sep=';')
        self.assertEqual(set(status['result']),set(UNKNOWN_RESULTS))
        self.assertTrue(status['value'].isna().all())
        self.assertTrue(status['status'].eq('NOT_DETERMINED').all())
        self.assertTrue(status['reason'].str.len().gt(0).all())

    def test_missing_history_preserved(self):
        self.assertEqual(self.p['sales12_observed_u'].isna().sum(),2)
        self.assertEqual(self.p['hits12_sum_proxy'].isna().sum(),2)
        self.assertEqual(self.p['observed_months_12'].eq(12).sum(),12450)
        self.assertEqual(self.s['metrics']['missing_sales_cells_36'],67948)

    def test_source_forecasts_unchanged_no_rescaling(self):
        source=pd.read_csv(ROOT/'forecast/out/forecast_SKU_FY2025.csv',sep=';',decimal=',').set_index('sku_id')
        expected=source.loc[self.p['sku_id'],'прогноз_FY2025_шт'].to_numpy()
        self.assertTrue(np.array_equal(expected,self.p['published_forecast_annual_u'].to_numpy()))
        fc=self.s['published_forecast']
        self.assertAlmostEqual(fc['published_group_annual_u'],82685)
        self.assertAlmostEqual(fc['published_SKU_annual_sum_u'],82690.26)
        self.assertAlmostEqual(fc['published_difference_u'],5.26)
        self.assertFalse(fc['forecast_changed'])

    def test_forecast_windows_do_not_extrapolate_or_become_S(self):
        windows=pd.read_csv(OUT/'forecast_window_sums.csv',sep=';')
        valid=windows['status'].eq('SUM_OF_PUBLISHED_MONTHS_ONLY')
        self.assertEqual(valid.sum(),19)
        self.assertTrue(windows.loc[~valid,'published_forecast_sum_u'].isna().all())
        selected=windows.loc[windows['start_month'].eq('2024-11')&windows['months'].eq(4)]
        self.assertEqual(selected['published_forecast_sum_u'].iloc[0],31508)
        self.assertNotIn('S_position_u',windows)

    def test_case_lead_times_not_invented(self):
        facts=pd.read_csv(OUT/'case_facts.csv',sep=';').set_index('parameter')
        self.assertEqual(facts.loc['S3_lead_months','value'],3)
        self.assertEqual(facts.loc['review_months','value'],1)
        self.assertTrue(pd.isna(facts.loc['SY_lead_months','value']))
        self.assertTrue(pd.isna(facts.loc['S0_lead_months','value']))

    def test_diagnostics_are_traceable_and_no_S_selected(self):
        p=self.p
        self.assertTrue(np.allclose(p['balance_plus_ordered_u'],p['available_balance_u']+p['ordered_u']))
        self.assertTrue(np.allclose(p['balance_plus_ordered_minus_reserve_u'],p['available_balance_u']+p['ordered_u']-p['reserved_u']))
        self.assertTrue(p['sku_id'].is_unique)
        self.assertTrue(np.allclose(p['available_value_at_price'],p['available_balance_u']*p['price']))
        self.assertEqual(p['flag_no_sales12_positive_balance'].sum(),1109)
        self.assertTrue(p['calculation_status'].str.startswith('NOT_DETERMINED').all())

    def test_missing_cost_requests_are_explicit(self):
        missing=pd.read_csv(OUT/'missing_inputs.csv',sep=';').set_index('parameter')
        for key in ['Cu','Co','cost_scope','demand_horizon_distribution','balance_definition','supplier_due_dates']:
            self.assertIn(key,missing.index)
            self.assertGreater(len(missing.loc[key,'request']),10)

    def test_original_sources_unchanged(self):
        manifest=json.loads((OUT/'input_manifest.json').read_text(encoding='utf-8'))
        for path,info in manifest.items():
            self.assertEqual(hashlib.sha256((ROOT/path).read_bytes()).hexdigest(),info['sha256'])

    def test_workbook_contains_blank_targets_for_every_SKU(self):
        path=OUT/'inventory_data_and_gaps.xlsx'
        with zipfile.ZipFile(path) as z:
            self.assertIsNone(z.testzip())
        wb=load_workbook(path,read_only=True,data_only=True)
        self.assertEqual(len(wb.sheetnames),12)
        rows=wb['SKU_дані'].iter_rows(values_only=True)
        header=next(rows)
        indices=[header.index(name) for name in UNKNOWN_RESULTS]
        count=0
        for row in rows:
            self.assertTrue(all(row[i] is None for i in indices))
            count+=1
        self.assertEqual(count,13398)
        wb.close()

    def test_no_retired_scenarios_in_active_outputs(self):
        old=['assumptions_used.json','scenario_summary.csv','cost_service_matrix.csv','backtest_windows.csv',
             'targets_SKU_S3.csv','targets_SKU_SY.csv','targets_SKU_S3_critical95.csv','targets_monthly.csv',
             'sensitivity.csv','inventory_tradeoff.png','inventory_service_COEFF.xlsx']
        self.assertFalse(any((OUT/name).exists() for name in old))
        self.assertFalse((ROOT/'inventory/assumptions.json').exists())

    def test_report_and_html_links_resolve_and_no_cost_calculator(self):
        report=ROOT/'conclusions/запаси-сервіс-coeff.md'
        text=report.read_text(encoding='utf-8')
        for target in re.findall(r'\]\(([^)]+)\)',text):
            if not target.startswith(('https:','http:','#')):
                self.assertTrue((report.parent/target).exists(),target)
        class Elements(HTMLParser):
            def __init__(self): super().__init__(); self.controls=[]
            def handle_starttag(self,tag,attrs):
                if tag in ('input','select'): self.controls.append(tag)
        parser=Elements()
        parser.feed((ROOT/'html/3b_service_coeff.html').read_text(encoding='utf-8'))
        self.assertEqual(parser.controls,[])


if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromModule(__import__(__name__,fromlist=['*']))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    report={'status':'PASS' if result.wasSuccessful() else 'FAIL','tests_run':result.testsRun,
            'failures':len(result.failures),'errors':len(result.errors),
            'economic_task_status':'INCOMPLETE_MISSING_INPUTS',
            'scope':'Observed arithmetic, source preservation, missing targets, no invented lead times or forecast extension; not economic task completion.'}
    (OUT/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    raise SystemExit(0 if result.wasSuccessful() else 1)
