# -*- coding: utf-8 -*-
import json
import os
import time
import traceback
from datetime import datetime
from typing import Dict, List, Optional, Any

import vk_api
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType
from vk_api.keyboard import VkKeyboard, VkKeyboardColor
from vk_api.utils import get_random_id

# ================== КОНФИГУРАЦИЯ ==================
# Токен группы (получить в настройках сообщества -> Работа с API -> Ключи доступа)
VK_TOKEN = "ВАШ_ТОКЕН_ГРУППЫ"
GROUP_ID = 123456789  # ID вашего сообщества

# Файлы для хранения данных
PRICES_FILE = "prices.json"
DRIVERS_FILE = "drivers.json"
ADMINS_FILE = "admins.json"
OPERATORS_FILE = "operators.json"
SHIFTS_FILE = "shifts.json"
ORDERS_FILE = "orders.json"
ERROR_LOG_FILE = "error.log"
ORDERS_COUNTER_FILE = "orders_counter.json"

# Телефон для связи с парком
PARK_PHONE = "+7 (999) 123-45-67 (городской)"

# ================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==================

def log_error(error_text: str) -> None:
    """Запись ошибки в лог-файл."""
    with open(ERROR_LOG_FILE, "a", encoding="utf-8") as f:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"[{timestamp}] {error_text}\n")
        f.write("-" * 50 + "\n")

def ensure_json_file(filename: str, default_data: Any) -> None:
    """Создать JSON-файл с данными по умолчанию, если его нет."""
    if not os.path.exists(filename):
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(default_data, f, ensure_ascii=False, indent=4)

def load_json(filename: str) -> Any:
    """Загрузить данные из JSON-файла."""
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        log_error(f"Ошибка загрузки {filename}: {e}")
        return None

def save_json(filename: str, data: Any) -> bool:
    """Сохранить данные в JSON-файл."""
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        return True
    except Exception as e:
        log_error(f"Ошибка сохранения {filename}: {e}")
        return False

def split_long_message(text: str, max_len: int = 4000) -> List[str]:
    """Разбить длинное сообщение на части."""
    if len(text) <= max_len:
        return [text]
    
    parts = []
    current_part = ""
    
    for line in text.split("\n"):
        if len(current_part) + len(line) + 1 > max_len:
            if current_part:
                parts.append(current_part.strip())
            current_part = line + "\n"
        else:
            current_part += line + "\n"
    
    if current_part:
        parts.append(current_part.strip())
    
    return parts

def get_next_order_id() -> int:
    """Получить следующий ID заказа."""
    counter = load_json(ORDERS_COUNTER_FILE)
    if counter is None:
        counter = {"counter": 0}
    
    counter["counter"] += 1
    save_json(ORDERS_COUNTER_FILE, counter)
    return counter["counter"]

# ================== КЛАСС БОТА ==================

