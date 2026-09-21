"""
Единый процесс: Telegram-бот (aiogram, polling) + веб-сервер (FastAPI),
который отдаёт Mini App и обслуживает его API. Так проще всего задеплоить
на бесплатный тариф Render/Railway одним сервисом.
"""
import asyncio
import datetime
import os

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup,
                            KeyboardButton, Message, ReplyKeyboardMarkup,
                            WebAppInfo)
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

import auth
import forecast
import gsheets
import users

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
WEBAPP_URL = os.environ["WEBAPP_URL"]

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

app = FastAPI()
app.mount("/app", StaticFiles(directory="webapp", html=True), name="webapp")


# ---------------------------------------------------------------- бот

@dp.message(CommandStart())
async def cmd_start(message: Message):
    user = message.from_user
    if users.is_admin(user.id, ADMIN_ID) or users.is_allowed(user.id):
        kb = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="Открыть приложение", web_app=WebAppInfo(url=f"{WEBAPP_URL}/app/"))]],
            resize_keyboard=True,
        )
        await message.answer("Доступ есть. Открывайте приложение кнопкой ниже.", reply_markup=kb)
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
        kb = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="Открыть приложение", web_app=WebAppInfo(url=f"{WEBAPP_URL}/app/"))]],
            resize_keyboard=True,
        )
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

def _authed_user(init_data: str) -> dict:
    user = auth.verify_init_data(init_data, BOT_TOKEN)
    if not user:
        raise HTTPException(401, "Не удалось подтвердить подлинность запроса Telegram.")
    if not (users.is_admin(user["id"], ADMIN_ID) or users.is_allowed(user["id"])):
        raise HTTPException(403, "Нет доступа. Откройте бота и запросите доступ через /start.")
    return user


@app.get("/api/summary")
async def api_summary(init_data: str, year: int = datetime.date.today().year):
    _authed_user(init_data)
    revenue = gsheets.get_revenue_by_month(year)
    expenses = gsheets.get_expenses_by_month(year)
    by_cat = gsheets.get_expenses_by_category(year)
    today = datetime.date.today()
    upcoming = today.month + 1 if today.month < 12 else 1
    kpis = forecast.build_kpis(revenue, expenses, upcoming if today.month < 12 else 13)

    months = list(range(1, 13))
    return JSONResponse({
        "revenue_by_month": {m: revenue.get(m, 0) for m in months},
        "expenses_by_month": {m: expenses.get(m, 0) for m in months},
        "expenses_by_category": by_cat,
        "kpis": kpis,
    })


@app.post("/api/expense")
async def api_add_expense(request: Request):
    body = await request.json()
    init_data = body.get("init_data", "")
    user = _authed_user(init_data)

    required = ["date", "category", "description", "account", "currency", "amount"]
    if any(k not in body for k in required):
        raise HTTPException(400, "Не хватает полей.")

    gsheets.add_expense(
        date=body["date"],
        category=body["category"],
        description=body["description"],
        account=body["account"],
        currency=body["currency"],
        amount=float(body["amount"]),
        rate=float(body.get("rate") or 1),
        has_doc=body.get("has_doc", "Нет"),
        added_by=user.get("first_name", str(user["id"])),
    )
    return {"ok": True}


# ---------------------------------------------------------------- запуск бота вместе с сервером

@app.on_event("startup")
async def on_startup():
    asyncio.create_task(dp.start_polling(bot))
