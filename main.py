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
    CITIES = ["Москва", "Санкт-Петербург", "Казань", "Екатеринбург", "Новосибирск"]

# === КОНФИГУРАЦИЯ ===
BOT_TOKEN = "8104721228:AAHPnw-PHAMYMJARBvBULtm5_SeFcrhfm3g"
ADMIN_IDS = [931410785]
PLATFORM_FEE = 99
PAYMENT_LINK = "https://yoomoney.ru/pay/..."

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
    CONTACT = State()
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
            
            await db.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT,
                    custom_type TEXT,
                    city TEXT,
                    date TEXT,
                    time TEXT,
                    max_participants INTEGER,
                    description TEXT,
                    contact TEXT,
                    status TEXT DEFAULT 'ACTIVE',
                    chat_id INTEGER,
                    creator_id INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (creator_id) REFERENCES users(id)
                )
            """)
            
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
                    max_participants, description, contact, creator_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event_data['type'],
                event_data.get('custom_type'),
                event_data['city'],
                event_data['date'],
                event_data['time'],
                event_data['max_participants'],
                event_data['description'],
                event_data['contact'],
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
                    e.contact,
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
            
            cursor = await db.execute("""
                SELECT id FROM event_participants 
                WHERE event_id = ? AND user_id = ?
            """, (event_id, user_id))
            
            if await cursor.fetchone():
                return False, "Вы уже записаны на это событие"
            
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

    # === НОВЫЕ МЕТОДЫ ДЛЯ АДМИНКИ И ПРОФИЛЯ ===
    
    async def get_user_bookings(self, telegram_id):
        """Получить все брони пользователя"""
        async with aiosqlite.connect(self.db_path) as db:
            user_id = await self.get_user_id(telegram_id)
            cursor = await db.execute("""
                SELECT 
                    e.id,
                    CASE WHEN e.custom_type IS NOT NULL THEN e.custom_type ELSE e.type END as display_type,
                    e.city,
                    e.date || ' ' || e.time as date_time,
                    ep.created_at as booking_date
                FROM event_participants ep
                JOIN events e ON ep.event_id = e.id
                WHERE ep.user_id = ? AND ep.status = 'CONFIRMED'
                ORDER BY ep.created_at DESC
            """, (user_id,))
            return await cursor.fetchall()

    async def get_user_created_events(self, telegram_id):
        """Получить события, созданные пользователем"""
        async with aiosqlite.connect(self.db_path) as db:
            user_id = await self.get_user_id(telegram_id)
            cursor = await db.execute("""
                SELECT 
                    id,
                    CASE WHEN custom_type IS NOT NULL THEN custom_type ELSE type END as display_type,
                    city,
                    date || ' ' || time as date_time,
                    status,
                    (SELECT COUNT(*) FROM event_participants ep 
                     WHERE ep.event_id = events.id AND ep.status = 'CONFIRMED') as participants_count,
                    max_participants
                FROM events
                WHERE creator_id = ?
                ORDER BY created_at DESC
            """, (user_id,))
            return await cursor.fetchall()

    async def get_event_participants_list(self, event_id):
        """Получить список участников события"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                SELECT 
                    u.username,
                    u.telegram_id,
                    u.name,
                    ep.created_at
                FROM event_participants ep
                JOIN users u ON ep.user_id = u.id
                WHERE ep.event_id = ? AND ep.status = 'CONFIRMED'
                ORDER BY ep.created_at ASC
            """, (event_id,))
            return await cursor.fetchall()

    async def get_admin_stats(self):
        """Получить статистику для админки"""
        async with aiosqlite.connect(self.db_path) as db:
            # Всего пользователей
            cursor = await db.execute("SELECT COUNT(*) FROM users WHERE onboarded = 1")
            total_users = (await cursor.fetchone())[0]
            
            # Всего событий
            cursor = await db.execute("SELECT COUNT(*) FROM events")
            total_events = (await cursor.fetchone())[0]
            
            # Всего бронирований
            cursor = await db.execute("SELECT COUNT(*) FROM event_participants WHERE status = 'CONFIRMED'")
            total_bookings = (await cursor.fetchone())[0]
            
            # Общий оборот
            total_revenue = total_bookings * PLATFORM_FEE
            
            # Топ городов
            cursor = await db.execute("""
                SELECT city, COUNT(*) as count 
                FROM events 
                WHERE city IS NOT NULL AND city != ''
                GROUP BY city 
                ORDER BY count DESC 
                LIMIT 5
            """)
            top_cities = await cursor.fetchall()
            
            # Активные события
            cursor = await db.execute("SELECT COUNT(*) FROM events WHERE status = 'ACTIVE'")
            active_events = (await cursor.fetchone())[0]
            
            # Новые пользователи за последние 7 дней
            cursor = await db.execute("""
                SELECT COUNT(*) FROM users 
                WHERE date(created_at) >= date('now', '-7 days')
            """)
            new_users_week = (await cursor.fetchone())[0]
            
            return {
                'total_users': total_users,
                'total_events': total_events,
                'total_bookings': total_bookings,
                'total_revenue': total_revenue,
                'top_cities': top_cities,
                'active_events': active_events,
                'new_users_week': new_users_week
            }

    async def get_user_full_info(self, telegram_id):
        """Получить полную информацию о пользователе"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                SELECT 
                    name, 
                    city, 
                    username, 
                    rating,
                    created_at,
                    (SELECT COUNT(*) FROM events WHERE creator_id = users.id) as events_created,
                    (SELECT COUNT(*) FROM event_participants WHERE user_id = users.id AND status = 'CONFIRMED') as bookings_made
                FROM users 
                WHERE telegram_id = ?
            """, (telegram_id,))
            return await cursor.fetchone()

db = Database()

# === УТИЛИТЫ ДЛЯ УВЕДОМЛЕНИЙ ===
async def notify_admin(event_type: str, data: dict):
    """Отправить уведомление всем администраторам"""
    if not ADMIN_IDS:
        return
    
    notifications = {
        'user_start': "🆕 Новый пользователь\n"
                     f"👤 @{data.get('username', 'без username')} (id: {data['user_id']})\n"
                     f"📅 Дата регистрации: {data.get('created_at', 'только что')}",
        
        'onboard_complete': "✅ Пользователь завершил онбординг\n"
                          f"👤 {data['name']} (@{data.get('username', 'без username')})\n"
                          f"🏙 Город: {data['city']}\n"
                          f"🆔 ID: {data['user_id']}",
        
        'event_created': "🎉 Создано новое событие\n"
                        f"🎯 Тип: {data['event_type']}\n"
                        f"🏙 Город: {data['city']}\n"
                        f"📅 Дата: {data['date']} {data['time']}\n"
                        f"👤 Создатель: {data['creator_name']} (@{data.get('creator_username', 'без username')})\n"
                        f"🆔 ID создателя: {data['creator_id']}",
        
        'booking_confirmed': "💳 Подтверждена новая бронь\n"
                           f"🎯 Событие: {data['event_type']}\n"
                           f"🏙 Город: {data['city']}\n"
                           f"👤 Участник: {data['participant_name']} (@{data.get('participant_username', 'без username')})\n"
                           f"🆔 ID участника: {data['participant_id']}\n"
                           f"💰 Сбор: {PLATFORM_FEE} ₽"
    }
    
    message = notifications.get(event_type)
    if not message:
        return
    
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, message)
        except Exception as e:
            logging.error(f"Failed to send notification to admin {admin_id}: {e}")

# === КЛАВИАТУРЫ ===
def get_cities_keyboard(page=0, items_per_page=8):
    """Клавиатура с пагинацией для выбора города"""
    start_idx = page * items_per_page
    end_idx = start_idx + items_per_page
    cities_slice = CITIES[start_idx:end_idx]
    
    buttons = []
    row = []
    for i, city in enumerate(cities_slice):
        row.append(InlineKeyboardButton(text=city, callback_data=f"city_select_{city}"))
        if i % 2 == 1:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"city_page_{page-1}"))
    if end_idx < len(CITIES):
        nav_buttons.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"city_page_{page+1}"))
    
    if nav_buttons:
        buttons.append(nav_buttons)
    
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_onboarding")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_main_menu_kb(telegram_id):
    """Главное меню с учетом роли пользователя"""
    keyboard = []
    
    if telegram_id in ADMIN_IDS:
        keyboard.append([KeyboardButton(text="👑 Админка")])
    
    keyboard.extend([
        [KeyboardButton(text="🔍 Найти событие")],
        [KeyboardButton(text="➕ Создать событие")],
        [KeyboardButton(text="👤 Мой профиль")],
        [KeyboardButton(text="ℹ️ Как пользоваться")]
    ])
    
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

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

def get_profile_kb(telegram_id, is_creator=False):
    """Клавиатура для профиля"""
    keyboard = []
    
    if telegram_id in ADMIN_IDS:
        keyboard.append([InlineKeyboardButton(text="👑 Админ-панель", callback_data="admin_panel")])
    
    keyboard.append([InlineKeyboardButton(text="📋 Мои брони", callback_data="my_bookings")])
    
    if is_creator:
        keyboard.append([InlineKeyboardButton(text="🎯 Мои события", callback_data="my_events")])
    
    keyboard.append([InlineKeyboardButton(text="⬅️ В главное меню", callback_data="back_to_main")])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_admin_kb():
    """Клавиатура админки"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
            [InlineKeyboardButton(text="👥 Все пользователи", callback_data="admin_all_users")],
            [InlineKeyboardButton(text="🎯 Все события", callback_data="admin_all_events")],
            [InlineKeyboardButton(text="⬅️ В профиль", callback_data="back_to_profile")]
        ]
    )

