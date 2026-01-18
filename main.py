import asyncio
import logging
import aiosqlite
from datetime import datetime, timedelta
from typing import Optional
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.state import State, StatesGroup, default_state
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup,
    KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardRemove
)

# === КОНФИГУРАЦИЯ ===
BOT_TOKEN = "8104721228:AAHPnw-PHAMYMJARBvBULtm5_SeFcrhfm3g"
ADMIN_IDS = [931410785]
PLATFORM_FEE = 99  # Фиксированный сервисный сбор 99 ₽
PAYMENT_LINK = "https://yoomoney.ru/pay/..."  # Захардкоженная ссылка на оплату

# Список доступных городов
CITIES = [
    "Москва",
    "Санкт-Петербург", 
    "Казань",
    "Екатеринбург",
    "Новосибирск",
    "Краснодар",
    "Ростов-на-Дону"
]

# === FSM СТРУКТУРА ===
class MainStates(StatesGroup):
    MAIN_MENU = State()
    VIEWING_EVENT = State()

class CreateEventStates(StatesGroup):
    TYPE = State()
    TYPE_OTHER = State()
    CITY = State()
    DATE_TIME = State()
    PRICE = State()
    MIN_PARTICIPANTS = State()
    MAX_PARTICIPANTS = State()
    CONFIRMATION = State()

class SearchEventsStates(StatesGroup):
    ENTER_CITY = State()
    SELECT_EVENT = State()

class JoinEventStates(StatesGroup):
    PAYMENT_INFO = State()

# === ИНИЦИАЛИЗАЦИЯ ===
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

# === БАЗА ДАННЫХ ===
class Database:
    def __init__(self, db_path='vibez.db'):
        self.db_path = db_path

    async def init_db(self):
        async with aiosqlite.connect(self.db_path) as db:
            # Пользователи
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER UNIQUE,
                    username TEXT,
                    rating REAL DEFAULT 5.0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # События
            await db.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT,
                    city TEXT,
                    date_time TEXT,
                    price INTEGER,
                    min_participants INTEGER,
                    max_participants INTEGER,
                    status TEXT DEFAULT 'ACTIVE',
                    chat_id INTEGER,
                    creator_id INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (creator_id) REFERENCES users(id)
                )
            """)
            
            # Участники событий
            await db.execute("""
                CREATE TABLE IF NOT EXISTS event_participants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id INTEGER,
                    user_id INTEGER,
                    status TEXT DEFAULT 'PENDING',
                    invited_by INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (event_id) REFERENCES events(id),
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """)
            
            # Черный список
            await db.execute("""
                CREATE TABLE IF NOT EXISTS blacklist (
                    user_id INTEGER PRIMARY KEY,
                    reason TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """)
            
            await db.commit()

    async def add_user(self, telegram_id, username):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO users (telegram_id, username) VALUES (?, ?)",
                (telegram_id, username or "")
            )
            await db.commit()

    async def get_user_id(self, telegram_id):
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT id FROM users WHERE telegram_id = ?",
                (telegram_id,)
            )
            result = await cursor.fetchone()
            return result[0] if result else None

    async def create_event(self, event_data, creator_telegram_id):
        async with aiosqlite.connect(self.db_path) as db:
            creator_id = await self.get_user_id(creator_telegram_id)
            
            cursor = await db.execute("""
                INSERT INTO events (
                    type, city, date_time, price, 
                    min_participants, max_participants, creator_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                event_data['type'],
                event_data['city'],
                event_data['date_time'],
                event_data['price'],
                event_data['min_participants'],
                event_data['max_participants'],
                creator_id
            ))
            
            await db.commit()
            return cursor.lastrowid

    async def get_events_by_city(self, city):
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                SELECT 
                    e.id, e.type, e.price, e.date_time, 
                    e.min_participants,
                    (SELECT COUNT(*) FROM event_participants ep 
                     WHERE ep.event_id = e.id AND ep.status = 'CONFIRMED') as confirmed_count
                FROM events e
                WHERE e.city = ? AND e.status = 'ACTIVE'
                ORDER BY e.created_at DESC
            """, (city,))
            
            return await cursor.fetchall()

    async def get_event_details(self, event_id):
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                SELECT 
                    e.type, e.city, e.date_time, e.price, 
                    e.min_participants, e.max_participants, e.status, 
                    e.chat_id, e.creator_id, e.created_at, 
                    u.username,
                    (SELECT COUNT(*) FROM event_participants ep 
                     WHERE ep.event_id = e.id AND ep.status = 'CONFIRMED') as confirmed_count
                FROM events e
                JOIN users u ON e.creator_id = u.id
                WHERE e.id = ?
            """, (event_id,))
            
            return await cursor.fetchone()

    async def add_participant(self, event_id, user_telegram_id, invited_by=None):
        async with aiosqlite.connect(self.db_path) as db:
            user_id = await self.get_user_id(user_telegram_id)
            
            # Проверяем, не записан ли уже
            cursor = await db.execute("""
                SELECT id FROM event_participants 
                WHERE event_id = ? AND user_id = ?
            """, (event_id, user_id))
            
            if await cursor.fetchone():
                return False
            
            # Добавляем участника
            await db.execute("""
                INSERT INTO event_participants (event_id, user_id, invited_by, status)
                VALUES (?, ?, ?, 'PENDING')
            """, (event_id, user_id, invited_by))
            
            await db.commit()
            return True

    async def confirm_participant(self, event_id, user_telegram_id):
        async with aiosqlite.connect(self.db_path) as db:
            user_id = await self.get_user_id(user_telegram_id)
            
            await db.execute("""
                UPDATE event_participants 
                SET status = 'CONFIRMED' 
                WHERE event_id = ? AND user_id = ?
            """, (event_id, user_id))
            
            # Проверяем кворум
            cursor = await db.execute("""
                SELECT e.min_participants 
                FROM events e 
                WHERE e.id = ?
            """, (event_id,))
            
            min_participants = (await cursor.fetchone())[0]
            
            # Считаем подтвержденных участников
            cursor = await db.execute("""
                SELECT COUNT(*) 
                FROM event_participants 
                WHERE event_id = ? AND status = 'CONFIRMED'
            """, (event_id,))
            
            confirmed_count = (await cursor.fetchone())[0]
            
            # Если достигли кворума - обновляем статус события
            if confirmed_count >= min_participants:
                await db.execute("""
                    UPDATE events 
                    SET status = 'CONFIRMED' 
                    WHERE id = ?
                """, (event_id,))
            
            await db.commit()
            return True

    async def get_user_participations(self, user_telegram_id):
        async with aiosqlite.connect(self.db_path) as db:
            user_id = await self.get_user_id(user_telegram_id)
            
            cursor = await db.execute("""
                SELECT 
                    e.type, e.city, e.date_time, ep.status, e.id
                FROM event_participants ep
                JOIN events e ON ep.event_id = e.id
                WHERE ep.user_id = ? AND ep.status = 'CONFIRMED'
                ORDER BY ep.created_at DESC
            """, (user_id,))
            
            return await cursor.fetchall()

    async def get_all_events(self):
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                SELECT 
                    e.id, e.type, e.city, e.date_time, e.status,
                    u.username,
                    (SELECT COUNT(*) FROM event_participants ep 
                     WHERE ep.event_id = e.id AND ep.status = 'CONFIRMED') as participants_count
                FROM events e
                JOIN users u ON e.creator_id = u.id
                ORDER BY e.created_at DESC
            """)
            
            return await cursor.fetchall()

    async def delete_event(self, event_id):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM events WHERE id = ?", (event_id,))
            await db.execute("DELETE FROM event_participants WHERE event_id = ?", (event_id,))
            await db.commit()

    async def get_event_participants_count(self, event_id):
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                SELECT COUNT(*) 
                FROM event_participants 
                WHERE event_id = ? AND status = 'CONFIRMED'
            """, (event_id,))
            
            result = await cursor.fetchone()
            return result[0] if result else 0

    async def is_user_confirmed(self, event_id, user_telegram_id):
        async with aiosqlite.connect(self.db_path) as db:
            user_id = await self.get_user_id(user_telegram_id)
            cursor = await db.execute("""
                SELECT id FROM event_participants 
                WHERE event_id = ? AND user_id = ? AND status = 'CONFIRMED'
            """, (event_id, user_id))
            return await cursor.fetchone() is not None

    async def ban_user(self, telegram_id, reason):
        async with aiosqlite.connect(self.db_path) as db:
            user_id = await self.get_user_id(telegram_id)
            if user_id:
                await db.execute("""
                    INSERT OR REPLACE INTO blacklist (user_id, reason) VALUES (?, ?)
                """, (user_id, reason))
                await db.commit()

    async def update_event_price(self, event_id, new_price):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE events SET price = ? WHERE id = ?", (new_price, event_id))
            await db.commit()

    async def update_event_status(self, event_id, status):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE events SET status = ? WHERE id = ?", (status, event_id))
            await db.commit()

