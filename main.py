import asyncio
import logging
import math
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

from texts import *
from admin import register_admin
from database import Database

try:
    from cities import CITIES
except ImportError:
    CITIES = ["Москва", "Санкт-Петербург", "Казань", "Екатеринбург", "Новосибирск"]

BOT_TOKEN = "8104721228:AAHPnw-PHAMYMJARBvBULtm5_SeFcrhfm3g"
ADMIN_IDS = [931410785]
PLATFORM_FEE = 99
PAYMENT_LINK = "https://yoomoney.ru/pay/..."

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
CB_EVENT_SET_CHATLINK = "event:set_chatlink:"

CB_PROFILE_MY_BOOKINGS = "profile:my_bookings"
CB_PROFILE_MY_EVENTS = "profile:my_events"

CB_NAV_BACK_TO_MAIN = "nav:back_to_main"
CB_NAV_BACK_TO_PROFILE = "nav:back_to_profile"
CB_NAV_BACK_TO_MY_EVENTS = "nav:back_to_my_events"
CB_NAV_BACK_TO_SEARCH = "nav:back_to_search"

CB_USER_INFO = "user:info:"

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

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

db = Database()

async def notify_admin_booking(event_data: dict):
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                ADMIN_NEW_BOOKING.format(
                    event_title=event_data['event_title'],
                    city=event_data['city'],
                    date=event_data['date'],
                    username=event_data['username'],
                    user_id=event_data['user_id'],
                    confirmed_count=event_data['confirmed_count'],
                    max_participants=event_data['max_participants']
                )
            )
        except Exception as e:
            logging.error(f"Failed to send notification to admin {admin_id}: {e}")

async def notify_event_participants(event_id: int, new_participant_data: dict):
    try:
        participants = await db.get_all_confirmed_participants(event_id, new_participant_data['telegram_id'])
        
        event = await db.get_event_details(event_id)
        if not event:
            return
        
        event_type = event[1] or event[0]
        confirmed_count = event[12]
        
        for participant in participants:
            participant_id, username, name = participant
            try:
                await bot.send_message(
                    participant_id,
                    PARTICIPANT_NOTIFICATION.format(
                        username=new_participant_data['username'],
                        event_type=event_type,
                        confirmed_count=confirmed_count,
                        max_participants=event[5]
                    )
                )
            except Exception as e:
                logging.error(f"Failed to send notification to participant {participant_id}: {e}")
    except Exception as e:
        logging.error(f"Failed to send participant notifications: {e}")

def get_cities_keyboard(page=0, items_per_page=8):
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

def get_main_menu_kb(telegram_id):
    items = []
    if telegram_id in ADMIN_IDS:
        items.append(KeyboardButton(text=BTN_ADMIN))

    items.extend([
        KeyboardButton(text=BTN_FIND),
        KeyboardButton(text=BTN_CREATE),
        KeyboardButton(text=BTN_PROFILE),
        KeyboardButton(text=BTN_HELP)
    ])

    keyboard = []
    row = []
    for btn in items:
        row.append(btn)
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        row.append(KeyboardButton(text=BTN_HELP))
        keyboard.append(row)

    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_back_cancel_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_BACK), KeyboardButton(text=BTN_CANCEL)]
        ],
        resize_keyboard=True
    )

def get_event_types_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎉 Туса"), KeyboardButton(text="🎳 Страйкбол")],
            [KeyboardButton(text="🔫 Пейнтбол"), KeyboardButton(text="🎯 Другое")],
            [KeyboardButton(text=BTN_BACK), KeyboardButton(text=BTN_CANCEL)]
        ],
        resize_keyboard=True
    )

def get_confirm_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_CONFIRM), KeyboardButton(text=BTN_EDIT)],
            [KeyboardButton(text=BTN_BACK), KeyboardButton(text=BTN_CANCEL)]
        ],
        resize_keyboard=True
    )

def get_event_list_kb(events):
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
    buttons = []
    
    if not is_confirmed:
        buttons.append([InlineKeyboardButton(text="💳 Забронировать", callback_data=f"{CB_EVENT_JOIN}{event_id}")])
    
    buttons.append([
        InlineKeyboardButton(text="📲 Пригласить друга", callback_data=f"{CB_EVENT_INVITE}{event_id}:{user_telegram_id}")
    ])
    buttons.append([InlineKeyboardButton(text=BTN_BACK, callback_data=CB_NAV_BACK_TO_SEARCH)])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_payment_kb(event_id):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить 99 ₽", url=PAYMENT_LINK)],
            [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"{CB_EVENT_PAID}{event_id}")],
            [InlineKeyboardButton(text=BTN_BACK, callback_data=f"{CB_EVENT_BACK}{event_id}")]
        ]
    )

