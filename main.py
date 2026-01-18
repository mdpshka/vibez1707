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

# Импортируем города из отдельного файла
try:
    from cities import CITIES
except ImportError:
    # Если файл не найден, используем минимальный список
    CITIES = ["Москва", "Санкт-Петербург", "Казань", "Екатеринбург", "Новосибирск"]

# === КОНФИГУРАЦИЯ ===
BOT_TOKEN = "8104721228:AAHPnw-PHAMYMJARBvBULtm5_SeFcrhfm3g"
ADMIN_IDS = [931410785]
PLATFORM_FEE = 99  # Фиксированный сервисный сбор 99 ₽
PAYMENT_LINK = "https://yoomoney.ru/pay/..."  # Захардкоженная ссылка на оплату

# === FSM СТРУКТУРА ===
class MainStates(StatesGroup):
    MAIN_MENU = State()
    VIEWING_EVENT = State()

class OnboardingStates(StatesGroup):
    NAME = State()
    CITY = State()

class CreateEventStates(StatesGroup):
    TYPE = State()
    TYPE_OTHER = State()
    DATE = State()
    TIME = State()
    MAX_PARTICIPANTS = State()
    DESCRIPTION = State()
    CONFIRMATION = State()

class SearchEventsStates(StatesGroup):
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
            # Пользователи с доп. полями
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER UNIQUE,
                    username TEXT,
                    name TEXT,
                    city TEXT,
                    rating REAL DEFAULT 5.0,
                    onboarded BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # События с описанием
            await db.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT,
                    custom_type TEXT,
                    city TEXT,
                    date TEXT,
                    time TEXT,
                    price INTEGER,
                    min_participants INTEGER DEFAULT 2,
                    max_participants INTEGER,
                    description TEXT,
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

    async def update_user_profile(self, telegram_id, name, city):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET name = ?, city = ?, onboarded = 1 WHERE telegram_id = ?",
                (name, city, telegram_id)
            )
            await db.commit()

    async def get_user_profile(self, telegram_id):
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT name, city, onboarded FROM users WHERE telegram_id = ?",
                (telegram_id,)
            )
            result = await cursor.fetchone()
            return result if result else (None, None, 0)

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
                    type, custom_type, city, date, time, 
                    max_participants, description, creator_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event_data['type'],
                event_data.get('custom_type'),
                event_data['city'],
                event_data['date'],
                event_data['time'],
                event_data['max_participants'],
                event_data['description'],
                creator_id
            ))
            
            await db.commit()
            return cursor.lastrowid

    async def get_events_by_city(self, city):
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                SELECT 
                    e.id, 
                    CASE WHEN e.custom_type IS NOT NULL THEN e.custom_type ELSE e.type END as display_type,
                    e.max_participants,
                    e.date || ' ' || e.time as date_time,
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
                    e.type,
                    e.custom_type,
                    e.city,
                    e.date,
                    e.time,
                    e.max_participants,
                    e.description,
                    e.status,
                    e.creator_id,
                    u.username as creator_username,
                    u.name as creator_name,
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
            
            # Проверяем лимит участников
            cursor = await db.execute(
                "SELECT max_participants FROM events WHERE id = ?",
                (event_id,)
            )
            max_participants = (await cursor.fetchone())[0]
            
            cursor = await db.execute("""
                SELECT COUNT(*) FROM event_participants 
                WHERE event_id = ? AND status = 'CONFIRMED'
            """, (event_id,))
            confirmed_count = (await cursor.fetchone())[0]
            
            if confirmed_count >= max_participants:
                return False, "Достигнут лимит участников"
            
            # Проверяем, не записан ли уже
            cursor = await db.execute("""
                SELECT id FROM event_participants 
                WHERE event_id = ? AND user_id = ?
            """, (event_id, user_id))
            
            if await cursor.fetchone():
                return False, "Вы уже записаны на это событие"
            
            # Добавляем участника
            await db.execute("""
                INSERT INTO event_participants (event_id, user_id, invited_by, status)
                VALUES (?, ?, ?, 'PENDING')
            """, (event_id, user_id, invited_by))
            
            await db.commit()
            return True, "Успешно"

    async def confirm_participant(self, event_id, user_telegram_id):
        async with aiosqlite.connect(self.db_path) as db:
            user_id = await self.get_user_id(user_telegram_id)
            
            await db.execute("""
                UPDATE event_participants 
                SET status = 'CONFIRMED' 
                WHERE event_id = ? AND user_id = ?
            """, (event_id, user_id))
            
            await db.commit()
            return True

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

    async def get_creator_telegram_id(self, event_id):
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                SELECT u.telegram_id 
                FROM events e
                JOIN users u ON e.creator_id = u.id
                WHERE e.id = ?
            """, (event_id,))
            result = await cursor.fetchone()
            return result[0] if result else None

db = Database()

# === КЛАВИАТУРЫ ===
def get_cities_keyboard(page=0, items_per_page=8):
    """Клавиатура с пагинацией для выбора города"""
    start_idx = page * items_per_page
    end_idx = start_idx + items_per_page
    cities_slice = CITIES[start_idx:end_idx]
    
    buttons = []
    row = []
    for i, city in enumerate(cities_slice):
        row.append(InlineKeyboardButton(text=city, callback_data=f"city_{city}"))
        if i % 2 == 1:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    
    # Кнопки пагинации
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"city_page_{page-1}"))
    if end_idx < len(CITIES):
        nav_buttons.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"city_page_{page+1}"))
    
    if nav_buttons:
        buttons.append(nav_buttons)
    
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_onboarding")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

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
            [KeyboardButton(text="🔫 Пейнтбол"), KeyboardButton(text="🎯 Другое")],
            [KeyboardButton(text="⬅️ Назад"), KeyboardButton(text="❌ Отмена")]
        ],
        resize_keyboard=True
    )

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
        event_id, event_type, max_participants, date_time, confirmed_count = event
        
        buttons.append([
            InlineKeyboardButton(
                text=f"{event_type[:20]} • {confirmed_count}/{max_participants} • {date_time}",
                callback_data=f"view_event_{event_id}"
            )
        ])
    buttons.append([InlineKeyboardButton(text="⬅️ В главное меню", callback_data="back_to_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_event_details_kb(event_id, user_telegram_id, is_confirmed=False):
    """Кнопки для деталей события"""
    buttons = []
    
    if not is_confirmed:
        buttons.append([InlineKeyboardButton(text="💳 Забронировать", callback_data=f"join_{event_id}")])
    
    buttons.append([
        InlineKeyboardButton(text="📲 Пригласить друга", 
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

# === ОБРАБОТЧИКИ ===

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    """Старт бота с онбордингом"""
    await db.add_user(message.from_user.id, message.from_user.username)
    
    # Проверяем, прошел ли пользователь онбординг
    name, city, onboarded = await db.get_user_profile(message.from_user.id)
    
    if not onboarded:
        # Запускаем онбординг
        await state.set_state(OnboardingStates.NAME)
        await message.answer(
            "👋 Добро пожаловать в VIBEZ!\n\n"
            "Для начала расскажите немного о себе.\n\n"
            "Как вас зовут? (Введите ваше имя):",
            reply_markup=ReplyKeyboardRemove()
        )
    else:
        # Пользователь уже онбордирован
        await state.set_state(MainStates.MAIN_MENU)
        await message.answer(
            f"👋 Привет, {name}!\n\n"
            "VIBEZ — бот для создания и поиска реальных событий в твоём городе.\n\n"
            "🔍 Найти событие\n"
            "➕ Создать событие\n"
            "💳 Забронировать участие",
            reply_markup=get_main_menu_kb()
        )

@router.message(OnboardingStates.NAME)
async def process_name(message: Message, state: FSMContext):
    """Обработка ввода имени при онбординге"""
    name = message.text.strip()
    if len(name) < 2:
        await message.answer("Имя должно содержать минимум 2 символа. Попробуйте еще раз:")
        return
    
    await state.update_data(name=name)
    await state.set_state(OnboardingStates.CITY)
    
    await message.answer(
        f"Отлично, {name}!\n\n"
        "Теперь выберите ваш город из списка:",
        reply_markup=get_cities_keyboard()
    )

@router.callback_query(F.data.startswith("city_"))
async def process_city_selection(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора города"""
    city = callback.data.split("_", 1)[1]
    data = await state.get_data()
    name = data['name']
    
    # Сохраняем профиль пользователя
    await db.update_user_profile(callback.from_user.id, name, city)
    
    await state.set_state(MainStates.MAIN_MENU)
    await callback.message.edit_text(
        f"👋 Привет, {name}!\n\n"
        f"VIBEZ — бот для создания и поиска реальных событий в твоём городе ({city}).\n\n"
        "🔍 Найти событие\n"
        "➕ Создать событие\n"
        "💳 Забронировать участие"
    )
    await callback.message.answer(
        "Выберите действие:",
        reply_markup=get_main_menu_kb()
    )
    await callback.answer()