db = Database()

# === КЛАВИАТУРЫ ===
def get_main_menu_kb():
    """Главное меню"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔍 Найти событие")],
            [KeyboardButton(text="➕ Создать событие")],
            [KeyboardButton(text="👤 Мой профиль")],
            [KeyboardButton(text="ℹ️ Как пользоваться")]
        ],
        resize_keyboard=True
    )

def get_back_cancel_kb():
    """Кнопки Назад/Отмена для FSM"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⬅️ Назад"), KeyboardButton(text="❌ Отмена")]
        ],
        resize_keyboard=True
    )

def get_event_types_kb():
    """Типы событий"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎉 Туса"), KeyboardButton(text="🎳 Страйкбол")],
            [KeyboardButton(text="🔫 Пейнтбол"), KeyboardButton(text="🤝 Другое")],
            [KeyboardButton(text="⬅️ Назад"), KeyboardButton(text="❌ Отмена")]
        ],
        resize_keyboard=True
    )

def get_cities_kb():
    """Клавиатура с городами"""
    buttons = []
    row = []
    for i, city in enumerate(CITIES, 1):
        row.append(KeyboardButton(text=city))
        if i % 2 == 0 or i == len(CITIES):
            buttons.append(row)
            row = []
    buttons.append([KeyboardButton(text="⬅️ Назад"), KeyboardButton(text="❌ Отмена")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_confirm_kb():
    """Кнопки для подтверждения"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Да, создать событие")],
            [KeyboardButton(text="✏️ Нет, исправить")],
            [KeyboardButton(text="⬅️ Назад"), KeyboardButton(text="❌ Отмена")]
        ],
        resize_keyboard=True
    )

def get_event_list_kb(events):
    """Список событий"""
    buttons = []
    for event in events:
        event_id, event_type, price, date_time, min_participants, confirmed_count = event
        event_type_emoji = {
            "Туса": "🎉",
            "Страйкбол": "🎳",
            "Пейнтбол": "🔫"
        }.get(event_type, "🤝")
        
        buttons.append([
            InlineKeyboardButton(
                text=f"{event_type_emoji} {event_type[:15]} • {price}₽ • {confirmed_count}/{min_participants}",
                callback_data=f"view_event_{event_id}"
            )
        ])
    buttons.append([InlineKeyboardButton(text="⬅️ В главное меню", callback_data="back_to_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_event_details_kb(event_id, user_telegram_id, is_confirmed=False):
    """Кнопки для деталей события"""
    buttons = []
    
    if not is_confirmed:
        buttons.append([InlineKeyboardButton(text="✅ Записаться", callback_data=f"join_{event_id}")])
    
    buttons.append([
        InlineKeyboardButton(text="🔗 Пригласить друга", 
                           callback_data=f"invite_{event_id}_{user_telegram_id}")
    ])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад к списку", callback_data="back_to_search")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_payment_kb(event_id):
    """Кнопки для оплаты"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить 99 ₽", url=PAYMENT_LINK)],
            [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"paid_{event_id}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"back_to_event_{event_id}")]
        ]
    )

def get_admin_kb():
    """Админ-панель"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📋 Все события", callback_data="admin_all_events")],
            [InlineKeyboardButton(text="✏️ Изменить цену", callback_data="admin_change_price")],
            [InlineKeyboardButton(text="🔄 Изменить статус", callback_data="admin_change_status")],
            [InlineKeyboardButton(text="🗑️ Удалить событие", callback_data="admin_delete_event")],
            [InlineKeyboardButton(text="🚫 Заблокировать пользователя", callback_data="admin_ban_user")]
        ]
    )