def get_profile_kb(telegram_id, is_creator=False):
    keyboard = []
    
    if telegram_id in ADMIN_IDS:
        keyboard.append([InlineKeyboardButton(text=BTN_ADMIN, callback_data="admin:menu")])
    
    keyboard.append([InlineKeyboardButton(text=BTN_MY_BOOKINGS, callback_data=CB_PROFILE_MY_BOOKINGS)])
    
    keyboard.append([InlineKeyboardButton(text=BTN_MY_EVENTS, callback_data=CB_PROFILE_MY_EVENTS)])
    
    keyboard.append([InlineKeyboardButton(text=BTN_BACK + " в главное меню", callback_data=CB_NAV_BACK_TO_MAIN)])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_my_events_kb(events):
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
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👥 Список участников", callback_data=f"{CB_EVENT_PARTICIPANTS}{event_id}")],
            [InlineKeyboardButton(text=BTN_BACK, callback_data=CB_NAV_BACK_TO_MY_EVENTS)]
        ]
    )

def get_participants_kb(event_id, participants):
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

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
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
                    INVITE_WELCOME,
                    reply_markup=ReplyKeyboardRemove()
                )
                return
            else:
                event = await db.get_event_details(event_id)
                if event:
                    (event_type, custom_type, event_city, date, time, max_participants, 
                     description, contact, status, creator_id, creator_username, 
                     creator_name, confirmed_count) = event
                    
                    display_type = custom_type or event_type
                    
                    is_confirmed = await db.is_user_confirmed(event_id, message.from_user.id)
                    
                    text = INVITE_EVENT_TEXT.format(
                        event_type=display_type,
                        city=event_city,
                        date=date,
                        time=time,
                        creator=creator_name or '@' + creator_username,
                        contact=contact,
                        confirmed_count=confirmed_count,
                        max_participants=max_participants,
                        description=description
                    )
                    
                    if is_confirmed:
                        text += EVENT_ALREADY_CONFIRMED
                    else:
                        text += EVENT_JOIN_PROMPT
                    
                    await state.set_state(MainStates.VIEWING_EVENT)
                    await state.update_data(current_event_id=event_id)
                    
                    await message.answer(
                        text, 
                        reply_markup=get_event_details_kb(event_id, message.from_user.id, is_confirmed), 
                        parse_mode="HTML"
                    )
                else:
                    await message.answer(ERROR_EVENT_NOT_FOUND)
                return
        except Exception as e:
            logging.error(f"Error processing invite: {e}")
    
    await db.add_user(message.from_user.id, message.from_user.username)
    
    name, city, onboarded = await db.get_user_profile(message.from_user.id)
    
    if not onboarded:
        await state.set_state(OnboardingStates.NAME)
        await message.answer(
            WELCOME_ONBOARDING,
            reply_markup=ReplyKeyboardRemove()
        )
    else:
        await state.set_state(MainStates.MAIN_MENU)
        await message.answer(
            MAIN_MENU_WELCOME.format(name=name),
            reply_markup=get_main_menu_kb(message.from_user.id)
        )

@router.message(OnboardingStates.NAME)
async def process_name(message: Message, state: FSMContext):
    if message.text == BTN_CANCEL:
        await state.clear()
        await state.set_state(MainStates.MAIN_MENU)
        await message.answer(ONBOARDING_CANCELLED, reply_markup=get_main_menu_kb(message.from_user.id))
        return
    
    name = message.text.strip()
    if len(name) < 2:
        await message.answer(ERROR_NAME_TOO_SHORT)
        return
    
    await state.update_data(name=name)
    await state.set_state(OnboardingStates.CITY)
    
    await message.answer(
        ONBOARDING_CITY_SELECTION.format(name=name),
        reply_markup=get_cities_keyboard()
    )

@router.callback_query(F.data.startswith(CB_CITY_SELECT))
async def process_city_selection(callback: CallbackQuery, state: FSMContext):
    city = callback.data.split(CB_CITY_SELECT, 1)[1]
    data = await state.get_data()
    name = data['name']
    
    await db.update_user_profile(callback.from_user.id, name, city)
    
    invite_event_id = data.get('invite_event_id')
    
    if invite_event_id:
        await state.set_state(MainStates.VIEWING_EVENT)
        await state.update_data(current_event_id=invite_event_id)
        
        event = await db.get_event_details(invite_event_id)
        if event:
            (event_type, custom_type, event_city, date, time, max_participants, 
             description, contact, status, creator_id, creator_username, 
             creator_name, confirmed_count) = event
            
            display_type = custom_type or event_type
            
            is_confirmed = await db.is_user_confirmed(invite_event_id, callback.from_user.id)
            
            text = INVITE_EVENT_TEXT.format(
                event_type=display_type,
                city=event_city,
                date=date,
                time=time,
                creator=creator_name or '@' + creator_username,
                contact=contact,
                confirmed_count=confirmed_count,
                max_participants=max_participants,
                description=description
            )
            
            if is_confirmed:
                text += EVENT_ALREADY_CONFIRMED
            else:
                text += EVENT_JOIN_PROMPT
            
            await callback.message.edit_text(text, parse_mode="HTML")
            await callback.message.answer(
                text, 
                reply_markup=get_event_details_kb(invite_event_id, callback.from_user.id, is_confirmed), 
                parse_mode="HTML"
            )
        else:
            await callback.message.edit_text(
                ONBOARDING_COMPLETE.format(name=name, city=city)
            )
            await state.set_state(MainStates.MAIN_MENU)
            await callback.message.answer(
                BACK_TO_MAIN,
                reply_markup=get_main_menu_kb(callback.from_user.id)
            )
    else:
        await state.set_state(MainStates.MAIN_MENU)
        await callback.message.edit_text(
            ONBOARDING_COMPLETE.format(name=name, city=city)
        )
        await callback.message.answer(
            BACK_TO_MAIN,
            reply_markup=get_main_menu_kb(callback.from_user.id)
        )
    await callback.answer()