def get_my_events_kb(events):
    """Клавиатура для списка моих событий"""
    buttons = []
    for event in events:
        event_id, event_type, city, date_time, status, participants_count, max_participants = event
        
        status_emoji = "✅" if status == 'ACTIVE' else "❌"
        text = f"{status_emoji} {event_type[:15]} • {city} • {participants_count}/{max_participants}"
        
        buttons.append([
            InlineKeyboardButton(
                text=text,
                callback_data=f"my_event_{event_id}"
            )
        ])
    
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_profile")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_my_bookings_kb(bookings):
    """Клавиатура для списка моих бронирований"""
    buttons = []
    for booking in bookings:
        event_id, event_type, city, date_time, booking_date = booking
        
        booking_dt = datetime.fromisoformat(booking_date.replace(' ', 'T'))
        formatted_date = booking_dt.strftime("%d.%m.%Y")
        
        text = f"✅ {event_type[:15]} • {city} • {date_time[:10]}"
        
        buttons.append([
            InlineKeyboardButton(
                text=text,
                callback_data=f"view_event_{event_id}"
            )
        ])
    
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_profile")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_event_manage_kb(event_id):
    """Клавиатура для управления событием"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👥 Список участников", callback_data=f"event_participants_{event_id}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_my_events")]
        ]
    )

def get_participants_kb(event_id, participants):
    """Клавиатура со списком участников"""
    buttons = []
    for participant in participants:
        username, telegram_id, name, joined_at = participant
        display_name = f"@{username}" if username else name or f"ID: {telegram_id}"
        
        buttons.append([
            InlineKeyboardButton(
                text=f"👤 {display_name[:25]}",
                callback_data=f"user_info_{telegram_id}"
            )
        ])
    
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"my_event_{event_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# === ОБРАБОТЧИКИ КНОПОК ГЛАВНОГО МЕНЮ ===

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    """Старт бота с онбордингом"""
    await db.add_user(message.from_user.id, message.from_user.username)
    
    # Уведомление админу о новом пользователе
    await notify_admin('user_start', {
        'user_id': message.from_user.id,
        'username': message.from_user.username,
        'created_at': datetime.now().strftime("%d.%m.%Y %H:%M")
    })
    
    name, city, onboarded = await db.get_user_profile(message.from_user.id)
    
    if not onboarded:
        await state.set_state(OnboardingStates.NAME)
        await message.answer(
            "👋 Добро пожаловать в VIBEZ!\n\n"
            "Для начала расскажите немного о себе.\n\n"
            "Как вас зовут? (Введите ваше имя):",
            reply_markup=ReplyKeyboardRemove()
        )
    else:
        await state.set_state(MainStates.MAIN_MENU)
        await message.answer(
            f"👋 Привет, {name}!\n\n"
            "VIBEZ — бот для создания и поиска реальных событий в твоём городе.\n",
            reply_markup=get_main_menu_kb(message.from_user.id)
        )

@router.message(F.text == "👤 Мой профиль", MainStates.MAIN_MENU)
async def my_profile(message: Message, state: FSMContext):
    """Мой профиль - полная информация"""
    user_info = await db.get_user_full_info(message.from_user.id)
    
    if not user_info:
        await message.answer(
            "❌ Профиль не найден. Пройдите онбординг: /start",
            reply_markup=get_main_menu_kb(message.from_user.id)
        )
        return
    
    name, city, username, rating, created_at, events_created, bookings_made = user_info
    
    created_date = datetime.fromisoformat(created_at.replace(' ', 'T')).strftime("%d.%m.%Y")
    
    profile_text = (
        f"👤 <b>Ваш профиль</b>\n\n"
        f"<b>Имя:</b> {name}\n"
        f"<b>Город:</b> {city}\n"
        f"<b>Username:</b> @{username if username else 'не указан'}\n"
        f"<b>Рейтинг:</b> {rating} ⭐\n\n"
        f"<b>Статистика:</b>\n"
        f"• Создано событий: {events_created}\n"
        f"• Забронировано мест: {bookings_made}\n"
        f"• В системе с: {created_date}\n"
    )
    
    # Проверяем, является ли пользователь инициатором событий
    user_events = await db.get_user_created_events(message.from_user.id)
    is_creator = len(user_events) > 0
    
    await message.answer(
        profile_text,
        parse_mode="HTML",
        reply_markup=get_profile_kb(message.from_user.id, is_creator)
    )

@router.message(F.text == "ℹ️ Как пользоваться", MainStates.MAIN_MENU)
async def how_to_use(message: Message, state: FSMContext):
    """Как пользоваться"""
    await message.answer(
        "📖 <b>Как пользоваться VIBEZ:</b>\n\n"
        "1. 🔍 <b>Найти событие</b> — ищешь активные события в твоём городе\n"
        "2. ➕ <b>Создать событие</b> — организуешь свою встречу\n"
        "3. 💳 <b>Забронировать</b> — оплачиваешь участие (99 ₽ сервисный сбор)\n"
        "4. 📲 <b>Приглашать друзей</b> — делись ссылкой на событие\n\n"
        "<b>Важно:</b>\n"
        "• VIBEZ не создаёт чаты автоматически\n"
        "• Организатор связывается с участниками сам\n"
        "• Все платежи проходят через безопасную систему\n"
        "• Рейтинг формируется по отзывам участников",
        parse_mode="HTML",
        reply_markup=get_main_menu_kb(message.from_user.id)
    )

@router.message(F.text == "👑 Админка", MainStates.MAIN_MENU)
async def admin_access(message: Message, state: FSMContext):
    """Доступ к админке из главного меню"""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет доступа к админке")
        return
    
    await message.answer(
        "👑 <b>Панель администратора</b>\n\n"
        "Выберите действие:",
        parse_mode="HTML",
        reply_markup=get_admin_kb()
    )

# === ОНБОРДИНГ ===

@router.message(OnboardingStates.NAME)
async def process_name(message: Message, state: FSMContext):
    """Обработка ввода имени при онбординге"""
    if message.text == "❌ Отмена":
        await state.clear()
        await state.set_state(MainStates.MAIN_MENU)
        await message.answer("Онбординг отменен.", reply_markup=get_main_menu_kb(message.from_user.id))
        return
    
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

@router.callback_query(F.data.startswith("city_select_"))
async def process_city_selection(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора города"""
    city = callback.data.split("city_select_", 1)[1]
    data = await state.get_data()
    name = data['name']
    
    await db.update_user_profile(callback.from_user.id, name, city)
    
    # Уведомление админу о завершении онбординга
    await notify_admin('onboard_complete', {
        'user_id': callback.from_user.id,
        'name': name,
        'username': callback.from_user.username,
        'city': city
    })
    
    await state.set_state(MainStates.MAIN_MENU)
    await callback.message.edit_text(
        f"👋 Привет, {name}!\n\n"
        f"Город: {city}\n\n"
        "VIBEZ — бот для создания и поиска реальных событий в твоём городе."
    )
    await callback.message.answer(
        "Выберите действие:",
        reply_markup=get_main_menu_kb(callback.from_user.id)
    )
    await callback.answer()

