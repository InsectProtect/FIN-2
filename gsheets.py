"""
Чтение выручки из «Ip 2026» и запись/чтение расходов в «Расходы» через
сервисный аккаунт Google (gspread). Обе таблицы должны быть расшарены
на email сервисного аккаунта (см. шаг 3 инструкции) с правом редактора.
"""
import os
import datetime
from collections import defaultdict

import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

MONTH_SHEETS = ["Ianuarie", "Februarie", "Martie", "Aprilie", "Mai",
                "Iunie", "Iulie", "August ", "Septembrie", "Octombrie",
                "Noiembrie", "Decembrie"]
MONTH_RU = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
            "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]

_client = None


def _get_client():
    global _client
    if _client is None:
        creds_file = os.environ["GOOGLE_SERVICE_ACCOUNT_FILE"]
        creds = Credentials.from_service_account_file(creds_file, scopes=SCOPES)
        _client = gspread.authorize(creds)
    return _client


def get_revenue_by_month(year: int) -> dict:
    """{month_index(1-12): revenue_mdl} из листов заказов «Ip 2026»."""
    sh = _get_client().open(os.environ["SHEET_REVENUE_NAME"])
    out = defaultdict(float)
    for i, month_name in enumerate(MONTH_SHEETS, start=1):
        try:
            ws = sh.worksheet(month_name)
        except gspread.WorksheetNotFound:
            continue
        rows = ws.get_all_values()
        if not rows:
            continue
        header = [h.strip() for h in rows[0]]
        cash_cols = [idx for idx, h in enumerate(header) if h.upper().startswith("PRET C")]
        inv_col = next((idx for idx, h in enumerate(header) if h.strip() == "PRET F"), None)
        for row in rows[1:]:
            for idx in cash_cols:
                if idx < len(row):
                    out[i] += _to_float(row[idx])
            if inv_col is not None and inv_col < len(row):
                out[i] += _to_float(row[inv_col])
    return out


def _to_float(v: str) -> float:
    v = (v or "").strip().replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return 0.0


def add_expense(date: str, category: str, description: str, account: str,
                 currency: str, amount: float, rate: float, has_doc: str, added_by: str) -> None:
    sh = _get_client().open(os.environ["SHEET_EXPENSES_NAME"])
    ws = sh.worksheet("Расходы")
    mdl = amount if currency == "MDL" else amount * rate
    d = datetime.datetime.strptime(date, "%Y-%m-%d")
    month_name = MONTH_RU[d.month - 1]
    ws.append_row([
        date, month_name, category, description, account, currency,
        amount, rate, mdl, has_doc, f"добавлено через Telegram: {added_by}"
    ], value_input_option="USER_ENTERED")


def get_expenses_by_month(year: int) -> dict:
    """{month_index(1-12): expenses_mdl} из листа «Расходы»."""
    sh = _get_client().open(os.environ["SHEET_EXPENSES_NAME"])
    ws = sh.worksheet("Расходы")
    rows = ws.get_all_values()[4:]  # первые 4 строки — заголовок/пояснения
    out = defaultdict(float)
    for row in rows:
        if len(row) < 9 or not row[0]:
            continue
        try:
            d = datetime.datetime.strptime(row[0], "%Y-%m-%d")
        except ValueError:
            continue
        if d.year != year:
            continue
        out[d.month] += _to_float(row[8])
    return out


def get_expenses_by_category(year: int) -> dict:
    sh = _get_client().open(os.environ["SHEET_EXPENSES_NAME"])
    ws = sh.worksheet("Расходы")
    rows = ws.get_all_values()[4:]
    out = defaultdict(float)
    for row in rows:
        if len(row) < 9 or not row[0]:
            continue
        try:
            d = datetime.datetime.strptime(row[0], "%Y-%m-%d")
        except ValueError:
            continue
        if d.year != year:
            continue
        out[row[2]] += _to_float(row[8])
    return out