@router.message(F.text == BTN_PROFILE, MainStates.MAIN_MENU)
async def my_profile(message: Message, state: FSMContext):
    user_info = await db.get_user_full_info(message.from_user.id)
    
    if not user_info:
        await message.answer(
            PROFILE_NOT_FOUND,
            reply_markup=get_main_menu_kb(message.from_user.id)
        )
        return
    
    name, city, username, rating, created_at, events_created, bookings_made = user_info
    
    created_date = datetime.fromisoformat(created_at.replace(' ', 'T')).strftime("%d.%m.%Y")
    
    profile_text = PROFILE_TEXT.format(
        name=name,
        city=city,
        username=username if username else 'не указан',
        rating=rating,
        events_created=events_created,
        bookings_made=bookings_made,
        created_date=created_date
    )
    
    user_events = await db.get_user_created_events(message.from_user.id)
    is_creator = len(user_events) > 0
    
    await state.set_state(MainStates.MAIN_MENU)
    await message.answer(
        profile_text,
        parse_mode="HTML",
        reply_markup=get_profile_kb(message.from_user.id, is_creator)
    )

@router.message(F.text == BTN_HELP, MainStates.MAIN_MENU)
async def how_to_use(message: Message, state: FSMContext):
    await state.set_state(MainStates.MAIN_MENU)
    await message.answer(
        HELP_TEXT,
        parse_mode="HTML",
        reply_markup=get_main_menu_kb(message.from_user.id)
    )