# === ОБРАБОТЧИКИ ===

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    """Старт бота с обработкой инвайт-ссылок"""
    await db.add_user(message.from_user.id, message.from_user.username)
    
    # Проверяем наличие инвайт-параметра
    args = message.text.split()
    if len(args) > 1 and args[1].startswith("invite_"):
        try:
            parts = args[1].split("_")
            event_id = int(parts[1])
            inviter_id = int(parts[2]) if len(parts) > 2 else None
            
            await state.update_data(inviter_id=inviter_id)
            
            # Показываем событие
            event = await db.get_event_details(event_id)
            if event:
                await state.set_state(MainStates.VIEWING_EVENT)
                await state.update_data(current_event_id=event_id)
                
                (event_type, city, date_time, price, min_participants, 
                 max_participants, status, chat_id, creator_id, 
                 created_at, creator_username, confirmed_count) = event
                
                text = (
                    f"🎉 <b>Вас пригласили на событие!</b>\n\n"
                    f"📋 <b>Детали события:</b>\n"
                    f"🎯 <b>Тип:</b> {event_type}\n"
                    f"🏙️ <b>Город:</b> {city}\n"
                    f"📅 <b>Дата:</b> {date_time}\n"
                    f"💰 <b>Цена:</b> {price} руб.\n"
                    f"👤 <b>Инициатор:</b> @{creator_username}\n"
                    f"✅ <b>Подтверждено:</b> {confirmed_count}/{min_participants} участников\n\n"
                    "<i>Чтобы присоединиться, нажмите кнопку 'Записаться'</i>"
                )
                
                is_confirmed = await db.is_user_confirmed(event_id, message.from_user.id)
                await message.answer(
                    text, 
                    reply_markup=get_event_details_kb(event_id, message.from_user.id, is_confirmed), 
                    parse_mode="HTML"
                )
                return
        except:
            pass
    
    # Обычный старт
    await state.set_state(MainStates.MAIN_MENU)
    await message.answer(
        "🎉 <b>Добро пожаловать в VIBEZ</b>\n\n"
        "Платформа для реальных событий и встреч.\n\n"
        "Выберите действие:",
        reply_markup=get_main_menu_kb(),
        parse_mode="HTML"
    )

@router.message(F.text == "ℹ️ Как пользоваться", MainStates.MAIN_MENU)
async def how_to_use(message: Message):
    """Экран с информацией о работе сервиса"""
    text = """
🎯 <b>Что такое VIBEZ?</b>
VIBEZ — это платформа для организации и поиска реальных встреч и событий.

💰 <b>Что такое сервисный сбор?</b>
При бронировании вы оплачиваете <b>сервисный сбор платформы VIBEZ 99 ₽</b>.
<b>ВАЖНО:</b> Эти деньги <b>НЕ переводятся инициатору события</b>.

🤝 <b>Как работает бронирование?</b>
1. Вы находите событие и нажимаете "Записаться"
2. Оплачиваете сервисный сбор 99 ₽ через платёжную ссылку
3. Нажимаете кнопку "Я оплатил"
4. Вы сразу становитесь участником события

📅 <b>Как происходит встреча?</b>
1. В назначенное время вы встречаетесь с участниками
2. Инициатор организует мероприятие
3. Все дополнительные расходы решаются на месте

❌ <b>Что если событие не состоялось?</b>
Если не набралось минимальное количество участников:
• Вы получите уведомление
• Сервисный сбор будет возвращен
• Вы можете выбрать другое событие

🔒 <b>Почему это безопасно?</b>
• Все участники проходят через платформу
• Вы видите инициатора события (его Telegram)
• Платформа гарантирует возврат средств если событие отменено

💡 <b>Советы:</b>
• Приглашайте друзей — так событие состоится быстрее
• Всегда уточняйте детали в чате события
• Сообщайте о проблемах администрации
    """
    
    await message.answer(text, parse_mode="HTML")

