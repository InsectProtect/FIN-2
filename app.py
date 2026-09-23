"""
Единый процесс: Telegram-бот (aiogram, polling) + веб-сервер (FastAPI),
который отдаёт Mini App и обслуживает его API. Так проще всего задеплоить
на бесплатный тариф Render/Railway одним сервисом.
"""
import asyncio
import datetime
import json
import os

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (BufferedInputFile, InlineKeyboardButton,
                            InlineKeyboardMarkup, Message,
                            ReplyKeyboardRemove, WebAppInfo)
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

import auth
import bank_statement
import forecast
import gdrive
import gsheets
import report
import users

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
WEBAPP_URL = os.environ["WEBAPP_URL"]

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

app = FastAPI()
app.mount("/app", StaticFiles(directory="webapp", html=True), name="webapp")


@app.get("/")
async def root_redirect():
    return RedirectResponse(url="/app/")


# ---------------------------------------------------------------- бот

@dp.message(CommandStart())
async def cmd_start(message: Message):
    user = message.from_user
    if users.is_admin(user.id, ADMIN_ID) or users.is_allowed(user.id):
        # Убираем старую постоянную кнопку внизу экрана (если она была
        # показана раньше) и вместо неё даём обычную кнопку прямо в
        # сообщении — она не занимает место вместо клавиатуры.
        await message.answer("Доступ есть.", reply_markup=ReplyKeyboardRemove())
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Открыть приложение", web_app=WebAppInfo(url=f"{WEBAPP_URL}/app/")),
        ]])
        await message.answer("Открывайте приложение кнопкой ниже.", reply_markup=kb)
        return

    users.add_pending(user.id, user.full_name)
    await message.answer("Заявка на доступ отправлена администратору. Как только одобрят — пришлю кнопку входа.")

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Разрешить", callback_data=f"approve:{user.id}"),
        InlineKeyboardButton(text="✖️ Отклонить", callback_data=f"deny:{user.id}"),
    ]])
    await bot.send_message(
        ADMIN_ID,
        f"Запрос доступа к финансовому приложению:\n{user.full_name} (id {user.id}, @{user.username or '—'})",
        reply_markup=kb,
    )


@dp.callback_query(F.data.startswith("approve:"))
async def cb_approve(callback):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Только администратор может это делать.", show_alert=True)
        return
    user_id = int(callback.data.split(":")[1])
    name = users.approve(user_id)
    await callback.message.edit_text(f"{callback.message.text}\n\n→ Одобрено.")
    if name:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Открыть приложение", web_app=WebAppInfo(url=f"{WEBAPP_URL}/app/")),
        ]])
        await bot.send_message(user_id, "Вам открыли доступ. Открывайте приложение кнопкой ниже.", reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data.startswith("deny:"))
async def cb_deny(callback):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Только администратор может это делать.", show_alert=True)
        return
    user_id = int(callback.data.split(":")[1])
    users.deny(user_id)
    await callback.message.edit_text(f"{callback.message.text}\n\n→ Отклонено.")
    await bot.send_message(user_id, "Доступ к приложению не одобрен.")
    await callback.answer()