@router.message(F.text == BTN_CANCEL, StateFilter(None, default_state))
@router.message(F.text == BTN_CANCEL)
async def cancel_anywhere(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(MainStates.MAIN_MENU)
    await message.answer(
        CANCELLED_ACTION,
        reply_markup=get_main_menu_kb(message.from_user.id)
    )

@router.message(F.text == BTN_BACK)
async def go_back(message: Message, state: FSMContext):
    current_state = await state.get_state()
    
    if current_state == CreateEventStates.TYPE:
        await state.set_state(MainStates.MAIN_MENU)
        await message.answer(BACK_TO_MAIN, reply_markup=get_main_menu_kb(message.from_user.id))
    
    elif current_state == CreateEventStates.TYPE_OTHER:
        await state.set_state(CreateEventStates.TYPE)
        await message.answer(CREATE_EVENT_START, reply_markup=get_event_types_kb())
    
    elif current_state == CreateEventStates.DATE:
        await state.set_state(CreateEventStates.TYPE)
        await message.answer(CREATE_EVENT_START, reply_markup=get_event_types_kb())
    
    elif current_state == CreateEventStates.TIME:
        await state.set_state(CreateEventStates.DATE)
        await message.answer(
            "[Создание события 2/7]\n\nВведите дату в формате ДД.ММ.ГГГГ\nНапример: 25.12.2024",
            reply_markup=get_back_cancel_kb()
        )
    
    elif current_state == CreateEventStates.MAX_PARTICIPANTS:
        await state.set_state(CreateEventStates.TIME)
        await message.answer(
            "[Создание события 3/7]\n\nВведите время в формате ЧЧ:ММ\nНапример: 19:00",
            reply_markup=get_back_cancel_kb()
        )
    
    elif current_state == CreateEventStates.DESCRIPTION:
        await state.set_state(CreateEventStates.MAX_PARTICIPANTS)
        await message.answer(
            "[Создание события 4/7]\n\nВведите максимальное количество участников:",
            reply_markup=get_back_cancel_kb()
        )
    
    elif current_state == CreateEventStates.CONTACT:
        await state.set_state(CreateEventStates.DESCRIPTION)
        await message.answer(
            "[Создание события 5/7]\n\n📝 Введите описание события (обязательно):",
            reply_markup=get_back_cancel_kb()
        )
    
    elif current_state == CreateEventStates.CONFIRMATION:
        await state.set_state(CreateEventStates.CONTACT)
        await message.answer(
            "[Создание события 6/7]\n\n📞 Введите ваш контакт для связи с участников:",
            reply_markup=get_back_cancel_kb()
        )
    
    else:
        await state.set_state(MainStates.MAIN_MENU)
        await message.answer(BACK_TO_MAIN, reply_markup=get_main_menu_kb(message.from_user.id))

@router.message(F.text == BTN_CREATE, MainStates.MAIN_MENU)
async def start_create_event(message: Message, state: FSMContext):
    name, city, onboarded = await db.get_user_profile(message.from_user.id)
    
    if not onboarded:
        await message.answer("❌ Сначала завершите онбординг. Нажмите /start")
        return
    
    await state.update_data(city=city)
    await state.set_state(CreateEventStates.TYPE)
    
    await message.answer(CREATE_EVENT_START, reply_markup=get_event_types_kb())

@router.message(CreateEventStates.TYPE)
async def process_event_type(message: Message, state: FSMContext):
    if message.text == BTN_CANCEL:
        await cancel_anywhere(message, state)
        return
    if message.text == BTN_BACK:
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
        await message.answer(CREATE_EVENT_TYPE_OTHER, reply_markup=get_back_cancel_kb())
        return
    
    event_type = message.text[2:]
    await state.update_data(type=event_type, custom_type=None)
    await state.set_state(CreateEventStates.DATE)
    
    await message.answer(
        CREATE_EVENT_DATE.format(event_type=event_type),
        reply_markup=get_back_cancel_kb()
    )

@router.message(CreateEventStates.TYPE_OTHER)
async def process_event_type_other(message: Message, state: FSMContext):
    if message.text == BTN_CANCEL:
        await cancel_anywhere(message, state)
        return
    if message.text == BTN_BACK:
        await go_back(message, state)
        return
    
    custom_type = message.text.strip()
    
    if len(custom_type) < 3:
        await message.answer("Название события должно содержать минимум 3 символа. Введите снова:")
        return
    
    await state.update_data(type="Другое", custom_type=custom_type)
    await state.set_state(CreateEventStates.DATE)
    
    await message.answer(
        CREATE_EVENT_DATE.format(event_type=custom_type),
        reply_markup=get_back_cancel_kb()
    )

@router.message(CreateEventStates.DATE)
async def process_event_date(message: Message, state: FSMContext):
    if message.text == BTN_CANCEL:
        await cancel_anywhere(message, state)
        return
    if message.text == BTN_BACK:
        await go_back(message, state)
        return
    
    date_str = message.text.strip()
    
    try:
        event_date = datetime.strptime(date_str, "%d.%m.%Y").date()
        today = datetime.now().date()
        
        if event_date < today:
            await message.answer(
                "❌ Дата не может быть в прошлом.\nВведите будущую дату в формате ДД.ММ.ГГГГ:"
            )
            return
    except ValueError:
        await message.answer(ERROR_INVALID_DATE + "\nВведите дату в формате ДД.ММ.ГГГГ\nНапример: 25.12.2024")
        return
    
    await state.update_data(date=date_str)
    await state.set_state(CreateEventStates.TIME)
    
    await message.answer(
        CREATE_EVENT_TIME.format(date=date_str),
        reply_markup=get_back_cancel_kb()
    )

@router.message(CreateEventStates.TIME)
async def process_event_time(message: Message, state: FSMContext):
    if message.text == BTN_CANCEL:
        await cancel_anywhere(message, state)
        return
    if message.text == BTN_BACK:
        await go_back(message, state)
        return
    
    time_str = message.text.strip()
    
    try:
        datetime.strptime(time_str, "%H:%M")
    except ValueError:
        await message.answer(ERROR_INVALID_TIME + "\nВведите время в формате ЧЧ:ММ\nНапример: 19:00")
        return
    
    await state.update_data(time=time_str)
    await state.set_state(CreateEventStates.MAX_PARTICIPANTS)
    
    await message.answer(
        CREATE_EVENT_MAX_PARTICIPANTS.format(time=time_str),
        reply_markup=get_back_cancel_kb()
    )

@router.message(CreateEventStates.MAX_PARTICIPANTS)
async def process_max_participants(message: Message, state: FSMContext):
    if message.text == BTN_CANCEL:
        await cancel_anywhere(message, state)
        return
    if message.text == BTN_BACK:
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
        CREATE_EVENT_DESCRIPTION.format(max_participants=max_participants),
        reply_markup=get_back_cancel_kb()
    )

@router.message(CreateEventStates.DESCRIPTION)
async def process_description(message: Message, state: FSMContext):
    if message.text == BTN_CANCEL:
        await cancel_anywhere(message, state)
        return
    if message.text == BTN_BACK:
        await go_back(message, state)
        return
    
    description = message.text.strip()
    
    if len(description) < 10:
        await message.answer(ERROR_DESCRIPTION_TOO_SHORT)
        return
    
    await state.update_data(description=description)
    await state.set_state(CreateEventStates.CONTACT)
    
    await message.answer(
        CREATE_EVENT_CONTACT.format(description_preview=description[:100]),
        reply_markup=get_back_cancel_kb()
    )

@router.message(CreateEventStates.CONTACT)
async def process_contact(message: Message, state: FSMContext):
    if message.text == BTN_CANCEL:
        await cancel_anywhere(message, state)
        return
    if message.text == BTN_BACK:
        await go_back(message, state)
        return
    
    contact = message.text.strip()
    
    if len(contact) < 3:
        await message.answer(ERROR_CONTACT_TOO_SHORT)
        return
    
    await state.update_data(contact=contact)
    await state.set_state(CreateEventStates.CONFIRMATION)
    
    data = await state.get_data()
    event_type = data.get('custom_type') or data['type']
    
    text = CREATE_EVENT_CONFIRMATION.format(
        event_type=event_type,
        city=data['city'],
        date=data['date'],
        time=data['time'],
        max_participants=data['max_participants'],
        description_preview=data['description'][:100],
        contact=contact
    )
    
    await message.answer(text, reply_markup=get_confirm_kb(), parse_mode="HTML")

