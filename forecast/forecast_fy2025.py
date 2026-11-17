"""Прогноз попиту на запчастини групи B (трактори) на FY2025 (листопад 2024 - жовтень 2025).

Вхід: agrosem_results/data/monthly_group.csv, monthly_long.csv.gz, sku_master.csv
Вихід: forecast/out/*.csv, *.json, *.png

Запуск з кореня репозиторію:  python forecast/forecast_fy2025.py
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "agrosem_results" / "data"
OUT = Path(__file__).resolve().parent / "out"
OUT.mkdir(parents=True, exist_ok=True)

MONTH_UA = {11: "листопад", 12: "грудень", 1: "січень", 2: "лютий", 3: "березень", 4: "квітень",
            5: "травень", 6: "червень", 7: "липень", 8: "серпень", 9: "вересень", 10: "жовтень"}

# ---------------------------------------------------------------- дані
g = pd.read_csv(DATA / "monthly_group.csv", sep=";", encoding="utf-8-sig")
g["period"] = pd.PeriodIndex(g["month"], freq="M")
y = pd.Series(g["sold_observed_u"].astype(float).values, index=g["period"], name="units")
assert len(y) == 36 and str(y.index[0]) == "2021-11" and str(y.index[-1]) == "2024-10"


# ---------------------------------------------------------------- допоміжне
def seasonal_index(hist: pd.Series) -> pd.Series:
    """Мультиплікативні індекси: відношення до центрованого 12-місячного ковзного середнього.
    Середнє 12 індексів нормовано до 1. Повертає Series з індексом 1..12 (номер місяця)."""
    cma = hist.rolling(12, center=True).mean().rolling(2).mean().shift(-1)
    ratio = (hist / cma).dropna()
    si = ratio.groupby(ratio.index.month).mean()
    si = si.reindex(range(1, 13))
    return si / si.mean()


def yoy_growth(hist: pd.Series) -> float:
    """Ріст останніх 12 місяців до попередніх 12."""
    return hist.iloc[-12:].sum() / hist.iloc[-24:-12].sum()


# Кожна модель: f(hist, horizon_periods) -> np.array прогнозу
def m_snaive(hist, fut):
    return np.array([hist[p - 12] for p in fut])


def m_snaive_growth(hist, fut):
    gr = yoy_growth(hist)
    return np.array([hist[p - 12] for p in fut]) * gr


def m_si_level(hist, fut):
    """Сезонний індекс x рівень останніх 12 місяців без тренду."""
    si = seasonal_index(hist)
    lvl = hist.iloc[-12:].mean()
    return np.array([lvl * si[p.month] for p in fut])


def m_si_trend(hist, fut):
    """Сезонний індекс x рівень x місячний темп росту (з річного росту)."""
    si = seasonal_index(hist)
    lvl = hist.iloc[-12:].mean()          # рівень у середині останнього року (~6.5 міс тому)
    gm = yoy_growth(hist) ** (1 / 12)
    last = hist.index[-1]
    return np.array([lvl * gm ** ((p - last).n + 5.5) * si[p.month] for p in fut])


def m_si_trend_damped(hist, fut, damp=0.5):
    """Як попередня, але річний ріст беремо наполовину (обережний тренд)."""
    si = seasonal_index(hist)
    lvl = hist.iloc[-12:].mean()
    gm = (1 + (yoy_growth(hist) - 1) * damp) ** (1 / 12)
    last = hist.index[-1]
    return np.array([lvl * gm ** ((p - last).n + 5.5) * si[p.month] for p in fut])


def m_holt_winters(hist, fut):
    from statsmodels.tsa.holtwinters import ExponentialSmoothing
    model = ExponentialSmoothing(hist.values, trend="add", damped_trend=True,
                                 seasonal="mul", seasonal_periods=12,
                                 initialization_method="estimated").fit(optimized=True)
    h = (fut[-1] - hist.index[-1]).n
    fc = model.forecast(h)
    return np.array([fc[(p - hist.index[-1]).n - 1] for p in fut])


MODELS = {
    "M1 Сезонний наївний (той самий місяць торік)": m_snaive,
    "M2 Той самий місяць торік x річний ріст": m_snaive_growth,
    "M3 Сезонний індекс x рівень року (без росту)": m_si_level,
    "M4 Сезонний індекс x рівень x ріст": m_si_trend,
    "M5 Сезонний індекс x рівень x половина росту": m_si_trend_damped,
    "M6 Holt-Winters (мульт. сезонність, загасаючий тренд)": m_holt_winters,
}


def metrics(actual, pred):
    actual, pred = np.asarray(actual, float), np.asarray(pred, float)
    err = pred - actual
    return {
        "MAE": float(np.mean(np.abs(err))),
        "WAPE_%": float(np.sum(np.abs(err)) / np.sum(actual) * 100),
        "Bias_%": float(np.sum(err) / np.sum(actual) * 100),
        "Річна_похибка_%": float((pred.sum() - actual.sum()) / actual.sum() * 100),
    }


# ---------------------------------------------------------------- бектест A: весь FY2024 наперед
train_a, test_a = y.iloc[:24], y.iloc[24:]
rows_a, preds_a = [], {"month": [str(p) for p in test_a.index], "факт": test_a.values}
for name, f in MODELS.items():
    p = f(train_a, list(test_a.index))
    preds_a[name] = np.round(p, 0)
    rows_a.append({"модель": name, **metrics(test_a.values, p)})
bt_a = pd.DataFrame(rows_a)
pd.DataFrame(preds_a).to_csv(OUT / "backtest_A_FY2024_forecasts.csv", sep=";", index=False, encoding="utf-8-sig", decimal=",")

# ---------------------------------------------------------------- бектест B: ковзні зрізи в FY2024, горизонти 1 і 3
rows_b, resid_rows = [], []
for h in (1, 3):
    for name, f in MODELS.items():
        acts, prs = [], []
        for origin in range(24, 36 - h + 1):     # останній відомий місяць = origin-1
            hist = y.iloc[:origin]
            target = y.index[origin + h - 1]
            p = f(hist, [target])[0]
            acts.append(y[target]); prs.append(p)
            resid_rows.append({"модель": name, "горизонт": h, "зріз": str(hist.index[-1]),
                               "місяць": str(target), "факт": y[target], "прогноз": p,
                               "лог_відношення": np.log(y[target] / p)})
        rows_b.append({"модель": name, "горизонт_міс": h, "к-сть_перевірок": len(acts),
                       **{k: v for k, v in metrics(acts, prs).items() if k != "Річна_похибка_%"}})
bt_b = pd.DataFrame(rows_b)
resid = pd.DataFrame(resid_rows)

# ---------------------------------------------------------------- вибір і фінальний прогноз FY2025
fut = list(pd.period_range("2024-11", "2025-10", freq="M"))
fc_all = pd.DataFrame({"month": [str(p) for p in fut]})
for name, f in MODELS.items():
    fc_all[name] = f(y, fut)

# Базовий прогноз: середнє M5 і M6. Обидві моделі добре пройшли бектест і обережно
# закладають ріст (M5 бере половину річного росту, M6 має загасаючий тренд).
# Оптимістичний = M4 (повний ріст), песимістичний = M3 (без росту).
BASE = ["M5 Сезонний індекс x рівень x половина росту",
        "M6 Holt-Winters (мульт. сезонність, загасаючий тренд)"]
fc = pd.DataFrame({
    "month": fc_all["month"],
    "місяць": [MONTH_UA[p.month] for p in fut],
    "сезонний_індекс": [seasonal_index(y)[p.month] for p in fut],
    "песимістичний_без_росту": fc_all["M3 Сезонний індекс x рівень року (без росту)"],
    "базовий": fc_all[BASE].mean(axis=1),
    "оптимістичний_повний_ріст": fc_all["M4 Сезонний індекс x рівень x ріст"],
})

# Інтервали: емпіричні лог-похибки однокрокового і трикрокового прогнозу базових моделей,
# плюс бектест A. Через малу вибірку (до 24 точок) беремо sigma лог-похибки і нормальне
# наближення: 80% -> +-1.2816 sigma, 95% -> +-1.96 sigma.
la = []
for name in BASE:
    la += list(np.log(test_a.values / MODELS[name](train_a, list(test_a.index))))
la += list(resid[(resid["модель"].isin(BASE)) & (resid["горизонт"] == 3)]["лог_відношення"])
sigma = float(np.std(la, ddof=1))
for lvl, z in (("80", 1.2816), ("95", 1.96)):
    fc[f"нижня_{lvl}%"] = fc["базовий"] * np.exp(-z * sigma)
    fc[f"верхня_{lvl}%"] = fc["базовий"] * np.exp(z * sigma)
for c in fc.columns[3:]:
    fc[c] = fc[c].round(0).astype(int)
fc["сезонний_індекс"] = fc["сезонний_індекс"].round(3)

# ---------------------------------------------------------------- прогноз по серіях (згори вниз)
mlong = pd.read_csv(DATA / "monthly_long.csv.gz")
sku = pd.read_csv(DATA / "sku_master.csv", sep=";", encoding="utf-8-sig")
mlong = mlong.merge(sku[["sku_id", "series"]], on="sku_id", how="left")
ser_m = mlong.pivot_table(index="month", columns="series", values="sold_units", aggfunc="sum").fillna(0)
ser_m.index = pd.PeriodIndex(ser_m.index, freq="M")
assert np.allclose(ser_m.sum(axis=1).values, y.values)
share12 = ser_m.iloc[-12:].sum() / ser_m.iloc[-12:].sum().sum()
# сезонність серії 8 стабільна, решта серій шумні -> місячний розподіл беремо груповий,
# частку серії в році - за останні 12 місяців
ser_fc = pd.DataFrame({"month": fc["month"]})
for s_ in share12.index:
    ser_fc[s_] = (fc["базовий"] * share12[s_]).round(1)
ser_annual = pd.DataFrame({
    "серія": share12.index,
    "FY2022": ser_m.iloc[0:12].sum().values, "FY2023": ser_m.iloc[12:24].sum().values,
    "FY2024": ser_m.iloc[24:36].sum().values,
    "частка_FY2024_%": (share12.values * 100).round(1),
    "прогноз_FY2025_базовий": (fc["базовий"].sum() * share12.values).round(0),
})

# ---------------------------------------------------------------- прогноз по SKU (згори вниз) + бектест SKU
def sku_annual_base(df_m: pd.DataFrame, months: list[str]) -> pd.Series:
    """Річна база SKU за вказані 12 місяців; якщо частина місяців невідома (NaN) -
    середнє за відомі місяці x 12."""
    sub = df_m[df_m["month"].isin(months)]
    agg = sub.groupby("sku_id").agg(s=("sold_units", "sum"), n=("sold_units", "count"))
    return (agg["s"] / agg["n"].replace(0, np.nan) * 12).fillna(np.nan), agg["n"]

months_fy = {fy: [str(p) for p in pd.period_range(f"{fy-1}-11", f"{fy}-10", freq="M")] for fy in (2022, 2023, 2024)}

# бектест SKU: прогноз FY2024 з даних до жовтня 2023
b23, n23 = sku_annual_base(mlong, months_fy[2023])
b22, n22 = sku_annual_base(mlong, months_fy[2022])
act24 = mlong[mlong["month"].isin(months_fy[2024])].groupby("sku_id")["sold_units"].sum()
gr_bt = y.iloc[12:24].sum() / y.iloc[:12].sum()
bt_sku = pd.DataFrame({"act": act24}).join(b23.rename("b23")).join(b22.rename("b22"))
bt_sku["S1_останній_рік"] = bt_sku["b23"]
bt_sku["S2_останній_рік_x_ріст"] = bt_sku["b23"] * gr_bt
bt_sku["S3_середнє_2_років_x_ріст"] = bt_sku[["b22", "b23"]].mean(axis=1) * gr_bt
bt_sku = bt_sku.fillna({"S1_останній_рік": 0, "S2_останній_рік_x_ріст": 0, "S3_середнє_2_років_x_ріст": 0})
sku_bt_rows = []
for c in ["S1_останній_рік", "S2_останній_рік_x_ріст", "S3_середнє_2_років_x_ріст"]:
    e = bt_sku[c] - bt_sku["act"]
    sku_bt_rows.append({"метод_SKU": c, "SKU": len(bt_sku),
                        "WAPE_річний_%": e.abs().sum() / bt_sku["act"].sum() * 100,
                        "Bias_%": e.sum() / bt_sku["act"].sum() * 100,
                        "MAE_шт_на_SKU": e.abs().mean()})
sku_bt = pd.DataFrame(sku_bt_rows)

# фінальний SKU-прогноз: середнє двох останніх років (річна база, знижує шум переривчастого
# попиту), узгоджене з груповим базовим прогнозом
b24, n24 = sku_annual_base(mlong, months_fy[2024])
base = pd.DataFrame({"b24": b24, "b23": b23, "n24": n24}).reindex(sku["sku_id"])
base["raw"] = base[["b23", "b24"]].mean(axis=1)
base["raw"] = base["raw"].where(base["b23"].notna(), base["b24"])  # лише 1 рік історії
base["raw"] = base["raw"].fillna(0)
scale = fc["базовий"].sum() / base["raw"].sum()
base["fy2025"] = base["raw"] * scale
si = seasonal_index(y)
w = np.array([si[p.month] for p in fut]); w = w / w.sum()

s_out = sku[["sku_id", "name", "series", "critical_code", "sold_12_observed_u",
             "positive_months_12", "observed_months_12"]].copy()
s_out["прогноз_FY2025_шт"] = base["fy2025"].values.round(2)
s_out["середній_місяць_шт"] = (s_out["прогноз_FY2025_шт"] / 12).round(3)
s_out["пік_лютий_шт"] = (s_out["прогноз_FY2025_шт"] * w[3]).round(3)


def kind(r):
    if r["observed_months_12"] < 12:
        return "коротка історія (рік не повний)"
    if r["sold_12_observed_u"] == 0:
        return "без продажів за 12 міс"
    if r["positive_months_12"] >= 7:
        return "регулярний"
    if r["positive_months_12"] >= 4:
        return "нерегулярний"
    return "рідкісний (1-3 міс із продажами)"


s_out["тип_попиту"] = s_out.apply(kind, axis=1)
s_out["метод"] = np.where(base["b23"].isna().values, "лише FY2024 (річна база)", "середнє FY2023-FY2024")
s_out = s_out.sort_values("прогноз_FY2025_шт", ascending=False)
s_out.to_csv(OUT / "forecast_SKU_FY2025.csv", sep=";", index=False, encoding="utf-8-sig", decimal=",")
mix = s_out.groupby("тип_попиту").agg(SKU=("sku_id", "count"), прогноз_шт=("прогноз_FY2025_шт", "sum")).round(0)
mix["частка_прогнозу_%"] = (mix["прогноз_шт"] / mix["прогноз_шт"].sum() * 100).round(1)

# ---------------------------------------------------------------- збереження
bt_a.round(2).to_csv(OUT / "backtest_A_metrics.csv", sep=";", index=False, encoding="utf-8-sig", decimal=",")
bt_b.round(2).to_csv(OUT / "backtest_B_rolling_metrics.csv", sep=";", index=False, encoding="utf-8-sig", decimal=",")
fc.to_csv(OUT / "forecast_group_FY2025.csv", sep=";", index=False, encoding="utf-8-sig", decimal=",")
fc_all.round(0).to_csv(OUT / "forecast_all_models_FY2025.csv", sep=";", index=False, encoding="utf-8-sig", decimal=",")
ser_fc.to_csv(OUT / "forecast_series_FY2025_monthly.csv", sep=";", index=False, encoding="utf-8-sig", decimal=",")
ser_annual.to_csv(OUT / "forecast_series_FY2025_annual.csv", sep=";", index=False, encoding="utf-8-sig", decimal=",")
sku_bt.round(2).to_csv(OUT / "backtest_SKU_FY2024.csv", sep=";", index=False, encoding="utf-8-sig", decimal=",")
mix.to_csv(OUT / "SKU_demand_types.csv", sep=";", encoding="utf-8-sig", decimal=",")

summary = {
    "fy2024_факт": int(y.iloc[24:].sum()),
    "fy2025_песимістичний": int(fc["песимістичний_без_росту"].sum()),
    "fy2025_базовий": int(fc["базовий"].sum()),
    "fy2025_оптимістичний": int(fc["оптимістичний_повний_ріст"].sum()),
    "річні_суми_всіх_моделей": {k: int(v) for k, v in fc_all.drop(columns="month").sum().items()},
    "sigma_лог_похибки_місяця": round(sigma, 4),
    "відносна_похибка_місяця_1sigma_%": round((np.exp(sigma) - 1) * 100, 1),
    "базові_моделі": BASE,
    "сезонні_індекси_CMA": {MONTH_UA[m]: round(float(seasonal_index(y)[m]), 3) for m in [11, 12, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]},
    "лют_бер_частка_прогнозу_%": round(float(fc["базовий"].iloc[3:5].sum() / fc["базовий"].sum() * 100), 1),
    "SKU_масштаб_узгодження": round(float(scale), 4),
    "SKU_сума_прогнозу": round(float(s_out["прогноз_FY2025_шт"].sum()), 1),
}
(OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

# ---------------------------------------------------------------- графік
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(12, 5.2))
x_hist = [p.to_timestamp() for p in y.index]
x_fut = [p.to_timestamp() for p in fut]
ax.plot(x_hist, y.values, color="#1f5fa8", lw=2, label="Факт, шт")
ax.fill_between(x_fut, fc["нижня_80%"], fc["верхня_80%"], color="#e07b1f", alpha=0.18, label="80% інтервал")
ax.plot(x_fut, fc["базовий"], color="#e07b1f", lw=2.2, marker="o", ms=4, label="Прогноз FY2025, базовий")
ax.plot(x_fut, fc["оптимістичний_повний_ріст"], color="#e07b1f", lw=1, ls="--", label="З повним ростом")
ax.plot(x_fut, fc["песимістичний_без_росту"], color="#8a8a8a", lw=1, ls="--", label="Без росту")
bt_best = MODELS[BASE[0]](train_a, list(test_a.index)) / 2 + MODELS[BASE[1]](train_a, list(test_a.index)) / 2
ax.plot(x_hist[24:], bt_best, color="#2a9d5c", lw=1.3, ls=":", label="Перевірка: прогноз FY2024 з даних до жовт. 2023")
for fy in (2022, 2023, 2024, 2025):
    ax.axvspan(pd.Timestamp(f"{fy}-02-01"), pd.Timestamp(f"{fy}-03-31"), color="#f2c94c", alpha=0.15, lw=0)
ax.axvline(pd.Timestamp("2024-11-01"), color="black", lw=0.8)
ax.set_title("Група B (трактори): продажі запчастин по місяцях і прогноз на FY2025")
ax.set_ylabel("шт за місяць"); ax.grid(alpha=0.25); ax.legend(loc="upper left", fontsize=8.5)
fig.tight_layout(); fig.savefig(OUT / "forecast_FY2025.png", dpi=150)

print(bt_a.round(2).to_string()); print(bt_b.round(2).to_string()); print(fc.to_string())
print(ser_annual.to_string()); print(sku_bt.round(2).to_string()); print(mix.to_string())
print(json.dumps(summary, ensure_ascii=False, indent=1))