# ... (остальной код онбординга без изменений)

# === КНОПКИ НАЗАД/ОТМЕНА ===

@router.message(F.text == "❌ Отмена", StateFilter(None, default_state))
@router.message(F.text == "❌ Отмена")
async def cancel_anywhere(message: Message, state: FSMContext):
    """Отмена в любом состоянии"""
    await state.clear()
    await state.set_state(MainStates.MAIN_MENU)
    await message.answer(
        "Действие отменено.",
        reply_markup=get_main_menu_kb(message.from_user.id)
    )

# ... (остальные обработчики назад/отмена без изменений)

# === СОЗДАНИЕ СОБЫТИЯ ===

@router.message(F.text == "➕ Создать событие", MainStates.MAIN_MENU)
async def start_create_event(message: Message, state: FSMContext):
    """Начало создания события"""
    name, city, onboarded = await db.get_user_profile(message.from_user.id)
    
    if not city:
        await message.answer("❌ Сначала завершите онбординг. Нажмите /start")
        return
    
    await state.update_data(city=city)
    await state.set_state(CreateEventStates.TYPE)
    
    await message.answer(
        "[Создание события 1/7]\n\n"
        "🎯 Выберите тип события:",
        reply_markup=get_event_types_kb()
    )

# ... (обработчики создания события без изменений до подтверждения)

