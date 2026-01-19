# admin.py
"""
Модуль админ-панели бота VIBEZ
"""
import math
import logging
from datetime import datetime
from typing import List

from aiogram import Router, Bot, F
from aiogram.types import (
    CallbackQuery, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton,
    Message
)
from aiogram.filters import StateFilter

from texts import (
    ADMIN_MENU_TITLE, ADMIN_NO_ACCESS, ADMIN_STATS_TITLE, ADMIN_STATS_TEXT,
    ADMIN_EVENTS_LIST_TITLE, ADMIN_EVENTS_EMPTY, ADMIN_EVENTS_FOUND,
    ADMIN_EVENT_DETAILS, ADMIN_BOOKINGS_TITLE, ADMIN_BOOKINGS_EMPTY,
    ADMIN_BOOKINGS_LIST, ADMIN_BOOKING_DETAILS, BTN_ADMIN, BTN_BACK,
    ERROR_EVENT_NOT_FOUND, ERROR_USER_NOT_FOUND, BTN_CANCEL
)

# === CALLBACK DATA PREFIXES ===
CB_ADMIN_MENU = "admin:menu"
CB_ADMIN_STATS = "admin:stats"
CB_ADMIN_EVENTS_LIST = "admin:events"
CB_ADMIN_EVENTS_DETAIL = "admin:event:"
CB_ADMIN_BOOKINGS = "admin:bookings"
CB_ADMIN_BOOKINGS_PAGE = "admin:bookings_page:"

