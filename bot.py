"""Telegram-бот: миникошелёк путешественника."""

from __future__ import annotations

import re
from typing import Any

import telebot
from telebot import types

import database as db
from config import TELEGRAM_TOKEN
from countries import resolve_country
from current_api import CurrencyApiError, get_rate

if not TELEGRAM_TOKEN:
    raise RuntimeError("Не задан TELEGRAM_TOKEN в .env")

bot = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode="HTML")

# Состояния диалога: user_id -> dict
user_states: dict[int, dict[str, Any]] = {}

YES_WORDS = {"да", "yes", "y", "+", "ок", "ok", "ага", "угу"}
NO_WORDS = {"нет", "no", "n", "-", "не"}


def fmt_money(value: float) -> str:
    return f"{value:,.2f}".replace(",", " ").replace(".00", "")


def main_menu_keyboard() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("Создать новое путешествие", callback_data="menu:newtrip"),
        types.InlineKeyboardButton("Мои путешествия", callback_data="menu:trips"),
        types.InlineKeyboardButton("Баланс", callback_data="menu:balance"),
        types.InlineKeyboardButton("История расходов", callback_data="menu:history"),
        types.InlineKeyboardButton("Изменить курс", callback_data="menu:setrate"),
    )
    return kb


def confirm_rate_keyboard() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("✅ Да, подходит", callback_data="rate:yes"),
        types.InlineKeyboardButton("❌ Нет, введу сам", callback_data="rate:no"),
    )
    return kb


def expense_confirm_keyboard(amount: float) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    # amount в callback — строка; ограничим точность
    payload = f"{amount:.4f}".rstrip("0").rstrip(".")
    kb.add(
        types.InlineKeyboardButton("✅ Да", callback_data=f"exp:yes:{payload}"),
        types.InlineKeyboardButton("❌ Нет", callback_data="exp:no"),
    )
    return kb


def trips_keyboard(user_id: int) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=1)
    active = db.get_active_trip(user_id)
    active_id = active["id"] if active else None
    for trip in db.list_trips(user_id):
        mark = "✅ " if trip["id"] == active_id else ""
        title = f"{mark}{trip['from_country']} → {trip['to_country']} ({trip['dest_currency']})"
        kb.add(types.InlineKeyboardButton(title, callback_data=f"trip:select:{trip['id']}"))
    kb.add(types.InlineKeyboardButton("⬅️ В меню", callback_data="menu:home"))
    return kb


def send_menu(chat_id: int, text: str | None = None) -> None:
    bot.send_message(
        chat_id,
        text or "Главное меню путешественника:",
        reply_markup=main_menu_keyboard(),
    )


def clear_state(user_id: int) -> None:
    user_states.pop(user_id, None)


def set_state(user_id: int, state: str, **extra: Any) -> None:
    data = user_states.get(user_id, {})
    data["state"] = state
    data.update(extra)
    user_states[user_id] = data


def parse_number(text: str) -> float | None:
    cleaned = text.strip().replace(" ", "").replace(",", ".")
    if not re.fullmatch(r"-?\d+(\.\d+)?", cleaned):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def start_new_trip(chat_id: int, user_id: int) -> None:
    set_state(user_id, "await_from_country")
    bot.send_message(
        chat_id,
        "Создаём новое путешествие.\n"
        "Введите <b>страну отправления</b> (например: Россия):",
    )


def show_balance(chat_id: int, user_id: int) -> None:
    trip = db.get_active_trip(user_id)
    if not trip:
        bot.send_message(chat_id, "Нет активного путешествия. Создайте новое или выберите из списка.")
        send_menu(chat_id)
        return
    bot.send_message(
        chat_id,
        (
            f"Путешествие: <b>{trip['from_country']} → {trip['to_country']}</b>\n"
            f"Курс: 1 {trip['dest_currency']} = {fmt_money(float(trip['rate']))} {trip['home_currency']}\n"
            f"Остаток: <b>{fmt_money(float(trip['balance_dest']))} {trip['dest_currency']}</b> = "
            f"<b>{fmt_money(float(trip['balance_home']))} {trip['home_currency']}</b>"
        ),
        reply_markup=main_menu_keyboard(),
    )


def show_history(chat_id: int, user_id: int) -> None:
    trip = db.get_active_trip(user_id)
    if not trip:
        bot.send_message(chat_id, "Нет активного путешествия — история недоступна.")
        send_menu(chat_id)
        return
    expenses = db.list_expenses(trip["id"], user_id)
    if not expenses:
        bot.send_message(chat_id, "Пока нет расходов в этом путешествии.", reply_markup=main_menu_keyboard())
        return
    lines = [f"История расходов ({trip['to_country']}):"]
    for item in expenses:
        lines.append(
            f"• {fmt_money(float(item['amount_dest']))} {trip['dest_currency']} = "
            f"{fmt_money(float(item['amount_home']))} {trip['home_currency']}"
        )
    bot.send_message(chat_id, "\n".join(lines), reply_markup=main_menu_keyboard())