@router.message(CreateEventStates.CONFIRMATION)
async def process_confirmation(message: Message, state: FSMContext):
    if message.text == BTN_CANCEL:
        await cancel_anywhere(message, state)
        return
    if message.text == BTN_BACK:
        await go_back(message, state)
        return
    
    if message.text == BTN_CONFIRM:
        data = await state.get_data()
        
        event_id = await db.create_event(data, message.from_user.id)
        
        invite_link = f"https://t.me/{bot._me.username}?start=invite_{event_id}_{message.from_user.id}"
        
        event_type = data.get('custom_type') or data['type']
        
        text = EVENT_CREATED.format(
            event_type=event_type,
            city=data['city'],
            date=data['date'],
            time=data['time'],
            max_participants=data['max_participants'],
            description_preview=data['description'][:200],
            contact=data['contact']
        )
        
        await state.clear()
        await state.set_state(MainStates.MAIN_MENU)
        await message.answer(text, reply_markup=get_main_menu_kb(message.from_user.id), parse_mode="HTML")
        
        instructions = EVENT_NEXT_STEPS.format(invite_link=invite_link)
        
        await message.answer(instructions, parse_mode="HTML")
        
    elif message.text == BTN_EDIT:
        await state.set_state(CreateEventStates.TYPE)
        await message.answer(CREATE_EVENT_START, reply_markup=get_event_types_kb())
    else:
        await message.answer(
            "Пожалуйста, выберите вариант из предложенных:",
            reply_markup=get_confirm_kb()
        )

