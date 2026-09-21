"""
Чтение выручки из «Ip 2026» и запись/чтение расходов в «Расходы» через
сервисный аккаунт Google (gspread). Обе таблицы должны быть расшарены
на email сервисного аккаунта (см. шаг 3 инструкции) с правом редактора.

Важно: у Google Sheets API довольно скромный лимит запросов в минуту на
пользователя, поэтому здесь сведено к минимуму количество отдельных
обращений — таблицы открываются один раз и кэшируются, а выручка по всем
12 листам читается одним batch-запросом вместо 12 отдельных.
"""
import os
import datetime
from collections import defaultdict

import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

MONTH_SHEETS = ["Ianuarie", "Februarie", "Martie", "Aprilie", "Mai",
                "Iunie", "Iulie", "August ", "Septembrie", "Octombrie",
                "Noiembrie", "Decembrie"]
MONTH_RU = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
            "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]

_client = None
_sheet_cache: dict[str, gspread.Spreadsheet] = {}


def _get_client():
    global _client
    if _client is None:
        creds_file = os.environ["GOOGLE_SERVICE_ACCOUNT_FILE"]
        creds = Credentials.from_service_account_file(creds_file, scopes=SCOPES)
        _client = gspread.authorize(creds)
    return _client


def _open(title: str) -> gspread.Spreadsheet:
    """Открывает таблицу по названию и кэширует результат — open() сам по
    себе делает запрос к API, а название/id таблицы за время жизни
    процесса не меняются."""
    sh = _sheet_cache.get(title)
    if sh is None:
        sh = _get_client().open(title)
        _sheet_cache[title] = sh
    return sh


def get_revenue_by_month(year: int) -> dict:
    """{month_index(1-12): {"cash": ..., "invoice": ..., "total": ...}}
    из листов заказов «Ip 2026» — наличные (PRET C*) и по счёту (PRET F)
    считаются раздельно.

    Читает все 12 листов ОДНИМ batch-запросом (values_batch_get), а не по
    одному запросу на лист — иначе быстро упираемся в лимит API."""
    sh = _open(os.environ["SHEET_REVENUE_NAME"])
    existing_titles = {ws.title for ws in sh.worksheets()}
    ranges = [f"'{name}'" for name in MONTH_SHEETS if name in existing_titles]
    out = defaultdict(lambda: {"cash": 0.0, "invoice": 0.0, "total": 0.0})
    if not ranges:
        return out

    value_ranges = sh.values_batch_get(ranges)["valueRanges"]
    month_by_range_index = [i for i, name in enumerate(MONTH_SHEETS, start=1) if name in existing_titles]

    for month_i, vr in zip(month_by_range_index, value_ranges):
        rows = vr.get("values", [])
        if not rows:
            continue
        header = [h.strip() for h in rows[0]]
        cash_cols = [idx for idx, h in enumerate(header) if h.upper().startswith("PRET C")]
        inv_col = next((idx for idx, h in enumerate(header) if h.strip() == "PRET F"), None)
        for row in rows[1:]:
            for idx in cash_cols:
                if idx < len(row):
                    out[month_i]["cash"] += _to_float(row[idx])
            if inv_col is not None and inv_col < len(row):
                out[month_i]["invoice"] += _to_float(row[inv_col])
        out[month_i]["total"] = out[month_i]["cash"] + out[month_i]["invoice"]
    return out


# Точные значения из выпадающего списка «Daunatori» в таблице (скриншот
# пользователя): plosnita, roscat, negru, zburatoare, rozatoare, purici,
# furnici, viespe. "roscat" и "negru" — это два вида тараканов (рыжий и
# чёрный), поэтому оба относятся к группе "Тараканы".
SERVICE_GROUPS = {
    "roscat": "cockroach",
    "negru": "cockroach",
    "plosnita": "bedbug",
    "zburatoare": "flying",
    "rozatoare": "rodent",
}
SERVICE_LABELS = {
    "cockroach": "Тараканы",
    "bedbug": "Клопы",
    "flying": "Летающие",
    "rodent": "Грызуны",
    "other": "Другое",
}
SERVICE_ORDER = ["cockroach", "bedbug", "flying", "rodent"]