class TaxiBot:
    """Основной класс бота таксопарка."""
    
    def __init__(self):
        """Инициализация бота."""
        # Инициализация файлов данных
        self._init_data_files()
        
        # Подключение к VK API
        self.vk_session = vk_api.VkApi(token=VK_TOKEN)
        self.vk = self.vk_session.get_api()
        self.longpoll = VkBotLongPoll(self.vk_session, GROUP_ID)
        
        # FSM: словарь состояний пользователей
        self.user_states = {}
        # Временное хранилище данных во время FSM
        self.temp_data = {}
        
        # Загрузка данных в память
        self.prices = load_json(PRICES_FILE)
        self.drivers = load_json(DRIVERS_FILE)
        self.admins = load_json(ADMINS_FILE)
        self.operators = load_json(OPERATORS_FILE)
        self.shifts = load_json(SHIFTS_FILE)
        
    def _init_data_files(self) -> None:
        """Создание всех необходимых JSON-файлов с начальными данными."""
        # Цены и тарифы
        ensure_json_file(PRICES_FILE, {
            "price_per_km": 25.0,
            "price_per_min": 8.0,
            "night_coeff": 1.2,
            "daily_plan": 1500.0
        })
        
        # Водители
        ensure_json_file(DRIVERS_FILE, {})
        
        # Администраторы
        ensure_json_file(ADMINS_FILE, [])
        
        # Операторы
        ensure_json_file(OPERATORS_FILE, [])
        
        # Смены
        ensure_json_file(SHIFTS_FILE, {})
        
        # Заказы
        ensure_json_file(ORDERS_FILE, [])
        
        # Счетчик заказов
        ensure_json_file(ORDERS_COUNTER_FILE, {"counter": 0})
    
    def is_admin(self, user_id: int) -> bool:
        """Проверка, является ли пользователь администратором."""
        return user_id in self.admins
    
    def is_operator(self, user_id: int) -> bool:
        """Проверка, является ли пользователь оператором."""
        return user_id in self.operators
    
    def is_driver(self, user_id: int) -> bool:
        """Проверка, является ли пользователь водителем."""
        return str(user_id) in self.drivers
    
    def get_online_drivers(self) -> Dict[str, dict]:
        """Получить список водителей на линии."""
        return {k: v for k, v in self.drivers.items() if v.get("online", False)}
    
    def get_main_keyboard(self, user_id: int) -> str:
        """Создание клавиатуры главного меню."""
        keyboard = VkKeyboard(one_time=False, inline=False)
        
        # Первый ряд
        keyboard.add_button("📋 Новый заказ", color=VkKeyboardColor.PRIMARY)
        keyboard.add_button("👤 Водители на линии", color=VkKeyboardColor.PRIMARY)
        
        keyboard.add_line()
        keyboard.add_button("💰 Баланс смены", color=VkKeyboardColor.PRIMARY)
        keyboard.add_button("📊 Отчет", color=VkKeyboardColor.PRIMARY)
        
        keyboard.add_line()
        
        # Админ-панель показываем только админам
        if self.is_admin(user_id):
            keyboard.add_button("⚙️ Админ-панель", color=VkKeyboardColor.SECONDARY)
        else:
            keyboard.add_button("⚙️ Админ-панель", color=VkKeyboardColor.SECONDARY)  # Скрывать не можем, но проверка будет при нажатии
        
        keyboard.add_button("📞 Связь с парком", color=VkKeyboardColor.PRIMARY)
        
        return keyboard.get_keyboard()
    
    def get_cancel_keyboard(self) -> str:
        """Создание клавиатуры с кнопкой Отмена."""
        keyboard = VkKeyboard(one_time=False, inline=False)
        keyboard.add_button("❌ Отмена", color=VkKeyboardColor.NEGATIVE)
        return keyboard.get_keyboard()
    
    def get_back_keyboard(self) -> str:
        """Создание клавиатуры с кнопкой Назад."""
        keyboard = VkKeyboard(one_time=False, inline=False)
        keyboard.add_button("🔙 Назад", color=VkKeyboardColor.SECONDARY)
        return keyboard.get_keyboard()
    
    def calculate_price(self, km: float, minutes_est: int = 0) -> dict:
        """Расчет стоимости поездки."""
        # Определяем коэффициент (день/ночь)
        current_hour = datetime.now().hour
        if 22 <= current_hour or current_hour < 6:
            coeff = self.prices.get("night_coeff", 1.2)
        else:
            coeff = 1.0
        
        # Расчет
        cost_km = km * self.prices.get("price_per_km", 25.0)
        cost_min = minutes_est * self.prices.get("price_per_min", 8.0)
        total = (cost_km + cost_min) * coeff
        
        return {
            "km_cost": cost_km,
            "min_cost": cost_min,
            "coeff": coeff,
            "total": round(total, 2)
        }
    
    def send_message(self, user_id: int, message: str, keyboard: Optional[str] = None) -> None:
        """Отправка сообщения пользователю с поддержкой длинных сообщений."""
        try:
            parts = split_long_message(message)
            
            for i, part in enumerate(parts):
                # Клавиатуру отправляем только с первой частью
                current_keyboard = keyboard if i == 0 else None
                
                self.vk.messages.send(
                    user_id=user_id,
                    message=part,
                    random_id=get_random_id(),
                    keyboard=current_keyboard
                )
                # Небольшая задержка между частями
                if i < len(parts) - 1:
                    time.sleep(0.3)
                    
        except Exception as e:
            log_error(f"Ошибка отправки сообщения user_id={user_id}: {e}\n{traceback.format_exc()}")
    
    def reset_user_state(self, user_id: int) -> None:
        """Сброс состояния пользователя."""
        if user_id in self.user_states:
            del self.user_states[user_id]
        if str(user_id) in self.temp_data:
            del self.temp_data[str(user_id)]
    
    # ================== ОБРАБОТЧИКИ КОМАНД И КНОПОК ==================
    
    def handle_start(self, user_id: int) -> None:
        """Обработка команды старт / показ главного меню."""
        self.reset_user_state(user_id)
        
        welcome_msg = (
            "🚕 Добро пожаловать в систему управления таксопарком!\n\n"
            "Выберите действие на клавиатуре:"
        )
        self.send_message(user_id, welcome_msg, self.get_main_keyboard(user_id))
    
    def handle_ping(self, user_id: int) -> None:
        """Обработка тестовой команды !ping."""
        self.send_message(user_id, "🟢 Бот работает. Потерь пакетов: 0")
    
    def handle_contact_park(self, user_id: int) -> None:
        """Обработка кнопки связи с парком."""
        message = f"📞 Звони старшему: {PARK_PHONE}"
        self.send_message(user_id, message, self.get_main_keyboard(user_id))
    
    def handle_drivers_online(self, user_id: int) -> None:
        """Показать список водителей на линии."""
        online_drivers = self.get_online_drivers()
        
        if not online_drivers:
            message = "😴 Сейчас нет водителей на линии."
        else:
            lines = ["👤 **ВОДИТЕЛИ НА ЛИНИИ:**\n"]
            for driver_id, info in online_drivers.items():
                shift_info = self.shifts.get(driver_id, {})
                orders_count = shift_info.get("orders_count", 0)
                earned = shift_info.get("total_earned", 0)
                
                lines.append(
                    f"🚗 {info['name']} | {info['car']}\n"
                    f"   Заказов: {orders_count} | Заработано: {earned:.2f}₽\n"
                )
            message = "\n".join(lines)
        
        self.send_message(user_id, message, self.get_main_keyboard(user_id))
    
    def handle_shift_balance(self, user_id: int) -> None:
        """Показать баланс текущей смены."""
        online_drivers = self.get_online_drivers()
        
        if not online_drivers:
            message = "😴 Нет активных водителей на смене."
        else:
            total_earned = 0
            total_orders = 0
            
            lines = ["💰 **БАЛАНС СМЕНЫ:**\n"]
            
            for driver_id, info in online_drivers.items():
                shift_info = self.shifts.get(driver_id, {})
                earned = shift_info.get("total_earned", 0)
                orders = shift_info.get("orders_count", 0)
                
                total_earned += earned
                total_orders += orders
                
                lines.append(f"{info['name']}: {earned:.2f}₽ ({orders} заказов)")
            
            # План
            daily_plan = self.prices.get("daily_plan", 1500.0)
            plan_percent = (total_earned / daily_plan * 100) if daily_plan > 0 else 0
            
            lines.append("")
            lines.append(f"📊 **ИТОГО:** {total_earned:.2f}₽")
            lines.append(f"📋 Всего заказов: {total_orders}")
            lines.append(f"🎯 Выполнение плана: {plan_percent:.1f}% ({total_earned:.2f} / {daily_plan:.2f})")
            
            message = "\n".join(lines)
        
        self.send_message(user_id, message, self.get_main_keyboard(user_id))
    
    def handle_report(self, user_id: int) -> None:
        """Показать отчет по всем заказам."""
        orders = load_json(ORDERS_FILE)
        
        if not orders:
            message = "📭 Заказов пока нет."
            self.send_message(user_id, message, self.get_main_keyboard(user_id))
            return
        
        # Последние 10 заказов
        recent_orders = orders[-10:]
        recent_orders.reverse()
        
        total_sum = sum(order.get("price", 0) for order in orders)
        
        lines = ["📊 **ОТЧЕТ ПО ЗАКАЗАМ:**\n"]
        lines.append(f"Всего заказов: {len(orders)}")
        lines.append(f"Общая сумма: {total_sum:.2f}₽\n")
        lines.append("**Последние заказы:**\n")
        
        for order in recent_orders:
            order_id = order.get("order_id", "?")
            driver_name = order.get("driver_name", "Не назначен")
            price = order.get("price", 0)
            created = order.get("created_at", "")
            
            lines.append(f"#{order_id} | {driver_name} | {price:.2f}₽")
            lines.append(f"   {created}")
        
        message = "\n".join(lines)
        self.send_message(user_id, message, self.get_main_keyboard(user_id))
    
    # ================== FSM: СОЗДАНИЕ ЗАКАЗА ==================
    
    def start_order_creation(self, user_id: int) -> None:
        """Начало процесса создания заказа."""
        self.user_states[user_id] = "order_client_name"
        self.temp_data[str(user_id)] = {}
        
        message = "📋 **НОВЫЙ ЗАКАЗ**\n\nВведите имя клиента:"
        self.send_message(user_id, message, self.get_cancel_keyboard())
    
    def process_order_step(self, user_id: int, text: str) -> None:
        """Обработка шагов создания заказа."""
        state = self.user_states.get(user_id)
        temp = self.temp_data.get(str(user_id), {})
        
        if state == "order_client_name":
            temp["client_name"] = text
            self.user_states[user_id] = "order_address_from"
            message = "📍 Введите адрес подачи:"
            self.send_message(user_id, message, self.get_cancel_keyboard())
            
        elif state == "order_address_from":
            temp["address_from"] = text
            self.user_states[user_id] = "order_address_to"
            message = "🎯 Введите адрес назначения:"
            self.send_message(user_id, message, self.get_cancel_keyboard())
            
        elif state == "order_address_to":
            temp["address_to"] = text
            self.user_states[user_id] = "order_km"
            message = "📏 Примерный километраж (только число):"
            self.send_message(user_id, message, self.get_cancel_keyboard())
            
        elif state == "order_km":
            try:
                km = float(text.replace(",", "."))
                if km <= 0:
                    raise ValueError("Километраж должен быть положительным")
                
                temp["km"] = km
                
                # Рассчитываем примерную стоимость
                price_info = self.calculate_price(km)
                temp["price_info"] = price_info
                
                # Показываем список доступных водителей
                online_drivers = self.get_online_drivers()
                
                if not online_drivers:
                    self.send_message(user_id, "❌ Нет доступных водителей на линии!", self.get_main_keyboard(user_id))
                    self.reset_user_state(user_id)
                    return
                
                self.user_states[user_id] = "order_select_driver"
                
                # Создаем клавиатуру с водителями
                keyboard = VkKeyboard(one_time=False, inline=False)
                
                for i, (driver_id, info) in enumerate(online_drivers.items()):
                    button_text = f"🚗 {info['name']} ({info['car']})"
                    # Сохраняем ID водителя во временных данных для обработки
                    driver_key = f"driver_{i}"
                    temp[driver_key] = driver_id
                    keyboard.add_button(button_text, color=VkKeyboardColor.PRIMARY)
                    keyboard.add_line()
                
                keyboard.add_button("❌ Отмена", color=VkKeyboardColor.NEGATIVE)
                
                message = (
                    f"👤 Клиент: {temp['client_name']}\n"
                    f"📍 Откуда: {temp['address_from']}\n"
                    f"🎯 Куда: {temp['address_to']}\n"
                    f"📏 Расстояние: {km} км\n"
                    f"💰 Примерная стоимость: {price_info['total']}₽\n\n"
                    f"👤 **Выберите водителя:**"
                )
                
                self.send_message(user_id, message, keyboard.get_keyboard())
                
            except ValueError:
                self.send_message(user_id, "❌ Введите корректное число!", self.get_cancel_keyboard())
                
        elif state == "order_select_driver":
            self.send_message(user_id, "⚠️ Пожалуйста, выберите водителя из списка кнопок.")
            
        self.temp_data[str(user_id)] = temp
    
    def complete_order(self, user_id: int, driver_name: str, driver_id: str) -> None:
        """Завершение создания заказа."""
        temp = self.temp_data.get(str(user_id), {})
        
        if not temp:
            self.send_message(user_id, "❌ Ошибка: данные заказа утеряны.", self.get_main_keyboard(user_id))
            self.reset_user_state(user_id)
            return
        
        # Получаем следующий ID заказа
        order_id = get_next_order_id()
        price_info = temp.get("price_info", {})
        total_price = price_info.get("total", 0)
        
        # Создаем запись о заказе
        order = {
            "order_id": order_id,
            "client_name": temp.get("client_name", "Не указано"),
            "address_from": temp.get("address_from", ""),
            "address_to": temp.get("address_to", ""),
            "km": temp.get("km", 0),
            "price": total_price,
            "driver_id": driver_id,
            "driver_name": driver_name,
            "created_by": user_id,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": "active"
        }
        
        # Сохраняем заказ
        orders = load_json(ORDERS_FILE) or []
        orders.append(order)
        save_json(ORDERS_FILE, orders)
        
        # Обновляем статистику водителя в смене
        if driver_id in self.shifts:
            self.shifts[driver_id]["total_earned"] = self.shifts[driver_id].get("total_earned", 0) + total_price
            self.shifts[driver_id]["orders_count"] = self.shifts[driver_id].get("orders_count", 0) + 1
            save_json(SHIFTS_FILE, self.shifts)
        
        # Сообщение об успехе
        message = (
            f"✅ **ЗАКАЗ #{order_id} СОЗДАН**\n\n"
            f"👤 Клиент: {order['client_name']}\n"
            f"📍 Откуда: {order['address_from']}\n"
            f"🎯 Куда: {order['address_to']}\n"
            f"📏 Расстояние: {order['km']} км\n"
            f"💰 Стоимость: {total_price}₽\n"
            f"🚗 Водитель: {driver_name}"
        )
        
        self.send_message(user_id, message, self.get_main_keyboard(user_id))
        self.reset_user_state(user_id)
    
    # ================== АДМИН-ПАНЕЛЬ ==================
    
    def show_admin_panel(self, user_id: int) -> None:
        """Показать панель администратора."""
        if not self.is_admin(user_id):
            self.send_message(user_id, "⛔ Доступ запрещен.", self.get_main_keyboard(user_id))
            return
        
        self.reset_user_state(user_id)
        self.user_states[user_id] = "admin_menu"
        
        keyboard = VkKeyboard(one_time=False, inline=False)
        keyboard.add_button("➕ Добавить води/опер", color=VkKeyboardColor.PRIMARY)
        keyboard.add_button("➖ Удалить", color=VkKeyboardColor.NEGATIVE)
        keyboard.add_line()
        keyboard.add_button("📝 Тарифы", color=VkKeyboardColor.PRIMARY)
        keyboard.add_button("🔄 Сброс смены", color=VkKeyboardColor.PRIMARY)
        keyboard.add_line()
        keyboard.add_button("🔙 Назад", color=VkKeyboardColor.SECONDARY)
        
        message = "⚙️ **АДМИН-ПАНЕЛЬ**\n\nВыберите действие:"
        self.send_message(user_id, message, keyboard.get_keyboard())
    
    def process_admin_action(self, user_id: int, text: str) -> None:
        """Обработка действий в админ-панели."""
        if text == "➕ Добавить води/опер":
            self.user_states[user_id] = "admin_add_type"
            keyboard = VkKeyboard(one_time=False, inline=False)
            keyboard.add_button("🚗 Водитель", color=VkKeyboardColor.PRIMARY)
            keyboard.add_button("📞 Оператор", color=VkKeyboardColor.PRIMARY)
            keyboard.add_line()
            keyboard.add_button("🔙 Назад", color=VkKeyboardColor.SECONDARY)
            
            self.send_message(user_id, "Выберите тип добавляемого пользователя:", keyboard.get_keyboard())
            
        elif text == "➖ Удалить":
            # Показываем список для удаления
            message = "**Список пользователей для удаления:**\n\n"
            
            if self.drivers:
                message += "🚗 **Водители:**\n"
                for driver_id, info in self.drivers.items():
                    message += f"  ID: {driver_id} - {info['name']}\n"
            
            if self.operators:
                message += "\n📞 **Операторы:**\n"
                for op_id in self.operators:
                    message += f"  ID: {op_id}\n"
            
            if self.admins:
                message += "\n👑 **Админы:**\n"
                for admin_id in self.admins:
                    message += f"  ID: {admin_id}\n"
            
            message += "\n\nВведите ID пользователя для удаления:"
            self.user_states[user_id] = "admin_delete_id"
            self.send_message(user_id, message, self.get_back_keyboard())
            
        elif text == "📝 Тарифы":
            self.show_tariffs(user_id)
            
        elif text == "🔄 Сброс смены":
            keyboard = VkKeyboard(one_time=False, inline=False)
            keyboard.add_button("✅ Да, сбросить смену", color=VkKeyboardColor.POSITIVE)
            keyboard.add_line()
            keyboard.add_button("🔙 Назад", color=VkKeyboardColor.SECONDARY)
            
            message = (
                "⚠️ **СБРОС СМЕНЫ**\n\n"
                "Будут обнулены:\n"
                "• Заработок водителей за смену\n"
                "• Количество выполненных заказов\n\n"
                "Водители будут переведены в офлайн.\n\n"
                "Вы уверены?"
            )
            self.send_message(user_id, message, keyboard.get_keyboard())
            self.user_states[user_id] = "admin_confirm_reset"
    
    def show_tariffs(self, user_id: int) -> None:
        """Показать текущие тарифы."""
        self.user_states[user_id] = "admin_tariffs"
        self.temp_data[str(user_id)] = {"editing": None}
        
        keyboard = VkKeyboard(one_time=False, inline=False)
        keyboard.add_button("📏 Цена за км", color=VkKeyboardColor.PRIMARY)
        keyboard.add_button("⏱ Цена за минуту", color=VkKeyboardColor.PRIMARY)
        keyboard.add_line()
        keyboard.add_button("🌙 Ночной коэф.", color=VkKeyboardColor.PRIMARY)
        keyboard.add_button("🎯 Дневной план", color=VkKeyboardColor.PRIMARY)
        keyboard.add_line()
        keyboard.add_button("🔙 Назад", color=VkKeyboardColor.SECONDARY)
        
        message = (
            f"📝 **ТЕКУЩИЕ ТАРИФЫ:**\n\n"
            f"📏 Цена за 1 км: {self.prices.get('price_per_km', 25.0)} ₽\n"
            f"⏱ Цена за 1 минуту: {self.prices.get('price_per_min', 8.0)} ₽\n"
            f"🌙 Ночной коэффициент (22:00-06:00): {self.prices.get('night_coeff', 1.2)}\n"
            f"🎯 Дневной план: {self.prices.get('daily_plan', 1500.0)} ₽\n\n"
            f"Выберите параметр для изменения:"
        )
        
        self.send_message(user_id, message, keyboard.get_keyboard())
    
    def process_tariff_change(self, user_id: int, text: str) -> None:
        """Обработка изменения тарифов."""
        temp = self.temp_data.get(str(user_id), {})
        
        # Определение, какой параметр редактируется
        tariff_map = {
            "📏 Цена за км": "price_per_km",
            "⏱ Цена за минуту": "price_per_min",
            "🌙 Ночной коэф.": "night_coeff",
            "🎯 Дневной план": "daily_plan"
        }
        
        if text in tariff_map:
            param = tariff_map[text]
            temp["editing"] = param
            
            keyboard = VkKeyboard(one_time=False, inline=False)
            keyboard.add_button("🔙 Назад", color=VkKeyboardColor.SECONDARY)
            
            message = (
                f"Текущее значение: {self.prices.get(param, 0)}\n\n"
                f"Введите новое значение (число):"
            )
            
            self.send_message(user_id, message, keyboard.get_keyboard())
            self.user_states[user_id] = "admin_tariff_input"
            
        elif text in ["+1₽", "-1₽"]:
            # Кнопки быстрого изменения
            editing = temp.get("editing")
            if editing:
                delta = 1 if text == "+1₽" else -1
                new_value = self.prices.get(editing, 0) + delta
                if new_value >= 0:
                    self.prices[editing] = new_value
                    save_json(PRICES_FILE, self.prices)
                    self.show_tariffs(user_id)
        
        self.temp_data[str(user_id)] = temp
    
    def process_tariff_input(self, user_id: int, text: str) -> None:
        """Обработка ввода нового значения тарифа."""
        temp = self.temp_data.get(str(user_id), {})
        editing = temp.get("editing")
        
        if editing:
            try:
                new_value = float(text.replace(",", "."))
                if new_value < 0:
                    raise ValueError("Значение не может быть отрицательным")
                
                self.prices[editing] = new_value
                save_json(PRICES_FILE, self.prices)
                
                self.send_message(user_id, f"✅ Значение обновлено!")
                self.show_tariffs(user_id)
                
            except ValueError:
                self.send_message(user_id, "❌ Введите корректное число!")
    
    def process_add_user(self, user_id: int, text: str) -> None:
        """Обработка добавления пользователя."""
        state = self.user_states.get(user_id)
        temp = self.temp_data.get(str(user_id), {})
        
        if state == "admin_add_type":
            if text == "🚗 Водитель":
                temp["type"] = "driver"
                self.user_states[user_id] = "admin_add_id"
                self.send_message(user_id, "Введите ID водителя ВКонтакте:", self.get_back_keyboard())
            elif text == "📞 Оператор":
                temp["type"] = "operator"
                self.user_states[user_id] = "admin_add_id"
                self.send_message(user_id, "Введите ID оператора ВКонтакте:", self.get_back_keyboard())
                
        elif state == "admin_add_id":
            try:
                new_id = int(text)
                temp["id"] = new_id
                
                if temp["type"] == "driver":
                    self.user_states[user_id] = "admin_add_name"
                    self.send_message(user_id, "Введите ФИО водителя:", self.get_back_keyboard())
                else:
                    # Добавляем оператора
                    self.operators.append(new_id)
                    save_json(OPERATORS_FILE, self.operators)
                    self.send_message(user_id, f"✅ Оператор с ID {new_id} добавлен!")
                    self.show_admin_panel(user_id)
                    
            except ValueError:
                self.send_message(user_id, "❌ ID должен быть числом!")
                
        elif state == "admin_add_name":
            temp["name"] = text
            self.user_states[user_id] = "admin_add_car"
            self.send_message(user_id, "Введите марку и номер автомобиля:", self.get_back_keyboard())
            
        elif state == "admin_add_car":
            # Сохраняем водителя
            driver_id = str(temp["id"])
            self.drivers[driver_id] = {
                "name": temp["name"],
                "car": text,
                "personal_km": 0,
                "personal_min": 0,
                "online": False
            }
            save_json(DRIVERS_FILE, self.drivers)
            
            self.send_message(user_id, f"✅ Водитель {temp['name']} добавлен!")
            self.show_admin_panel(user_id)
        
        self.temp_data[str(user_id)] = temp
    
    def process_delete_user(self, user_id: int, text: str) -> None:
        """Обработка удаления пользователя."""
        try:
            delete_id = int(text)
            str_id = str(delete_id)
            
            deleted = False
            
            # Проверяем во всех списках
            if str_id in self.drivers:
                del self.drivers[str_id]
                save_json(DRIVERS_FILE, self.drivers)
                deleted = True
                
            if delete_id in self.operators:
                self.operators.remove(delete_id)
                save_json(OPERATORS_FILE, self.operators)
                deleted = True
                
            if delete_id in self.admins:
                self.admins.remove(delete_id)
                save_json(ADMINS_FILE, self.admins)
                deleted = True
            
            if str_id in self.shifts:
                del self.shifts[str_id]
                save_json(SHIFTS_FILE, self.shifts)
            
            if deleted:
                self.send_message(user_id, f"✅ Пользователь с ID {delete_id} удален!")
            else:
                self.send_message(user_id, f"❌ Пользователь с ID {delete_id} не найден.")
                
            self.show_admin_panel(user_id)
            
        except ValueError:
            self.send_message(user_id, "❌ ID должен быть числом!")
    
    def process_reset_shift(self, user_id: int) -> None:
        """Сброс смены."""
        # Обнуляем смены
        self.shifts = {}
        save_json(SHIFTS_FILE, self.shifts)
        
        # Переводим всех водителей в офлайн
        for driver_id in self.drivers:
            self.drivers[driver_id]["online"] = False
        save_json(DRIVERS_FILE, self.drivers)
        
        self.send_message(user_id, "✅ Смена сброшена. Все водители переведены в офлайн.")
        self.show_admin_panel(user_id)
    
    # ================== ОБРАБОТКА ВОДИТЕЛЕЙ ==================
    
    def toggle_driver_status(self, user_id: int) -> None:
        """Переключение статуса водителя (онлайн/офлайн)."""
        str_id = str(user_id)
        
        if str_id not in self.drivers:
            self.send_message(user_id, "❌ Вы не зарегистрированы как водитель.")
            return
        
        current_status = self.drivers[str_id].get("online", False)
        
        if not current_status:
            # Начало смены
            self.drivers[str_id]["online"] = True
            self.shifts[str_id] = {
                "start_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "total_earned": 0.0,
                "orders_count": 0
            }
            message = "✅ Вы вышли на линию! Удачной смены!"
        else:
            # Конец смены
            self.drivers[str_id]["online"] = False
            shift_info = self.shifts.get(str_id, {})
            earned = shift_info.get("total_earned", 0)
            orders = shift_info.get("orders_count", 0)
            
            message = (
                f"🏁 **СМЕНА ЗАВЕРШЕНА**\n\n"
                f"📊 Выполнено заказов: {orders}\n"
                f"💰 Заработано: {earned:.2f}₽\n\n"
                f"Хорошего отдыха!"
            )
        
        save_json(DRIVERS_FILE, self.drivers)
        
        # Клавиатура для водителя
        keyboard = VkKeyboard(one_time=False, inline=False)
        if self.drivers[str_id]["online"]:
            keyboard.add_button("🏁 Завершить смену", color=VkKeyboardColor.NEGATIVE)
        else:
            keyboard.add_button("🚗 Выйти на линию", color=VkKeyboardColor.POSITIVE)
        
        self.send_message(user_id, message, keyboard.get_keyboard())
    
    # ================== ОСНОВНОЙ ОБРАБОТЧИК СООБЩЕНИЙ ==================
    
    def handle_message(self, user_id: int, text: str) -> None:
        """Главный обработчик входящих сообщений."""
        try:
            # Проверка на команду !ping (вне FSM)
            if text.lower() == "!ping":
                self.handle_ping(user_id)
                return
            
            # Проверяем наличие состояния (FSM)
            state = self.user_states.get(user_id)
            
            if state:
                # Обработка кнопки Отмена
                if text == "❌ Отмена":
                    self.reset_user_state(user_id)
                    self.send_message(user_id, "❌ Действие отменено.", self.get_main_keyboard(user_id))
                    return
                
                # Обработка кнопки Назад
                if text == "🔙 Назад":
                    if state.startswith("admin_"):
                        self.show_admin_panel(user_id)
                    else:
                        self.handle_start(user_id)
                    return
                
                # Обработка FSM состояний
                if state.startswith("order_"):
                    if state == "order_select_driver":
                        # Поиск выбранного водителя
                        online_drivers = self.get_online_drivers()
                        selected_driver_id = None
                        selected_driver_name = ""
                        
                        for driver_id, info in online_drivers.items():
                            if info['name'] in text:
                                selected_driver_id = driver_id
                                selected_driver_name = info['name']
                                break
                        
                        if selected_driver_id:
                            self.complete_order(user_id, selected_driver_name, selected_driver_id)
                        else:
                            self.send_message(user_id, "⚠️ Пожалуйста, выберите водителя из списка кнопок.")
                    else:
                        self.process_order_step(user_id, text)
                        
                elif state.startswith("admin_"):
                    if state == "admin_menu":
                        self.process_admin_action(user_id, text)
                    elif state in ["admin_add_type", "admin_add_id", "admin_add_name", "admin_add_car"]:
                        self.process_add_user(user_id, text)
                    elif state == "admin_delete_id":
                        self.process_delete_user(user_id, text)
                    elif state == "admin_tariffs":
                        self.process_tariff_change(user_id, text)
                    elif state == "admin_tariff_input":
                        self.process_tariff_input(user_id, text)
                    elif state == "admin_confirm_reset":
                        if text == "✅ Да, сбросить смену":
                            self.process_reset_shift(user_id)
                        else:
                            self.show_admin_panel(user_id)
            
            else:
                # Нет активного состояния - обрабатываем кнопки меню
                if text == "📋 Новый заказ":
                    self.start_order_creation(user_id)
                    
                elif text == "👤 Водители на линии":
                    self.handle_drivers_online(user_id)
                    
                elif text == "💰 Баланс смены":
                    self.handle_shift_balance(user_id)
                    
                elif text == "📊 Отчет":
                    self.handle_report(user_id)
                    
                elif text == "⚙️ Админ-панель":
                    self.show_admin_panel(user_id)
                    
                elif text == "📞 Связь с парком":
                    self.handle_contact_park(user_id)
                    
                elif text == "Меню" or text.lower() == "/start":
                    self.handle_start(user_id)
                    
                elif text in ["🚗 Выйти на линию", "🏁 Завершить смену"]:
                    self.toggle_driver_status(user_id)
                    
                else:
                    # Если водитель онлайн, проверяем статус
                    if self.is_driver(user_id) and self.drivers.get(str(user_id), {}).get("online", False):
                        # Водитель на линии, но не в FSM - предлагаем меню
                        self.handle_start(user_id)
                    else:
                        self.handle_start(user_id)
                        
        except Exception as e:
            log_error(f"Ошибка в handle_message для user_id={user_id}: {e}\n{traceback.format_exc()}")
            self.send_message(user_id, "⚠️ Произошла ошибка. Попробуйте позже.", self.get_main_keyboard(user_id))
            self.reset_user_state(user_id)
    
    # ================== ЗАПУСК БОТА ==================
    
    def run(self) -> None:
        """Запуск бота и главный цикл Long Poll."""
        print("=" * 50)
        print("🚕 Бот таксопарка запущен!")
        print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 50)
        
        while True:
            try:
                # Long Poll с таймаутом 25 секунд
                for event in self.longpoll.listen():
                    try:
                        if event.type == VkBotEventType.MESSAGE_NEW:
                            message = event.object.message
                            user_id = message['from_id']
                            text = message['text'].strip()
                            
                            # Игнорируем пустые сообщения
                            if not text:
                                continue
                            
                            # Обрабатываем сообщение
                            self.handle_message(user_id, text)
                            
                    except Exception as e:
                        log_error(f"Ошибка обработки события: {e}\n{traceback.format_exc()}")
                        continue
                        
            except Exception as e:
                # Ошибка соединения - логируем и продолжаем
                log_error(f"Ошибка Long Poll соединения: {e}\n{traceback.format_exc()}")
                print(f"⚠️ Ошибка соединения. Переподключение через 5 секунд...")
                time.sleep(5)
                continue

# ================== ТОЧКА ВХОДА ==================

if __name__ == '__main__':
    bot = TaxiBot()
    bot.run()