@router.message(CreateEventStates.CONFIRMATION)
async def process_confirmation(message: Message, state: FSMContext):
    """Обработка подтверждения создания события"""
    if message.text == "❌ Отмена":
        await cancel_anywhere(message, state)
        return
    if message.text == "⬅️ Назад":
        await go_back(message, state)
        return
    
    if message.text == "✅ Да, создать событие":
        data = await state.get_data()
        
        event_id = await db.create_event(data, message.from_user.id)
        
        # Уведомление админу о создании события
        name, city, onboarded = await db.get_user_profile(message.from_user.id)
        event_type = data.get('custom_type') or data['type']
        
        await notify_admin('event_created', {
            'event_id': event_id,
            'event_type': event_type,
            'city': data['city'],
            'date': data['date'],
            'time': data['time'],
            'creator_id': message.from_user.id,
            'creator_name': name,
            'creator_username': message.from_user.username
        })
        
        invite_link = f"https://t.me/{bot._me.username}?start=invite_{event_id}_{message.from_user.id}"
        
        text = (
            "✅ <b>Событие создано!</b>\n\n"
            f"🎯 <b>Тип:</b> {event_type}\n"
            f"🏙️ <b>Город:</b> {data['city']}\n"
            f"📅 <b>Дата:</b> {data['date']}\n"
            f"⏰ <b>Время:</b> {data['time']}\n"
            f"👥 <b>Максимум участников:</b> {data['max_participants']}\n"
            f"📝 <b>Описание:</b> {data['description'][:200]}...\n"
            f"📞 <b>Ваш контакт:</b> {data['contact']}\n\n"
        )
        
        await state.clear()
        await state.set_state(MainStates.MAIN_MENU)
        await message.answer(text, reply_markup=get_main_menu_kb(message.from_user.id), parse_mode="HTML")
        
        instructions = (
            "📌 <b>Что дальше?</b>\n\n"
            "— Люди бронируют участие через бот\n"
            "— Ты получаешь их контакты в уведомлениях\n"
            "— VIBEZ <b>НЕ создаёт чаты</b> автоматически\n"
            "— Ты сам связываешься с участниками\n"
            "— При желании создаёшь чат вручную\n\n"
            "<i>Это сделано специально, чтобы:</i>\n"
            "• не было хаоса\n"
            "• ты контролировал процесс\n"
            "• люди реально доходили до встречи\n\n"
            f"🔗 <b>Ссылка для приглашения друга:</b>\n"
            f"<code>{invite_link}</code>"
        )
        
        await message.answer(instructions, parse_mode="HTML")
        
    elif message.text == "✏️ Нет, исправить":
        await state.set_state(CreateEventStates.TYPE)
        await message.answer(
            "[Создание события 1/7]\n\n"
            "Выберите тип события заново:",
            reply_markup=get_event_types_kb()
        )
    else:
        await message.answer(
            "Пожалуйста, выберите вариант из предложенных:",
            reply_markup=get_confirm_kb()
        )