def get_revenue_by_service(year: int) -> dict:
    """{month_index(1-12): [{"key":.., "label":.., "count":.., "total":..}, ...]}
    — сколько вызовов и сколько денег принёс каждый вид вредителя/услуги
    (колонка Daunatori на листах месяцев «Ip {year}»), отдельно по каждому
    месяцу — чтобы в приложении можно было смотреть либо весь год, либо
    конкретный выбранный месяц. Читает те же 12 листов одним batch-запросом,
    что и get_revenue_by_month."""
    sh = _open(os.environ["SHEET_REVENUE_NAME"])
    existing_titles = {ws.title for ws in sh.worksheets()}
    ranges = [f"'{name}'" for name in MONTH_SHEETS if name in existing_titles]
    month_by_range_index = [i for i, name in enumerate(MONTH_SHEETS, start=1) if name in existing_titles]

    def _empty_stats():
        s = {key: {"count": 0, "total": 0.0} for key in SERVICE_ORDER}
        s["other"] = {"count": 0, "total": 0.0}
        return s

    out = {}
    if not ranges:
        return out

    value_ranges = sh.values_batch_get(ranges)["valueRanges"]
    for month_i, vr in zip(month_by_range_index, value_ranges):
        rows = vr.get("values", [])
        stats = _empty_stats()
        if rows:
            header = [h.strip() for h in rows[0]]
            cash_cols = [idx for idx, h in enumerate(header) if h.upper().startswith("PRET C")]
            inv_col = next((idx for idx, h in enumerate(header) if h.strip() == "PRET F"), None)
            daun_col = next((idx for idx, h in enumerate(header) if h.strip().lower() == "daunatori"), None)
            if daun_col is not None:
                for row in rows[1:]:
                    total = 0.0
                    for idx in cash_cols:
                        if idx < len(row):
                            total += _to_float(row[idx])
                    if inv_col is not None and inv_col < len(row):
                        total += _to_float(row[inv_col])
                    raw = row[daun_col].strip().lower() if daun_col < len(row) else ""
                    if not raw and total == 0:
                        continue  # пустая строка-разделитель между днями
                    key = SERVICE_GROUPS.get(raw, "other")
                    stats[key]["count"] += 1
                    stats[key]["total"] += total
        out[month_i] = _service_stats_to_list(stats)
    return out


def _service_stats_to_list(stats: dict) -> list:
    out = [{"key": key, "label": SERVICE_LABELS[key], **stats[key]} for key in SERVICE_ORDER]
    out.append({"key": "other", "label": "Другое", **stats["other"]})
    return out


def _to_float(v: str) -> float:
    v = (v or "").strip().replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return 0.0


# Даты в «Расходы» бывают двух видов: "2026-09-21" (когда расход добавлен
# через Mini App) и "21.09.2026" (когда строку вписали в таблицу вручную).
_DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y")