@router.message(F.text == "⬅️ Назад", StateFilter("*"))
async def handle_back(message: Message, state: FSMContext):
    """Обработка кнопки 'Назад'"""
    current_state = await state.get_state()
    
    # Логика возврата по шагам
    if current_state == CreateEventStates.TYPE_OTHER:
        await state.set_state(CreateEventStates.TYPE)
        await message.answer(
            "[Создание события 1/6]\n\n"
            "Выберите тип события:",
            reply_markup=get_event_types_kb()
        )
    
    elif current_state == CreateEventStates.CITY:
        await state.set_state(CreateEventStates.TYPE)
        await message.answer(
            "[Создание события 1/6]\n\n"
            "Выберите тип события:",
            reply_markup=get_event_types_kb()
        )
    
    elif current_state == CreateEventStates.DATE_TIME:
        await state.set_state(CreateEventStates.CITY)
        await message.answer(
            "[Создание события 2/6]\n\n"
            "Выберите город:",
            reply_markup=get_cities_kb()
        )
    
    elif current_state == CreateEventStates.PRICE:
        await state.set_state(CreateEventStates.DATE_TIME)
        await message.answer(
            "[Создание события 3/6]\n\n"
            "Введите дату и время в формате: ДД.ММ.ГГГГ ЧЧ:ММ\n\n"
            "Например: 25.12.2024 19:00", 
            reply_markup=get_back_cancel_kb()
        )
    
    elif current_state == CreateEventStates.MIN_PARTICIPANTS:
        await state.set_state(CreateEventStates.PRICE)
        await message.answer(
            "[Создание события 4/6]\n\n"
            "Введите цену участия (только число):", 
            reply_markup=get_back_cancel_kb()
        )
    
    elif current_state == CreateEventStates.MAX_PARTICIPANTS:
        await state.set_state(CreateEventStates.MIN_PARTICIPANTS)
        await message.answer(
            "[Создание события 5/6]\n\n"
            "Введите минимальное количество участников:", 
            reply_markup=get_back_cancel_kb()
        )
    
    elif current_state == CreateEventStates.CONFIRMATION:
        await state.set_state(CreateEventStates.MAX_PARTICIPANTS)
        await message.answer(
            "[Создание события 6/6]\n\n"
            "Введите максимальное количество участников:", 
            reply_markup=get_back_cancel_kb()
        )
    
    elif current_state == SearchEventsStates.ENTER_CITY:
        await state.set_state(MainStates.MAIN_MENU)
        await message.answer("Выберите действие:", reply_markup=get_main_menu_kb())
    
    elif current_state == JoinEventStates.PAYMENT_INFO:
        data = await state.get_data()
        event_id = data.get('join_event_id')
        if event_id:
            event = await db.get_event_details(event_id)
            if event:
                await state.set_state(MainStates.VIEWING_EVENT)
                (event_type, city, date_time, price, min_participants, 
                 max_participants, status, chat_id, creator_id, 
                 created_at, creator_username, confirmed_count) = event
                
                is_confirmed = await db.is_user_confirmed(event_id, message.from_user.id)
                
                text = (
                    f"📋 <b>Детали события:</b>\n\n"
                    f"🎯 <b>Тип:</b> {event_type}\n"
                    f"🏙️ <b>Город:</b> {city}\n"
                    f"📅 <b>Дата:</b> {date_time}\n"
                    f"💰 <b>Цена:</b> {price} руб.\n"
                    f"👤 <b>Инициатор:</b> @{creator_username}\n"
                    f"✅ <b>Подтверждено:</b> {confirmed_count}/{min_participants} участников\n"
                    f"👥 <b>Максимум:</b> {max_participants} участников\n"
                    f"📊 <b>Статус:</b> {status}\n"
                )
                
                await message.answer(
                    text, 
                    reply_markup=get_event_details_kb(event_id, message.from_user.id, is_confirmed), 
                    parse_mode="HTML"
                )
                return
    
    # Во всех остальных случаях - в главное меню
    await state.set_state(MainStates.MAIN_MENU)
    await message.answer("Выберите действие:", reply_markup=get_main_menu_kb())

@router.message(F.text == "❌ Отмена", StateFilter("*"))
async def handle_cancel(message: Message, state: FSMContext):
    """Обработка кнопки 'Отмена'"""
    await state.clear()
    await state.set_state(MainStates.MAIN_MENU)
    await message.answer(
        "❌ Действие отменено.\n\n"
        "Выберите действие:",
        reply_markup=get_main_menu_kb()
    )

# === ГЛАВНОЕ МЕНЮ ===

@router.message(F.text == "🔍 Найти событие", MainStates.MAIN_MENU)
async def start_search(message: Message, state: FSMContext):
    """Начало поиска событий"""
    await state.set_state(SearchEventsStates.ENTER_CITY)
    await message.answer(
        "🔍 <b>Поиск событий</b>\n\n"
        "🏙️ Выберите город для поиска:",
        reply_markup=get_cities_kb(),
        parse_mode="HTML"
    )

@router.message(F.text == "➕ Создать событие", MainStates.MAIN_MENU)
async def start_create_event(message: Message, state: FSMContext):
    """Начало создания события"""
    await state.set_state(CreateEventStates.TYPE)
    await message.answer(
        "[Создание события 1/6]\n\n"
        "➕ <b>Создание события</b>\n\n"
        "🎯 Выберите тип события:",
        reply_markup=get_event_types_kb(),
        parse_mode="HTML"
    )

@router.message(F.text == "👤 Мой профиль", MainStates.MAIN_MENU)
async def my_profile(message: Message, state: FSMContext):
    """Просмотр профиля"""
    participations = await db.get_user_participations(message.from_user.id)
    
    if not participations:
        await message.answer(
            "📭 <b>Ваш профиль</b>\n\n"
            "Вы еще не участвовали в событиях.\n"
            "Начните с поиска или создайте свое событие!",
            reply_markup=get_main_menu_kb(),
            parse_mode="HTML"
        )
        return
    
    text = "📋 <b>Ваши участия:</b>\n\n"
    for part in participations:
        event_type, city, date_time, status, event_id = part
        
        status_emoji = {
            'PENDING': '⏳',
            'CONFIRMED': '✅',
            'CANCELLED': '❌'
        }.get(status, '❓')
        
        text += (
            f"🎯 <b>{event_type}</b> в {city}\n"
            f"📅 {date_time}\n"
            f"📊 Статус: {status_emoji} {status}\n"
            f"🔢 ID: {event_id}\n"
            f"{'-'*20}\n\n"
        )
    
    await message.answer(text, reply_markup=get_main_menu_kb(), parse_mode="HTML")

# === ПОИСК СОБЫТИЙ ===

@router.message(SearchEventsStates.ENTER_CITY)
async def process_search_city(message: Message, state: FSMContext):
    """Обработка выбора города для поиска"""
    city = message.text.strip()
    
    if city not in CITIES:
        await message.answer(
            "❌ Пожалуйста, выберите город из списка:",
            reply_markup=get_cities_kb()
        )
        return
    
    # Сохраняем город в состоянии
    await state.update_data(search_city=city)
    
    # Ищем события в БД
    events = await db.get_events_by_city(city)
    
    if not events:
        await message.answer(
            f"😔 <b>В городе {city} пока нет активных событий.</b>\n\n"
            f"Попробуйте другой город или создайте свое событие!",
            reply_markup=get_cities_kb(),
            parse_mode="HTML"
        )
        return
    
    # Переходим к выбору события
    await state.set_state(SearchEventsStates.SELECT_EVENT)
    
    await message.answer(
        f"✅ <b>Найдено событий в {city}: {len(events)}</b>\n\n"
        f"Выберите событие для просмотра деталей:",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="HTML"
    )
    
    # Отправляем список событий
    await message.answer(
        "📋 <b>Список событий:</b>",
        reply_markup=get_event_list_kb(events),
        parse_mode="HTML"
    )