# ... (обработчики поиска событий без изменений)

# === БРОНИРОВАНИЕ И ОПЛАТА ===

@router.callback_query(F.data.startswith("paid_"))
async def process_payment(callback: CallbackQuery, state: FSMContext):
    """Обработка подтверждения оплаты"""
    event_id = int(callback.data.split("_")[1])
    
    success, message = await db.add_participant(event_id, callback.from_user.id)
    
    if not success:
        await callback.answer(f"❌ {message}")
        return
    
    await db.confirm_participant(event_id, callback.from_user.id)
    
    # Уведомление админу о новой брони
    event = await db.get_event_details(event_id)
    if event:
        (event_type, custom_type, event_city, date, time, max_participants, 
         description, contact, status, creator_id, creator_username, 
         creator_name, confirmed_count) = event
        
        display_type = custom_type or event_type
        
        name, city, onboarded = await db.get_user_profile(callback.from_user.id)
        participant_name = name or callback.from_user.first_name or "Пользователь"
        
        await notify_admin('booking_confirmed', {
            'event_id': event_id,
            'event_type': display_type,
            'city': event_city,
            'participant_id': callback.from_user.id,
            'participant_name': participant_name,
            'participant_username': callback.from_user.username
        })
    
    # ... (остальная часть обработчика без изменений)

# === ПРОФИЛЬ: МОИ БРОНИ И СОБЫТИЯ ===

