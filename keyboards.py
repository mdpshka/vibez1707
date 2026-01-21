# keyboards.py
"""
Все клавиатуры и кнопки бота
"""

from datetime import datetime
from aiogram.types import (
    KeyboardButton, 
    ReplyKeyboardMarkup, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton
)

from texts import *
try:
    from cities import CITIES
except ImportError:
    CITIES = ["Москва", "Санкт-Петербург", "Казань", "Екатеринбург", "Новосибирск"]

# === CALLBACK DATA PREFIXES ===
CB_CITY_SELECT = "city:select:"
CB_CITY_PAGE = "city:page:"
CB_ONBOARDING_CANCEL = "onboarding:cancel"
CB_EVENT_VIEW = "event:view:"
CB_EVENT_JOIN = "event:join:"
CB_EVENT_PAID = "event:paid:"
CB_EVENT_BACK = "event:back:"
CB_EVENT_INVITE = "event:invite:"
CB_EVENT_MY = "event:my:"
CB_EVENT_PARTICIPANTS = "event:participants:"
CB_PROFILE_MY_BOOKINGS = "profile:my_bookings"
CB_PROFILE_MY_EVENTS = "profile:my_events"
CB_NAV_BACK_TO_MAIN = "nav:back_to_main"
CB_NAV_BACK_TO_PROFILE = "nav:back_to_profile"
CB_NAV_BACK_TO_MY_EVENTS = "nav:back_to_my_events"
CB_NAV_BACK_TO_SEARCH = "nav:back_to_search"
CB_NAV_BACK_TO_MY_BOOKINGS = "nav:back_to_my_bookings"
CB_USER_INFO = "user:info:"

# === ОБЩИЕ КЛАВИАТУРЫ ===
def get_main_menu_kb(telegram_id, admin_ids):
    """Главное меню"""
    items = [
        KeyboardButton(text=BTN_FIND),
        KeyboardButton(text=BTN_CREATE),
        KeyboardButton(text=BTN_PROFILE),
        KeyboardButton(text=BTN_HELP)
    ]

    keyboard = []
    row = []
    for btn in items:
        row.append(btn)
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_back_cancel_kb():
    """Клавиатура с кнопками Назад и Отмена"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_BACK), KeyboardButton(text=BTN_CANCEL)]
        ],
        resize_keyboard=True
    )

# === КЛАВИАТУРЫ ОНБОРДИНГА ===
def get_cities_keyboard(page=0, items_per_page=8):
    """Клавиатура выбора города с пагинацией"""
    start_idx = page * items_per_page
    end_idx = start_idx + items_per_page
    cities_slice = CITIES[start_idx:end_idx]
    
    buttons = []
    row = []
    for i, city in enumerate(cities_slice):
        row.append(InlineKeyboardButton(text=city, callback_data=f"{CB_CITY_SELECT}{city}"))
        if i % 2 == 1:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CB_CITY_PAGE}{page-1}"))
    if end_idx < len(CITIES):
        nav_buttons.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"{CB_CITY_PAGE}{page+1}"))
    
    if nav_buttons:
        buttons.append(nav_buttons)
    
    buttons.append([InlineKeyboardButton(text=BTN_CANCEL, callback_data=CB_ONBOARDING_CANCEL)])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# === КЛАВИАТУРЫ СОЗДАНИЯ СОБЫТИЯ ===
def get_event_types_kb():
    """Выбор типа события"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎉 Туса"), KeyboardButton(text="🎳 Страйкбол")],
            [KeyboardButton(text="🔫 Пейнтбол"), KeyboardButton(text="🎯 Другое")],
            [KeyboardButton(text=BTN_BACK), KeyboardButton(text=BTN_CANCEL)]
        ],
        resize_keyboard=True
    )

