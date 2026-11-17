"""Завдання 1: сезонність і прогноз попиту для групи B (трактори). Відтворюваний розрахунок.

Запуск з кореня репозиторію (кеш розпарсеного Excel — поза репозиторієм, як у build_html_data.py):
    python html/analysis_task1.py --source inputs/12_11_2024_140029.xlsx --cache <шлях до pkl>

Метод:
  * сезонні індекси — класична мультиплікативна декомпозиція (центроване ковзне середнє 2x12),
    нормовані до середнього 1; порівнюємо зі спрощеними індексами «місяць / середнє року»;
  * прогноз = рівень (середнє за останні 12 міс.) x (1+g)^((6,5+i)/12) x індекс місяця;
    g — річний темп росту (історичний або сценарний);
  * перевірка — послідовні зрізи без підглядання в майбутнє: 3 місяці наперед (origins 25..33)
    і 12 місяців наперед від origin 24 (навчання FY2022-FY2023, перевірка FY2024);
  * метрики: WAPE по місяцях і похибка річної суми. MAPE не використовуємо (нулі в рядах).
Результат: html/data/task1.js
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_html_data as bhd  # noqa: E402  (load() з кешем)

ORDER = ['лис', 'гру', 'січ', 'лют', 'бер', 'кві', 'тра', 'чер', 'лип', 'сер', 'вер', 'жов']


def cma_index(v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Сезонний індекс за класичною декомпозицією; повертає (індекс[12], кількість спостережень на місяць[12]).
    Місяць k = позиція у ряді mod 12 (ряд починається з листопада)."""
    n = len(v)
    ma = np.full(n, np.nan)
    for i in range(6, n - 6):
        ma[i] = (0.5 * v[i - 6] + v[i - 5:i + 6].sum() + 0.5 * v[i + 6]) / 12
    ratio = v / ma
    idx = np.array([np.nanmean(ratio[k::12]) if np.any(~np.isnan(ratio[k::12])) else np.nan for k in range(12)])
    idx = np.where(np.isnan(idx), np.nanmean(idx), idx)
    cnt = np.array([int(np.sum(~np.isnan(ratio[k::12]))) for k in range(12)])
    return idx / idx.mean(), cnt


def fy_mean_index(v: np.ndarray) -> np.ndarray:
    n_years = len(v) // 12
    per = [v[k * 12:(k + 1) * 12] / v[k * 12:(k + 1) * 12].mean() for k in range(n_years)]
    return np.mean(per, axis=0)


def wape(a, f):
    a, f = np.asarray(a, float), np.asarray(f, float)
    return float(np.abs(a - f).sum() / a.sum())


def level_path(v: np.ndarray, t: int, g: float, h: int) -> np.ndarray:
    """Рівень (середньомісячний, без сезонності) для місяців t..t+h-1 за даними v[:t]."""
    base = v[t - 12:t].mean()                      # центр вікна ≈ 6,5 міс. тому
    return np.array([base * (1 + g) ** ((6.5 + i) / 12) for i in range(h)])


def forecast(v, t, h, g, kind):
    """kind: 'snaive' | 'snaive_g' | 'fy_idx' | 'cma_idx'. Використовує лише v[:t]."""
    if kind == 'snaive':
        return np.array([v[t - 12 + i] for i in range(h)])
    if kind == 'snaive_g':
        return np.array([v[t - 12 + i] * (1 + g) for i in range(h)])
    if kind == 'fy_idx':
        pos = [(t + i) % 12 for i in range(h)]
        yrs = t // 12                                # повні 12-місячні блоки, що закінчуються на t
        per = [v[t - 12 * (k + 1):t - 12 * k] / v[t - 12 * (k + 1):t - 12 * k].mean() for k in range(yrs)]
        idx = np.mean(per, axis=0)                  # індекс у порядку місяців, що починається з (t mod 12)
        return level_path(v, t, g, h) * np.array([idx[(p - t) % 12] for p in pos])
    if kind == 'cma_idx':
        idx, _ = cma_index(v[:t])
        return level_path(v, t, g, h) * np.array([idx[(t + i) % 12] for i in range(h)])
    raise ValueError(kind)


MODELS = [('snaive', 'Базова: той самий місяць торік'),
          ('snaive_g', 'Торік × (1 + історичний ріст)'),
          ('fy_idx', 'Індекс «місяць / середнє року» × рівень із трендом'),
          ('cma_idx', 'Індекс декомпозиції (без тренду) × рівень із трендом')]


def yoy(v, t):
    return float(v[t - 12:t].sum() / v[t - 24:t - 12].sum() - 1)