@router.callback_query(F.data == "my_bookings")
async def show_my_bookings(callback: CallbackQuery, state: FSMContext):
    """Показать мои бронирования"""
    bookings = await db.get_user_bookings(callback.from_user.id)
    
    if not bookings:
        await callback.message.edit_text(
            "📋 <b>Мои бронирования</b>\n\n"
            "У вас пока нет активных бронирований.\n\n"
            "Найдите интересное событие и забронируйте участие!",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔍 Найти события", callback_data="back_to_main")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_profile")]
            ])
        )
        await callback.answer()
        return
    
    bookings_text = "📋 <b>Мои бронирования</b>\n\n"
    
    for i, booking in enumerate(bookings[:10], 1):  # Ограничиваем 10 записями
        event_id, event_type, city, date_time, booking_date = booking
        booking_dt = datetime.fromisoformat(booking_date.replace(' ', 'T'))
        formatted_date = booking_dt.strftime("%d.%m.%Y")
        
        bookings_text += (
            f"{i}. <b>{event_type}</b>\n"
            f"   🏙 {city} | 📅 {date_time}\n"
            f"   🕐 Забронировано: {formatted_date}\n\n"
        )
    
    if len(bookings) > 10:
        bookings_text += f"\n... и ещё {len(bookings) - 10} бронирований"
    
    await callback.message.edit_text(
        bookings_text,
        parse_mode="HTML",
        reply_markup=get_my_bookings_kb(bookings[:10])
    )
    await callback.answer()

@router.callback_query(F.data == "my_events")
async def show_my_events(callback: CallbackQuery, state: FSMContext):
    """Показать мои события"""
    events = await db.get_user_created_events(callback.from_user.id)
    
    if not events:
        await callback.message.edit_text(
            "🎯 <b>Мои события</b>\n\n"
            "Вы ещё не создали ни одного события.\n\n"
            "Создайте первое событие и приглашайте участников!",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Создать событие", callback_data="back_to_main")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_profile")]
            ])
        )
        await callback.answer()
        return
    
    events_text = "🎯 <b>Мои события</b>\n\n"
    active_count = 0
    
    for event in events:
        event_id, event_type, city, date_time, status, participants_count, max_participants = event
        if status == 'ACTIVE':
            active_count += 1
            status_text = "✅ Активно"
        else:
            status_text = "❌ Неактивно"
        
        events_text += (
            f"<b>{event_type}</b>\n"
            f"🏙 {city} | 📅 {date_time}\n"
            f"👥 {participants_count}/{max_participants} участников\n"
            f"{status_text}\n\n"
        )
    
    events_text = f"🎯 <b>Мои события</b> ({active_count} активных)\n\n" + events_text[24:]
    
    await callback.message.edit_text(
        events_text,
        parse_mode="HTML",
        reply_markup=get_my_events_kb(events)
    )
    await callback.answer()

@router.callback_query(F.data.startswith("my_event_"))
async def show_my_event_details(callback: CallbackQuery, state: FSMContext):
    """Показать детали моего события"""
    event_id = int(callback.data.split("_")[2])
    
    event = await db.get_event_details(event_id)
    
    if not event:
        await callback.answer("❌ Событие не найдено")
        return
    
    (event_type, custom_type, city, date, time, max_participants, 
     description, contact, status, creator_id, creator_username, 
     creator_name, confirmed_count) = event
    
    display_type = custom_type or event_type
    
    participants = await db.get_event_participants_list(event_id)
    
    text = (
        f"🎯 <b>Детали события</b>\n\n"
        f"<b>Тип:</b> {display_type}\n"
        f"<b>Город:</b> {city}\n"
        f"<b>Дата и время:</b> {date} {time}\n"
        f"<b>Статус:</b> {'✅ Активно' if status == 'ACTIVE' else '❌ Неактивно'}\n"
        f"<b>Участники:</b> {confirmed_count}/{max_participants}\n"
        f"<b>Контакт для связи:</b> {contact}\n\n"
        f"<b>Описание:</b>\n{description}\n\n"
    )
    
    if participants:
        text += f"<b>Уже забронировали:</b> {len(participants)} участник(ов)\n"
    
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=get_event_manage_kb(event_id)
    )
    await callback.answer()

