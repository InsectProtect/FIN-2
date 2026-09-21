"""
Разбор PDF-выписки банка (сейчас настроено под формат BC "MAIB" SA — Bank
Statement с колонками N/O | Дата операции | No doc. | Контрагент |
Назначение платежа | Дебет | Кредит).

Берём только строки, где заполнена колонка «Дебет» — это списания со счёта
(расходы), «Кредит» — поступления, их не трогаем, т.к. пользователю нужны
только расходы для сверки с тем, что внесено в таблицу «Расходы» вручную.

Если банк сменится или формат выписки изменится — нужно будет
скорректировать разбор таблицы ниже (структура колонок может отличаться).
"""
import io
import re

import pdfplumber

_PERIOD_RE = re.compile(r"за период\s+(\d{2}\.\d{2}\.\d{4})\s*-\s*(\d{2}\.\d{2}\.\d{4})")
_DATE_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")


def parse_bank_statement(pdf_bytes: bytes) -> dict:
    """{"period_from":.., "period_to":.., "total_expenses":.., "count":..,
    "items": [{"date":.., "counterparty":.., "description":.., "amount":..}, ...]}"""
    items = []
    total = 0.0
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

                    debit_raw = (row[-2] or "").replace(",", ".").replace(" ", "").replace("\xa0", "").strip()
                    try:
                        debit_val = float(debit_raw) if debit_raw else 0.0
                    except ValueError:
                        continue
                    if debit_val <= 0:
                        continue  # это поступление, а не расход — пропускаем

                    counterparty_raw = (row[3] or "").split("\n")[0].strip()
                    counterparty = re.sub(r"^\(R\)\s*", "", counterparty_raw)
                    description = " ".join((row[4] or "").split())

                    items.append({
                        "date": date,
                        "counterparty": counterparty,
                        "description": description,
                        "amount": round(debit_val, 2),
                    })
                    total += debit_val

    items.sort(key=lambda x: x["date"])
    return {
        "period_from": period_from,
        "period_to": period_to,
        "total_expenses": round(total, 2),
        "count": len(items),
        "items": items,
    }