# === ПРОСМОТР СОБЫТИЯ ===

@router.callback_query(F.data.startswith("view_event_"))
async def view_event_details(callback: CallbackQuery, state: FSMContext):
    """Просмотр деталей события"""
    event_id = int(callback.data.split("_")[2])
    
    # Получаем детали события
    event = await db.get_event_details(event_id)
    
    if not event:
        await callback.answer("❌ Событие не найдено")
        await state.set_state(MainStates.MAIN_MENU)
        await callback.message.answer(
            "Событие не найдено. Вернитесь в главное меню:",
            reply_markup=get_main_menu_kb()
        )
        return
    
    # Распаковываем данные
    (event_type, city, date_time, price, min_participants, 
     max_participants, status, chat_id, creator_id, 
     created_at, creator_username, confirmed_count) = event
    
    # Сохраняем event_id в состоянии
    await state.update_data(current_event_id=event_id)
    await state.set_state(MainStates.VIEWING_EVENT)
    
    # Проверяем, является ли пользователь подтвержденным участником
    is_confirmed = await db.is_user_confirmed(event_id, callback.from_user.id)
    
    # Формируем текст
    text = (
        f"📋 <b>Детали события:</b>\n\n"
        f"🎯 <b>Тип:</b> {event_type}\n"
        f"🏙️ <b>Город:</b> {city}\n"
        f"📅 <b>Дата:</b> {date_time}\n"
        f"💰 <b>Цена:</b> {price} руб.\n"
        f"👤 <b>Инициатор:</b> @{creator_username}\n"
        f"✅ <b>Подтверждено:</b> {confirmed_count}/{min_participants} участников\n"
        f"👥 <b>Максимум:</b> {max_participants} участников\n"
        f"📊 <b>Статус:</b> {status}\n\n"
    )
    
    if is_confirmed:
        text += "✅ <b>Вы уже участвуете в этом событии</b>"
    else:
        text += "<i>Для записи нажмите кнопку 'Записаться'</i>"
    
    if callback.message.text:
        await callback.message.edit_text(
            text, 
            reply_markup=get_event_details_kb(event_id, callback.from_user.id, is_confirmed), 
            parse_mode="HTML"
        )
    else:
        await callback.message.answer(
            text, 
            reply_markup=get_event_details_kb(event_id, callback.from_user.id, is_confirmed), 
            parse_mode="HTML"
        )
    
    await callback.answer()

# === СОЗДАНИЕ СОБЫТИЯ ===

@router.message(CreateEventStates.TYPE)
async def process_event_type(message: Message, state: FSMContext):
    """Обработка выбора типа события"""
    if message.text not in ["🎉 Туса", "🎳 Страйкбол", "🔫 Пейнтбол", "🤝 Другое"]:
        await message.answer(
            "[Создание события 1/6]\n\n"
            "❌ Пожалуйста, выберите тип из предложенных вариантов:",
            reply_markup=get_event_types_kb()
        )
        return
    
    if message.text == "🤝 Другое":
        await state.set_state(CreateEventStates.TYPE_OTHER)
        await message.answer(
            "[Создание события 1/6]\n\n"
            "Напишите название события:",
            reply_markup=get_back_cancel_kb()
        )
        return
    
    # Убираем эмодзи для сохранения в БД
    event_type = message.text[2:] if message.text.startswith(("🎉", "🎳", "🔫", "🤝")) else message.text
    event_type = event_type.strip()
    
    await state.update_data(type=event_type)
    await state.set_state(CreateEventStates.CITY)
    
    await message.answer(
        "[Создание события 2/6]\n\n"
        f"🎯 <b>Тип:</b> {event_type}\n\n"
        f"🏙️ Выберите город проведения события:",
        reply_markup=get_cities_kb(),
        parse_mode="HTML"
    )

@router.message(CreateEventStates.TYPE_OTHER)
async def process_event_type_other(message: Message, state: FSMContext):
    """Обработка ввода названия для типа 'Другое'"""
    event_type = message.text.strip()
    
    if len(event_type) < 2:
        await message.answer(
            "[Создание события 1/6]\n\n"
            "❌ Название события слишком короткое.\n"
            "Введите название снова:",
            reply_markup=get_back_cancel_kb()
        )
        return
    
    await state.update_data(type=event_type)
    await state.set_state(CreateEventStates.CITY)
    
    await message.answer(
        "[Создание события 2/6]\n\n"
        f"🎯 <b>Тип:</b> {event_type}\n\n"
        f"🏙️ Выберите город проведения события:",
        reply_markup=get_cities_kb(),
        parse_mode="HTML"
    )

@router.message(CreateEventStates.CITY)
async def process_event_city(message: Message, state: FSMContext):
    """Обработка выбора города"""
    city = message.text.strip()
    
    if city not in CITIES:
        await message.answer(
            "[Создание события 2/6]\n\n"
            "❌ Пожалуйста, выберите город из списка:",
            reply_markup=get_cities_kb()
        )
        return
    
    await state.update_data(city=city)
    await state.set_state(CreateEventStates.DATE_TIME)
    
    await message.answer(
        "[Создание события 3/6]\n\n"
        f"🏙️ <b>Город:</b> {city}\n\n"
        f"📅 Введите дату и время в формате: ДД.ММ.ГГГГ ЧЧ:ММ\n\n"
        f"Например: 25.12.2024 19:00",
        reply_markup=get_back_cancel_kb(),
        parse_mode="HTML"
    )