@router.callback_query(F.data.startswith("event_participants_"))
async def show_event_participants(callback: CallbackQuery):
    """Показать список участников события"""
    event_id = int(callback.data.split("_")[2])
    
    participants = await db.get_event_participants_list(event_id)
    
    if not participants:
        await callback.message.edit_text(
            "👥 <b>Участники события</b>\n\n"
            "Пока нет подтверждённых участников.\n",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"my_event_{event_id}")]
            ])
        )
        await callback.answer()
        return
    
    participants_text = "👥 <b>Участники события</b>\n\n"
    
    for i, participant in enumerate(participants, 1):
        username, telegram_id, name, joined_at = participant
        display_name = f"@{username}" if username else name or f"ID: {telegram_id}"
        join_date = datetime.fromisoformat(joined_at.replace(' ', 'T')).strftime("%d.%m")
        
        participants_text += f"{i}. {display_name}\n   🆔 {telegram_id} | 📅 {join_date}\n"
    
    participants_text += f"\n<b>Всего:</b> {len(participants)} участник(ов)"
    
    await callback.message.edit_text(
        participants_text,
        parse_mode="HTML",
        reply_markup=get_participants_kb(event_id, participants)
    )
    await callback.answer()

# === АДМИН-ПАНЕЛЬ ===

@router.callback_query(F.data == "admin_panel")
async def admin_panel(callback: CallbackQuery):
    """Админ-панель"""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ У вас нет доступа")
        return
    
    await callback.message.edit_text(
        "👑 <b>Панель администратора</b>\n\n"
        "Выберите раздел:",
        parse_mode="HTML",
        reply_markup=get_admin_kb()
    )
    await callback.answer()

@router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    """Статистика админки"""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ У вас нет доступа")
        return
    
    stats = await db.get_admin_stats()
    
    top_cities_text = ""
    for city, count in stats['top_cities']:
        top_cities_text += f"• {city}: {count} событий\n"
    
    if not top_cities_text:
        top_cities_text = "Нет данных"
    
    stats_text = (
        "📊 <b>Статистика платформы</b>\n\n"
        f"<b>👥 Пользователи:</b>\n"
        f"• Всего: {stats['total_users']}\n"
        f"• Новых за неделю: {stats['new_users_week']}\n\n"
        
        f"<b>🎯 События:</b>\n"
        f"• Всего: {stats['total_events']}\n"
        f"• Активных: {stats['active_events']}\n\n"
        
        f"<b>💳 Бронирования:</b>\n"
        f"• Всего: {stats['total_bookings']}\n"
        f"• Оборот: {stats['total_revenue']} ₽\n\n"
        
        f"<b>📍 Топ городов:</b>\n{top_cities_text}"
    )
    
    await callback.message.edit_text(
        stats_text,
        parse_mode="HTML",
        reply_markup=get_admin_kb()
    )
    await callback.answer()

@router.callback_query(F.data == "admin_all_users")
async def admin_all_users(callback: CallbackQuery):
    """Список всех пользователей"""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ У вас нет доступа")
        return
    
    # Получаем краткий список пользователей
    async with aiosqlite.connect('vibez.db') as db_conn:
        cursor = await db_conn.execute("""
            SELECT telegram_id, username, name, city, created_at 
            FROM users 
            WHERE onboarded = 1 
            ORDER BY created_at DESC 
            LIMIT 20
        """)
        users = await cursor.fetchall()
    
    if not users:
        await callback.message.edit_text(
            "👥 <b>Пользователи</b>\n\n"
            "Нет зарегистрированных пользователей.",
            parse_mode="HTML",
            reply_markup=get_admin_kb()
        )
        await callback.answer()
        return
    
    users_text = "👥 <b>Последние 20 пользователей</b>\n\n"
    
    for user in users:
        telegram_id, username, name, city, created_at = user
        created_date = datetime.fromisoformat(created_at.replace(' ', 'T')).strftime("%d.%m")
        
        display_name = f"@{username}" if username else name or f"ID: {telegram_id}"
        users_text += f"• {display_name} | 🏙 {city or '?'} | 📅 {created_date}\n"
    
    await callback.message.edit_text(
        users_text,
        parse_mode="HTML",
        reply_markup=get_admin_kb()
    )
    await callback.answer()