def register_admin(router: Router, db, bot: Bot, admin_ids: List[int], platform_fee: int = 99):
    """
    Регистрация админ-роутера
    :param router: основной роутер aiogram
    :param db: экземпляр Database
    :param bot: экземпляр Bot
    :param admin_ids: список ID администраторов
    :param platform_fee: комиссия платформы
    """
    
    admin_router = Router()
    
    async def check_admin_access(user_id: int) -> bool:
        """Проверка доступа к админке"""
        return user_id in admin_ids
    
    # === КЛАВИАТУРЫ АДМИНКИ ===
    def get_admin_main_kb():
        """Главное меню админки"""
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📊 Статистика", callback_data=CB_ADMIN_STATS)],
                [InlineKeyboardButton(text="📅 Список событий", callback_data=CB_ADMIN_EVENTS_LIST)],
                [InlineKeyboardButton(text="🎟 Список бронирований", callback_data=CB_ADMIN_BOOKINGS)],
                [InlineKeyboardButton(text="🔙 Назад в главное", callback_data="nav:back_to_main")]
            ]
        )
    
    def get_admin_events_kb(events):
        """Список событий для админки"""
        buttons = []
        for event in events:
            event_id, event_type, city, date_time, creator_name, creator_username, status, participants_count, max_participants = event
            status_emoji = "✅" if status == "ACTIVE" else "❌"
            creator_display = f"@{creator_username}" if creator_username else creator_name or "Неизвестен"
            
            text = f"{status_emoji} {event_type[:15]} | {city} | {participants_count}/{max_participants}"
            
            buttons.append([
                InlineKeyboardButton(
                    text=text,
                    callback_data=f"{CB_ADMIN_EVENTS_DETAIL}{event_id}"
                )
            ])
        
        buttons.append([InlineKeyboardButton(text="🔙 Назад в админку", callback_data=CB_ADMIN_MENU)])
        return InlineKeyboardMarkup(inline_keyboard=buttons)
    
    def get_admin_event_detail_kb(event_id):
        """Детали события для админки"""
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад к списку", callback_data=CB_ADMIN_EVENTS_LIST)],
                [InlineKeyboardButton(text="🏠 В админку", callback_data=CB_ADMIN_MENU)]
            ]
        )
    
    def get_admin_bookings_kb(bookings, current_page=0, total_pages=1):
        """Список бронирований с пагинацией"""
        buttons = []
        
        for booking in bookings:
            booking_id, booking_date, status, telegram_id, user_name, username, event_id, event_type, city, event_datetime = booking
            date_str = datetime.fromisoformat(booking_date.replace(' ', 'T')).strftime("%d.%m %H:%M")
            user_display = f"@{username}" if username else user_name or f"ID:{telegram_id}"
            
            text = f"📅 {date_str} | {user_display[:15]} | {event_type[:15]}"
            
            buttons.append([
                InlineKeyboardButton(
                    text=text,
                    callback_data=f"booking_info:{booking_id}"
                )
            ])
        
        # Пагинация
        nav_buttons = []
        if current_page > 0:
            nav_buttons.append(InlineKeyboardButton(
                text="⬅️ Назад", 
                callback_data=f"{CB_ADMIN_BOOKINGS_PAGE}{current_page-1}"
            ))
        
        nav_buttons.append(InlineKeyboardButton(
            text=f"{current_page+1}/{total_pages}", 
            callback_data=CB_ADMIN_MENU
        ))
        
        if current_page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton(
                text="Вперед ➡️", 
                callback_data=f"{CB_ADMIN_BOOKINGS_PAGE}{current_page+1}"
            ))
        
        if nav_buttons:
            buttons.append(nav_buttons)
        
        buttons.append([InlineKeyboardButton(text="🔙 Назад в админку", callback_data=CB_ADMIN_MENU)])
        return InlineKeyboardMarkup(inline_keyboard=buttons)
    
    # === ОБРАБОТЧИКИ АДМИНКИ ===
    @admin_router.callback_query(F.data == CB_ADMIN_MENU)
    async def admin_menu_handler(callback: CallbackQuery):
        """Главное меню админки"""
        if not await check_admin_access(callback.from_user.id):
            await callback.answer(ADMIN_NO_ACCESS)
            return
        
        await callback.message.edit_text(ADMIN_MENU_TITLE, reply_markup=get_admin_main_kb(), parse_mode="HTML")
        await callback.answer()
    
    @admin_router.callback_query(F.data == CB_ADMIN_STATS)
    async def admin_stats_handler(callback: CallbackQuery):
        """Статистика платформы"""
        if not await check_admin_access(callback.from_user.id):
            await callback.answer(ADMIN_NO_ACCESS)
            return
        
        stats = await db.get_admin_stats()
        
        # Форматируем статистику
        total_revenue = stats['total_bookings'] * platform_fee
        
        # Формируем текст топ-городов
        top_cities_text = ""
        for city, count in stats['top_cities']:
            top_cities_text += f"• {city}: {count} событий\n"
        
        stats_text = ADMIN_STATS_TEXT.format(
            total_users=stats['total_users'],
            total_events=stats['total_events'],
            active_events=stats['active_events'],
            total_bookings=stats['total_bookings'],
            total_revenue=total_revenue,
            top_cities=top_cities_text
        )
        
        await callback.message.edit_text(
            stats_text,
            reply_markup=get_admin_main_kb(),
            parse_mode="HTML"
        )
        await callback.answer()
    
    @admin_router.callback_query(F.data == CB_ADMIN_EVENTS_LIST)
    async def admin_events_list_handler(callback: CallbackQuery):
        """Список всех событий"""
        if not await check_admin_access(callback.from_user.id):
            await callback.answer(ADMIN_NO_ACCESS)
            return
        
        events = await db.get_all_events_admin(limit=30)
        
        if not events:
            await callback.message.edit_text(
                ADMIN_EVENTS_EMPTY,
                reply_markup=get_admin_main_kb(),
                parse_mode="HTML"
            )
            await callback.answer()
            return
        
        await callback.message.edit_text(
            ADMIN_EVENTS_FOUND.format(count=len(events)),
            reply_markup=get_admin_events_kb(events),
            parse_mode="HTML"
        )
        await callback.answer()
    
    @admin_router.callback_query(F.data.startswith(CB_ADMIN_EVENTS_DETAIL))
    async def admin_event_detail_handler(callback: CallbackQuery):
        """Детали конкретного события"""
        if not await check_admin_access(callback.from_user.id):
            await callback.answer(ADMIN_NO_ACCESS)
            return
        
        try:
            event_id = int(callback.data.split(CB_ADMIN_EVENTS_DETAIL, 1)[1])
        except ValueError:
            await callback.answer("❌ Ошибка: неверный ID события")
            return
        
        event = await db.get_event_full_details(event_id)
        
        if not event:
            await callback.message.edit_text(
                ERROR_EVENT_NOT_FOUND,
                reply_markup=get_admin_main_kb()
            )
            await callback.answer()
            return
        
        # Распаковываем данные
        (e_id, e_type, custom_type, city, date, time, max_participants, 
         description, contact, status, created_at, creator_tg_id, 
         creator_name, creator_username, confirmed_count, total_participants) = event
        
        display_type = custom_type or e_type
        status_text = "✅ Активно" if status == "ACTIVE" else "❌ Неактивно"
        created_date = datetime.fromisoformat(created_at.replace(' ', 'T')).strftime("%d.%m.%Y %H:%M")
        creator_display = f"@{creator_username}" if creator_username else creator_name or "Неизвестен"
        
        event_text = ADMIN_EVENT_DETAILS.format(
            event_id=e_id,
            event_type=display_type,
            city=city,
            date=date,
            time=time,
            status_text=status_text,
            confirmed_count=confirmed_count,
            max_participants=max_participants,
            total_participants=total_participants,
            contact=contact,
            creator_display=creator_display,
            creator_telegram_id=creator_tg_id,
            created_date=created_date,
            description=description
        )
        
        await callback.message.edit_text(
            event_text,
            reply_markup=get_admin_event_detail_kb(event_id),
            parse_mode="HTML"
        )
        await callback.answer()
    
    @admin_router.callback_query(F.data == CB_ADMIN_BOOKINGS)
    async def admin_bookings_handler(callback: CallbackQuery):
        """Список бронирований"""
        if not await check_admin_access(callback.from_user.id):
            await callback.answer(ADMIN_NO_ACCESS)
            return
        
        # Настройки пагинации
        items_per_page = 10
        page = 0
        offset = page * items_per_page
        
        # Получаем данные
        bookings = await db.get_recent_bookings(limit=items_per_page, offset=offset)
        total_bookings = await db.get_bookings_count()
        total_pages = max(1, math.ceil(total_bookings / items_per_page))
        
        if not bookings:
            await callback.message.edit_text(
                ADMIN_BOOKINGS_EMPTY,
                reply_markup=get_admin_main_kb(),
                parse_mode="HTML"
            )
            await callback.answer()
            return
        
        # Формируем текст
        bookings_list_text = ""
        for i, booking in enumerate(bookings, 1):
            booking_id, booking_date, status, telegram_id, user_name, username, event_id, event_type, city, event_datetime = booking
            date_str = datetime.fromisoformat(booking_date.replace(' ', 'T')).strftime("%d.%m %H:%M")
            user_display = f"@{username}" if username else user_name or f"ID:{telegram_id}"
            
            bookings_list_text += (
                f"{i}. <b>{date_str}</b>\n"
                f"   👤 {user_display}\n"
                f"   🎯 {event_type} ({city})\n"
                f"   📅 {event_datetime}\n\n"
            )
        
        bookings_text = ADMIN_BOOKINGS_LIST.format(
            total=total_bookings,
            current_page=page+1,
            total_pages=total_pages,
            bookings_list=bookings_list_text
        )
        
        await callback.message.edit_text(
            bookings_text,
            reply_markup=get_admin_bookings_kb(bookings, page, total_pages),
            parse_mode="HTML"
        )
        await callback.answer()
    
    @admin_router.callback_query(F.data.startswith("booking_info:"))
    async def booking_info_handler(callback: CallbackQuery):
        """Детали бронирования"""
        if not await check_admin_access(callback.from_user.id):
            await callback.answer(ADMIN_NO_ACCESS)
            return

        try:
            booking_id = int(callback.data.split("booking_info:", 1)[1])
        except Exception:
            await callback.answer("❌ Неверный ID брони")
            return

        booking = await db.get_booking_by_id(booking_id)
        if not booking:
            await callback.message.edit_text(ERROR_EVENT_NOT_FOUND, reply_markup=get_admin_main_kb())
            await callback.answer()
            return

        (b_id, booking_date, status, telegram_id, user_name, username, event_id,
         event_type, city, event_datetime) = booking

        user_display = f"@{username}" if username else user_name or f"ID:{telegram_id}"
        booking_date_formatted = datetime.fromisoformat(booking_date.replace(' ', 'T')).strftime("%d.%m.%Y %H:%M")
        
        text = ADMIN_BOOKING_DETAILS.format(
            booking_id=b_id,
            user_display=user_display,
            telegram_id=telegram_id,
            event_type=event_type,
            event_id=event_id,
            city=city,
            event_datetime=event_datetime,
            booking_date=booking_date_formatted,
            status=status
        )

        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=get_admin_main_kb())
        await callback.answer()
    
    @admin_router.callback_query(F.data.startswith(CB_ADMIN_BOOKINGS_PAGE))
    async def admin_bookings_page_handler(callback: CallbackQuery):
        """Пагинация списка бронирований"""
        if not await check_admin_access(callback.from_user.id):
            await callback.answer(ADMIN_NO_ACCESS)
            return

        try:
            page = int(callback.data.split(CB_ADMIN_BOOKINGS_PAGE, 1)[1])
        except Exception:
            page = 0

        limit = 10
        total = await db.get_bookings_count()
        total_pages = max(1, math.ceil(total / limit))
        offset = page * limit

        bookings = await db.get_recent_bookings(limit=limit, offset=offset)
        
        if not bookings:
            await callback.message.edit_text(
                f"🎟 <b>Список бронирований</b>\n\nНа странице {page+1}/{total_pages} нет бронирований.",
                reply_markup=get_admin_main_kb(),
                parse_mode="HTML"
            )
            await callback.answer()
            return
        
        # Формируем текст
        bookings_list_text = ""
        for i, booking in enumerate(bookings, 1):
            booking_id, booking_date, status, telegram_id, user_name, username, event_id, event_type, city, event_datetime = booking
            date_str = datetime.fromisoformat(booking_date.replace(' ', 'T')).strftime("%d.%m %H:%M")
            user_display = f"@{username}" if username else user_name or f"ID:{telegram_id}"
            
            bookings_list_text += (
                f"{i}. <b>{date_str}</b>\n"
                f"   👤 {user_display}\n"
                f"   🎯 {event_type} ({city})\n"
                f"   📅 {event_datetime}\n\n"
            )
        
        bookings_text = ADMIN_BOOKINGS_LIST.format(
            total=total,
            current_page=page+1,
            total_pages=total_pages,
            bookings_list=bookings_list_text
        )
        
        await callback.message.edit_text(
            bookings_text,
            reply_markup=get_admin_bookings_kb(bookings, page, total_pages),
            parse_mode="HTML"
        )
        await callback.answer()
    
    @admin_router.message(F.text == BTN_ADMIN, StateFilter("*"))
    async def admin_access(message: Message):
        """Доступ к админке из главного меню"""
        if message.from_user.id not in admin_ids:
            await message.answer(ADMIN_NO_ACCESS)
            return
        
        await message.answer(ADMIN_MENU_TITLE, reply_markup=get_admin_main_kb(), parse_mode="HTML")
    
    # Включаем админ-роутер в основной роутер
    router.include_router(admin_router)
