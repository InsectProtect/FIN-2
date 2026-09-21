"""
Простой прогноз на следующий месяц: линейный тренд по последним закрытым
месяцам (по умолчанию — 3). Не заменяет ваш анализ, а даёт отправную точку —
её можно попросить Claude уточнить, дав больше контекста (сезонность,
известные будущие заказы и т.д.).
"""


def linear_forecast(values_by_month: dict, upcoming_month: int, lookback: int = 3) -> float | None:
    months = sorted(m for m in values_by_month if m < upcoming_month)
    months = months[-lookback:]
    if len(months) < 2:
        return None
    ys = [values_by_month[m] for m in months]
    xs = list(range(len(ys)))
    n = len(xs)
    x_mean = sum(xs) / n
    y_mean = sum(ys) / n
    num = sum((xs[i] - x_mean) * (ys[i] - y_mean) for i in range(n))
    den = sum((xs[i] - x_mean) ** 2 for i in range(n)) or 1
    slope = num / den
    intercept = y_mean - slope * x_mean
    next_x = n  # следующая точка после lookback-периода
    return max(0.0, intercept + slope * next_x)


def build_kpis(revenue: dict, expenses: dict, upcoming_month: int) -> dict:
    rev_forecast = linear_forecast(revenue, upcoming_month)
    exp_forecast = linear_forecast(expenses, upcoming_month)
    months_done = sorted(set(revenue) | set(expenses))
    total_rev = sum(revenue.values())
    total_exp = sum(expenses.values())
    avg_margin = None
    if total_rev:
        avg_margin = (total_rev - total_exp) / total_rev
    return {
        "revenue_forecast": rev_forecast,
        "expenses_forecast": exp_forecast,
        "profit_forecast": (rev_forecast - exp_forecast) if (rev_forecast is not None and exp_forecast is not None) else None,
        "avg_margin": avg_margin,
        "months_with_data": months_done,
    }