@router.callback_query(F.data.startswith("city_page_"))
async def process_city_pagination(callback: CallbackQuery, state: FSMContext):
    """Обработка пагинации городов"""
    page = int(callback.data.split("_")[2])
    await callback.message.edit_reply_markup(reply_markup=get_cities_keyboard(page))
    await callback.answer()

# === СОЗДАНИЕ СОБЫТИЯ ===

@router.message(F.text == "➕ Создать событие", MainStates.MAIN_MENU)
async def start_create_event(message: Message, state: FSMContext):
    """Начало создания события"""
    # Получаем город пользователя
    name, city, onboarded = await db.get_user_profile(message.from_user.id)
    
    if not city:
        await message.answer("Сначала завершите онбординг. Нажмите /start")
        return
    
    await state.update_data(city=city)
    await state.set_state(CreateEventStates.TYPE)
    
    await message.answer(
        "[Создание события 1/6]\n\n"
        "🎯 Выберите тип события:",
        reply_markup=get_event_types_kb()
    )

@router.message(CreateEventStates.TYPE)
async def process_event_type(message: Message, state: FSMContext):
    """Обработка выбора типа события"""
    if message.text not in ["🎉 Туса", "🎳 Страйкбол", "🔫 Пейнтбол", "🎯 Другое"]:
        await message.answer(
            "❌ Пожалуйста, выберите тип из предложенных вариантов:",
            reply_markup=get_event_types_kb()
        )
        return
    
    if message.text == "🎯 Другое":
        await state.set_state(CreateEventStates.TYPE_OTHER)
        await message.answer(
            "Введите название вашего события:",
            reply_markup=get_back_cancel_kb()
        )
        return
    
    event_type = message.text[2:]  # Убираем эмодзи
    await state.update_data(type=event_type, custom_type=None)
    await state.set_state(CreateEventStates.DATE)
    
    await message.answer(
        "[Создание события 2/6]\n\n"
        f"Тип: {event_type}\n\n"
        "Введите дату в формате ДД.ММ.ГГГГ\n"
        "Например: 25.12.2024",
        reply_markup=get_back_cancel_kb()
    )

