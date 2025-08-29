import logging
from contextlib import suppress

from aiogram import Bot, Router
from aiogram.enums import BotCommandScopeType
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import KICKED, ChatMemberUpdatedFilter, Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import BotCommandScopeChat, ChatMemberUpdated, Message, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup
from app.bot.enums.roles import UserRole
from app.bot.keyboards.menu_button import get_main_menu_commands
from app.bot.states.states import LangSG, SupportSG
from app.infrastructure.database.db import (
    add_user,
    change_user_alive_status,
    get_user,
    get_user_lang,
)
from psycopg.connection_async import AsyncConnection

logger = logging.getLogger(__name__)

# Инициализируем роутер уровня модуля
user_router = Router()


# Этот хэндлер срабатывает на команду /start
@user_router.message(CommandStart())
async def process_start_command(
    message: Message, 
    conn: AsyncConnection, 
    bot: Bot, 
    i18n: dict[str, str], 
    state: FSMContext, 
    admin_ids: list[int],
    translations: dict
):
    user_row = await get_user(conn, user_id=message.from_user.id)
    if user_row is None:
        if message.from_user.id in admin_ids:
            user_role = UserRole.ADMIN
        else:
            user_role = UserRole.USER

        await add_user(
            conn,
            user_id=message.from_user.id,
            username=message.from_user.username,
            language=message.from_user.language_code,
            role=user_role
        )
    else:
        user_role = UserRole(user_row[4])
        await change_user_alive_status(
            conn, 
            is_alive=True, 
            user_id=message.from_user.id, 
        )

    if await state.get_state() == LangSG.lang:
        data = await state.get_data()
        with suppress(TelegramBadRequest):
            msg_id = data.get("lang_settings_msg_id")
            if msg_id:
                await bot.edit_message_reply_markup(chat_id=message.from_user.id, message_id=msg_id)
        user_lang = await get_user_lang(conn, user_id=message.from_user.id)
        i18n = translations.get(user_lang)
    
    await bot.set_my_commands(
        commands=get_main_menu_commands(i18n=i18n, role=user_role),
        scope=BotCommandScopeChat(
            type=BotCommandScopeType.CHAT,
            chat_id=message.from_user.id
        )
    )

    await message.answer(text=i18n.get("/start"))
    await state.clear()


# Этот хэндлер срабатывает на команду /help
@user_router.message(Command(commands="help"))
async def process_help_command(message: Message, i18n: dict[str, str]):
    await message.answer(text=i18n.get("/help"))


# Этот хэндлер будет срабатывать на блокировку бота пользователем
@user_router.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=KICKED))
async def process_user_blocked_bot(event: ChatMemberUpdated, conn: AsyncConnection):
    logger.info("User %d has blocked the bot", event.from_user.id)
    await change_user_alive_status(conn, user_id=event.from_user.id, is_alive=False)


@user_router.message(Command(commands="get_bonus"))
async def process_get_bonus_command(message: Message, i18n: dict[str, str]):
    pdf_path = ""

    document = FSInputFile(pdf_path, filename="Бонус.pdf")

    await message.answer_document(
        document=document,
        caption=i18n.get("Вот ваш бонус 🎁")
    )

@user_router.message(Command(commands="signup_lesson"))
async def process_signup_lesson_command(message: Message, i18n: dict[str, str]):
    google_form_url = ""
    
    await message.answer(
    i18n["form_text"].format(url=google_form_url),
    parse_mode="HTML",
    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n["form_button"], url=google_form_url)]
    ])
)
    
@user_router.message(Command(commands="about_malina"))
async def process_about_malina(message: Message, i18n: dict[str, str]):
    text_about = ""


@user_router.message(Command(commands="contact_admin"))
async def process_contact_admin(
    message: Message, 
    state: FSMContext,
    i18n: dict[str, str]
    ):
    await message.answer(
        i18n.get("contact_admin_start", 
                 "Пожалуйста, введите ваше сообщение, и я передам его администратору")
    )

    await state.set_state(SupportSG.waiting_for_message)

@user_router.message(SupportSG.waiting_for_message)
async def process_support_message(
    message: Message, 
    state: FSMContext,
    bot: Bot,
    admin_id: int,
    i18n: dict[str, str]
):
    user_id = message.from_user.id
    username = message.from_user.username
    full_name = message.from_user.full_name

    try:
        admin_text = (
            f"<b>📩 Новое сообщение от пользователя</b>\n"
            f"🆔 <code>{user_id}</code>\n"
            f"👤 <b>{full_name}</b>\n"
            f"🔗 @{username or 'не указан'}\n"
            f"💬 Сообщение:"
        )
        await bot.send_message(admin_id, admin_text, parse_mode="HTML")

        await message.forward(admin_id)

        reply_markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="✉️ Ответить пользователю",
                callback_data=f"reply_{user_id}"
            )]
        ])
        await bot.send_message(
            admin_id,
            "Выберите действие:",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Не удалось отправить сообщение админу {admin_id}: {e}")

    await message.answer(
        i18n.get("contact_admin_success", "✅ Ваше сообщение отправлено администратору. Ожидайте ответа."),
    )
    await state.clear()


@user_router.message(
    lambda m: m.text and m.text.startswith("/reply_") and m.from_user.id == m.bot.workflow_data['admin_id']
)
async def process_admin_reply(message: Message, bot: Bot, i18n: dict[str, str]):
    try:
        parts = message.text.split(" ", 1)
        target_id = int(parts[0].split("_")[1])
        reply_text = parts[1].strip()

        if len(reply_text) < 2:
            await message.answer("❌ Текст ответа слишком короткий.")
            return

        await bot.send_message(
            target_id,
            f"📬 Ответ от администратора:\n\n{reply_text}",
        )
        await message.answer("✅ Сообщение отправлено пользователю.")
    except (IndexError, ValueError):
        await message.answer("❌ Неверный формат. Используйте: `/reply_123456789 текст`", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Ошибка при отправке ответа пользователю {target_id}: {e}")
        await message.answer("❌ Не удалось отправить сообщение.")