@router.message(F.text == BTN_FIND, MainStates.MAIN_MENU)
async def start_search(message: Message, state: FSMContext):
    name, city, onboarded = await db.get_user_profile(message.from_user.id)
    
    if not onboarded:
        await message.answer("❌ Сначала завершите онбординг. Нажмите /start")
        return
    
    events = await db.get_events_by_city(city)
    
    if not events:
        await message.answer(
            SEARCH_NO_EVENTS.format(city=city),
            parse_mode="HTML"
        )
        return
    
    await state.set_state(SearchEventsStates.SELECT_EVENT)
    
    await message.answer(
        SEARCH_FOUND_EVENTS.format(city=city, count=len(events)),
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="HTML"
    )
    
    await message.answer(
        SEARCH_EVENTS_LIST,
        reply_markup=get_event_list_kb(events),
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith(CB_EVENT_VIEW), SearchEventsStates.SELECT_EVENT)
async def view_event_details(callback: CallbackQuery, state: FSMContext):
    event_id = int(callback.data.split(CB_EVENT_VIEW, 1)[1])
    
    event = await db.get_event_details(event_id)
    
    if not event:
        await callback.answer(ERROR_EVENT_NOT_FOUND)
        await state.set_state(MainStates.MAIN_MENU)
        await callback.message.answer(
            ERROR_EVENT_NOT_FOUND + ". Вернитесь в главное меню:",
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
    
    text = EVENT_DETAILS.format(
        event_type=display_type,
        city=city,
        date=date,
        time=time,
        creator=creator_name or '@' + creator_username,
        contact=contact,
        confirmed_count=confirmed_count,
        max_participants=max_participants,
        status=status,
        description=description,
        user_status=EVENT_ALREADY_CONFIRMED if is_confirmed else EVENT_JOIN_PROMPT
    )
    
    await callback.message.edit_text(
        text, 
        reply_markup=get_event_details_kb(event_id, callback.from_user.id, is_confirmed), 
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data.startswith(CB_EVENT_JOIN), MainStates.VIEWING_EVENT)
async def join_event_start(callback: CallbackQuery, state: FSMContext):
    event_id = int(callback.data.split(CB_EVENT_JOIN, 1)[1])
    
    event = await db.get_event_details(event_id)
    
    if not event:
        await callback.answer(ERROR_EVENT_NOT_FOUND)
        return
    
    (event_type, custom_type, city, date, time, max_participants, 
     description, contact, status, creator_id, creator_username, 
     creator_name, confirmed_count) = event
    
    display_type = custom_type or event_type
    
    await state.update_data(event_id=event_id, join_event_id=event_id)
    await state.set_state(JoinEventStates.PAYMENT_INFO)
    
    text = BOOKING_PAYMENT_INFO.format(
        event_type=display_type,
        city=city,
        date=date,
        time=time,
        fee=PLATFORM_FEE
    )
    
    await callback.message.edit_text(text, reply_markup=get_payment_kb(event_id), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith(CB_EVENT_BACK), JoinEventStates.PAYMENT_INFO)
async def back_from_payment(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    event_id = data.get("event_id")
    
    if not event_id:
        try:
            event_id = int(callback.data.split(CB_EVENT_BACK, 1)[1])
        except Exception:
            await callback.answer("❌ Ошибка при возврате")
            return
    
    event = await db.get_event_details(event_id)
    if not event:
        await callback.answer(ERROR_EVENT_NOT_FOUND)
        return
    
    (event_type, custom_type, city, date, time, max_participants, 
     description, contact, status, creator_id, creator_username, 
     creator_name, confirmed_count) = event
    
    display_type = custom_type or event_type
    is_confirmed = await db.is_user_confirmed(event_id, callback.from_user.id)
    
    text = EVENT_DETAILS.format(
        event_type=display_type,
        city=city,
        date=date,
        time=time,
        creator=creator_name or '@' + creator_username,
        contact=contact,
        confirmed_count=confirmed_count,
        max_participants=max_participants,
        status=status,
        description=description,
        user_status=EVENT_ALREADY_CONFIRMED if is_confirmed else EVENT_JOIN_PROMPT
    )
    
    await state.set_state(MainStates.VIEWING_EVENT)
    await state.update_data(event_id=event_id)
    await callback.message.edit_text(text, reply_markup=get_event_details_kb(event_id, callback.from_user.id, is_confirmed), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith(CB_EVENT_PAID), JoinEventStates.PAYMENT_INFO)
async def process_payment(callback: CallbackQuery, state: FSMContext):
    event_id = int(callback.data.split(CB_EVENT_PAID, 1)[1])
    
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
        
        await notify_admin_booking({
            'event_title': display_type,
            'city': event_city,
            'date': f"{date} {time}",
            'username': participant_username,
            'user_id': callback.from_user.id,
            'confirmed_count': confirmed_count,
            'max_participants': max_participants
        })
        
        await notify_event_participants(event_id, {
            'telegram_id': callback.from_user.id,
            'username': participant_username,
            'name': participant_name
        })
        
        text = PAYMENT_CONFIRMED.format(
            event_type=display_type,
            city=event_city,
            date=date,
            time=time,
            contact=contact
        )
        
        await state.update_data(event_id=event_id)
        await state.set_state(MainStates.VIEWING_EVENT)
        
        buttons = [
            [InlineKeyboardButton(text="📲 Пригласить друга", callback_data=f"{CB_EVENT_INVITE}{event_id}:{callback.from_user.id}")],
            [InlineKeyboardButton(text="📌 К деталям события", callback_data=f"{CB_EVENT_BACK}{event_id}")],
            [InlineKeyboardButton(text="🏠 В главное меню", callback_data=CB_NAV_BACK_TO_MAIN)]
        ]
        
        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")
    
    await callback.answer()

@router.callback_query(F.data.startswith(CB_EVENT_INVITE))
async def invite_friend(callback: CallbackQuery):
    rest = callback.data.split(CB_EVENT_INVITE, 1)[1]
    if ":" in rest:
        event_id_str, inviter_id_str = rest.split(":", 1)
    elif "_" in rest:
        parts = rest.split("_")
        event_id_str = parts[0]
        inviter_id_str = parts[1] if len(parts) > 1 else str(callback.from_user.id)
    else:
        event_id_str = rest
        inviter_id_str = str(callback.from_user.id)

    event_id = int(event_id_str)
    inviter_id = int(inviter_id_str)
    invite_link = f"https://t.me/{bot._me.username}?start=invite_{event_id}_{inviter_id}"
    
    await callback.message.answer(
        INVITE_LINK_TEXT.format(invite_link=invite_link),
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data.startswith(CB_EVENT_BACK))
async def event_back_to_details(callback: CallbackQuery, state: FSMContext):
    try:
        event_id = int(callback.data.split(CB_EVENT_BACK, 1)[1])
    except Exception:
        await callback.answer("❌ Неверный идентификатор события")
        return

    event = await db.get_event_details(event_id)
    if not event:
        await callback.answer(ERROR_EVENT_NOT_FOUND)
        return

    (event_type, custom_type, city, date, time, max_participants, 
     description, contact, status, creator_id, creator_username, 
     creator_name, confirmed_count) = event

    display_type = custom_type or event_type
    is_confirmed = await db.is_user_confirmed(event_id, callback.from_user.id)

    text = EVENT_DETAILS.format(
        event_type=display_type,
        city=city,
        date=date,
        time=time,
        creator=creator_name or '@' + creator_username,
        contact=contact,
        confirmed_count=confirmed_count,
        max_participants=max_participants,
        status=status,
        description=description,
        user_status=EVENT_ALREADY_CONFIRMED if is_confirmed else EVENT_JOIN_PROMPT
    )

    await state.set_state(MainStates.VIEWING_EVENT)
    await callback.message.edit_text(text, reply_markup=get_event_details_kb(event_id, callback.from_user.id, is_confirmed), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == CB_PROFILE_MY_BOOKINGS, MainStates.MAIN_MENU)
async def show_my_bookings(callback: CallbackQuery, state: FSMContext):
    bookings = await db.get_user_bookings(callback.from_user.id)
    
    if not bookings:
        await callback.message.edit_text(
            MY_BOOKINGS_EMPTY,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔍 Найти события", callback_data=CB_NAV_BACK_TO_MAIN)],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_NAV_BACK_TO_PROFILE)]
            ])
        )
        await callback.answer()
        return
    
    bookings_text = MY_BOOKINGS_LIST
    
    for i, booking in enumerate(bookings[:10], 1):
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
    
    await state.set_state(MainStates.MAIN_MENU)
    await callback.message.edit_text(
        bookings_text,
        parse_mode="HTML",
        reply_markup=get_my_bookings_kb(bookings[:10])
    )
    await callback.answer()