@router.message(CreateEventStates.DATE_TIME)
async def process_event_datetime(message: Message, state: FSMContext):
    """Обработка ввода даты и времени"""
    date_time_str = message.text.strip()
    
    try:
        # Парсим дату
        event_datetime = datetime.strptime(date_time_str, "%d.%m.%Y %H:%M")
        
        # Проверяем, что дата не в прошлом
        if event_datetime < datetime.now():
            await message.answer(
                "[Создание события 3/6]\n\n"
                "❌ Дата не может быть в прошлом.\n"
                "Введите будущую дату и время:",
                reply_markup=get_back_cancel_kb()
            )
            return
            
    except ValueError:
        await message.answer(
            "[Создание события 3/6]\n\n"
            "❌ Неверный формат даты.\n"
            "Введите дату и время в формате: ДД.ММ.ГГГГ ЧЧ:ММ\n\n"
            "Например: 25.12.2024 19:00",
            reply_markup=get_back_cancel_kb()
        )
        return
    
    await state.update_data(date_time=date_time_str)
    await state.set_state(CreateEventStates.PRICE)
    
    await message.answer(
        "[Создание события 4/6]\n\n"
        f"📅 <b>Дата и время:</b> {date_time_str}\n\n"
        f"💰 Введите цену участия (только число, в рублях):",
        reply_markup=get_back_cancel_kb(),
        parse_mode="HTML"
    )

@router.message(CreateEventStates.PRICE)
async def process_event_price(message: Message, state: FSMContext):
    """Обработка ввода цены"""
    try:
        price = int(message.text)
        if price <= 0:
            await message.answer(
                "[Создание события 4/6]\n\n"
                "❌ Цена должна быть положительным числом.\n"
                "Введите снова:",
                reply_markup=get_back_cancel_kb()
            )
            return
    except ValueError:
        await message.answer(
            "[Создание события 4/6]\n\n"
            "❌ Пожалуйста, введите число (например: 1000):",
            reply_markup=get_back_cancel_kb()
        )
        return
    
    await state.update_data(price=price)
    await state.set_state(CreateEventStates.MIN_PARTICIPANTS)
    
    await message.answer(
        "[Создание события 5/6]\n\n"
        f"💰 <b>Цена:</b> {price} руб.\n\n"
        f"👥 Введите минимальное количество участников:",
        reply_markup=get_back_cancel_kb(),
        parse_mode="HTML"
    )

@router.message(CreateEventStates.MIN_PARTICIPANTS)
async def process_min_participants(message: Message, state: FSMContext):
    """Обработка ввода минимального количества участников"""
    try:
        min_participants = int(message.text)
        if min_participants < 2:
            await message.answer(
                "[Создание события 5/6]\n\n"
                "❌ Минимум должно быть 2 участника.\n"
                "Введите снова:",
                reply_markup=get_back_cancel_kb()
            )
            return
    except ValueError:
        await message.answer(
            "[Создание события 5/6]\n\n"
            "❌ Пожалуйста, введите число (например: 5):",
            reply_markup=get_back_cancel_kb()
        )
        return
    
    await state.update_data(min_participants=min_participants)
    await state.set_state(CreateEventStates.MAX_PARTICIPANTS)
    
    await message.answer(
        "[Создание события 6/6]\n\n"
        f"👥 <b>Минимум участников:</b> {min_participants}\n\n"
        f"👥 Введите максимальное количество участников:",
        reply_markup=get_back_cancel_kb(),
        parse_mode="HTML"
    )

@router.message(CreateEventStates.MAX_PARTICIPANTS)
async def process_max_participants(message: Message, state: FSMContext):
    """Обработка ввода максимального количества участников"""
    try:
        max_participants = int(message.text)
        data = await state.get_data()
        
        if max_participants < data['min_participants']:
            await message.answer(
                f"[Создание события 6/6]\n\n"
                f"❌ Максимум ({max_participants}) должен быть не меньше минимума ({data['min_participants']}).\n"
                f"Введите снова:",
                reply_markup=get_back_cancel_kb()
            )
            return
    except ValueError:
        await message.answer(
            "[Создание события 6/6]\n\n"
            "❌ Пожалуйста, введите число (например: 10):",
            reply_markup=get_back_cancel_kb()
        )
        return
    
    await state.update_data(max_participants=max_participants)
    await state.set_state(CreateEventStates.CONFIRMATION)
    
    # Показываем сводку для подтверждения
    text = (
        "[Подтверждение]\n\n"
        "✅ <b>Проверьте данные события:</b>\n\n"
        f"🎯 <b>Тип:</b> {data['type']}\n"
        f"🏙️ <b>Город:</b> {data['city']}\n"
        f"📅 <b>Дата:</b> {data['date_time']}\n"
        f"💰 <b>Цена:</b> {data['price']} руб.\n"
        f"👥 <b>Участники:</b> {data['min_participants']}-{max_participants}\n\n"
        "<b>Всё верно?</b>"
    )
    
    await message.answer(text, reply_markup=get_confirm_kb(), parse_mode="HTML")

@router.message(CreateEventStates.CONFIRMATION)
async def process_confirmation(message: Message, state: FSMContext):
    """Обработка подтверждения создания события"""
    if message.text == "✅ Да, создать событие":
        data = await state.get_data()
        
        # Создаем событие в БД
        event_id = await db.create_event(data, message.from_user.id)
        
        # Генерируем ссылку для приглашения
        invite_link = f"https://t.me/{bot._me.username}?start=invite_{event_id}"
        
        text = (
            "🎉 <b>Событие успешно создано!</b>\n\n"
            f"📋 <b>ID:</b> {event_id}\n"
            f"🎯 <b>Тип:</b> {data['type']}\n"
            f"🏙️ <b>Город:</b> {data['city']}\n"
            f"📅 <b>Дата:</b> {data['date_time']}\n"
            f"💰 <b>Цена:</b> {data['price']} руб.\n"
            f"👥 <b>Участники:</b> {data['min_participants']}-{data['max_participants']}\n\n"
            f"🔗 <b>Ссылка для приглашения:</b>\n"
            f"<code>{invite_link}</code>\n\n"
            "Теперь участники могут записываться на ваше событие!"
        )
        
        await state.clear()
        await state.set_state(MainStates.MAIN_MENU)
        await message.answer(text, reply_markup=get_main_menu_kb(), parse_mode="HTML")
        
    elif message.text == "✏️ Нет, исправить":
        await state.set_state(CreateEventStates.TYPE)
        await message.answer(
            "[Создание события 1/6]\n\n"
            "Выберите тип события заново:",
            reply_markup=get_event_types_kb()
        )
    else:
        await message.answer(
            "[Подтверждение]\n\n"
            "Пожалуйста, выберите вариант из предложенных:",
            reply_markup=get_confirm_kb()
        )