def get_confirm_kb():
    """Подтверждение создания события"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_CONFIRM), KeyboardButton(text=BTN_EDIT)],
            [KeyboardButton(text=BTN_BACK), KeyboardButton(text=BTN_CANCEL)]
        ],
        resize_keyboard=True
    )

# === КЛАВИАТУРЫ ПОИСКА СОБЫТИЙ ===
def get_event_list_kb(events):
    """Список событий для поиска"""
    buttons = []
    for event in events:
        event_id, event_type, max_participants, date_time, confirmed_count = event
        
        buttons.append([
            InlineKeyboardButton(
                text=f"{event_type[:20]} • {confirmed_count}/{max_participants} • {date_time}",
                callback_data=f"{CB_EVENT_VIEW}{event_id}"
            )
        ])
    buttons.append([InlineKeyboardButton(text=BTN_BACK, callback_data=CB_NAV_BACK_TO_MAIN)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_event_details_kb(event_id, user_telegram_id, is_confirmed=False):
    """Детали события"""
    buttons = []
    
    if not is_confirmed:
        buttons.append([InlineKeyboardButton(text="💳 Забронировать", callback_data=f"{CB_EVENT_JOIN}{event_id}")])
    
    buttons.append([
        InlineKeyboardButton(text="📲 Пригласить друга", callback_data=f"{CB_EVENT_INVITE}{event_id}:{user_telegram_id}")
    ])
    buttons.append([InlineKeyboardButton(text=BTN_BACK, callback_data=CB_NAV_BACK_TO_SEARCH)])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_payment_kb(event_id):
    """Оплата участия в событии"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить 99 ₽", url="https://yoomoney.ru/pay/...")],
            [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"{CB_EVENT_PAID}{event_id}")],
            [InlineKeyboardButton(text=BTN_BACK, callback_data=f"{CB_EVENT_BACK}{event_id}")]
        ]
    )

# === КЛАВИАТУРЫ ПРОФИЛЯ ===
def get_profile_kb(telegram_id, admin_ids, is_creator=False):
    """Меню профиля"""
    keyboard = []
    
    # Кнопка админки ТОЛЬКО для админов
    if telegram_id in admin_ids:
        keyboard.append([InlineKeyboardButton(text=BTN_ADMIN, callback_data="admin:menu")])
    
    # Основные кнопки профиля
    keyboard.append([InlineKeyboardButton(text=BTN_MY_BOOKINGS, callback_data=CB_PROFILE_MY_BOOKINGS)])
    keyboard.append([InlineKeyboardButton(text=BTN_MY_EVENTS, callback_data=CB_PROFILE_MY_EVENTS)])
    keyboard.append([InlineKeyboardButton(text=BTN_BACK + " в главное меню", callback_data=CB_NAV_BACK_TO_MAIN)])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_my_events_kb(events):
    """Список моих событий"""
    buttons = []
    for event in events:
        event_id, event_type, city, date_time, status, participants_count, max_participants = event
        
        status_emoji = "✅" if status == 'ACTIVE' else "❌"
        text = f"{status_emoji} {event_type[:15]} • {city} • {participants_count}/{max_participants}"
        
        buttons.append([
            InlineKeyboardButton(
                text=text,
                callback_data=f"{CB_EVENT_MY}{event_id}"
            )
        ])
    
    buttons.append([InlineKeyboardButton(text=BTN_BACK, callback_data=CB_NAV_BACK_TO_PROFILE)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_my_bookings_kb(bookings):
    """Список моих бронирований"""
    buttons = []
    for booking in bookings:
        event_id, event_type, city, date_time, booking_date = booking
        
        booking_dt = datetime.fromisoformat(booking_date.replace(' ', 'T'))
        formatted_date = booking_dt.strftime("%d.%m.%Y")
        
        text = f"✅ {event_type[:15]} • {city} • {date_time[:10]}"
        
        buttons.append([
            InlineKeyboardButton(
                text=text,
                callback_data=f"{CB_EVENT_VIEW}{event_id}"
            )
        ])
    
    buttons.append([InlineKeyboardButton(text=BTN_BACK, callback_data=CB_NAV_BACK_TO_PROFILE)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_event_manage_kb(event_id):
    """Управление моим событием"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👥 Список участников", callback_data=f"{CB_EVENT_PARTICIPANTS}{event_id}")],
            [InlineKeyboardButton(text=BTN_BACK, callback_data=CB_NAV_BACK_TO_MY_EVENTS)]
        ]
    )

def get_participants_kb(event_id, participants):
    """Список участников события"""
    buttons = []
    for participant in participants:
        username, telegram_id, name, joined_at = participant
        display_name = f"@{username}" if username else name or f"ID: {telegram_id}"
        
        buttons.append([
            InlineKeyboardButton(
                text=f"👤 {display_name[:25]}",
                callback_data=f"{CB_USER_INFO}{telegram_id}"
            )
        ])
    
    buttons.append([InlineKeyboardButton(text=BTN_BACK, callback_data=f"{CB_EVENT_MY}{event_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)