@router.callback_query(F.data == CB_PROFILE_MY_EVENTS, MainStates.MAIN_MENU)
async def show_my_events(callback: CallbackQuery, state: FSMContext):
    events = await db.get_user_created_events(callback.from_user.id)
    
    if not events:
        await callback.message.edit_text(
            MY_EVENTS_EMPTY,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Создать событие", callback_data=CB_NAV_BACK_TO_MAIN)],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_NAV_BACK_TO_PROFILE)]
            ])
        )
        await callback.answer()
        return
    
    events_text = MY_EVENTS_LIST
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
    
    events_text = MY_EVENTS_LIST.format(active_count=active_count) + events_text[24:]
    
    await state.set_state(MainStates.MAIN_MENU)
    await callback.message.edit_text(
        events_text,
        parse_mode="HTML",
        reply_markup=get_my_events_kb(events)
    )
    await callback.answer()

@router.callback_query(F.data.startswith(CB_EVENT_MY), MainStates.MAIN_MENU)
async def show_my_event_details(callback: CallbackQuery, state: FSMContext):
    event_id = int(callback.data.split(CB_EVENT_MY, 1)[1])
    
    event = await db.get_event_details(event_id)
    
    if not event:
        await callback.answer(ERROR_EVENT_NOT_FOUND)
        return
    
    (event_type, custom_type, city, date, time, max_participants, 
     description, contact, status, creator_id, creator_username, 
     creator_name, confirmed_count) = event
    
    display_type = custom_type or event_type
    
    participants = await db.get_event_participants_list(event_id)
    
    bottom_text = f"<b>Уже забронировали:</b> {len(participants)} участник(ов)\n" if participants else ""
    
    text = EVENT_MANAGEMENT_DETAILS.format(
        event_type=display_type,
        city=city,
        date=date,
        time=time,
        status='✅ Активно' if status == 'ACTIVE' else '❌ Неактивно',
        confirmed_count=confirmed_count,
        max_participants=max_participants,
        contact=contact,
        description=description,
        bottom_text=bottom_text
    )
    
    await state.set_state(MainStates.MAIN_MENU)
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=get_event_manage_kb(event_id)
    )
    await callback.answer()

@router.callback_query(F.data.startswith(CB_EVENT_PARTICIPANTS), MainStates.MAIN_MENU)
async def show_event_participants(callback: CallbackQuery, state: FSMContext):
    event_id = int(callback.data.split(CB_EVENT_PARTICIPANTS, 1)[1])
    
    participants = await db.get_event_participants_list(event_id)
    
    if not participants:
        await callback.message.edit_text(
            EVENT_PARTICIPANTS_EMPTY,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CB_EVENT_MY}{event_id}")]
            ])
        )
        await callback.answer()
        return
    
    participants_text = EVENT_PARTICIPANTS_LIST
    
    for i, participant in enumerate(participants, 1):
        username, telegram_id, name, joined_at = participant
        display_name = f"@{username}" if username else name or f"ID: {telegram_id}"
        join_date = datetime.fromisoformat(joined_at.replace(' ', 'T')).strftime("%d.%m")
        
        participants_text += f"{i}. {display_name}\n   🆔 {telegram_id} | 📅 {join_date}\n"
    
    participants_text += f"\n<b>Всего:</b> {len(participants)} участник(ов)"
    
    await state.set_state(MainStates.MAIN_MENU)
    await callback.message.edit_text(
        participants_text,
        parse_mode="HTML",
        reply_markup=get_participants_kb(event_id, participants)
    )
    await callback.answer()

@router.callback_query(F.data.startswith(CB_USER_INFO))
async def show_user_info(callback: CallbackQuery):
    try:
        telegram_id = int(callback.data.split(CB_USER_INFO, 1)[1])
    except Exception:
        await callback.answer("❌ Неверный идентификатор пользователя")
        return

    info = await db.get_user_full_info(telegram_id)
    if not info:
        await callback.answer(ERROR_USER_NOT_FOUND)
        return

    name, city, username, rating, created_at, events_created, bookings_made = info
    created_date = datetime.fromisoformat(created_at.replace(' ', 'T')).strftime("%d.%m.%Y")

    text = USER_INFO.format(
        name=name,
        city=city,
        username=username if username else 'не указан',
        rating=rating,
        events_created=events_created,
        bookings_made=bookings_made,
        created_date=created_date
    )

    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()