def show_trips(chat_id: int, user_id: int) -> None:
    trips = db.list_trips(user_id)
    if not trips:
        bot.send_message(chat_id, "У вас пока нет путешествий.", reply_markup=main_menu_keyboard())
        return
    bot.send_message(
        chat_id,
        "Выберите путешествие (активное отмечено ✅):",
        reply_markup=trips_keyboard(user_id),
    )


def begin_set_rate(chat_id: int, user_id: int) -> None:
    trip = db.get_active_trip(user_id)
    if not trip:
        bot.send_message(chat_id, "Сначала создайте или выберите путешествие.")
        send_menu(chat_id)
        return
    set_state(user_id, "await_manual_rate_only", trip_id=trip["id"])
    bot.send_message(
        chat_id,
        (
            f"Текущий курс: 1 {trip['dest_currency']} = "
            f"{fmt_money(float(trip['rate']))} {trip['home_currency']}\n"
            f"Введите новый курс (сколько {trip['home_currency']} за 1 {trip['dest_currency']}):"
        ),
    )


def prepare_rate_step(chat_id: int, user_id: int, draft: dict[str, Any]) -> None:
    home = draft["home_currency"]
    dest = draft["dest_currency"]
    try:
        # сколько dest за 1 home
        api_rate_home_to_dest = get_rate(home, dest)
        # сколько home за 1 dest — удобно для расходов
        rate_home_per_dest = 1.0 / api_rate_home_to_dest if api_rate_home_to_dest else 0.0
    except CurrencyApiError as exc:
        bot.send_message(chat_id, f"Не удалось получить курс: {exc}\nВведите курс вручную.")
        set_state(user_id, "await_manual_rate", **draft)
        return

    if rate_home_per_dest <= 0:
        bot.send_message(chat_id, "API вернуло некорректный курс. Введите курс вручную.")
        set_state(user_id, "await_manual_rate", **draft)
        return

    draft["rate"] = rate_home_per_dest
    draft["api_rate_home_to_dest"] = api_rate_home_to_dest
    set_state(user_id, "await_rate_confirm", **draft)
    bot.send_message(
        chat_id,
        (
            f"Валюты: <b>{home}</b> → <b>{dest}</b>\n"
            f"Курс API: 1 {home} = {fmt_money(api_rate_home_to_dest)} {dest}\n"
            f"(то есть 1 {dest} ≈ {fmt_money(rate_home_per_dest)} {home})\n\n"
            "Подходит ли этот курс?"
        ),
        reply_markup=confirm_rate_keyboard(),
    )


def finish_trip_creation(chat_id: int, user_id: int, amount_home: float) -> None:
    draft = user_states.get(user_id) or {}
    home = draft["home_currency"]
    dest = draft["dest_currency"]
    rate = float(draft["rate"])  # home за 1 dest

    # без повторного запроса к API: считаем по уже полученному/ручному курсу
    api_rate = draft.get("api_rate_home_to_dest")
    if api_rate and not draft.get("manual_rate"):
        amount_dest = amount_home * float(api_rate)
    else:
        amount_dest = amount_home / rate if rate else 0.0

    trip_id = db.create_trip(
        user_id=user_id,
        from_country=draft["from_country"],
        to_country=draft["to_country"],
        home_currency=home,
        dest_currency=dest,
        rate=rate,
        balance_home=amount_home,
        balance_dest=amount_dest,
    )
    clear_state(user_id)
    bot.send_message(
        chat_id,
        (
            f"Путешествие <b>#{trip_id}</b> создано!\n"
            f"{draft['from_country']} → {draft['to_country']}\n"
            f"Стартовый баланс:\n"
            f"<b>{fmt_money(amount_dest)} {dest}</b> = <b>{fmt_money(amount_home)} {home}</b>\n\n"
            "Пишите сумму расходов числом — я посчитаю в домашней валюте."
        ),
        reply_markup=main_menu_keyboard(),
    )


@bot.message_handler(commands=["start", "help", "menu"])
def cmd_start(message: types.Message) -> None:
    db.ensure_user(message.from_user.id)
    clear_state(message.from_user.id)
    bot.send_message(
        message.chat.id,
        "Привет! Я миникошелёк для путешественника.\n"
        "Можно создать поездку, следить за балансом и учитывать расходы.\n"
        "Число в чате = расход в валюте страны пребывания.",
    )
    send_menu(message.chat.id)