@dp.message(F.text == "/users")
async def cmd_users(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    allowed = users.list_allowed()
    if not allowed:
        await message.answer("Пока никто не одобрен, кроме вас.")
        return
    lines = [f"{uid}: {name}" for uid, name in allowed.items()]
    await message.answer("Одобренные пользователи:\n" + "\n".join(lines))


# ---------------------------------------------------------------- API для Mini App

APP_PIN = os.environ.get("APP_PIN")  # если не задан в Render — пин-экран отключён


def _authed_user(init_data: str, pin: str = "") -> dict:
    user = auth.verify_init_data(init_data, BOT_TOKEN)
    if not user:
        raise HTTPException(401, "Не удалось подтвердить подлинность запроса Telegram.")
    if not (users.is_admin(user["id"], ADMIN_ID) or users.is_allowed(user["id"])):
        raise HTTPException(403, "Нет доступа. Откройте бота и запросите доступ через /start.")
    if APP_PIN and pin != APP_PIN:
        raise HTTPException(401, "Неверный пин-код.")
    return user


@app.post("/api/verify_pin")
async def api_verify_pin(init_data: str = Form(...), pin: str = Form("")):
    _authed_user(init_data, pin)
    return {"ok": True}


@app.get("/api/summary")
async def api_summary(init_data: str, pin: str = "", year: int = datetime.date.today().year):
    _authed_user(init_data, pin)
    revenue = gsheets.get_revenue_by_month(year)
    expenses, by_cat, _ = gsheets.get_expenses_summary(year)
    by_service_month = gsheets.get_revenue_by_service(year)
    today = datetime.date.today()
    upcoming = today.month + 1 if today.month < 12 else 1

    # Если пользователь вручную вводил остаток (касса + расчётный счёт) —
    # используем его как точку отсчёта для HP вместо накопленной прибыли с
    # начала года (см. forecast.build_hp). Учитываем, только если остаток
    # введён в ТОМ ЖЕ году, что и запрошенный отчёт — иначе (остаток за
    # прошлый год) считаем его точкой отсчёта на начало этого года (month=0).
    latest_balance = gsheets.get_latest_balance()

    # Автоперенос: если последний введённый остаток относится к ПРОШЛОМУ
    # месяцу (пользователь давно не обновлял кассу/счёт), переносим те же
    # цифры в текущий месяц сами — новой строкой с сегодняшней датой. Так
    # остаток не "теряется" с началом нового месяца и не нужно вручную
    # вводить его заново каждый раз, если по факту ничего не изменилось
    # (изменится — можно поправить и сохранить как обычно).
    if latest_balance and (latest_balance["date"].year, latest_balance["date"].month) != (today.year, today.month):
        gsheets.set_balance(
            date=today.isoformat(), cash=latest_balance["cash"], account=latest_balance["account"],
            added_by="автоперенос с прошлого месяца",
        )
        latest_balance = gsheets.get_latest_balance()

    balance_for_hp = None
    balance_info = None
    if latest_balance:
        bal_month = latest_balance["date"].month if latest_balance["date"].year == year else 0
        balance_for_hp = {"month": bal_month, "total": latest_balance["total"]}
        balance_info = {
            "date": latest_balance["date"].isoformat(),
            "cash": latest_balance["cash"],
            "account": latest_balance["account"],
            "total": latest_balance["total"],
        }

    # forecast.py работает с простыми {month: total}, поэтому даём ему
    # только суммарную выручку (наличные + по счёту).
    revenue_total = {m: v["total"] for m, v in revenue.items()}
    kpis = forecast.build_kpis(revenue_total, expenses, upcoming if today.month < 12 else 13, balance=balance_for_hp)

    months = list(range(1, 13))
    empty = {"cash": 0, "invoice": 0, "total": 0}
    empty_service_list = [{"key": k, "label": gsheets.SERVICE_LABELS[k], "count": 0, "total": 0.0}
                           for k in gsheets.SERVICE_ORDER] + [{"key": "other", "label": "Другое", "count": 0, "total": 0.0}]
    return JSONResponse({
        "revenue_by_month": {m: revenue.get(m, empty)["total"] for m in months},
        "revenue_cash_by_month": {m: revenue.get(m, empty)["cash"] for m in months},
        "revenue_invoice_by_month": {m: revenue.get(m, empty)["invoice"] for m in months},
        "expenses_by_month": {m: expenses.get(m, 0) for m in months},
        "expenses_by_category": by_cat,
        "revenue_by_service_month": {m: by_service_month.get(m, empty_service_list) for m in months},
        "kpis": kpis,
        "balance": balance_info,
    })


@app.post("/api/balance")
async def api_set_balance(
    init_data: str = Form(...),
    pin: str = Form(""),
    cash: float = Form(...),
    account: float = Form(...),
):
    user = _authed_user(init_data, pin)
    added_by = user.get("first_name", str(user["id"]))
    today = datetime.date.today().isoformat()
    gsheets.set_balance(date=today, cash=cash, account=account, added_by=added_by)
    return {"ok": True}


@app.get("/api/recurring")
async def api_get_recurring(init_data: str, pin: str = ""):
    _authed_user(init_data, pin)
    items = gsheets.get_recurring_expenses()
    return {"items": items}


@app.post("/api/recurring/add")
async def api_add_recurring(
    init_data: str = Form(...),
    pin: str = Form(""),
    name: str = Form(...),
    category: str = Form(""),
    amount: float = Form(0),
    frequency: str = Form(""),
    note: str = Form(""),
):
    user = _authed_user(init_data, pin)
    added_by = user.get("first_name", str(user["id"]))
    gsheets.add_recurring_expense(name=name, category=category, amount=amount,
                                   frequency=frequency, note=note, added_by=added_by)
    return {"ok": True}


@app.post("/api/recurring/update")
async def api_update_recurring(
    init_data: str = Form(...),
    pin: str = Form(""),
    row: int = Form(...),
    name: str = Form(...),
    category: str = Form(""),
    amount: float = Form(0),
    frequency: str = Form(""),
    note: str = Form(""),
):
    _authed_user(init_data, pin)
    gsheets.update_recurring_expense(row=row, name=name, category=category, amount=amount,
                                      frequency=frequency, note=note)
    return {"ok": True}


@app.post("/api/recurring/delete")
async def api_delete_recurring(
    init_data: str = Form(...),
    pin: str = Form(""),
    row: int = Form(...),
):
    _authed_user(init_data, pin)
    gsheets.delete_recurring_expense(row=row)
    return {"ok": True}


@app.get("/api/report")
async def api_report(init_data: str, pin: str = "", year: int = datetime.date.today().year, month: str = "all"):
    _authed_user(init_data, pin)
    month_i = None
    if month and month != "all":
        try:
            month_i = int(month)
            if not 1 <= month_i <= 12:
                month_i = None
        except ValueError:
            month_i = None

    revenue = gsheets.get_revenue_by_month(year)
    expenses, by_cat, by_month_cat = gsheets.get_expenses_summary(year)
    by_cat_report = by_month_cat.get(month_i, {}) if month_i else by_cat

    # Разбивка по видам вредителей ("круглый дашборд", как на главном
    # экране приложения) — за выбранный месяц, либо суммарно за весь год.
    by_service_month = gsheets.get_revenue_by_service(year)
    if month_i:
        pest_list = by_service_month.get(month_i, [])
    else:
        agg = {}
        for items in by_service_month.values():
            for item in items:
                bucket = agg.setdefault(item["key"], {"key": item["key"], "label": item["label"], "count": 0, "total": 0.0})
                bucket["count"] += item["count"]
                bucket["total"] += item["total"]
        pest_list = list(agg.values())
    total_requests = sum(item["count"] for item in pest_list)

    # HP («здоровье компании») — тот же расчёт, что и на главном экране
    # приложения (см. /api/summary), чтобы в PDF-отчёте была та же картина.
    latest_balance = gsheets.get_latest_balance()
    balance_for_hp = None
    balance_info = None
    if latest_balance:
        bal_month = latest_balance["date"].month if latest_balance["date"].year == year else 0
        balance_for_hp = {"month": bal_month, "total": latest_balance["total"]}
        balance_info = {
            "date": latest_balance["date"].isoformat(),
            "cash": latest_balance["cash"],
            "account": latest_balance["account"],
            "total": latest_balance["total"],
        }
    revenue_total = {m: v["total"] for m, v in revenue.items()}
    hp = forecast.build_hp(revenue_total, expenses, balance=balance_for_hp)

    pdf_bytes = report.build_pdf(year, revenue, expenses, by_cat_report,
                                  month=month_i, pest_list=pest_list, total_requests=total_requests,
                                  hp=hp, balance=balance_info)
    filename = f"otchet_{year}.pdf" if not month_i else f"otchet_{year}_{month_i:02d}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/expense")
