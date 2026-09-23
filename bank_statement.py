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
import unicodedata
from collections import defaultdict

import pdfplumber

_PERIOD_RE = re.compile(r"за период\s+(\d{2}\.\d{2}\.\d{4})\s*-\s*(\d{2}\.\d{2}\.\d{4})")
_DATE_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")

# Операции по займам/кредитам (выдача или получение денег в долг) — это не
# реальная выручка и не реальный расход бизнеса, поэтому такие строки
# исключаются из подсчёта (и по Дебету, и по Кредиту). Список ключевых слов
# сделан широким (рум. + рус.), чтобы поймать разные формулировки в
# "Назначение платежа" / "Контрагент" — можно расширить, если в будущей
# выписке встретится новая формулировка, которую список не ловит.
_LOAN_KEYWORDS = [
    "imprumut",   # рум. "заём" (без диакритики после normalize)
    "credit",     # рум./рус. "кредит" (в т.ч. "achitare credit")
    "datorie",    # рум. "долг"
    "долг",
    "займ",
    "заём",
    "ссуда",
]

# Контрагенты, платежи которым НИКОГДА не считаются займом/кредитом, даже
# если в описании встречаются слова из _LOAN_KEYWORDS ("imprumut", "credit"
# и т.п.) — например, регулярные лизинговые платежи по договору лизинга
# оборудования/авто, это обычный текущий расход бизнеса, а не выдача или
# получение денег в долг. Проверяется по подстроке в "Контрагент".
_LOAN_EXCEPTION_COUNTERPARTIES = [
    "leasing",
]