@router.message(CreateEventStates.TYPE_OTHER)
async def process_event_type_other(message: Message, state: FSMContext):
    """Обработка ввода названия для типа 'Другое'"""
    custom_type = message.text.strip()
    
    if len(custom_type) < 3:
        await message.answer("Название события должно содержать минимум 3 символа. Введите снова:")
        return
    
    await state.update_data(type="Другое", custom_type=custom_type)
    await state.set_state(CreateEventStates.DATE)
    
    await message.answer(
        "[Создание события 2/6]\n\n"
        f"Тип: {custom_type}\n\n"
        "Введите дату в формате ДД.ММ.ГГГГ\n"
        "Например: 25.12.2024",
        reply_markup=get_back_cancel_kb()
    )

@router.message(CreateEventStates.DATE)
async def process_event_date(message: Message, state: FSMContext):
    """Обработка ввода даты"""
    date_str = message.text.strip()
    
    try:
        event_date = datetime.strptime(date_str, "%d.%m.%Y").date()
        today = datetime.now().date()
        
        if event_date < today:
            await message.answer(
                "❌ Дата не может быть в прошлом.\n"
                "Введите будущую дату в формате ДД.ММ.ГГГГ:"
            )
            return
    except ValueError:
        await message.answer(
            "❌ Неверный формат даты.\n"
            "Введите дату в формате ДД.ММ.ГГГГ\n"
            "Например: 25.12.2024"
        )
        return
    
    await state.update_data(date=date_str)
    await state.set_state(CreateEventStates.TIME)
    
    await message.answer(
        "[Создание события 3/6]\n\n"
        f"Дата: {date_str}\n\n"
        "Введите время в формате ЧЧ:ММ\n"
        "Например: 19:00",
        reply_markup=get_back_cancel_kb()
    )

