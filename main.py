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
            
            event_id = cursor.lastrowid
            
            # Автоматически добавляем создателя как подтвержденного участника
            if creator_id:
                await db.execute("""
                    INSERT OR IGNORE INTO event_participants (event_id, user_id, status)
                    VALUES (?, ?, 'CONFIRMED')
                """, (event_id, creator_id))
            
            await db.commit()
            return event_id

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

    async def get_all_confirmed_participants(self, event_id, exclude_telegram_id=None):
        """Получить всех подтвержденных участников, кроме указанного"""
        async with aiosqlite.connect(self.db_path) as db:
            if exclude_telegram_id:
                user_id = await self.get_user_id(exclude_telegram_id)
                cursor = await db.execute("""
                    SELECT u.telegram_id, u.username, u.name
                    FROM event_participants ep
                    JOIN users u ON ep.user_id = u.id
                    WHERE ep.event_id = ? AND ep.status = 'CONFIRMED' AND u.telegram_id != ?
                """, (event_id, exclude_telegram_id))
            else:
                cursor = await db.execute("""
                    SELECT u.telegram_id, u.username, u.name
                    FROM event_participants ep
                    JOIN users u ON ep.user_id = u.id
                    WHERE ep.event_id = ? AND ep.status = 'CONFIRMED'
                """, (event_id,))
            return await cursor.fetchall()

    async def get_admin_stats(self):
        """Получить статистику для админки"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM users WHERE onboarded = 1")
            total_users = (await cursor.fetchone())[0]
            
            cursor = await db.execute("SELECT COUNT(*) FROM events")
            total_events = (await cursor.fetchone())[0]
            
            cursor = await db.execute("SELECT COUNT(*) FROM event_participants WHERE status = 'CONFIRMED'")
            total_bookings = (await cursor.fetchone())[0]
            
            total_revenue = total_bookings * PLATFORM_FEE
            
            cursor = await db.execute("""
                SELECT city, COUNT(*) as count 
                FROM events 
                WHERE city IS NOT NULL AND city != ''
                GROUP BY city 
                ORDER BY count DESC 
                LIMIT 5
            """)
            top_cities = await cursor.fetchall()
            
            cursor = await db.execute("SELECT COUNT(*) FROM events WHERE status = 'ACTIVE'")
            active_events = (await cursor.fetchone())[0]
            
            return {
                'total_users': total_users,
                'total_events': total_events,
                'total_bookings': total_bookings,
                'total_revenue': total_revenue,
                'top_cities': top_cities,
                'active_events': active_events
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
async def notify_admin_booking(event_data: dict):
    """Уведомить администратора о новой брони"""
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"🔔 Новая бронь\n\n"
                f"Событие: {event_data['event_title']}\n"
                f"Город: {event_data['city']}\n"
                f"Дата: {event_data['date']}\n\n"
                f"Пользователь:\n"
                f"@{event_data['username']} (id: {event_data['user_id']})\n\n"
                f"Подтверждено: {event_data['confirmed_count']} / {event_data['max_participants']}"
            )
        except Exception as e:
            logging.error(f"Failed to send notification to admin {admin_id}: {e}")

async def notify_event_participants(event_id: int, new_participant_data: dict):
    """Уведомить всех участников события о новом участнике"""
    try:
        # Получаем всех подтвержденных участников, кроме нового
        participants = await db.get_all_confirmed_participants(event_id, new_participant_data['telegram_id'])
        
        # Получаем детали события
        event = await db.get_event_details(event_id)
        if not event:
            return
        
        event_type = event[1] or event[0]  # custom_type or type
        confirmed_count = event[12]  # confirmed_count
        
        for participant in participants:
            participant_id, username, name = participant
            try:
                await bot.send_message(
                    participant_id,
                    f"🔥 Новый участник!\n\n"
                    f"@{new_participant_data['username']} присоединился к событию «{event_type}»\n\n"
                    f"Участников: {confirmed_count} / {event[5]}"  # confirmed_count / max_participants
                )
            except Exception as e:
                logging.error(f"Failed to send notification to participant {participant_id}: {e}")
    except Exception as e:
        logging.error(f"Failed to send participant notifications: {e}")

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
    args = message.text.split()
    
    if len(args) > 1 and args[1].startswith("invite_"):
        try:
            parts = args[1].split("_")
            event_id = int(parts[1])
            inviter_id = int(parts[2]) if len(parts) > 2 else None
            
            await db.add_user(message.from_user.id, message.from_user.username)
            
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
                # Показываем событие, на которое пригласили
                event = await db.get_event_details(event_id)
                if event:
                    (event_type, custom_type, event_city, date, time, max_participants, 
                     description, contact, status, creator_id, creator_username, 
                     creator_name, confirmed_count) = event
                    
                    display_type = custom_type or event_type
                    
                    is_confirmed = await db.is_user_confirmed(event_id, message.from_user.id)
                    
                    text = (
                        f"🎉 <b>Вас пригласили на событие!</b>\n\n"
                        f"📋 <b>Детали события:</b>\n\n"
                        f"🎯 <b>Тип:</b> {display_type}\n"
                        f"🏙️ <b>Город:</b> {event_city}\n"
                        f"📅 <b>Дата:</b> {date} {time}\n"
                        f"👤 <b>Инициатор:</b> {creator_name or '@' + creator_username}\n"
                        f"📞 <b>Контакт для связи:</b> {contact}\n"
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
                    await state.set_state(MainStates.VIEWING_EVENT)
                else:
                    await message.answer("❌ Событие не найдено")
                return
        except Exception as e:
            logging.error(f"Error processing invite: {e}")
    
    # Обычный старт
    await db.add_user(message.from_user.id, message.from_user.username)
    
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
    
    # Проверяем, был ли это инвайт
    invite_event_id = data.get('invite_event_id')
    
    if invite_event_id:
        # Показываем событие, на которое пригласили
        await state.clear()
        
        event = await db.get_event_details(invite_event_id)
        if event:
            (event_type, custom_type, event_city, date, time, max_participants, 
             description, contact, status, creator_id, creator_username, 
             creator_name, confirmed_count) = event
            
            display_type = custom_type or event_type
            
            is_confirmed = await db.is_user_confirmed(invite_event_id, callback.from_user.id)
            
            text = (
                f"🎉 <b>Вас пригласили на событие!</b>\n\n"
                f"📋 <b>Детали события:</b>\n\n"
                f"🎯 <b>Тип:</b> {display_type}\n"
                f"🏙️ <b>Город:</b> {event_city}\n"
                f"📅 <b>Дата:</b> {date} {time}\n"
                f"👤 <b>Инициатор:</b> {creator_name or '@' + creator_username}\n"
                f"📞 <b>Контакт для связи:</b> {contact}\n"
                f"✅ <b>Забронировано:</b> {confirmed_count}/{max_participants} участников\n\n"
                f"📝 <b>Описание:</b>\n{description}\n\n"
            )
            
            if is_confirmed:
                text += "✅ <b>Вы уже участвуете в этом событии</b>"
            else:
                text += "<i>Для бронирования нажмите кнопку 'Забронировать'</i>"
            
            await callback.message.edit_text(text, parse_mode="HTML")
            await callback.message.answer(
                text, 
                reply_markup=get_event_details_kb(invite_event_id, callback.from_user.id, is_confirmed), 
                parse_mode="HTML"
            )
            await state.set_state(MainStates.VIEWING_EVENT)
        else:
            await callback.message.edit_text(
                f"👋 Привет, {name}!\n\n"
                f"Город: {city}\n\n"
                "VIBEZ — бот для создания и поиска реальных событий в твоём городе."
            )
            await state.set_state(MainStates.MAIN_MENU)
            await callback.message.answer(
                "Выберите действие:",
                reply_markup=get_main_menu_kb(callback.from_user.id)
            )
    else:
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

@router.callback_query(F.data.startswith("city_page_"))
async def process_city_pagination(callback: CallbackQuery, state: FSMContext):
    """Обработка пагинации городов"""
    page = int(callback.data.split("city_page_")[1])
    await callback.message.edit_reply_markup(reply_markup=get_cities_keyboard(page))
    await callback.answer()

@router.callback_query(F.data == "cancel_onboarding")
async def cancel_onboarding(callback: CallbackQuery, state: FSMContext):
    """Отмена онбординга"""
    await state.clear()
    await state.set_state(MainStates.MAIN_MENU)
    await callback.message.edit_text("Онбординг отменен.")
    await callback.message.answer(
        "Выберите действие:",
        reply_markup=get_main_menu_kb(callback.from_user.id)
    )
    await callback.answer()

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

@router.message(F.text == "⬅️ Назад")
async def go_back(message: Message, state: FSMContext):
    """Назад в любом состоянии"""
    current_state = await state.get_state()
    
    if current_state == CreateEventStates.TYPE:
        await state.set_state(MainStates.MAIN_MENU)
        await message.answer("Возврат в главное меню:", reply_markup=get_main_menu_kb(message.from_user.id))
    
    elif current_state == CreateEventStates.TYPE_OTHER:
        await state.set_state(CreateEventStates.TYPE)
        await message.answer(
            "[Создание события 1/7]\n\n"
            "🎯 Выберите тип события:",
            reply_markup=get_event_types_kb()
        )
    
    elif current_state == CreateEventStates.DATE:
        await state.set_state(CreateEventStates.TYPE)
        await message.answer(
            "[Создание события 1/7]\n\n"
            "🎯 Выберите тип события:",
            reply_markup=get_event_types_kb()
        )
    
    elif current_state == CreateEventStates.TIME:
        await state.set_state(CreateEventStates.DATE)
        await message.answer(
            "[Создание события 2/7]\n\n"
            "Введите дату в формате ДД.ММ.ГГГГ\n"
            "Например: 25.12.2024",
            reply_markup=get_back_cancel_kb()
        )
    
    elif current_state == CreateEventStates.MAX_PARTICIPANTS:
        await state.set_state(CreateEventStates.TIME)
        await message.answer(
            "[Создание события 3/7]\n\n"
            "Введите время в формате ЧЧ:ММ\n"
            "Например: 19:00",
            reply_markup=get_back_cancel_kb()
        )
    
    elif current_state == CreateEventStates.DESCRIPTION:
        await state.set_state(CreateEventStates.MAX_PARTICIPANTS)
        await message.answer(
            "[Создание события 4/7]\n\n"
            "Введите максимальное количество участников:",
            reply_markup=get_back_cancel_kb()
        )
    
    elif current_state == CreateEventStates.CONTACT:
        await state.set_state(CreateEventStates.DESCRIPTION)
        await message.answer(
            "[Создание события 5/7]\n\n"
            "📝 Введите описание события (обязательно):",
            reply_markup=get_back_cancel_kb()
        )
    
    elif current_state == CreateEventStates.CONFIRMATION:
        await state.set_state(CreateEventStates.CONTACT)
        await message.answer(
            "[Создание события 6/7]\n\n"
            "📞 Введите ваш контакт для связи с участниками:",
            reply_markup=get_back_cancel_kb()
        )
    
    else:
        await state.set_state(MainStates.MAIN_MENU)
        await message.answer("Возврат в главное меню:", reply_markup=get_main_menu_kb(message.from_user.id))

# === СОЗДАНИЕ СОБЫТИЯ ===

@router.message(F.text == "➕ Создать событие", MainStates.MAIN_MENU)
async def start_create_event(message: Message, state: FSMContext):
    """Начало создания события"""
    name, city, onboarded = await db.get_user_profile(message.from_user.id)
    
    if not onboarded:
        await message.answer("❌ Сначала завершите онбординг. Нажмите /start")
        return
    
    await state.update_data(city=city)
    await state.set_state(CreateEventStates.TYPE)
    
    await message.answer(
        "[Создание события 1/7]\n\n"
        "🎯 Выберите тип события:",
        reply_markup=get_event_types_kb()
    )

@router.message(CreateEventStates.TYPE)
async def process_event_type(message: Message, state: FSMContext):
    """Обработка выбора типа события"""
    if message.text == "❌ Отмена":
        await cancel_anywhere(message, state)
        return
    if message.text == "⬅️ Назад":
        await go_back(message, state)
        return
    
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
        "[Создание события 2/7]\n\n"
        f"Тип: {event_type}\n\n"
        "Введите дату в формате ДД.ММ.ГГГГ\n"
        "Например: 25.12.2024",
        reply_markup=get_back_cancel_kb()
    )

@router.message(CreateEventStates.TYPE_OTHER)
async def process_event_type_other(message: Message, state: FSMContext):
    """Обработка ввода названия для типа 'Другое'"""
    if message.text == "❌ Отмена":
        await cancel_anywhere(message, state)
        return
    if message.text == "⬅️ Назад":
        await go_back(message, state)
        return
    
    custom_type = message.text.strip()
    
    if len(custom_type) < 3:
        await message.answer("Название события должно содержать минимум 3 символа. Введите снова:")
        return
    
    await state.update_data(type="Другое", custom_type=custom_type)
    await state.set_state(CreateEventStates.DATE)
    
    await message.answer(
        "[Создание события 2/7]\n\n"
        f"Тип: {custom_type}\n\n"
        "Введите дату в формате ДД.ММ.ГГГГ\n"
        "Например: 25.12.2024",
        reply_markup=get_back_cancel_kb()
    )

@router.message(CreateEventStates.DATE)
async def process_event_date(message: Message, state: FSMContext):
    """Обработка ввода даты"""
    if message.text == "❌ Отмена":
        await cancel_anywhere(message, state)
        return
    if message.text == "⬅️ Назад":
        await go_back(message, state)
        return
    
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
        "[Создание события 3/7]\n\n"
        f"Дата: {date_str}\n\n"
        "Введите время в формате ЧЧ:ММ\n"
        "Например: 19:00",
        reply_markup=get_back_cancel_kb()
    )

@router.message(CreateEventStates.TIME)
async def process_event_time(message: Message, state: FSMContext):
    """Обработка ввода времени"""
    if message.text == "❌ Отмена":
        await cancel_anywhere(message, state)
        return
    if message.text == "⬅️ Назад":
        await go_back(message, state)
        return
    
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
        "[Создание события 4/7]\n\n"
        f"Время: {time_str}\n\n"
        "Введите максимальное количество участников:",
        reply_markup=get_back_cancel_kb()
    )

@router.message(CreateEventStates.MAX_PARTICIPANTS)
async def process_max_participants(message: Message, state: FSMContext):
    """Обработка ввода максимального количества участников"""
    if message.text == "❌ Отмена":
        await cancel_anywhere(message, state)
        return
    if message.text == "⬅️ Назад":
        await go_back(message, state)
        return
    
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
        "[Создание события 5/7]\n\n"
        f"Максимум участников: {max_participants}\n\n"
        "📝 Введите описание события:",
        reply_markup=get_back_cancel_kb()
    )

@router.message(CreateEventStates.DESCRIPTION)
async def process_description(message: Message, state: FSMContext):
    """Обработка ввода описания"""
    if message.text == "❌ Отмена":
        await cancel_anywhere(message, state)
        return
    if message.text == "⬅️ Назад":
        await go_back(message, state)
        return
    
    description = message.text.strip()
    
    if len(description) < 10:
        await message.answer(
            "❌ Описание слишком короткое. Минимум 10 символов.\n"
            "Опишите подробно, что будет происходить:"
        )
        return
    
    await state.update_data(description=description)
    await state.set_state(CreateEventStates.CONTACT)
    
    await message.answer(
        "[Создание события 6/7]\n\n"
        f"Описание: {description[:100]}...\n\n"
        "📞 Введите ваш контакт для связи с участниками:",
        reply_markup=get_back_cancel_kb()
    )

@router.message(CreateEventStates.CONTACT)
async def process_contact(message: Message, state: FSMContext):
    """Обработка ввода контакта инициатора"""
    if message.text == "❌ Отмена":
        await cancel_anywhere(message, state)
        return
    if message.text == "⬅️ Назад":
        await go_back(message, state)
        return
    
    contact = message.text.strip()
    
    if len(contact) < 3:
        await message.answer(
            "❌ Контакт слишком короткий. Минимум 3 символа.\n"
            "Введите ваш контакт для связи:"
        )
        return
    
    await state.update_data(contact=contact)
    await state.set_state(CreateEventStates.CONFIRMATION)
    
    data = await state.get_data()
    event_type = data.get('custom_type') or data['type']
    
    text = (
        "[Создание события 7/7]\n\n"
        "✅ <b>Проверьте данные события:</b>\n\n"
        f"🎯 <b>Тип:</b> {event_type}\n"
        f"🏙️ <b>Город:</b> {data['city']}\n"
        f"📅 <b>Дата:</b> {data['date']}\n"
        f"⏰ <b>Время:</b> {data['time']}\n"
        f"👥 <b>Максимум участников:</b> {data['max_participants']}\n"
        f"📝 <b>Описание:</b> {data['description'][:100]}...\n"
        f"📞 <b>Контакт для связи:</b> {contact}\n\n"
        "<b>Всё верно?</b>"
    )
    
    await message.answer(text, reply_markup=get_confirm_kb(), parse_mode="HTML")

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
        
        invite_link = f"https://t.me/{bot._me.username}?start=invite_{event_id}_{message.from_user.id}"
        
        event_type = data.get('custom_type') or data['type']
        
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
            "— Вы автоматически добавлены как участник\n"
            "— Люди бронируют участие через бот\n"
            "— Ты получаешь их контакты в уведомлениях\n"
            "— VIBEZ <b>НЕ создаёт чаты</b> автоматически\n"
            "— Ты сам связываешься с участниками\n"
            "— При желании создаёшь чат вручную\n\n"
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

# === ПОИСК СОБЫТИЙ ===

@router.message(F.text == "🔍 Найти событие", MainStates.MAIN_MENU)
async def start_search(message: Message, state: FSMContext):
    """Начало поиска событий"""
    name, city, onboarded = await db.get_user_profile(message.from_user.id)
    
    if not onboarded:
        await message.answer("❌ Сначала завершите онбординг. Нажмите /start")
        return
    
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
    
    event = await db.get_event_details(event_id)
    
    if not event:
        await callback.answer("❌ Событие не найдено")
        await state.set_state(MainStates.MAIN_MENU)
        await callback.message.answer(
            "Событие не найдено. Вернитесь в главное меню:",
            reply_markup=get_main_menu_kb(callback.from_user.id)
        )
        return
    
    (event_type, custom_type, city, date, time, max_participants, 
     description, contact, status, creator_id, creator_username, 
     creator_name, confirmed_count) = event
    
    display_type = custom_type or event_type
    
    await state.update_data(current_event_id=event_id)
    await state.set_state(MainStates.VIEWING_EVENT)
    
    is_confirmed = await db.is_user_confirmed(event_id, callback.from_user.id)
    
    text = (
        f"📋 <b>Детали события:</b>\n\n"
        f"🎯 <b>Тип:</b> {display_type}\n"
        f"🏙️ <b>Город:</b> {city}\n"
        f"📅 <b>Дата:</b> {date}\n"
        f"⏰ <b>Время:</b> {time}\n"
        f"👤 <b>Инициатор:</b> {creator_name or '@' + creator_username}\n"
        f"📞 <b>Контакт для связи:</b> {contact}\n"
        f"✅ <b>Забронировано:</b> {confirmed_count}/{max_participants} участников\n"
        f"📊 <b>Статус:</b> {status}\n\n"
        f"📝 <b>Описание:</b>\n{description}\n\n"
    )
    
    if is_confirmed:
        text += "✅ <b>Вы уже участвуете в этом событии</b>"
    else:
        text += "<i>Для бронирования нажмите кнопку 'Забронировать'</i>"
    
    await callback.message.edit_text(
        text, 
        reply_markup=get_event_details_kb(event_id, callback.from_user.id, is_confirmed), 
        parse_mode="HTML"
    )
    await callback.answer()

# === БРОНИРОВАНИЕ И ОПЛАТА ===

@router.callback_query(F.data.startswith("join_"))
async def join_event_start(callback: CallbackQuery, state: FSMContext):
    """Начало бронирования события"""
    event_id = int(callback.data.split("_")[1])
    
    event = await db.get_event_details(event_id)
    
    if not event:
        await callback.answer("❌ Событие не найдено")
        return
    
    (event_type, custom_type, city, date, time, max_participants, 
     description, contact, status, creator_id, creator_username, 
     creator_name, confirmed_count) = event
    
    display_type = custom_type or event_type
    
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
        "<b>Для подтверждения бронирования\n"
        f"оплатите сервисный сбор {PLATFORM_FEE} ₽\n"
        "по ссылке ниже 👇</b>"
    )
    
    await callback.message.edit_text(text, reply_markup=get_payment_kb(event_id), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("paid_"))
async def process_payment(callback: CallbackQuery, state: FSMContext):
    """Обработка подтверждения оплаты"""
    event_id = int(callback.data.split("_")[1])
    
    success, message = await db.add_participant(event_id, callback.from_user.id)
    
    if not success:
        await callback.answer(f"❌ {message}")
        return
    
    await db.confirm_participant(event_id, callback.from_user.id)
    
    name, city, onboarded = await db.get_user_profile(callback.from_user.id)
    participant_name = name or callback.from_user.first_name or "Пользователь"
    participant_username = callback.from_user.username or "нет username"
    
    event = await db.get_event_details(event_id)
    if event:
        (event_type, custom_type, event_city, date, time, max_participants, 
         description, contact, status, creator_id, creator_username, 
         creator_name, confirmed_count) = event
        
        display_type = custom_type or event_type
        
        # Уведомление администратору
        await notify_admin_booking({
            'event_title': display_type,
            'city': event_city,
            'date': f"{date} {time}",
            'username': participant_username,
            'user_id': callback.from_user.id,
            'confirmed_count': confirmed_count,
            'max_participants': max_participants
        })
        
        # Уведомление участникам события
        await notify_event_participants(event_id, {
            'telegram_id': callback.from_user.id,
            'username': participant_username,
            'name': participant_name
        })
        
        text = (
            "✅ <b>Оплата подтверждена!</b>\n\n"
            "Вы успешно забронировали участие в событии.\n\n"
            f"🎯 <b>Событие:</b> {display_type}\n"
            f"🏙️ <b>Город:</b> {event_city}\n"
            f"📅 <b>Дата:</b> {date} {time}\n"
            f"📞 <b>Контакт инициатора:</b> {contact}\n\n"
            "📋 <b>Что дальше:</b>\n"
            "1. Ждем встречи в назначенное время\n"
            "2. Инициатор свяжется с вами по указанному контакту\n"
            "3. Приходите вовремя и наслаждайтесь событием!\n\n"
            "🔥 <b>Пригласите друзей — так будет веселее!</b>"
        )
        
        await state.set_state(MainStates.MAIN_MENU)
        await callback.message.edit_text(text, parse_mode="HTML")
        
        await callback.message.answer(
            "📲 Пригласите друзей:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text="📲 Пригласить друга", 
                    callback_data=f"invite_{event_id}_{callback.from_user.id}"
                )
            ]])
        )
        
        await callback.message.answer(
            "Выберите действие:",
            reply_markup=get_main_menu_kb(callback.from_user.id)
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
        f"• Всего: {stats['total_users']}\n\n"
        
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

@router.callback_query(F.data == "back_to_search")
async def back_to_search(callback: CallbackQuery, state: FSMContext):
    """Возврат к поиску"""
    await state.set_state(SearchEventsStates.SELECT_EVENT)
    
    name, city, onboarded = await db.get_user_profile(callback.from_user.id)
    events = await db.get_events_by_city(city)
    
    if events:
        text = f"✅ <b>Найдено событий в {city}: {len(events)}</b>\n\nВыберите событие:"
        await callback.message.edit_text(text, reply_markup=get_event_list_kb(events), parse_mode="HTML")
    else:
        await callback.message.edit_text(f"😔 <b>В городе {city} пока нет активных событий.</b>", parse_mode="HTML")
        await callback.message.answer(
            "Возврат в главное меню:",
            reply_markup=get_main_menu_kb(callback.from_user.id)
        )
        await state.set_state(MainStates.MAIN_MENU)
    
    await callback.answer()

@router.callback_query(F.data.startswith("back_to_event_"))
async def back_to_event(callback: CallbackQuery, state: FSMContext):
    """Возврат к событию"""
    event_id = int(callback.data.split("_")[3])
    
    event = await db.get_event_details(event_id)
    
    if event:
        (event_type, custom_type, city, date, time, max_participants, 
         description, contact, status, creator_id, creator_username, 
         creator_name, confirmed_count) = event
        
        display_type = custom_type or event_type
        
        is_confirmed = await db.is_user_confirmed(event_id, callback.from_user.id)
        
        text = (
            f"📋 <b>Детали события:</b>\n\n"
            f"🎯 <b>Тип:</b> {display_type}\n"
            f"🏙️ <b>Город:</b> {city}\n"
            f"📅 <b>Дата:</b> {date}\n"
            f"⏰ <b>Время:</b> {time}\n"
            f"👤 <b>Инициатор:</b> {creator_name or '@' + creator_username}\n"
            f"📞 <b>Контакт для связи:</b> {contact}\n"
            f"✅ <b>Забронировано:</b> {confirmed_count}/{max_participants} участников\n"
            f"📊 <b>Статус:</b> {status}\n\n"
            f"📝 <b>Описание:</b>\n{description}\n"
        )
        
        await callback.message.edit_text(
            text, 
            reply_markup=get_event_details_kb(event_id, callback.from_user.id, is_confirmed), 
            parse_mode="HTML"
        )
    
    await callback.answer()

@router.callback_query(F.data == "back_to_profile")
async def back_to_profile(callback: CallbackQuery, state: FSMContext):
    """Возврат в профиль"""
    user_info = await db.get_user_full_info(callback.from_user.id)
    
    if not user_info:
        await callback.answer("❌ Профиль не найдена")
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

# === ОБРАБОТКА НЕОЖИДАННОГО ВВОДА ===

@router.message(StateFilter("*"))
async def handle_unexpected_input(message: Message, state: FSMContext):
    """Обработка неожиданного ввода"""
    current_state = await state.get_state()
    
    if current_state is None:
        await state.set_state(MainStates.MAIN_MENU)
        await message.answer(
            "Выберите действие:",
            reply_markup=get_main_menu_kb(message.from_user.id)
        )
        return
    
    # Если пользователь в главном меню, предлагаем варианты
    if current_state == MainStates.MAIN_MENU:
        await message.answer(
            "Пожалуйста, используйте кнопки меню:",
            reply_markup=get_main_menu_kb(message.from_user.id)
        )
        return
    
    # Если пользователь в процессе создания события
    if str(current_state).startswith("CreateEventStates"):
        await message.answer(
            "✋ <b>Сейчас вы создаёте событие.</b>\n\n"
            "Пожалуйста, используйте кнопки навигации.\n"
            "Нажмите '⬅️ Назад' для возврата или '❌ Отмена' для выхода в главное меню.",
            reply_markup=get_back_cancel_kb(),
            parse_mode="HTML"
        )
        return
    
    # Если пользователь в процессе онбординга
    if str(current_state).startswith("OnboardingStates"):
        if current_state == OnboardingStates.NAME:
            await message.answer(
                "✋ <b>Сейчас вы проходите онбординг.</b>\n\n"
                "Введите ваше имя (минимум 2 символа):",
                reply_markup=ReplyKeyboardRemove(),
                parse_mode="HTML"
            )
        elif current_state == OnboardingStates.CITY:
            await message.answer(
                "✋ <b>Сейчас вы проходите онбординг.</b>\n\n"
                "Выберите город из списка:",
                parse_mode="HTML"
            )
        return
    
    # Если пользователь в поиске событий
    if current_state == SearchEventsStates.SELECT_EVENT:
        await message.answer(
            "✋ <b>Сейчас вы в поиске событий.</b>\n\n"
            "Выберите событие из списка.",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="HTML"
        )
        return
    
    # Если пользователь в процессе оплаты
    if current_state == JoinEventStates.PAYMENT_INFO:
        await message.answer(
            "✋ <b>Сейчас вы бронируете участие.</b>\n\n"
            "Оплатите по ссылке и нажмите 'Я оплатил'.",
            parse_mode="HTML"
        )
        return
    
    # Общий случай
    await message.answer(
        "✋ <b>Пожалуйста, используйте кнопки навигации.</b>\n\n"
        "Если вы хотите вернуться в главное меню, нажмите '❌ Отмена'.",
        reply_markup=get_back_cancel_kb(),
        parse_mode="HTML"
    )

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