@bot.message_handler(commands=["newtrip"])
def cmd_newtrip(message: types.Message) -> None:
    start_new_trip(message.chat.id, message.from_user.id)


@bot.message_handler(commands=["switch"])
def cmd_switch(message: types.Message) -> None:
    show_trips(message.chat.id, message.from_user.id)


@bot.message_handler(commands=["balance"])
def cmd_balance(message: types.Message) -> None:
    show_balance(message.chat.id, message.from_user.id)


@bot.message_handler(commands=["history"])
def cmd_history(message: types.Message) -> None:
    show_history(message.chat.id, message.from_user.id)


@bot.message_handler(commands=["setrate"])
def cmd_setrate(message: types.Message) -> None:
    begin_set_rate(message.chat.id, message.from_user.id)


@bot.callback_query_handler(func=lambda call: True)
def on_callback(call: types.CallbackQuery) -> None:
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    data = call.data or ""
    bot.answer_callback_query(call.id)

    if data == "menu:home":
        clear_state(user_id)
        send_menu(chat_id)
        return
    if data == "menu:newtrip":
        start_new_trip(chat_id, user_id)
        return
    if data == "menu:trips":
        show_trips(chat_id, user_id)
        return
    if data == "menu:balance":
        show_balance(chat_id, user_id)
        return
    if data == "menu:history":
        show_history(chat_id, user_id)
        return
    if data == "menu:setrate":
        begin_set_rate(chat_id, user_id)
        return

    if data.startswith("trip:select:"):
        trip_id = int(data.split(":")[-1])
        if db.set_active_trip(user_id, trip_id):
            trip = db.get_trip(trip_id, user_id)
            bot.send_message(
                chat_id,
                f"Активное путешествие: <b>{trip['from_country']} → {trip['to_country']}</b>",
            )
            show_balance(chat_id, user_id)
        else:
            bot.send_message(chat_id, "Не удалось выбрать путешествие.")
        return

    if data == "rate:yes":
        draft = user_states.get(user_id)
        if not draft or draft.get("state") != "await_rate_confirm":
            bot.send_message(chat_id, "Сессия создания устарела. Начните заново.")
            return
        set_state(user_id, "await_initial_amount", **{k: v for k, v in draft.items() if k != "state"})
        bot.send_message(
            chat_id,
            f"Введите начальную сумму в домашней валюте (<b>{draft['home_currency']}</b>):",
        )
        return

    if data == "rate:no":
        draft = user_states.get(user_id)
        if not draft or draft.get("state") != "await_rate_confirm":
            bot.send_message(chat_id, "Сессия создания устарела. Начните заново.")
            return
        set_state(user_id, "await_manual_rate", **{k: v for k, v in draft.items() if k != "state"})
        bot.send_message(
            chat_id,
            (
                f"Введите свой курс обмена:\n"
                f"сколько <b>{draft['home_currency']}</b> за 1 <b>{draft['dest_currency']}</b>"
            ),
        )
        return

    if data == "exp:no":
        clear_state(user_id)
        bot.send_message(chat_id, "Ок, расход не учитываю.", reply_markup=main_menu_keyboard())
        return

    if data.startswith("exp:yes:"):
        trip = db.get_active_trip(user_id)
        if not trip:
            bot.send_message(chat_id, "Нет активного путешествия.")
            return
        try:
            amount_dest = float(data.split(":")[-1])
        except ValueError:
            bot.send_message(chat_id, "Не удалось прочитать сумму.")
            return
        result = db.add_expense(trip["id"], user_id, amount_dest)
        clear_state(user_id)
        if not result:
            bot.send_message(chat_id, "Не удалось записать расход.")
            return
        bot.send_message(
            chat_id,
            (
                f"Учтено: {fmt_money(result['amount_dest'])} {result['dest_currency']} = "
                f"{fmt_money(result['amount_home'])} {result['home_currency']}\n"
                f"Остаток: <b>{fmt_money(result['balance_dest'])} {result['dest_currency']}</b> = "
                f"<b>{fmt_money(result['balance_home'])} {result['home_currency']}</b>"
            ),
            reply_markup=main_menu_keyboard(),
        )
        return