# === ЗАПИСЬ НА СОБЫТИЕ ===

@router.callback_query(F.data.startswith("join_"))
async def join_event_start(callback: CallbackQuery, state: FSMContext):
    """Начало записи на событие"""
    event_id = int(callback.data.split("_")[1])
    
    # Получаем детали события
    event = await db.get_event_details(event_id)
    
    if not event:
        await callback.answer("❌ Событие не найдено")
        return
    
    price = event[3]  # Цена из кортежа
    event_type = event[0]
    
    # Сохраняем данные в состоянии
    await state.update_data(join_event_id=event_id)
    await state.set_state(JoinEventStates.PAYMENT_INFO)
    
    text = (
        "💳 <b>Оплата сервисного сбора VIBEZ</b>\n\n"
        f"🎯 <b>Событие:</b> {event_type}\n"
        f"💰 <b>Цена события:</b> {price} руб.\n"
        f"💵 <b>Сервисный сбор платформы:</b> {PLATFORM_FEE} руб.\n\n"
        "⚠️ <b>ВАЖНО:</b>\n"
        "• Вы оплачиваете <b>сервисный сбор платформы VIBEZ {PLATFORM_FEE} ₽</b>\n"
        "• Деньги <b>НЕ переводятся инициатору</b>\n"
        "• Основные расчеты (если есть) — при встрече\n"
        "• Сбор гарантирует ваше участие и возвращается если событие не состоится\n\n"
        "<b>Для бронирования слота необходимо оплатить сервисный сбор {PLATFORM_FEE} ₽.</b>\n"
        "<b>Оплата производится через платёжную ссылку.</b>\n"
        "<b>После оплаты нажмите кнопку \"Я оплатил\".</b>"
    )
    
    await callback.message.edit_text(text, reply_markup=get_payment_kb(event_id), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("paid_"))
async def process_payment(callback: CallbackQuery, state: FSMContext):
    """Обработка подтверждения оплаты сервисного сбора"""
    event_id = int(callback.data.split("_")[1])
    
    # Добавляем участника в событие
    success = await db.add_participant(event_id, callback.from_user.id)
    
    if success:
        # Сразу подтверждаем участника
        await db.confirm_participant(event_id, callback.from_user.id)
        
        text = (
            "✅ <b>Оплата подтверждена!</b>\n\n"
            "Вы успешно забронировали участие в событии.\n\n"
            "📋 <b>Что дальше:</b>\n"
            "1. Ждем набора минимального количества участников\n"
            "2. При наборе кворума событие будет подтверждено\n"
            "3. Встречаемся в назначенное время!\n\n"
            "🔥 <b>Пригласите друзей — так событие состоится быстрее!</b>"
        )
        
        await state.set_state(MainStates.MAIN_MENU)
        await callback.message.edit_text(text, parse_mode="HTML")
        
        # Кнопка приглашения друга
        await callback.message.answer(
            "🔗 Пригласите друзей:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text="🔗 Пригласить друга", 
                    callback_data=f"invite_{event_id}_{callback.from_user.id}"
                )
            ]])
        )
        
        await callback.message.answer(
            "Выберите действие:",
            reply_markup=get_main_menu_kb()
        )
    else:
        await callback.answer("⚠️ Вы уже записаны на это событие")
    
    await callback.answer()

# === ПРИГЛАШЕНИЕ ДРУЗЕЙ ===

@router.callback_query(F.data.startswith("invite_"))
async def invite_friend(callback: CallbackQuery):
    """Генерация инвайт-ссылки"""
    parts = callback.data.split("_")
    event_id = int(parts[1])
    inviter_id = int(parts[2])
    
    invite_link = f"https://t.me/{bot._me.username}?start=invite_{event_id}_{inviter_id}"
    
    await callback.message.answer(
        f"🔗 <b>Ссылка для приглашения:</b>\n\n"
        f"<code>{invite_link}</code>\n\n"
        "Отправьте эту ссылку другу, чтобы он мог присоединиться к событию.",
        parse_mode="HTML"
    )
    await callback.answer()

# === НАВИГАЦИОННЫЕ КНОПКИ ===

@router.callback_query(F.data == "back_to_main")
async def back_to_main_menu(callback: CallbackQuery, state: FSMContext):
    """Возврат в главное меню"""
    await state.set_state(MainStates.MAIN_MENU)
    await callback.message.edit_text("Выберите действие:")
    await callback.message.answer(
        "Главное меню:",
        reply_markup=get_main_menu_kb()
    )
    await callback.answer()

@router.callback_query(F.data == "back_to_search")
async def back_to_search(callback: CallbackQuery, state: FSMContext):
    """Возврат к поиску"""
    data = await state.get_data()
    city = data.get('search_city', 'неизвестный город')
    
    events = await db.get_events_by_city(city)
    
    if events:
        await state.set_state(SearchEventsStates.SELECT_EVENT)
        text = f"✅ <b>Найдено событий в {city}: {len(events)}</b>\n\nВыберите событие:"
        await callback.message.edit_text(text, reply_markup=get_event_list_kb(events), parse_mode="HTML")
    else:
        await state.set_state(SearchEventsStates.ENTER_CITY)
        await callback.message.edit_text("Выберите город для поиска:")
        await callback.message.answer(
            "🏙️ Выберите город для поиска событий:",
            reply_markup=get_cities_kb()
        )
    
    await callback.answer()