def _normalize(text: str) -> str:
    """Убирает диакритику (ă, î, â, ș, ț -> a, i, a, s, t) и приводит к
    нижнему регистру, чтобы ключевые слова ловили разные написания
    ('împrumut' и 'imprumut')."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.lower()


def _is_loan_related(counterparty: str, description: str) -> bool:
    counterparty_norm = _normalize(counterparty)
    if any(exc in counterparty_norm for exc in _LOAN_EXCEPTION_COUNTERPARTIES):
        return False
    combined = _normalize(f"{counterparty} {description}")
    return any(kw in combined for kw in _LOAN_KEYWORDS)


def _month_key(date_ddmmyyyy: str) -> str:
    """'21.09.2026' -> '2026-09' — нужно, чтобы правильно раскладывать суммы
    по месяцам, даже если выписка загружена одним PDF-документом сразу за
    несколько месяцев (например, с начала года), а не помесячно."""
    d, m, y = date_ddmmyyyy.split(".")
    return f"{y}-{m}"


def _month_key_next(month_key: str) -> str:
    """'2026-01' -> '2026-02', '2026-12' -> '2027-01'."""
    y, m = (int(x) for x in month_key.split("-"))
    if m == 12:
        return f"{y + 1}-01"
    return f"{y}-{m + 1:02d}"


def _longest_consecutive_streak(month_keys: set[str]) -> int:
    """Длина самой длинной цепочки подряд идущих месяцев в наборе
    'YYYY-MM'. Например, {2026-01, 2026-02, 2026-04} -> 2 (январь-февраль),
    а не 3, т.к. март пропущен."""
    if not month_keys:
        return 0
    best = 1
    for mk in month_keys:
        if mk in month_keys:
            # считаем цепочку, только начиная с месяца, у которого нет
            # предыдущего в наборе — иначе одна и та же цепочка посчитается
            # несколько раз с разной длиной
            y, m = (int(x) for x in mk.split("-"))
            prev = f"{y}-{m - 1:02d}" if m > 1 else f"{y - 1}-12"
            if prev in month_keys:
                continue
            length = 1
            cur = mk
            while _month_key_next(cur) in month_keys:
                cur = _month_key_next(cur)
                length += 1
            best = max(best, length)
    return best


def find_recurring_expenses(items: list[dict], min_consecutive_months: int = 2) -> list[dict]:
    """Ищет расходы (строки из parse_bank_statement()["items"], т.е. только
    Дебет), которые повторяются у одного и того же контрагента минимум
    `min_consecutive_months` месяцев ПОДРЯД (не просто N раз за произвольные
    месяцы) — это и есть «регулярный» расход в бытовом смысле: платёж,
    который идёт из месяца в месяц без перерыва.

    Возвращает список {"name", "amount" (среднее), "months_count" (сколько
    всего месяцев встречался), "streak" (длина самой длинной подряд идущей
    цепочки), "sample_description"} — отсортированный по длине цепочки, от
    самых регулярных к менее."""
    by_counterparty = defaultdict(list)
    for it in items:
        by_counterparty[it["counterparty"].strip()].append(it)

    out = []
    for name, rows in by_counterparty.items():
        months = set(_month_key(r["date"]) for r in rows)
        streak = _longest_consecutive_streak(months)
        if streak < min_consecutive_months:
            continue
        amounts = [r["amount"] for r in rows]
        out.append({
            "name": name,
            "amount": round(sum(amounts) / len(amounts), 2),
            "months_count": len(months),
            "streak": streak,
            "sample_description": rows[0]["description"],
        })

    out.sort(key=lambda r: (-r["streak"], -r["months_count"]))
    return out


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
    "items": [...], "total_income":.., "income_count":..,
    "expenses_by_month": {"YYYY-MM": сумма, ...}, "income_by_month": {...},
    "excluded_loan_expenses":.., "excluded_loan_income":..,
    "excluded_loan_count":.., "excluded_loan_items": [...]}

    total_expenses/items — сумма и строки по колонке «Дебет» (расходы).
    total_income/income_count — сумма и количество операций по колонке
    «Кредит» (поступления/перечисления), без построчной расшифровки —
    нужна только итоговая сумма.

    expenses_by_month/income_by_month — те же суммы, но разложенные по
    месяцу РЕАЛЬНОЙ даты операции. Это важно, если выписка загружается
    одним PDF сразу за несколько месяцев (например, с начала года) — тогда
    общую сумму нельзя целиком приписывать одному месяцу, а нужно
    раскладывать по датам самих операций.

    Строки, в которых «Контрагент»/«Назначение платежа» похожи на
    заём/кредит (см. _LOAN_KEYWORDS), НЕ попадают в total_expenses/
    total_income и в *_by_month — это не реальная выручка и не реальный
    расход бизнеса. Их сумма и список видны отдельно в excluded_loan_*, для
    прозрачности (например, чтобы показать пользователю, что именно было
    исключено)."""
    items = []
    total_expenses = 0.0
    total_income = 0.0
    income_count = 0
    expenses_by_month = defaultdict(float)
    income_by_month = defaultdict(float)
    period_from = period_to = None

    excluded_loan_items = []
    excluded_loan_expenses = 0.0
    excluded_loan_income = 0.0

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
                    if debit_val <= 0 and credit_val <= 0:
                        continue

                    counterparty_raw = (row[3] or "").split("\n")[0].strip()
                    counterparty = re.sub(r"^\(R\)\s*", "", counterparty_raw)
                    description = " ".join((row[4] or "").split())

                    if _is_loan_related(counterparty, description):
                        amount = debit_val if debit_val > 0 else credit_val
                        excluded_loan_items.append({
                            "date": date,
                            "counterparty": counterparty,
                            "description": description,
                            "amount": round(amount, 2),
                            "type": "expense" if debit_val > 0 else "income",
                        })
                        if debit_val > 0:
                            excluded_loan_expenses += debit_val
                        else:
                            excluded_loan_income += credit_val
                        continue

                    if debit_val > 0:
                        items.append({
                            "date": date,
                            "counterparty": counterparty,
                            "description": description,
                            "amount": round(debit_val, 2),
                        })
                        total_expenses += debit_val
                        expenses_by_month[_month_key(date)] += debit_val
                    elif credit_val > 0:
                        total_income += credit_val
                        income_count += 1
                        income_by_month[_month_key(date)] += credit_val

    items.sort(key=lambda x: x["date"])
    excluded_loan_items.sort(key=lambda x: x["date"])
    return {
        "period_from": period_from,
        "period_to": period_to,
        "total_expenses": round(total_expenses, 2),
        "count": len(items),
        "items": items,
        "total_income": round(total_income, 2),
        "income_count": income_count,
        "expenses_by_month": {k: round(v, 2) for k, v in expenses_by_month.items()},
        "income_by_month": {k: round(v, 2) for k, v in income_by_month.items()},
        "excluded_loan_expenses": round(excluded_loan_expenses, 2),
        "excluded_loan_income": round(excluded_loan_income, 2),
        "excluded_loan_count": len(excluded_loan_items),
        "excluded_loan_items": excluded_loan_items,
        # Расходы одному контрагенту минимум 2 месяца подряд — кандидаты в
        # "Регулярные расходы", предлагаются пользователю на вкладке
        # «Сверка с банком» после загрузки выписки (не только из готового
        # разбора за год — из ЛЮБОЙ загруженной выписки, если она покрывает
        # 2+ месяцев).
        "recurring_suggestions": find_recurring_expenses(items),
    }