def backtest(v: np.ndarray) -> dict:
    out = {}
    # 3 міс. наперед, origins 25..33 (потрібно ≥ 25 міс. для декомпозиції)
    folds = list(range(25, 34))
    A = np.concatenate([v[t:t + 3] for t in folds])
    out['rolling3'] = []
    for kind, name in MODELS:
        F = np.concatenate([forecast(v, t, 3, yoy(v, t), kind) for t in folds])
        out['rolling3'].append({'model': kind, 'name': name, 'wape': round(wape(A, F), 4)})
    out['rolling3_folds'] = len(folds)
    # 12 міс. наперед від origin 24: FY2024 за даними FY2022-FY2023
    t = 24
    out['fy24'] = []
    for kind, name in MODELS:
        f = forecast(v, t, 12, yoy(v, t), kind)
        out['fy24'].append({'model': kind, 'name': name, 'wape': round(wape(v[t:t + 12], f), 4),
                            'total_err': round(float(f.sum() / v[t:t + 12].sum() - 1), 4)})
    # чутливість до росту: який g «вгадав би» FY2024 (cma_idx) і що буде при g = 0, 10%, 20%
    out['growth_sens'] = []
    for g in (0.0, 0.10, 0.20, yoy(v, t)):
        f = forecast(v, t, 12, g, 'cma_idx')
        out['growth_sens'].append({'g': round(g, 4), 'wape': round(wape(v[t:t + 12], f), 4),
                                   'total_err': round(float(f.sum() / v[t:t + 12].sum() - 1), 4)})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', type=Path, default=bhd.ROOT / 'inputs' / '12_11_2024_140029.xlsx')
    ap.add_argument('--cache', type=Path, default=None)
    args = ap.parse_args()
    d = bhd.load(args.source, args.cache)
    m, s = d['m'], d['s']
    cols = sorted(s.columns)
    assert cols[0] == '2021-11' and cols[-1] == '2024-10' and len(cols) == 36

    def ser(mask):
        return np.array([float(s.loc[mask, c].sum()) for c in cols])

    allm = np.ones(len(m), bool)
    groups = {'all': ser(allm), 'cohort': ser(m['complete_36'].to_numpy())}
    for name in sorted(m['series'].unique()):
        groups['s:' + name] = ser(m['series'].eq(name).to_numpy())

    res = {'months': ORDER, 'method_note': 'Місяці в порядку бюджетного року: листопад → жовтень.', 'groups': {}}
    for key in ('all', 'cohort', 's:Трактори 8-ї серії', 's:Трактори 6-ї серії'):
        v = groups[key]
        idx, cnt = cma_index(v)
        fy = [float(v[k * 12:(k + 1) * 12].sum()) for k in range(3)]
        res['groups'][key] = {
            'cma_index': [round(float(x), 3) for x in idx], 'cma_obs_per_month': [int(x) for x in cnt],
            'fy_index': [round(float(x), 3) for x in fy_mean_index(v)],
            'fy_totals': {'FY2022': fy[0], 'FY2023': fy[1], 'FY2024': fy[2]},
            'growth': {'FY2023': round(fy[1] / fy[0] - 1, 4), 'FY2024': round(fy[2] / fy[1] - 1, 4)},
            'backtest': backtest(v)}

    # --- прогноз FY2025 для групи (усі позиції) за сценаріями росту ---
    v = groups['all']
    idx = np.array(res['groups']['all']['cma_index'])
    gA = res['groups']['all']['growth']
    gC = res['groups']['cohort']['growth']
    damp_all = gA['FY2024'] * gA['FY2024'] / gA['FY2023']        # згасання темпу, як між FY2023 і FY2024
    damp_coh = gC['FY2024'] * gC['FY2024'] / gC['FY2023']
    scen = [('Стрес: без росту', 0.0), ('Помірний: +10%', 0.10),
            ('Базовий: згасання росту сталої когорти', damp_coh),
            ('Високий: темп FY2024 усіх позицій', gA['FY2024'])]
    res['forecast_fy2025'] = []
    for name, g in scen:
        f = forecast(v, 36, 12, g, 'cma_idx')
        res['forecast_fy2025'].append({'name': name, 'g': round(g, 4), 'total': round(float(f.sum())),
                                       'monthly': [round(float(x)) for x in f]})
    res['growth_notes'] = {'damped_all': round(damp_all, 4), 'damped_cohort': round(damp_coh, 4)}
    # похибка місячного прогнозу для діапазону: середній WAPE 3-міс. перевірки найкращої моделі
    rb = res['groups']['all']['backtest']['rolling3']
    res['band_wape'] = min(x['wape'] for x in rb)
    (HERE / 'data' / 'task1.js').write_text(
        'window.AGRO = window.AGRO || {};\nwindow.AGRO.task1 = ' + json.dumps(res, ensure_ascii=False, separators=(',', ':')) + ';\n',
        encoding='utf-8')

    # --- друк для людини ---
    print('CMA-індекс (лис..жов), усі:', res['groups']['all']['cma_index'])
    print('Спостережень на місяць:', res['groups']['all']['cma_obs_per_month'])
    for key in res['groups']:
        b = res['groups'][key]['backtest']
        print(key, 'ріст', res['groups'][key]['growth'])
        print('   3 міс. наперед:', [(x['model'], x['wape']) for x in b['rolling3']])
        print('   FY2024 (12 міс.):', [(x['model'], x['wape'], x['total_err']) for x in b['fy24']])
    print('Чутливість до росту (all):', res['groups']['all']['backtest']['growth_sens'])
    for x in res['forecast_fy2025']:
        print(x['name'], x['g'], x['total'], x['monthly'])
    print('growth_notes', res['growth_notes'], 'band', res['band_wape'])


if __name__ == '__main__':
    main()