async def api_add_expense(
    init_data: str = Form(...),
    pin: str = Form(""),
    date: str = Form(...),
    category: str = Form(...),
    description: str = Form(...),
    account: str = Form(...),
    currency: str = Form(...),
    amount: float = Form(...),
    rate: float = Form(1),
    receipt: UploadFile | None = File(None),
):
    user = _authed_user(init_data, pin)
    added_by = user.get("first_name", str(user["id"]))

    has_doc = "Нет"
    receipt_link = ""
    if receipt is not None and receipt.filename:
        has_doc = "Да"
        data = await receipt.read()
        filename = receipt.filename or "receipt"
        content_type = (receipt.content_type or "").lower()

        try:
            await _send_receipt_to_telegram(
                data, filename, content_type, date=date, category=category,
                description=description, amount=amount, currency=currency, added_by=added_by,
            )
        except Exception:
            # Даже если отправка чека в Telegram не удалась, расход всё
            # равно должен сохраниться в таблице — не блокируем на этом.
            has_doc = "Да (ошибка отправки в Telegram)"

        try:
            drive_filename = f"{date}_{category}_{amount}{os.path.splitext(filename)[1] or ''}"
            receipt_link = gdrive.upload_receipt(data, drive_filename, content_type)
        except Exception:
            # Если не получилось загрузить на Google Диск, расход всё равно
            # сохраняем — просто без ссылки на чек в таблице.
            pass

    gsheets.add_expense(
        date=date,
        category=category,
        description=description,
        account=account,
        currency=currency,
        amount=amount,
        rate=rate,
        has_doc=has_doc,
        added_by=added_by,
        receipt_link=receipt_link,
    )
    return {"ok": True}