@router.callback_query(F.data.startswith("back_to_event_"))
async def back_to_event(callback: CallbackQuery, state: FSMContext):
    """Возврат к событию"""
    event_id = int(callback.data.split("_")[3])
    
    event = await db.get_event_details(event_id)
    
    if event:
        await state.set_state(MainStates.VIEWING_EVENT)
        (event_type, city, date_time, price, min_participants, 
         max_participants, status, chat_id, creator_id, 
         created_at, creator_username, confirmed_count) = event
        
        is_confirmed = await db.is_user_confirmed(event_id, callback.from_user.id)
        
        text = (
            f"📋 <b>Детали события:</b>\n\n"
            f"🎯 <b>Тип:</b> {event_type}\n"
            f"🏙️ <b>Город:</b> {city}\n"
            f"📅 <b>Дата:</b> {date_time}\n"
            f"💰 <b>Цена:</b> {price} руб.\n"
            f"👤 <b>Инициатор:</b> @{creator_username}\n"
            f"✅ <b>Подтверждено:</b> {confirmed_count}/{min_participants} участников\n"
            f"👥 <b>Максимум:</b> {max_participants} участников\n"
            f"📊 <b>Статус:</b> {status}\n"
        )
        
        await callback.message.edit_text(
            text, 
            reply_markup=get_event_details_kb(event_id, callback.from_user.id, is_confirmed), 
            parse_mode="HTML"
        )
    
    await callback.answer()

# === АДМИН-ПАНЕЛЬ ===

@router.message(Command("admin"))
async def admin_panel(message: Message):
    """Админ-панель"""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Доступ запрещен")
        return
    
    await message.answer(
        "👑 <b>Админ-панель VIBEZ</b>",
        reply_markup=get_admin_kb(),
        parse_mode="HTML"
    )

@router.callback_query(F.data == "admin_all_events")
async def admin_all_events(callback: CallbackQuery):
    """Просмотр всех событий"""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Доступ запрещен")
        return
    
    events = await db.get_all_events()
    
    if not events:
        text = "📭 <b>Нет активных событий</b>"
        await callback.message.edit_text(text, parse_mode="HTML")
        return
    
    text = "📋 <b>Все события:</b>\n\n"
    
    for event in events:
        event_id, event_type, city, date_time, status, username, participants_count = event
        text += (
            f"🔢 <b>ID:</b> {event_id}\n"
            f"🎯 <b>Тип:</b> {event_type}\n"
            f"🏙️ <b>Город:</b> {city}\n"
            f"📅 <b>Дата:</b> {date_time}\n"
            f"👤 <b>Инициатор:</b> @{username}\n"
            f"✅ <b>Участников:</b> {participants_count}\n"
            f"📊 <b>Статус:</b> {status}\n"
            f"{'-'*30}\n\n"
        )
    
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")]
        ]
    )
    
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "admin_back")
async def admin_back(callback: CallbackQuery):
    """Возврат в админ-панель"""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Доступ запрещен")
        return
    
    await callback.message.edit_text(
        "👑 <b>Админ-панель VIBEZ</b>",
        reply_markup=get_admin_kb(),
        parse_mode="HTML"
    )
    await callback.answer()

# === ОБРАБОТКА НЕОЖИДАННОГО ВВОДА ===

@router.message(StateFilter("*"))
async def handle_unexpected_input(message: Message, state: FSMContext):
    """Обработка неожиданного ввода"""
    current_state = await state.get_state()
    
    if current_state is None:
        await state.set_state(MainStates.MAIN_MENU)
        await message.answer(
            "Выберите действие:",
            reply_markup=get_main_menu_kb()
        )
        return
    
    # Определяем текущий режим и подсказываем
    if "CreateEventStates" in str(current_state):
        step_info = {
            "CreateEventStates:TYPE": "[Создание события 1/6]",
            "CreateEventStates:TYPE_OTHER": "[Создание события 1/6]",
            "CreateEventStates:CITY": "[Создание события 2/6]", 
            "CreateEventStates:DATE_TIME": "[Создание события 3/6]",
            "CreateEventStates:PRICE": "[Создание события 4/6]",
            "CreateEventStates:MIN_PARTICIPANTS": "[Создание события 5/6]",
            "CreateEventStates:MAX_PARTICIPANTS": "[Создание события 6/6]",
            "CreateEventStates:CONFIRMATION": "[Подтверждение]"
        }.get(str(current_state), "")
        
        await message.answer(
            f"{step_info}\n\n"
            "✋ <b>Сейчас вы создаёте событие.</b>\n\n"
            "Пожалуйста, используйте кнопки или введите запрошенные данные.\n"
            "Нажмите '⬅️ Назад' для возврата или '❌ Отмена' для выхода.",
            reply_markup=get_back_cancel_kb(),
            parse_mode="HTML"
        )
    elif "SearchEventsStates" in str(current_state):
        await message.answer(
            "✋ <b>Сейчас вы находитесь в режиме поиска.</b>\n\n"
            "Выберите город из списка или используйте кнопки навигации.\n"
            "Нажмите '⬅️ Назад' для возврата или '❌ Отмена' для выхода.",
            reply_markup=get_cities_kb(),
            parse_mode="HTML"
        )
    elif "JoinEventStates" in str(current_state):
        await message.answer(
            "✋ <b>Сейчас вы записываетесь на событие.</b>\n\n"
            "Используйте кнопки для навигации.\n"
            "Нажмите '⬅️ Назад' для возврата или '❌ Отмена' для выхода.",
            reply_markup=get_back_cancel_kb(),
            parse_mode="HTML"
        )
    else:
        await message.answer(
            "✋ <b>Пожалуйста, используйте кнопки навигации.</b>\n\n"
            "Если вы хотите вернуться в главное меню, нажмите '❌ Отмена'.",
            reply_markup=get_back_cancel_kb(),
            parse_mode="HTML"
        )

# === ЗАПУСК БОТА ===

async def main():
    """Основная функция запуска бота"""
    # Инициализация БД
    await db.init_db()
    
    # Удаляем вебхук и запускаем polling
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    asyncio.run(main())