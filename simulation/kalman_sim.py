"""Симуляція попиту (фільтр Кальмана) та запасів і закупівель групи B (трактори) на 3 роки.

Запуск з кореня репозиторію (інтерактивне вікно):
    python simulation/kalman_sim.py

Без вікна, збереження картинки:
    python simulation/kalman_sim.py --save out.png --mode damped --halflife 18 --scenario bal --lead 2

Що робить програма
------------------
1. ПОПИТ. Модель у просторі станів на логарифмах місячних продажів групи: рівень + тренд + 12 сезонних
   поправок (стандартна «структурна» модель). Перший рік (12 міс.) задає початковий стан, далі фільтр
   Кальмана проходить цикл «передбачити → виміряти» по кожному з наступних 24 місяців.
   Від кінця історії (жовтень 2024) модель прогнозує ще 36 місяців із діапазоном невизначеності
   (Монте-Карло: траєкторії з шумом стану й спостереження).
2. ТРЕНД на прогнозі: три режими — «згасання» (повзунок: піврозпад росту, міс.), «фільтр» (як оцінив
   фільтр, без згасання), «зростання» (повзунок: фіксований ріст % на рік).
3. ПЕРЕВІРКА НА МИНУЛОМУ: Кальман проти простого методу «індекс місяця × рівень з ростом»:
   прогноз FY2024 за двома роками і 3 місяці наперед на 10 послідовних зрізах. Без підглядання.
4. ЗАПАС І ЗАКУПІВЛІ групи (у вартості за прайсом, умовні одиниці): щомісяця замовляємо
   Q = max(0, S − позиція запасу), де S = k × очікуваний попит за період L+R. Коефіцієнт k береться
   з рівня окремих позицій (13 398 SKU, їхній μ і σ) для обраного сценарію сервісу (як на сторінці
   html/3c_scenarios.html), тому група не недооцінює страховий запас.

ВАЖЛИВІ ОБМЕЖЕННЯ (дублюються у вікні): продажі ≠ попит (нестача деталей занижує історію); лише 36 точок
для 14 станів; далекий прогноз визначається трендом; вартості й строки поставки — припущення;
груповий запас не дорівнює рівню сервісу по окремих деталях.

Дані: agrosem_results/data/monthly_group.csv, sku_master.csv, agrosem_results/metrics.json.
Залежності: numpy, pandas, matplotlib.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'agrosem_results' / 'data'

# ---------- налаштування, які можна змінювати ----------
N_PATHS = 500                 # кількість траєкторій Монте-Карло
HORIZON = 36                  # прогноз, місяців (листопад 2024 — жовтень 2027)
SEED = 7
R_REVIEW = 1                  # щомісячний перегляд замовлень (кейс)
ALPHA_PRESETS = {             # цільовий рівень сервісу за класом деталі — як у html/3c_scenarios.html
    'lean': ('Ощадливий', {'A': 90, 'B': 75, 'C': 60, 'D': 50}),
    'bal': ('Збалансований', {'A': 95, 'B': 85, 'C': 70, 'D': 55}),
    'svc': ('Сервісний', {'A': 98, 'B': 92, 'C': 80, 'D': 65}),
}
CLASS_CODES = {'A': [11, 12, 13, 14, 51, 52, 54], 'B': [15, 55], 'C': [20, 50], 'D': [21, 90, 91, 99, 0]}
MODES = {'damped': 'Згасання', 'filter': 'Фільтр', 'growth': 'Зростання'}
MONTH_UA = ['січ', 'лют', 'бер', 'кві', 'тра', 'чер', 'лип', 'сер', 'вер', 'жов', 'лис', 'гру']

# ======================================================================================
# Дані
# ======================================================================================


def load_inputs():
    g = pd.read_csv(DATA / 'monthly_group.csv', sep=';', encoding='utf-8-sig')
    assert len(g) == 36 and g['month'].iloc[0] == '2021-11' and g['month'].iloc[-1] == '2024-10', 'очікується 36 місяців 2021-11…2024-10'
    units = g['sold_observed_u'].to_numpy(float)
    value = g['sold_price_proxy'].to_numpy(float)
    assert int(units[24:].sum()) == 71162 and int(units[0]) == 3471, 'контрольні суми не збігаються з перевіреними'
    cols = ['sku_id', 'price', 'critical_code', 'available_balance_u', 'ordered_u', 'avg_month_observed_u', 'std_month_observed_u']
    sk = pd.read_csv(DATA / 'sku_master.csv', sep=';', encoding='utf-8-sig', usecols=cols)
    assert len(sk) == 13398, 'очікується 13 398 позицій'
    m = json.loads((ROOT / 'agrosem_results' / 'metrics.json').read_text(encoding='utf-8'))
    return units, value, sk, m


def class_of(cc: int) -> str:
    for k, codes in CLASS_CODES.items():
        if cc in codes:
            return k
    return 'D'


# ======================================================================================
# Фільтр Кальмана: рівень + тренд + 11 сезонних станів (на логарифмах)
# ======================================================================================
S_DIM = 11
DIM = 2 + S_DIM
H = np.zeros(DIM)
H[0] = 1.0
H[2] = 1.0


def build_F(phi: float = 1.0) -> np.ndarray:
    F = np.zeros((DIM, DIM))
    F[0, 0] = 1.0
    F[0, 1] = phi
    F[1, 1] = phi
    F[2, 2:] = -1.0                   # сума сезонних поправок за 12 місяців = 0
    for i in range(1, S_DIM):
        F[2 + i, 1 + i] = 1.0
    return F


def build_Q(p) -> np.ndarray:
    Q = np.zeros((DIM, DIM))
    Q[0, 0], Q[1, 1], Q[2, 2] = p['sm'] ** 2, p['sb'] ** 2, p['sg'] ** 2
    return Q


def init_state(logy: np.ndarray):
    """Початковий стан за перший рік: рівень = середнє, сезонні поправки = відхилення, тренд = 0 з великою невизначеністю."""
    y1 = logy[:12]
    lvl = y1.mean()
    dev = y1 - lvl
    x = np.zeros(DIM)
    x[0] = lvl
    x[2:] = dev[11:0:-1]              # γ(t), γ(t-1) … γ(t-10), де t = останній місяць першого року
    P = np.diag([0.10 ** 2, 0.02 ** 2] + [0.12 ** 2] * S_DIM)
    return x, P


def kalman(logy, t_end, p, x0, P0, record=False):
    """Цикл predict → measure для місяців 12 … t_end−1. Повертає стан після місяця t_end−1."""
    F, Q, r2 = build_F(1.0), build_Q(p), p['se'] ** 2
    x, P, ll = x0.copy(), P0.copy(), 0.0
    rec = {'t': [], 'pred': [], 'sd': [], 'level': [], 'slope': []}
    for t in range(12, t_end):
        x = F @ x
        P = F @ P @ F.T + Q                      # --- predict
        yhat = H @ x
        Sv = H @ P @ H + r2
        e = logy[t] - yhat                       # --- measure: порівняти з фактом
        ll += -0.5 * (np.log(2 * np.pi * Sv) + e * e / Sv)
        K = P @ H / Sv
        x = x + K * e
        P = P - np.outer(K, H @ P)
        P = (P + P.T) / 2
        if record:
            rec['t'].append(t); rec['pred'].append(yhat); rec['sd'].append(np.sqrt(Sv))
            rec['level'].append(x[0]); rec['slope'].append(x[1])
    return x, P, ll, rec


GRID = [dict(sm=sm, sb=sb, sg=sg, se=se) for sm in (0.005, 0.02, 0.06) for sb in (0.0005, 0.003, 0.01) for sg in (0.005, 0.02, 0.06)
        for se in (0.06, 0.12, 0.25)]
BOUNDS = dict(sm=(0.002, 0.10), sb=(0.0002, 0.02), sg=(0.002, 0.10), se=(0.03, 0.35))


def fit_noise(logy, t_end):
    """Підбір шумів за максимальною правдоподібністю: груба сітка + уточнення (Нелдер—Мід, якщо є scipy).
    Використовує лише дані до t_end, тому у перевірці на минулому майбутнє не підглядається."""
    x0, P0 = init_state(logy)
    best = max(GRID, key=lambda p: kalman(logy, t_end, p, x0, P0)[2])
    try:
        from scipy.optimize import minimize
    except ImportError:
        return best
    names = ['sm', 'sb', 'sg', 'se']

    def neg_ll(theta):
        p = {n: float(np.clip(np.exp(t), *BOUNDS[n])) for n, t in zip(names, theta)}
        return -kalman(logy, t_end, p, x0, P0)[2]

    r = minimize(neg_ll, np.log([best[n] for n in names]), method='Nelder-Mead', options={'xatol': 1e-2, 'fatol': 1e-3, 'maxiter': 200})
    return {n: float(np.clip(np.exp(t), *BOUNDS[n])) for n, t in zip(names, r.x)}


# ======================================================================================
# Прогноз
# ======================================================================================


def phi_from_halflife(h: float) -> float:
    return 0.5 ** (1.0 / max(h, 0.5))


def mode_slope(mode, x_slope, g):
    return np.log1p(g) / 12 if mode == 'growth' else x_slope


def det_forecast(x, mode, halflife, g, h):
    """Детермінований (медіанний) прогноз від стану x на h місяців, у штуках."""
    x = x.copy()
    if mode == 'growth':
        x[1] = np.log1p(g) / 12
    F = build_F(phi_from_halflife(halflife) if mode == 'damped' else 1.0)
    out = np.empty(h)
    for k in range(h):
        x = F @ x
        out[k] = np.exp(H @ x)
    return out


def simulate_paths(x, P, p, mode, halflife, g, h, n=N_PATHS, seed=SEED):
    """Траєкторії попиту (штуки): стан ~ N(x, P), далі кроки з шумом стану й спостереження."""
    rng = np.random.default_rng(seed)
    xs = rng.multivariate_normal(x, P, size=n, method='eigh')
    if mode == 'growth':
        xs[:, 1] = xs[:, 1] - x[1] + np.log1p(g) / 12      # середній тренд задано, невизначеність лишається
    F = build_F(phi_from_halflife(halflife) if mode == 'damped' else 1.0)
    out = np.empty((n, h))
    for k in range(h):
        xs = xs @ F.T
        xs[:, 0] += rng.normal(0, p['sm'], n)
        xs[:, 1] += rng.normal(0, p['sb'], n)
        xs[:, 2] += rng.normal(0, p['sg'], n)
        out[:, k] = np.exp(xs[:, 0] + xs[:, 2] + rng.normal(0, p['se'], n))
    return out


# ---------- простий метод: індекс «місяць / середнє року» × рівень з ростом ----------


def simple_forecast(v, t, h, mode, halflife, g, g_hist):
    yrs = t // 12
    per = [v[t - 12 * (k + 1):t - 12 * k] / v[t - 12 * (k + 1):t - 12 * k].mean() for k in range(yrs)]
    idx = np.mean(per, axis=0)                               # елемент j — місяць (t + j) mod 12
    g0 = g if mode == 'growth' else g_hist
    r0 = np.log1p(g0) / 12
    if mode == 'damped':
        phi = phi_from_halflife(halflife)
        r = r0 * phi ** np.arange(h)
    else:
        r = np.full(h, r0)
    expo = 5.5 * r[0] + np.cumsum(r)                          # для сталого росту = (6,5 + i) × r
    base = v[t - 12:t].mean()
    return np.array([base * np.exp(expo[i]) * idx[i % 12] for i in range(h)])


def wape(a, f):
    a, f = np.asarray(a, float), np.asarray(f, float)
    return float(np.abs(a - f).sum() / a.sum())


class Model:
    """Фільтр і перевірки на минулому обчислюються один раз; повзунки лише змінюють прогноз."""

    def __init__(self, units, value, sk, metrics):
        self.units, self.value = units, value
        self.logy = np.log(units)
        self.p = fit_noise(self.logy, 36)
        x0, P0 = init_state(self.logy)
        self.x, self.P, self.ll, self.rec = kalman(self.logy, 36, self.p, x0, P0, record=True)
        # зрізи для перевірки: параметри підбираються лише за даними до зрізу
        self.origins = {}
        for t in range(24, 34):
            p_t = fit_noise(self.logy, t)
            x0, P0 = init_state(self.logy)
            xt, Pt, _, _ = kalman(self.logy, t, p_t, x0, P0)
            self.origins[t] = xt
        self.metrics = metrics
        self.price_unit = value[24:].sum() / units[24:].sum()          # середня ціна за штуку, FY2024
        # --- рівень позицій для коефіцієнта страхового запасу k
        s = sk.copy()
        s['cls'] = s['critical_code'].astype(int).map(class_of)
        s['mu'] = s['avg_month_observed_u']
        s['sd'] = s['std_month_observed_u'].fillna(0.0)
        s['active'] = s['mu'] > 0
        self.sku = s[s['active']].reset_index(drop=True)
        d = s[~s['active']]
        self.dead_value = float(((d['available_balance_u'] + d['ordered_u']) * d['price']).sum())
        a = self.sku
        self.stock0 = float((a['available_balance_u'] * a['price']).sum())
        self.pipeline0 = float((a['ordered_u'] * a['price']).sum())
        self.nd = NormalDist()

    def k_factor(self, alphas, T):
        a = self.sku
        z = a['cls'].map(lambda c: self.nd.inv_cdf(min(0.999, max(0.01, alphas[c] / 100)))).to_numpy()
        w = a['price'].to_numpy()
        mu, sd = a['mu'].to_numpy(), a['sd'].to_numpy()
        S_i = mu * T + z * sd * np.sqrt(T)
        return float((w * S_i).sum() / (w * mu * T).sum())

    # ---------- перевірка на минулому ----------
    def backtest(self, mode, halflife, g):
        v = self.units
        res = {}
        # 12 місяців: FY2024 за даними FY2022–FY2023
        t = 24
        act = v[t:t + 12]
        fk = det_forecast(self.origins[t], mode, halflife, g, 12)
        gh = v[t - 12:t].sum() / v[t - 24:t - 12].sum() - 1
        fs = simple_forecast(v, t, 12, mode, halflife, g, gh)
        res['fy24'] = {'k': (wape(act, fk), float(fk.sum() / act.sum() - 1)), 's': (wape(act, fs), float(fs.sum() / act.sum() - 1))}
        # 3 місяці наперед, зрізи 24…33
        A, FK, FS = [], [], []
        for t in range(24, 34):
            A.append(v[t:t + 3])
            FK.append(det_forecast(self.origins[t], mode, halflife, g, 3))
            gh = v[t - 12:t].sum() / v[t - 24:t - 12].sum() - 1
            FS.append(simple_forecast(v, t, 3, mode, halflife, g, gh))
        A, FK, FS = map(np.concatenate, (A, FK, FS))
        res['roll3'] = {'k': wape(A, FK), 's': wape(A, FS), 'n': 10}
        return res

    # ---------- прогноз 3 роки ----------
    def forecast(self, mode, halflife, g, extra=4):
        h = HORIZON + extra
        paths = simulate_paths(self.x, self.P, self.p, mode, halflife, g, h)
        gh = self.units[24:].sum() / self.units[12:24].sum() - 1
        simple = simple_forecast(self.units, 36, h, mode, halflife, g, gh)
        return paths, simple

    # ---------- запас і закупівлі групи (вартість) ----------
    def stock_sim(self, paths, scenario, L, include_pipeline):
        alphas = ALPHA_PRESETS[scenario][1]
        n, h = paths.shape
        T = L + R_REVIEW
        k = self.k_factor(alphas, T)
        D = paths * self.price_unit                                  # попит у вартості
        med = np.median(D, axis=0)
        S_t = np.array([k * med[t:t + T].sum() for t in range(HORIZON)])
        stock = np.full(n, self.stock0)
        arr = np.zeros((n, HORIZON + L + 2))
        if include_pipeline and self.pipeline0 > 0:
            for j in range(max(L, 1)):
                arr[:, j] += self.pipeline0 / max(L, 1)
        onorder = arr.sum(axis=1)
        stock_end = np.zeros((n, HORIZON)); orders = np.zeros((n, HORIZON)); sales = np.zeros((n, HORIZON)); short = np.zeros((n, HORIZON))
        for t in range(HORIZON):
            got = arr[:, t]
            stock = stock + got
            onorder = onorder - got
            q = np.maximum(0.0, S_t[t] - (stock + onorder))          # замовлення на початку місяця
            arr[:, t + L] += q if L > 0 else 0
            if L == 0:
                stock = stock + q
            else:
                onorder = onorder + q
            orders[:, t] = q
            sold = np.minimum(stock, D[:, t])
            sales[:, t], short[:, t] = sold, D[:, t] - sold
            stock = stock - sold
            stock_end[:, t] = stock
        return {'k': k, 'S': S_t, 'stock': stock_end, 'orders': orders, 'sales': sales, 'short': short, 'D': D, 'T': T}


# ======================================================================================
# Підсумки у вигляді тексту
# ======================================================================================


def fy_sums(a, axis=-1):
    """Суми по 12 місяців (3 бюджетні роки) для масиву з останньою віссю = місяці."""
    return np.stack([a[..., i * 12:(i + 1) * 12].sum(axis=axis) for i in range(3)], axis=-1)


def summarize(model, mode, halflife, g, scenario, L, pipe):
    paths, simple = model.forecast(mode, halflife, g)
    ph = paths[:, :HORIZON]
    sim = model.stock_sim(paths, scenario, L, pipe)
    bt = model.backtest(mode, halflife, g)
    return {'paths': ph, 'simple': simple[:HORIZON], 'sim': sim, 'bt': bt}


def format_metrics(model, st, mode, halflife, g, scenario, L, pipe):
    ph, sim, bt = st['paths'], st['sim'], st['bt']
    yearly = fy_sums(ph)                                        # (n, 3)
    q = lambda a, p: np.percentile(a, p)
    lines = []
    if mode == 'damped':
        lines.append(f'Тренд: згасання, піврозпад росту {halflife:.0f} міс.')
    elif mode == 'growth':
        lines.append(f'Тренд: фіксований ріст {g * 100:.0f}% на рік')
    else:
        lines.append('Тренд: як оцінив фільтр (без згасання)')
    b = model.rec['slope'][-1]
    lines.append(f'Фільтр оцінив зараз ріст ≈ {(np.exp(12 * b) - 1) * 100:+.0f}% на рік')
    at_bound = [n for n, (lo, hi) in BOUNDS.items() if model.p[n] <= lo * 1.02 or model.p[n] >= hi * 0.98]
    if at_bound:
        lines.append('! Шуми фільтра на межі дозволеного')
        lines.append('  (' + ', '.join(at_bound) + '): 36 точок замало')
        lines.append('  для надійного підбору параметрів')
    lines.append('')
    lines.append('ПЕРЕВІРКА НА МИНУЛОМУ (WAPE, менше краще)')
    lines.append(f'  FY2024 за 2 роки:  Кальман {bt["fy24"]["k"][0] * 100:.0f}% | простий {bt["fy24"]["s"][0] * 100:.0f}%')
    lines.append(f'   помилка суми року: {bt["fy24"]["k"][1] * 100:+.0f}% | {bt["fy24"]["s"][1] * 100:+.0f}%')
    lines.append(f'  3 міс. наперед ({bt["roll3"]["n"]} зрізів): {bt["roll3"]["k"] * 100:.0f}% | {bt["roll3"]["s"] * 100:.0f}%')
    better = 'Кальман' if bt['roll3']['k'] < bt['roll3']['s'] else 'простий метод'
    lines.append(f'  Краще на 3 міс.: {better}')
    lines.append('')
    lines.append('ПРОГНОЗ ПОПИТУ, шт (медіана; 80% діапазон)')
    base = model.units[24:].sum()
    lines.append(f'  FY2024 (факт): {base:,.0f}'.replace(',', ' '))
    for i in range(3):
        med, lo, hi = q(yearly[:, i], 50), q(yearly[:, i], 10), q(yearly[:, i], 90)
        lines.append(f'  FY{2025 + i}: {med:,.0f}  [{lo:,.0f}–{hi:,.0f}]'.replace(',', ' '))
    cu = model.metrics.get('current_u', 0)
    nov_ext = cu / 12 * 30
    nov_fc = np.median(ph[:, 0])
    lines.append(f'  Лис. 2024: 12 днів = {cu:,.0f} шт ≈ {nov_ext:,.0f}/міс; прогноз {nov_fc:,.0f}'.replace(',', ' '))
    lines.append('')
    lines.append(f'ЗАПАС І ЗАКУПІВЛІ, тис. ({ALPHA_PRESETS[scenario][0]}, L={L})')
    lines.append(f'  Страховий коеф. k = {sim["k"]:.2f} (S = k × попит за L+R)')
    purch, sold = fy_sums(sim['orders']), fy_sums(sim['sales'])
    avg = np.stack([sim['stock'][:, i * 12:(i + 1) * 12].mean(axis=1) for i in range(3)], axis=-1)
    for i in range(3):
        turn = np.median(sold[:, i] / np.maximum(avg[:, i], 1))
        lines.append(f'  FY{2025 + i}: куп. {np.median(purch[:, i]) / 1e3:,.0f}; запас {np.median(avg[:, i]) / 1e3:,.0f}; оберт {turn:.1f}'.replace(',', ' '))
    low = np.percentile(sim['stock'].min(axis=1), 10) / 1e3
    lines.append(f'  Мін. залишок у 10% найгірших траєкторій: {low:,.0f} тис.'.replace(',', ' '))
    lines.append('  (дефіцит по групі не показовий: сервіс')
    lines.append('   по деталях дивіться на сторінці 3c)')
    lines.append(f'  Ціль Агросем 3,5 оберту; John Deere 2,5')
    lines.append(f'  Залишок без попиту поза симуляцією: {model.dead_value / 1e3:,.0f} тис.'.replace(',', ' '))
    return '\n'.join(lines)


# ======================================================================================
# Малювання
# ======================================================================================


def draw(model, st, ax_d, ax_s, mode, halflife, g, scenario, L):
    import matplotlib.dates as mdates
    idx_hist = pd.period_range('2021-11', periods=36, freq='M').to_timestamp()
    idx_fc = pd.period_range('2024-11', periods=HORIZON, freq='M').to_timestamp()
    ph, simple, sim = st['paths'], st['simple'], st['sim']
    for ax in (ax_d, ax_s):
        ax.clear()
    # --- попит
    q = lambda p: np.percentile(ph, p, axis=0)
    ax_d.fill_between(idx_fc, q(2.5), q(97.5), color='#1d4ed8', alpha=.12, label='95% діапазон')
    ax_d.fill_between(idx_fc, q(10), q(90), color='#1d4ed8', alpha=.25, label='80% діапазон')
    ax_d.plot(idx_fc, q(50), color='#1d4ed8', lw=2, label='Прогноз Кальмана (медіана)')
    ax_d.plot(idx_fc, simple, color='#d97706', lw=1.8, ls='--', label='Простий метод (індекс × ріст)')
    ax_d.plot(idx_hist, model.units, color='#111827', lw=1.6, label='Факт (продажі групи B)')
    t_idx = [idx_hist[t] for t in model.rec['t']]
    ax_d.plot(t_idx, np.exp(model.rec['pred']), color='#6b7280', lw=1, ls=':', label='Очікування фільтра (крок «передбачити»)')
    ax_d.axvline(idx_hist[12], color='#9ca3af', lw=.8, ls=':', label='Початок вимірювань (після 1-го року)')
    cu = model.metrics.get('current_u', 0)
    ax_d.scatter([idx_fc[0]], [cu / 12 * 30], marker='X', s=70, color='#b91c1c', zorder=5, label='Лис. 2024: 12 днів → 30 (≈)')
    ax_d.axvline(idx_fc[0], color='#374151', lw=1)
    ax_d.set_ylabel('штук за місяць')
    ax_d.set_title('Попит на запчастини до тракторів (група B): факт і прогноз на 3 роки', fontsize=11)
    ax_d.legend(fontsize=7.5, ncol=2, loc='upper left')
    ax_d.grid(alpha=.25)
    ax_d.xaxis.set_major_locator(mdates.MonthLocator(bymonth=(1, 7)))
    ax_d.xaxis.set_major_formatter(mdates.DateFormatter('%m.%y'))
    # --- запас і закупівлі
    k = 1e3
    sq = lambda a, p: np.percentile(a, p, axis=0) / k
    ax_s.fill_between(idx_fc, sq(sim['stock'], 10), sq(sim['stock'], 90), color='#0f766e', alpha=.22, label='Залишок: 80% діапазон')
    ax_s.plot(idx_fc, sq(sim['stock'], 50), color='#0f766e', lw=2, label='Залишок на складі (кінець місяця), медіана')
    ax_s.plot(idx_fc, sim['S'] / k, color='#7c3aed', lw=1.6, ls='--', label='Цільовий запас S')
    ax_s.bar(idx_fc, sq(sim['orders'], 50), width=22, color='#d97706', alpha=.55, label='Закупівлі за місяць (медіана)')
    sales_fy = fy_sums(sim['sales'])
    for i in range(3):
        sl = slice(i * 12, i * 12 + 12)
        cap35 = np.median(sales_fy[:, i]) / 3.5 / k
        cap25 = np.median(sales_fy[:, i]) / 2.5 / k
        ax_s.hlines(cap35, idx_fc[sl][0], idx_fc[sl][-1], colors='#b91c1c', lw=1.6, label='Сер. запас за 3,5 оберту' if i == 0 else None)
        ax_s.hlines(cap25, idx_fc[sl][0], idx_fc[sl][-1], colors='#6b7280', lw=1.2, ls=':', label='Сер. запас за 2,5 оберту' if i == 0 else None)
    ax_s.set_ylabel('тис., умовні одиниці прайсу')
    ax_s.set_title(f'Запас і закупівлі групи B ({ALPHA_PRESETS[scenario][0]}, строк поставки L={L} міс.)', fontsize=11)
    ax_s.legend(fontsize=7.5, ncol=2, loc='upper right')
    ax_s.grid(alpha=.25)
    ax_s.xaxis.set_major_locator(mdates.MonthLocator(bymonth=(1, 7)))
    ax_s.xaxis.set_major_formatter(mdates.DateFormatter('%m.%y'))


NOTE = ('Обмеження: продажі ≠ попит (нестача деталей занижує історію); 36 точок на 14 станів; далекий прогноз визначає тренд; '
        'вартості й строки поставки — припущення; груповий запас не дорівнює сервісу по окремих деталях (див. html/3c_scenarios.html).')


def run_static(model, args):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    st = summarize(model, args.mode, args.halflife, args.growth, args.scenario, args.lead, not args.no_pipeline)
    fig = plt.figure(figsize=(15, 9.2))
    gs = fig.add_gridspec(2, 2, width_ratios=[3, 1.25], height_ratios=[1, 1], hspace=.32, wspace=.08, left=.05, right=.99, top=.95, bottom=.08)
    ax_d, ax_s = fig.add_subplot(gs[0, 0]), fig.add_subplot(gs[1, 0])
    ax_t = fig.add_subplot(gs[:, 1]); ax_t.axis('off')
    draw(model, st, ax_d, ax_s, args.mode, args.halflife, args.growth, args.scenario, args.lead)
    ax_t.text(0, 1, format_metrics(model, st, args.mode, args.halflife, args.growth, args.scenario, args.lead, not args.no_pipeline),
              va='top', ha='left', family='monospace', fontsize=7.6)
    fig.text(.05, .015, NOTE, fontsize=7, color='#6b7280')
    fig.savefig(args.save, dpi=130)
    print('збережено', args.save)


def run_interactive(model, args, show=True):
    import matplotlib.pyplot as plt
    from matplotlib.widgets import CheckButtons, RadioButtons, Slider
    state = {'mode': args.mode, 'halflife': args.halflife, 'growth': args.growth, 'scenario': args.scenario, 'lead': args.lead, 'pipe': not args.no_pipeline}
    fig = plt.figure(figsize=(15, 9.2))
    fig.canvas.manager.set_window_title('Агросем: симуляція попиту і запасів (Кальман)')
    gs = fig.add_gridspec(2, 2, width_ratios=[3, 1.25], height_ratios=[1, 1], hspace=.32, wspace=.08, left=.05, right=.99, top=.95, bottom=.08)
    ax_d, ax_s = fig.add_subplot(gs[0, 0]), fig.add_subplot(gs[1, 0])
    ax_t = fig.add_axes([.74, .02, .25, .50]); ax_t.axis('off')
    # --- панель керування (праворуч угорі)
    fig.text(.745, .93, 'ТРЕНД НА 3 РОКИ', fontsize=9, weight='bold')
    ax_mode = fig.add_axes([.74, .83, .12, .09]); rb_mode = RadioButtons(ax_mode, list(MODES.values()), active=list(MODES).index(state['mode']))
    ax_scn = fig.add_axes([.87, .83, .12, .09]); rb_scn = RadioButtons(ax_scn, [v[0] for v in ALPHA_PRESETS.values()], active=list(ALPHA_PRESETS).index(state['scenario']))
    fig.text(.87, .93, 'СЕРВІС', fontsize=9, weight='bold')
    ax_h = fig.add_axes([.78, .78, .20, .022]); sl_h = Slider(ax_h, 'Піврозпад,\nміс.', 3, 60, valinit=state['halflife'], valstep=1)
    ax_g = fig.add_axes([.78, .73, .20, .022]); sl_g = Slider(ax_g, 'Ріст,\n% на рік', -10, 40, valinit=state['growth'] * 100, valstep=1)
    ax_l = fig.add_axes([.78, .68, .20, .022]); sl_l = Slider(ax_l, 'Строк\nпоставки L', 0, 4, valinit=state['lead'], valstep=1)
    ax_c = fig.add_axes([.74, .60, .24, .05]); cb = CheckButtons(ax_c, ['Враховувати замовлене (в дорозі)'], [state['pipe']])
    ax_t.set_position([.74, .02, .25, .55])
    keys = list(MODES)
    scn_keys = list(ALPHA_PRESETS)

    def apply_active():
        for slider, on in ((sl_h, state['mode'] == 'damped'), (sl_g, state['mode'] == 'growth')):
            slider.set_active(on)
            slider.ax.set_facecolor('#ffffff' if on else '#e5e7eb')
            slider.poly.set_alpha(1 if on else .25)

    def refresh(_=None):
        st = summarize(model, state['mode'], state['halflife'], state['growth'], state['scenario'], state['lead'], state['pipe'])
        draw(model, st, ax_d, ax_s, state['mode'], state['halflife'], state['growth'], state['scenario'], state['lead'])
        ax_t.clear(); ax_t.axis('off')
        ax_t.text(0, 1, format_metrics(model, st, state['mode'], state['halflife'], state['growth'], state['scenario'], state['lead'], state['pipe']),
                  va='top', ha='left', family='monospace', fontsize=7.4)
        fig.canvas.draw_idle()

    rb_mode.on_clicked(lambda lab: (state.update(mode=keys[list(MODES.values()).index(lab)]), apply_active(), refresh()))
    rb_scn.on_clicked(lambda lab: (state.update(scenario=scn_keys[[v[0] for v in ALPHA_PRESETS.values()].index(lab)]), refresh()))
    sl_h.on_changed(lambda v: (state.update(halflife=v), refresh()))
    sl_g.on_changed(lambda v: (state.update(growth=v / 100), refresh()))
    sl_l.on_changed(lambda v: (state.update(lead=int(v)), refresh()))
    cb.on_clicked(lambda _l: (state.update(pipe=cb.get_status()[0]), refresh()))
    fig.text(.05, .015, NOTE, fontsize=7, color='#6b7280')
    apply_active()
    refresh()
    if show:
        plt.show()
    return fig, dict(rb_mode=rb_mode, rb_scn=rb_scn, sl_h=sl_h, sl_g=sl_g, sl_l=sl_l, cb=cb, ax_t=ax_t, state=state)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--mode', choices=list(MODES), default='damped', help='тренд: damped (згасання) | filter | growth')
    ap.add_argument('--halflife', type=float, default=18, help='піврозпад росту, міс. (режим згасання)')
    ap.add_argument('--growth', type=float, default=0.10, help='ріст на рік, частка (режим зростання)')
    ap.add_argument('--scenario', choices=list(ALPHA_PRESETS), default='bal')
    ap.add_argument('--lead', type=int, default=2, help='строк поставки L, міс. (припущення)')
    ap.add_argument('--no-pipeline', action='store_true', help='не враховувати вже замовлене')
    ap.add_argument('--save', type=Path, default=None, help='зберегти PNG і вийти (без вікна)')
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
    units, value, sk, metrics = load_inputs()
    model = Model(units, value, sk, metrics)
    print('шуми фільтра (підібрані):', {k: v for k, v in model.p.items()})
    if args.save:
        run_static(model, args)
    else:
        run_interactive(model, args)


if __name__ == '__main__':
    main()
