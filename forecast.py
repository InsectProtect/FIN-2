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


def build_kpis(revenue: dict, expenses: dict, upcoming_month: int, balance: dict | None = None) -> dict:
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
        "hp": build_hp(revenue, expenses, balance=balance),
    }


def build_hp(revenue: dict, expenses: dict, lookback: int = 3, balance: dict | None = None) -> dict:
    """"HP" компании — запас прочности в месяцах, как полоска здоровья в
    игре: сколько месяцев компания продержится при текущем среднем темпе
    расходов, если выручка вдруг остановится.

    reserve — точка отсчёта:
    - Если задан `balance` (последний вручную введённый остаток "касса +
      расчётный счёт на сегодня", см. gsheets.get_latest_balance) —
      reserve = этот остаток + прибыль (выручка минус расходы) за месяцы
      ПОСЛЕ месяца, в котором остаток вводили (чтобы не задвоить месяц
      ввода — его часть уже "внутри" введённого остатка). balance —
      {"month": int, "total": float}; месяц ожидается в том же году, что
      и revenue/expenses (иначе, если остаток вводили в прошлом году,
      считаем его как точку отсчёта на начало текущего — month=0).
    - Если `balance` не задан — reserve = накопленная прибыль с начала
      года (сумма «выручка минус расходы» по всем месяцам, за которые
      есть данные). Это не то же самое, что реальный остаток на счёте
      (деньги могли быть потрачены/получены мимо таблицы), но лучшая
      оценка без ручного ввода.

    avg_burn — средний расход в месяц за последние `lookback` месяцев (по
    умолчанию 3), с данными.

    months = reserve / avg_burn.
    status: "good" (>=3 мес.), "warn" (1-3 мес.), "critical" (<1 мес. или
    резерв уже отрицательный), "unknown" (пока не из чего считать —
    например, ни одного месяца с расходами)."""
    months_done = sorted(set(revenue) | set(expenses))

    if balance is not None:
        baseline_month = balance.get("month", 0)
        months_after = [m for m in months_done if m > baseline_month]
        reserve = balance["total"] + sum(revenue.get(m, 0.0) - expenses.get(m, 0.0) for m in months_after)
    else:
        reserve = sum(revenue.get(m, 0.0) - expenses.get(m, 0.0) for m in months_done)

    burn_months = months_done[-lookback:]
    avg_burn = (sum(expenses.get(m, 0.0) for m in burn_months) / len(burn_months)) if burn_months else 0.0

    hp_months = (reserve / avg_burn) if avg_burn > 0 else None

    if hp_months is None:
        status = "unknown"
    elif reserve <= 0 or hp_months < 1:
        status = "critical"
    elif hp_months < 3:
        status = "warn"
    else:
        status = "good"

    return {
        "reserve": round(reserve, 2),
        "avg_burn": round(avg_burn, 2),
        "months": round(hp_months, 1) if hp_months is not None else None,
        "status": status,
        "has_balance": balance is not None,
    }