@router.callback_query()
async def callback_fallback(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(MainStates.MAIN_MENU)
    try:
        await callback.message.edit_text(FALLBACK_MESSAGE)
    except Exception:
        pass
    await callback.message.answer(
        "Выберите действие:",
        reply_markup=get_main_menu_kb(callback.from_user.id)
    )
    await callback.answer()

@router.callback_query(F.data == CB_NAV_BACK_TO_MAIN)
async def back_to_main_menu(callback: CallbackQuery, state: FSMContext):
    await state.set_state(MainStates.MAIN_MENU)
    await callback.message.edit_text(BACK_TO_MAIN)
    await callback.message.answer(
        "Выберите действие:",
        reply_markup=get_main_menu_kb(callback.from_user.id)
    )
    await callback.answer()

@router.callback_query(F.data == CB_NAV_BACK_TO_SEARCH)
async def back_to_search(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SearchEventsStates.SELECT_EVENT)
    
    name, city, onboarded = await db.get_user_profile(callback.from_user.id)
    events = await db.get_events_by_city(city)
    
    if events:
        text = SEARCH_FOUND_EVENTS.format(city=city, count=len(events)) + "\n\nВыберите событие:"
        await callback.message.edit_text(text, reply_markup=get_event_list_kb(events), parse_mode="HTML")
    else:
        await callback.message.edit_text(SEARCH_NO_EVENTS.format(city=city), parse_mode="HTML")
        await callback.message.answer(
            BACK_TO_MAIN,
            reply_markup=get_main_menu_kb(callback.from_user.id)
        )
        await state.set_state(MainStates.MAIN_MENU)
    
    await callback.answer()

@router.callback_query(F.data == CB_NAV_BACK_TO_PROFILE)
async def back_to_profile(callback: CallbackQuery, state: FSMContext):
    user_info = await db.get_user_full_info(callback.from_user.id)
    
    if not user_info:
        await callback.answer("❌ Профиль не найдена")
        return
    
    name, city, username, rating, created_at, events_created, bookings_made = user_info
    created_date = datetime.fromisoformat(created_at.replace(' ', 'T')).strftime("%d.%m.%Y")
    
    profile_text = PROFILE_TEXT.format(
        name=name,
        city=city,
        username=username if username else 'не указан',
        rating=rating,
        events_created=events_created,
        bookings_made=bookings_made,
        created_date=created_date
    )
    
    user_events = await db.get_user_created_events(callback.from_user.id)
    is_creator = len(user_events) > 0
    
    await state.set_state(MainStates.MAIN_MENU)
    await callback.message.edit_text(
        profile_text,
        parse_mode="HTML",
        reply_markup=get_profile_kb(callback.from_user.id, is_creator)
    )
    await callback.answer()

@router.callback_query(F.data == CB_NAV_BACK_TO_MY_EVENTS)
async def back_to_my_events(callback: CallbackQuery, state: FSMContext):
    events = await db.get_user_created_events(callback.from_user.id)
    
    if not events:
        await callback.message.edit_text(
            MY_EVENTS_EMPTY,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_NAV_BACK_TO_PROFILE)]
            ])
        )
        await callback.answer()
        return
    
    events_text = MY_EVENTS_LIST
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
    
    events_text = MY_EVENTS_LIST.format(active_count=active_count) + events_text[24:]
    
    await state.set_state(MainStates.MAIN_MENU)
    await callback.message.edit_text(
        events_text,
        parse_mode="HTML",
        reply_markup=get_my_events_kb(events)
    )
    await callback.answer()

@router.message(StateFilter("*"))
async def handle_unexpected_input(message: Message, state: FSMContext):
    current_state = await state.get_state()
    
    if current_state is None:
        await state.set_state(MainStates.MAIN_MENU)
        await message.answer(
            "Выберите действие:",
            reply_markup=get_main_menu_kb(message.from_user.id)
        )
        return
    
    if current_state == MainStates.MAIN_MENU:
        await message.answer(
            "Пожалуйста, используйте кнопки меню:",
            reply_markup=get_main_menu_kb(message.from_user.id)
        )
        return
    
    if str(current_state).startswith("CreateEventStates"):
        await message.answer(
            "✋ <b>Сейчас вы создаёте событие.</b>\n\nПожалуйста, используйте кнопки навигации.\nНажмите '⬅️ Назад' для возврата или '❌ Отмена' для выхода в главное меню.",
            reply_markup=get_back_cancel_kb(),
            parse_mode="HTML"
        )
        return
    
    if current_state == SearchEventsStates.SELECT_EVENT:
        await message.answer(
            "✋ <b>Сейчас вы в поиске событий.</b>\n\nВыберите событие из списка.",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="HTML"
        )
        return
    
    if current_state == JoinEventStates.PAYMENT_INFO:
        await message.answer(
            "✋ <b>Сейчас вы бронируете участие.</b>\n\nОплатите по ссылке и нажмите 'Я оплатил'.",
            parse_mode="HTML"
        )
        return
    
    await message.answer(
        UNEXPECTED_INPUT,
        reply_markup=get_back_cancel_kb(),
        parse_mode="HTML"
    )

async def main():
    await db.init_db()
    register_admin(router, db, bot, ADMIN_IDS, PLATFORM_FEE)
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    asyncio.run(main())
