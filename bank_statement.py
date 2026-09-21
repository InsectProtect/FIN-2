"""
Разбор PDF-выписки банка (сейчас настроено под формат BC "MAIB" SA — Bank
Statement с колонками N/O | Дата операции | No doc. | Контрагент |
Назначение платежа | Дебет | Кредит).

«Дебет» — списания со счёта (расходы), «Кредит» — поступления (в т.ч.
оплаты по счёту / перечисления от клиентов). Считаем и то, и другое:
расходы — для сверки с таблицей «Расходы», поступления — как замену
колонке PRET F (выручка «по счёту» теперь считается из реальных
поступлений по выписке, а не из таблицы заказов).

Если банк сменится или формат выписки изменится — нужно будет
скорректировать разбор таблицы ниже (структура колонок может отличаться).
"""
import io
import re

import pdfplumber

_PERIOD_RE = re.compile(r"за период\s+(\d{2}\.\d{2}\.\d{4})\s*-\s*(\d{2}\.\d{2}\.\d{4})")
_DATE_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")


def _to_float(raw: str) -> float:
    raw = (raw or "").replace(",", ".").replace(" ", "").replace("\xa0", "").strip()
    if not raw:
        return 0.0
    try:
        return float(raw)
    except ValueError:
        return 0.0


def parse_bank_statement(pdf_bytes: bytes) -> dict:
    """{"period_from":.., "period_to":.., "total_expenses":.., "count":..,
    "items": [...], "total_income":.., "income_count":..}
    total_expenses/items — сумма и строки по колонке «Дебет» (расходы).
    total_income/income_count — сумма и количество операций по колонке
    «Кредит» (поступления/перечисления), без построчной расшифровки —
    нужна только итоговая сумма."""
    items = []
    total_expenses = 0.0
    total_income = 0.0
    income_count = 0
    period_from = period_to = None

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        first_text = pdf.pages[0].extract_text() or ""
        m = _PERIOD_RE.search(first_text)
        if m:
            period_from, period_to = m.group(1), m.group(2)

        for page in pdf.pages:
            for table in page.extract_tables():
                for row in table:
                    if not row or len(row) < 7:
                        continue
                    date = (row[1] or "").strip()
                    if not _DATE_RE.match(date):
                        continue  # не строка операции (заголовок/мусор)

                    debit_val = _to_float(row[-2])
                    credit_val = _to_float(row[-1])

                    if debit_val > 0:
                        counterparty_raw = (row[3] or "").split("\n")[0].strip()
                        counterparty = re.sub(r"^\(R\)\s*", "", counterparty_raw)
                        description = " ".join((row[4] or "").split())
                        items.append({
                            "date": date,
                            "counterparty": counterparty,
                            "description": description,
                            "amount": round(debit_val, 2),
                        })
                        total_expenses += debit_val
                    elif credit_val > 0:
                        total_income += credit_val
                        income_count += 1

    items.sort(key=lambda x: x["date"])
    return {
        "period_from": period_from,
        "period_to": period_to,
        "total_expenses": round(total_expenses, 2),
        "count": len(items),
        "items": items,
        "total_income": round(total_income, 2),
        "income_count": income_count,
    }