@app.get("/api/expenses")
async def api_list_expenses(init_data: str, pin: str = "", year: int = datetime.date.today().year,
                             limit: int = 50):
    _authed_user(init_data, pin)
    return {"items": gsheets.list_expenses(year, limit)}


@app.post("/api/expense/update")
async def api_update_expense(
    init_data: str = Form(...),
    pin: str = Form(""),
    row: int = Form(...),
    date: str = Form(...),
    category: str = Form(...),
    description: str = Form(...),
    account: str = Form(...),
    currency: str = Form(...),
    amount: float = Form(...),
    rate: float = Form(1),
):
    _authed_user(init_data, pin)
    gsheets.update_expense(
        row=row, date=date, category=category, description=description,
        account=account, currency=currency, amount=amount, rate=rate,
    )
    return {"ok": True}


@app.post("/api/expense/delete")
async def api_delete_expense(init_data: str = Form(...), pin: str = Form(""), row: int = Form(...)):
    _authed_user(init_data, pin)
    gsheets.delete_expense(row)
    return {"ok": True}


@app.post("/api/bank_statement")
async def api_bank_statement(
    init_data: str = Form(...),
    pin: str = Form(""),
    file: UploadFile = File(...),
):
    _authed_user(init_data, pin)
    data = await file.read()
    result = bank_statement.parse_bank_statement(data)

    sheet_total = None
    if result.get("period_from") and result.get("period_to"):
        try:
            sheet_total = gsheets.get_expenses_total_in_range(result["period_from"], result["period_to"])
        except Exception:
            sheet_total = None
    result["sheet_total"] = sheet_total
    return JSONResponse(result)


@app.post("/api/bank_statement/apply")
async def api_bank_statement_apply(
    init_data: str = Form(...),
    pin: str = Form(""),
    period_from: str = Form(...),
    period_to: str = Form(...),
    by_month: str = Form(...),  # JSON: {"2026-09": 108169.49, ...}
):
    user = _authed_user(init_data, pin)
    added_by = user.get("first_name", str(user["id"]))
    months = json.loads(by_month)
    for month_key, amount in months.items():
        if amount and amount > 0:
            gsheets.upsert_bank_expense(period_from=period_from, period_to=period_to,
                                         month_key=month_key, amount=amount, added_by=added_by)
    return {"ok": True}


@app.post("/api/bank_statement/apply_income")
async def api_bank_statement_apply_income(
    init_data: str = Form(...),
    pin: str = Form(""),
    period_from: str = Form(...),
    period_to: str = Form(...),
    by_month: str = Form(...),  # JSON: {"2026-09": 92603.04, ...}
):
    _authed_user(init_data, pin)
    months = json.loads(by_month)
    for month_key, amount in months.items():
        if amount and amount > 0:
            gsheets.upsert_bank_income(period_from=period_from, period_to=period_to,
                                        month_key=month_key, amount=amount)
    return {"ok": True}


async def _send_receipt_to_telegram(data: bytes, filename: str, content_type: str, *,
                                     date: str, category: str, description: str,
                                     amount: float, currency: str, added_by: str) -> None:
    caption = (
        f"🧾 Чек к расходу\n"
        f"Дата: {date}\n"
        f"Категория: {category}\n"
        f"Описание: {description}\n"
        f"Сумма: {amount} {currency}\n"
        f"Добавил(а): {added_by}"
    )
    file_obj = BufferedInputFile(data, filename=filename)
    if content_type.startswith("image/"):
        await bot.send_photo(ADMIN_ID, photo=file_obj, caption=caption)
    else:
        await bot.send_document(ADMIN_ID, document=file_obj, caption=caption)


# ---------------------------------------------------------------- запуск бота вместе с сервером

@app.on_event("startup")
async def on_startup():
    asyncio.create_task(dp.start_polling(bot))