@router.message(CreateEventStates.TIME)
async def process_event_time(message: Message, state: FSMContext):
    """Обработка ввода времени"""
    time_str = message.text.strip()
    
    try:
        datetime.strptime(time_str, "%H:%M")
    except ValueError:
        await message.answer(
            "❌ Неверный формат времени.\n"
            "Введите время в формате ЧЧ:ММ\n"
            "Например: 19:00"
        )
        return
    
    await state.update_data(time=time_str)
    await state.set_state(CreateEventStates.MAX_PARTICIPANTS)
    
    await message.answer(
        "[Создание события 4/6]\n\n"
        f"Время: {time_str}\n\n"
        "Введите максимальное количество участников:",
        reply_markup=get_back_cancel_kb()
    )

@router.message(CreateEventStates.MAX_PARTICIPANTS)
async def process_max_participants(message: Message, state: FSMContext):
    """Обработка ввода максимального количества участников"""
    try:
        max_participants = int(message.text)
        if max_participants < 2:
            await message.answer("❌ Минимум должно быть 2 участника. Введите снова:")
            return
    except ValueError:
        await message.answer("❌ Пожалуйста, введите число (например: 10):")
        return
    
    await state.update_data(max_participants=max_participants)
    await state.set_state(CreateEventStates.DESCRIPTION)
    
    await message.answer(
        "[Создание события 5/6]\n\n"
        f"Максимум участников: {max_participants}\n\n"
        "📝 Введите описание события (обязательно):\n"
        "• Что будет происходить\n"
        "• Что взять с собой\n"
        "• Формат встречи",
        reply_markup=get_back_cancel_kb()
    )