def _parse_date(v: str):
    v = (v or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.datetime.strptime(v, fmt)
        except ValueError:
            continue
    return None


def add_expense(date: str, category: str, description: str, account: str,
                 currency: str, amount: float, rate: float, has_doc: str, added_by: str,
                 receipt_link: str = "") -> None:
    sh = _open(os.environ["SHEET_EXPENSES_NAME"])
    ws = sh.worksheet("Расходы")
    mdl = amount if currency == "MDL" else amount * rate
    d = datetime.datetime.strptime(date, "%Y-%m-%d")
    month_name = MONTH_RU[d.month - 1]
    # Последняя колонка — кликабельная ссылка на фото чека (если было
    # загружено). HYPERLINK делает её ссылкой прямо в ячейке, а не голым URL.
    receipt_cell = f'=HYPERLINK("{receipt_link}"; "Чек")' if receipt_link else ""
    ws.append_row([
        date, month_name, category, description, account, currency,
        amount, rate, mdl, has_doc, f"добавлено через Telegram: {added_by}", receipt_cell
    ], value_input_option="USER_ENTERED")


def get_expenses_summary(year: int) -> tuple[dict, dict]:
    """Возвращает (by_month, by_category) — оба словаря считаются из ОДНОГО
    чтения листа «Расходы», а не из двух отдельных запросов."""
    sh = _open(os.environ["SHEET_EXPENSES_NAME"])
    ws = sh.worksheet("Расходы")
    rows = ws.get_all_values()[4:]  # первые 4 строки — заголовок/пояснения

    by_month = defaultdict(float)
    by_category = defaultdict(float)
    for row in rows:
        if len(row) < 9 or not row[0]:
            continue
        d = _parse_date(row[0])
        if d is None:
            continue
        if d.year != year:
            continue
        amount = _to_float(row[8])
        by_month[d.month] += amount
        by_category[row[2]] += amount
    return by_month, by_category


# Оставлены для обратной совместимости (используют то же кэширование).
def get_expenses_by_month(year: int) -> dict:
    return get_expenses_summary(year)[0]


def get_expenses_by_category(year: int) -> dict:
    return get_expenses_summary(year)[1]


def list_expenses(year: int, limit: int = 50) -> list:
    """Список последних расходов (для истории в приложении, с
    возможностью редактировать/удалять). "row" — номер строки в самой
    Google Таблице (с 1), он же используется как id для update/delete —
    так что не стоит вручную переставлять местами строки в «Расходы»,
    пока пользуетесь историей в приложении."""
    sh = _open(os.environ["SHEET_EXPENSES_NAME"])
    ws = sh.worksheet("Расходы")
    all_rows = ws.get_all_values()
    data_rows = all_rows[4:]  # первые 4 строки — заголовок/пояснения

    items = []
    for i, row in enumerate(data_rows):
        if not row or not row[0]:
            continue
        d = _parse_date(row[0])
        if d is None or d.year != year:
            continue
        get = lambda idx: row[idx] if idx < len(row) else ""
        items.append({
            "row": i + 5,
            "date": get(0),
            "category": get(2),
            "description": get(3),
            "account": get(4),
            "currency": get(5),
            "amount": get(6),
            "rate": get(7),
            "mdl": get(8),
            "has_doc": get(9),
        })
    items.sort(key=lambda x: x["row"], reverse=True)
    return items[:limit]


def update_expense(row: int, date: str, category: str, description: str, account: str,
                    currency: str, amount: float, rate: float) -> None:
    sh = _open(os.environ["SHEET_EXPENSES_NAME"])
    ws = sh.worksheet("Расходы")
    mdl = amount if currency == "MDL" else amount * rate
    d = datetime.datetime.strptime(date, "%Y-%m-%d")
    month_name = MONTH_RU[d.month - 1]
    ws.update(f"A{row}:I{row}", [[
        date, month_name, category, description, account, currency, amount, rate, mdl,
    ]], value_input_option="USER_ENTERED")


def delete_expense(row: int) -> None:
    sh = _open(os.environ["SHEET_EXPENSES_NAME"])
    ws = sh.worksheet("Расходы")
    ws.delete_rows(row)


def get_expenses_total_in_range(date_from: str, date_to: str) -> float:
    """Сумма (в MDL) расходов, внесённых в «Расходы», по датам в диапазоне
    [date_from, date_to] включительно. date_from/date_to — в формате
    ДД.ММ.ГГГГ (как в банковской выписке) — используется для сверки с
    расходами, распознанными из PDF-выписки банка."""
    d_from = datetime.datetime.strptime(date_from, "%d.%m.%Y")
    d_to = datetime.datetime.strptime(date_to, "%d.%m.%Y")
    sh = _open(os.environ["SHEET_EXPENSES_NAME"])
    ws = sh.worksheet("Расходы")
    rows = ws.get_all_values()[4:]

    total = 0.0
    for row in rows:
        if len(row) < 9 or not row[0]:
            continue
        d = _parse_date(row[0])
        if d is None:
            continue
        if d_from <= d <= d_to:
            total += _to_float(row[8])
    return round(total, 2)