@bot.message_handler(func=lambda message: True, content_types=["text"])
def on_text(message: types.Message) -> None:
    user_id = message.from_user.id
    chat_id = message.chat.id
    text = (message.text or "").strip()
    db.ensure_user(user_id)

    state_data = user_states.get(user_id) or {}
    state = state_data.get("state")

    # --- создание путешествия ---
    if state == "await_from_country":
        resolved = resolve_country(text)
        if not resolved:
            bot.send_message(
                chat_id,
                "Не узнаю эту страну. Попробуйте ещё раз (например: Россия, Китай, Турция).",
            )
            return
        currency, country = resolved
        set_state(user_id, "await_to_country", from_country=country, home_currency=currency)
        bot.send_message(chat_id, "Теперь введите <b>страну назначения</b>:")
        return

    if state == "await_to_country":
        resolved = resolve_country(text)
        if not resolved:
            bot.send_message(
                chat_id,
                "Не узнаю страну назначения. Пример: Китай, Япония, ОАЭ.",
            )
            return
        dest_currency, to_country = resolved
        home_currency = state_data["home_currency"]
        if dest_currency == home_currency:
            bot.send_message(chat_id, "Валюты совпадают. Выберите другую страну назначения.")
            return

        draft = {
            "from_country": state_data["from_country"],
            "to_country": to_country,
            "home_currency": home_currency,
            "dest_currency": dest_currency,
        }
        bot.send_message(chat_id, "Запрашиваю курс в API…")
        prepare_rate_step(chat_id, user_id, draft)
        return

    if state == "await_manual_rate":
        rate = parse_number(text)
        if rate is None or rate <= 0:
            bot.send_message(chat_id, "Нужно положительное число — курс обмена.")
            return
        draft = {k: v for k, v in state_data.items() if k != "state"}
        draft["rate"] = rate
        draft["manual_rate"] = True
        set_state(user_id, "await_initial_amount", **draft)
        bot.send_message(
            chat_id,
            f"Курс сохранён. Введите начальную сумму в <b>{draft['home_currency']}</b>:",
        )
        return

    if state == "await_manual_rate_only":
        rate = parse_number(text)
        if rate is None or rate <= 0:
            bot.send_message(chat_id, "Нужно положительное число.")
            return
        trip_id = state_data.get("trip_id")
        if not trip_id or not db.update_rate(trip_id, user_id, rate):
            bot.send_message(chat_id, "Не удалось обновить курс.")
            clear_state(user_id)
            return
        clear_state(user_id)
        bot.send_message(chat_id, "Курс обновлён.")
        show_balance(chat_id, user_id)
        return

    if state == "await_initial_amount":
        amount = parse_number(text)
        if amount is None or amount <= 0:
            bot.send_message(chat_id, "Введите положительную сумму числом.")
            return
        finish_trip_creation(chat_id, user_id, amount)
        return

    if state == "await_rate_confirm":
        lower = text.lower()
        if lower in YES_WORDS:
            set_state(
                user_id,
                "await_initial_amount",
                **{k: v for k, v in state_data.items() if k != "state"},
            )
            bot.send_message(
                chat_id,
                f"Введите начальную сумму в <b>{state_data['home_currency']}</b>:",
            )
            return
        if lower in NO_WORDS:
            set_state(
                user_id,
                "await_manual_rate",
                **{k: v for k, v in state_data.items() if k != "state"},
            )
            bot.send_message(
                chat_id,
                (
                    f"Введите свой курс: сколько <b>{state_data['home_currency']}</b> "
                    f"за 1 <b>{state_data['dest_currency']}</b>"
                ),
            )
            return
        bot.send_message(chat_id, "Нажмите кнопку или ответьте «да» / «нет».")
        return

    # --- расход числом ---
    amount = parse_number(text)
    if amount is not None:
        if amount <= 0:
            bot.send_message(chat_id, "Сумма расхода должна быть больше нуля.")
            return
        trip = db.get_active_trip(user_id)
        if not trip:
            bot.send_message(
                chat_id,
                "Сначала создайте путешествие — иначе некуда записывать расход.",
                reply_markup=main_menu_keyboard(),
            )
            return
        home_amount = amount * float(trip["rate"])
        set_state(user_id, "await_expense_confirm", pending_amount=amount)
        bot.send_message(
            chat_id,
            (
                f"{fmt_money(amount)} {trip['dest_currency']} = "
                f"{fmt_money(home_amount)} {trip['home_currency']}\n"
                "Учесть как расход?"
            ),
            reply_markup=expense_confirm_keyboard(amount),
        )
        return

    bot.send_message(
        chat_id,
        "Не понял сообщение. Откройте меню или отправьте сумму расхода числом.",
        reply_markup=main_menu_keyboard(),
    )


def main() -> None:
    db.init_db()
    print("Бот запущен…")
    bot.infinity_polling(skip_pending=True)


if __name__ == "__main__":
    main()