@router.message(CreateEventStates.DESCRIPTION)
async def process_description(message: Message, state: FSMContext):
    """Обработка ввода описания"""
    description = message.text.strip()
    
    if len(description) < 10:
        await message.answer(
            "❌ Описание слишком короткое. Минимум 10 символов.\n"
            "Опишите подробно, что будет происходить:"
        )
        return
    
    await state.update_data(description=description)
    await state.set_state(CreateEventStates.CONFIRMATION)
    
    data = await state.get_data()
    event_type = data.get('custom_type') or data['type']
    
    text = (
        "[Создание события 6/6]\n\n"
        "✅ <b>Проверьте данные события:</b>\n\n"
        f"🎯 <b>Тип:</b> {event_type}\n"
        f"🏙️ <b>Город:</b> {data['city']}\n"
        f"📅 <b>Дата:</b> {data['date']}\n"
        f"⏰ <b>Время:</b> {data['time']}\n"
        f"👥 <b>Максимум участников:</b> {data['max_participants']}\n"
        f"📝 <b>Описание:</b> {description[:100]}...\n\n"
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
        invite_link = f"https://t.me/{bot._me.username}?start=invite_{event_id}_{message.from_user.id}"
        
        event_type = data.get('custom_type') or data['type']
        
        text = (
            "✅ <b>Событие создано</b>\n\n"
            f"🎯 <b>Тип:</b> {event_type}\n"
            f"🏙️ <b>Город:</b> {data['city']}\n"
            f"📅 <b>Дата:</b> {data['date']}\n"
            f"⏰ <b>Время:</b> {data['time']}\n"
            f"👥 <b>Максимум участников:</b> {data['max_participants']}\n\n"
            "👥 <b>Участники будут записываться через бота</b>\n"
            "🔔 <b>Когда кто-то забронирует место, ты получишь уведомление</b>\n"
            "📩 <b>Для связи с участниками используй:</b>\n"
            "— Telegram-ники, которые бот пришлёт\n"
            "— или добавь в описание события контакт (по желанию)\n\n"
            "❗ <b>ЧАТЫ НЕ СОЗДАВАТЬ</b>\n"
            "❗ <b>Инициатор сам связывается с участниками напрямую</b>\n\n"
            f"🔗 <b>Ссылка для приглашения:</b>\n"
            f"<code>{invite_link}</code>"
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
            "Пожалуйста, выберите вариант из предложенных:",
            reply_markup=get_confirm_kb()
        )

# === ПОИСК СОБЫТИЙ ===

@router.message(F.text == "🔍 Найти событие", MainStates.MAIN_MENU)
async def start_search(message: Message, state: FSMContext):
    """Начало поиска событий"""
    # Получаем город пользователя
    name, city, onboarded = await db.get_user_profile(message.from_user.id)
    
    if not city:
        await message.answer("Сначала завершите онбординг. Нажмите /start")
        return
    
    await state.update_data(search_city=city)
    
    # Ищем события в БД
    events = await db.get_events_by_city(city)
    
    if not events:
        await message.answer(
            f"😔 <b>В городе {city} пока нет активных событий.</b>\n\n"
            f"Попробуйте другой город или создайте свое событие!",
            parse_mode="HTML"
        )
        return
    
    await state.set_state(SearchEventsStates.SELECT_EVENT)
    
    await message.answer(
        f"✅ <b>Найдено событий в {city}: {len(events)}</b>",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="HTML"
    )
    
    # Отправляем список событий
    await message.answer(
        "📋 <b>Список событий:</b>",
        reply_markup=get_event_list_kb(events),
        parse_mode="HTML"
    )

# === БРОНИРОВАНИЕ И ОПЛАТА ===

@router.callback_query(F.data.startswith("join_"))
async def join_event_start(callback: CallbackQuery, state: FSMContext):
    """Начало бронирования события"""
    event_id = int(callback.data.split("_")[1])
    
    # Получаем детали события
    event = await db.get_event_details(event_id)
    
    if not event:
        await callback.answer("❌ Событие не найдено")
        return
    
    event_type, custom_type, city, date, time, max_participants, description, status, creator_id, creator_username, creator_name, confirmed_count = event
    display_type = custom_type or event_type
    
    # Сохраняем данные в состоянии
    await state.update_data(join_event_id=event_id)
    await state.set_state(JoinEventStates.PAYMENT_INFO)
    
    text = (
        "💳 <b>Бронирование участия</b>\n\n"
        f"🎯 <b>Событие:</b> {display_type}\n"
        f"🏙️ <b>Город:</b> {city}\n"
        f"📅 <b>Дата:</b> {date} {time}\n\n"
        f"💰 <b>Стоимость бронирования — {PLATFORM_FEE} ₽</b>\n"
        f"Это сервисный сбор VIBEZ.\n"
        f"Деньги получает платформа, а не организатор.\n\n"
        f"<b>Для бронирования оплатите {PLATFORM_FEE} ₽ по ссылке ниже</b>"
    )
    
    await callback.message.edit_text(text, reply_markup=get_payment_kb(event_id), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("paid_"))
async def process_payment(callback: CallbackQuery, state: FSMContext):
    """Обработка подтверждения оплаты"""
    event_id = int(callback.data.split("_")[1])
    
    # Проверяем, можно ли записаться
    success, message = await db.add_participant(event_id, callback.from_user.id)
    
    if not success:
        await callback.answer(f"❌ {message}")
        return
    
    # Подтверждаем участника
    await db.confirm_participant(event_id, callback.from_user.id)
    
    # Получаем информацию о пользователе
    name, city, onboarded = await db.get_user_profile(callback.from_user.id)
    participant_name = name or callback.from_user.username or "Пользователь"
    participant_username = callback.from_user.username or "нет username"
    
    # Отправляем уведомление инициатору
    creator_telegram_id = await db.get_creator_telegram_id(event_id)
    if creator_telegram_id:
        try:
            await bot.send_message(
                creator_telegram_id,
                f"🎉 <b>Новый участник!</b>\n\n"
                f"👤 <b>Имя:</b> {participant_name}\n"
                f"🔗 <b>Telegram:</b> @{participant_username}",
                parse_mode="HTML"
            )
        except:
            pass  # Игнорируем ошибки отправки
    
    text = (
        "✅ <b>Оплата подтверждена!</b>\n\n"
        "Вы успешно забронировали участие в событии.\n\n"
        "📋 <b>Что дальше:</b>\n"
        "1. Ждем встречи в назначенное время\n"
        "2. Инициатор свяжется с вами напрямую\n"
        "3. Приходите вовремя и наслаждайтесь событием!\n\n"
        "🔥 <b>Пригласите друзей — так будет веселее!</b>"
    )
    
    await state.set_state(MainStates.MAIN_MENU)
    await callback.message.edit_text(text, parse_mode="HTML")
    
    await callback.message.answer(
        "Выберите действие:",
        reply_markup=get_main_menu_kb()
    )
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
        f"📲 <b>Ссылка для приглашения друга:</b>\n\n"
        f"<code>{invite_link}</code>\n\n"
        "Отправьте эту ссылку другу, чтобы он мог присоединиться к событию.",
        parse_mode="HTML"
    )
    await callback.answer()