@router.callback_query(F.data == "admin_all_events")
async def admin_all_events(callback: CallbackQuery):
    """Список всех событий"""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ У вас нет доступа")
        return
    
    async with aiosqlite.connect('vibez.db') as db_conn:
        cursor = await db_conn.execute("""
            SELECT 
                e.id,
                CASE WHEN e.custom_type IS NOT NULL THEN e.custom_type ELSE e.type END as display_type,
                e.city,
                e.date || ' ' || e.time as date_time,
                e.status,
                u.username as creator_username,
                (SELECT COUNT(*) FROM event_participants ep 
                 WHERE ep.event_id = e.id AND ep.status = 'CONFIRMED') as participants_count
            FROM events e
            LEFT JOIN users u ON e.creator_id = u.id
            ORDER BY e.created_at DESC 
            LIMIT 15
        """)
        events = await cursor.fetchall()
    
    if not events:
        await callback.message.edit_text(
            "🎯 <b>События</b>\n\n"
            "Нет созданных событий.",
            parse_mode="HTML",
            reply_markup=get_admin_kb()
        )
        await callback.answer()
        return
    
    events_text = "🎯 <b>Последние 15 событий</b>\n\n"
    
    for event in events:
        event_id, event_type, city, date_time, status, creator_username, participants_count = event
        status_emoji = "✅" if status == 'ACTIVE' else "❌"
        creator = f"@{creator_username}" if creator_username else "Аноним"
        
        events_text += f"{status_emoji} <b>{event_type}</b>\n"
        events_text += f"   🏙 {city} | 📅 {date_time}\n"
        events_text += f"   👤 {creator} | 👥 {participants_count}\n\n"
    
    await callback.message.edit_text(
        events_text,
        parse_mode="HTML",
        reply_markup=get_admin_kb()
    )
    await callback.answer()

# === НАВИГАЦИОННЫЕ КНОПКИ ДЛЯ ПРОФИЛЯ ===

@router.callback_query(F.data == "back_to_profile")
async def back_to_profile(callback: CallbackQuery, state: FSMContext):
    """Возврат в профиль"""
    user_info = await db.get_user_full_info(callback.from_user.id)
    
    if not user_info:
        await callback.answer("❌ Профиль не найден")
        return
    
    name, city, username, rating, created_at, events_created, bookings_made = user_info
    created_date = datetime.fromisoformat(created_at.replace(' ', 'T')).strftime("%d.%m.%Y")
    
    profile_text = (
        f"👤 <b>Ваш профиль</b>\n\n"
        f"<b>Имя:</b> {name}\n"
        f"<b>Город:</b> {city}\n"
        f"<b>Username:</b> @{username if username else 'не указан'}\n"
        f"<b>Рейтинг:</b> {rating} ⭐\n\n"
        f"<b>Статистика:</b>\n"
        f"• Создано событий: {events_created}\n"
        f"• Забронировано мест: {bookings_made}\n"
        f"• В системе с: {created_date}\n"
    )
    
    user_events = await db.get_user_created_events(callback.from_user.id)
    is_creator = len(user_events) > 0
    
    await callback.message.edit_text(
        profile_text,
        parse_mode="HTML",
        reply_markup=get_profile_kb(callback.from_user.id, is_creator)
    )
    await callback.answer()

@router.callback_query(F.data == "back_to_my_events")
async def back_to_my_events(callback: CallbackQuery):
    """Возврат к списку моих событий"""
    events = await db.get_user_created_events(callback.from_user.id)
    
    if not events:
        await callback.message.edit_text(
            "🎯 <b>Мои события</b>\n\n"
            "Вы ещё не создали ни одного события.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_profile")]
            ])
        )
        await callback.answer()
        return
    
    events_text = "🎯 <b>Мои события</b>\n\n"
    active_count = 0
    
    for event in events:
        event_id, event_type, city, date_time, status, participants_count, max_participants = event
        if status == 'ACTIVE':
            active_count += 1
        
        events_text += (
            f"<b>{event_type}</b>\n"
            f"🏙 {city} | 📅 {date_time}\n"
            f"👥 {participants_count}/{max_participants} участников\n"
            f"{'✅ Активно' if status == 'ACTIVE' else '❌ Неактивно'}\n\n"
        )
    
    events_text = f"🎯 <b>Мои события</b> ({active_count} активных)\n\n" + events_text[24:]
    
    await callback.message.edit_text(
        events_text,
        parse_mode="HTML",
        reply_markup=get_my_events_kb(events)
    )
    await callback.answer()

# === НАВИГАЦИОННЫЕ КНОПКИ ===

@router.callback_query(F.data == "back_to_main")
async def back_to_main_menu(callback: CallbackQuery, state: FSMContext):
    """Возврат в главное меню"""
    await state.set_state(MainStates.MAIN_MENU)
    await callback.message.edit_text("Возврат в главное меню:")
    await callback.message.answer(
        "Выберите действие:",
        reply_markup=get_main_menu_kb(callback.from_user.id)
    )
    await callback.answer()

# ... (остальные навигационные кнопки без изменений)

# === ЗАПУСК БОТА ===

async def main():
    """Основная функция запуска бота"""
    await db.init_db()
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    asyncio.run(main())
