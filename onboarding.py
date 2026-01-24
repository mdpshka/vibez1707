# onboarding.py
"""
Модуль онбординга бота VIBEZ
"""
import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardRemove

from texts import *
from keyboards import get_cities_keyboard, get_main_menu_kb, get_back_cancel_kb, get_event_details_kb
from database import Database

# === CALLBACK DATA PREFIXES ===
CB_CITY_SELECT = "city:select:"
CB_CITY_PAGE = "city:page:"
CB_ONBOARDING_CANCEL = "onboarding:cancel"

# === СОСТОЯНИЯ ОНБОРДИНГА ===
class OnboardingStates(StatesGroup):
    NAME = State()
    CITY = State()

def register_onboarding(router: Router, db: Database, admin_ids: list):
    """Регистрация онбординг-роутера"""
    
    onboarding_router = Router()
    
    @onboarding_router.message(OnboardingStates.NAME)
    async def process_name(message: Message, state: FSMContext):
        """Обработка ввода имени"""
        # Обработка кнопки Отмена
        if message.text == BTN_CANCEL:
            await state.clear()
            await state.set_state("MainStates:MAIN_MENU")
            await message.answer(ONBOARDING_CANCELLED, reply_markup=get_main_menu_kb(message.from_user.id, admin_ids))
            return
        
        # Обработка кнопки Назад (не должна быть доступна на этом этапе, но на всякий случай)
        if message.text == BTN_BACK:
            await state.set_state("MainStates:MAIN_MENU")
            await message.answer(
                "Возврат в главное меню",
                reply_markup=get_main_menu_kb(message.from_user.id, admin_ids)
            )
            return
        
        name = message.text.strip()
        if len(name) < 2:
            await message.answer(ERROR_NAME_TOO_SHORT)
            return
        
        # Сохраняем имя и переходим в состояние CITY
        await state.update_data(name=name)
        await state.set_state(OnboardingStates.CITY)
        
        # Отправляем inline-клавиатуру с городами
        await message.answer(
            ONBOARDING_CITY_SELECTION.format(name=name),
            reply_markup=get_cities_keyboard()
        )
        
        # УБРАНО: не отправляем текстовую клавиатуру, чтобы не перехватывать основной обработчик
    
    @onboarding_router.message(OnboardingStates.CITY)
    async def process_city_text(message: Message, state: FSMContext):
        """Обработка текстовых сообщений в состоянии выбора города"""
        if message.text == BTN_CANCEL:
            await state.clear()
            await state.set_state("MainStates:MAIN_MENU")
            await message.answer(
                ONBOARDING_CANCELLED,
                reply_markup=get_main_menu_kb(message.from_user.id, admin_ids)
            )
            return
        
        if message.text == BTN_BACK:
            await state.set_state(OnboardingStates.NAME)
            await message.answer(
                "Введите ваше имя:",
                reply_markup=ReplyKeyboardRemove()
            )
            return
        
        # Если пользователь ввел текст вместо выбора из списка
        data = await state.get_data()
        name = data.get('name', 'Пользователь')
        
        await message.answer(
            f"Пожалуйста, выберите город из списка кнопок выше.\n\n"
            f"Вы ввели: '{message.text}'",
            reply_markup=get_cities_keyboard()
        )
    
    @onboarding_router.callback_query(F.data.startswith(CB_CITY_PAGE), OnboardingStates.CITY)
    async def process_city_page(callback: CallbackQuery, state: FSMContext):
        """Обработчик пагинации списка городов"""
        try:
            page = int(callback.data.split(CB_CITY_PAGE, 1)[1])
            await callback.message.edit_reply_markup(
                reply_markup=get_cities_keyboard(page)
            )
        except Exception as e:
            logging.error(f"Error in city pagination: {e}")
        await callback.answer()
    
    @onboarding_router.callback_query(F.data.startswith(CB_CITY_SELECT), OnboardingStates.CITY)
    async def process_city_selection(callback: CallbackQuery, state: FSMContext):
        """Обработка выбора города"""
        city = callback.data.split(CB_CITY_SELECT, 1)[1]
        data = await state.get_data()
        name = data['name']
        
        # Сохраняем данные пользователя
        await db.update_user_profile(callback.from_user.id, name, city)
        
        invite_event_id = data.get('invite_event_id')
        
        if invite_event_id:
            # Если пользователь пришел по инвайт-ссылке
            await state.set_state("MainStates:VIEWING_EVENT")
            await state.update_data(current_event_id=invite_event_id)
            
            # Получаем данные события
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
                
                # Показываем детали события
                await callback.message.edit_text(
                    text, 
                    reply_markup=get_event_details_kb(invite_event_id, callback.from_user.id, is_confirmed), 
                    parse_mode="HTML"
                )
            else:
                # Если событие не найдено, просто завершаем онбординг
                await callback.message.edit_text(
                    ONBOARDING_COMPLETE.format(name=name, city=city)
                )
                await state.set_state("MainStates:MAIN_MENU")
                await callback.message.answer(
                    BACK_TO_MAIN,
                    reply_markup=get_main_menu_kb(callback.from_user.id, admin_ids)
                )
        else:
            # Обычное завершение онбординга
            await state.set_state("MainStates:MAIN_MENU")
            await callback.message.edit_text(
                ONBOARDING_COMPLETE.format(name=name, city=city)
            )
            await callback.message.answer(
                BACK_TO_MAIN,
                reply_markup=get_main_menu_kb(callback.from_user.id, admin_ids)
            )
        await callback.answer()
    
    @onboarding_router.callback_query(F.data == CB_ONBOARDING_CANCEL)
    async def cancel_onboarding(callback: CallbackQuery, state: FSMContext):
        """Отмена онбординга через inline-кнопку"""
        await state.clear()
        await state.set_state("MainStates:MAIN_MENU")
        
        try:
            await callback.message.edit_text(ONBOARDING_CANCELLED)
        except:
            # Если нельзя редактировать сообщение (например, оно слишком старое)
            await callback.message.answer(ONBOARDING_CANCELLED)
        
        await callback.message.answer(
            BACK_TO_MAIN,
            reply_markup=get_main_menu_kb(callback.from_user.id, admin_ids)
        )
        await callback.answer()
    
    # Включаем онбординг-роутер в основной
    router.include_router(onboarding_router)