# === ОБРАБОТКА ИНВАЙТ-ССЫЛОК ===

@router.message(Command("start"))
async def cmd_start_with_invite(message: Message, state: FSMContext):
    """Обработка /start с инвайт-параметром"""
    args = message.text.split()
    
    if len(args) > 1 and args[1].startswith("invite_"):
        try:
            parts = args[1].split("_")
            event_id = int(parts[1])
            inviter_id = int(parts[2]) if len(parts) > 2 else None
            
            # Проверяем онбординг
            name, city, onboarded = await db.get_user_profile(message.from_user.id)
            
            if not onboarded:
                await state.update_data(inviter_id=inviter_id, invite_event_id=event_id)
                await state.set_state(OnboardingStates.NAME)
                await message.answer(
                    "👋 Вас пригласили на событие!\n\n"
                    "Для начала расскажите немного о себе.\n\n"
                    "Как вас зовут? (Введите ваше имя):",
                    reply_markup=ReplyKeyboardRemove()
                )
                return
            else:
                # Показываем событие
                event = await db.get_event_details(event_id)
                if event:
                    await show_event_details(message, event_id, inviter_id)
                    return
        except:
            pass
    
    # Обычный /start
    await cmd_start(message, state)

async def show_event_details(message: Message, event_id: int, inviter_id: int = None):
    """Показ деталей события"""
    event = await db.get_event_details(event_id)
    
    if not event:
        await message.answer("❌ Событие не найдено")
        return
    
    event_type, custom_type, city, date, time, max_participants, description, status, creator_id, creator_username, creator_name, confirmed_count = event
    display_type = custom_type or event_type
    
    is_confirmed = await db.is_user_confirmed(event_id, message.from_user.id)
    
    text = (
        f"🎉 <b>Вас пригласили на событие!</b>\n\n"
        f"📋 <b>Детали события:</b>\n\n"
        f"🎯 <b>Тип:</b> {display_type}\n"
        f"🏙️ <b>Город:</b> {city}\n"
        f"📅 <b>Дата:</b> {date} {time}\n"
        f"👤 <b>Инициатор:</b> {creator_name or '@' + creator_username}\n"
        f"✅ <b>Забронировано:</b> {confirmed_count}/{max_participants} участников\n\n"
        f"📝 <b>Описание:</b>\n{description}\n\n"
    )
    
    if is_confirmed:
        text += "✅ <b>Вы уже участвуете в этом событии</b>"
    else:
        text += "<i>Для бронирования нажмите кнопку 'Забронировать'</i>"
    
    await message.answer(
        text, 
        reply_markup=get_event_details_kb(event_id, message.from_user.id, is_confirmed), 
        parse_mode="HTML"
    )

# === ПРОЧИЕ ОБРАБОТЧИКИ ===

@router.message(F.text == "⬅️ Назад", StateFilter("*"))
async def handle_back(message: Message, state: FSMContext):
    """Обработка кнопки 'Назад'"""
    current_state = await state.get_state()
    
    if current_state == CreateEventStates.TYPE_OTHER:
        await state.set_state(CreateEventStates.TYPE)
        await message.answer(
            "Выберите тип события:",
            reply_markup=get_event_types_kb()
        )
    elif current_state == CreateEventStates.DATE:
        await state.set_state(CreateEventStates.TYPE)
        await message.answer(
            "Выберите тип события:",
            reply_markup=get_event_types_kb()
        )
    elif current_state == CreateEventStates.TIME:
        await state.set_state(CreateEventStates.DATE)
        await message.answer(
            "Введите дату в формате ДД.ММ.ГГГГ:",
            reply_markup=get_back_cancel_kb()
        )
    elif current_state == CreateEventStates.MAX_PARTICIPANTS:
        await state.set_state(CreateEventStates.TIME)
        await message.answer(
            "Введите время в формате ЧЧ:ММ:",
            reply_markup=get_back_cancel_kb()
        )
    elif current_state == CreateEventStates.DESCRIPTION:
        await state.set_state(CreateEventStates.MAX_PARTICIPANTS)
        await message.answer(
            "Введите максимальное количество участников:",
            reply_markup=get_back_cancel_kb()
        )
    elif current_state == CreateEventStates.CONFIRMATION:
        await state.set_state(CreateEventStates.DESCRIPTION)
        await message.answer(
            "Введите описание события:",
            reply_markup=get_back_cancel_kb()
        )
    else:
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