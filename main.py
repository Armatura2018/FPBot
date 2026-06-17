import asyncio
import aiohttp
import os
import json
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

# ================= НАСТРОЙКИ (Берутся с хостинга) =================
TG_TOKEN = os.getenv("TG_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID")) if os.getenv("ADMIN_ID") else 0
VEXBOOST_KEY = os.getenv("VEXBOOST_KEY")

bot = Bot(token=TG_TOKEN)
dp = Dispatcher()

# ================= БАЗЫ ДАННЫХ =================
# Загрузка словаря товаров
def load_products():
    with open("products.json", "r", encoding="utf-8") as f:
        return json.load(f)

PRODUCTS_MAP = load_products()

# Временная память бота для заказов и таймеров
# Формат: { "fp_id": {"status": "in_progress", "vex_id": 123, "time_completed": datetime_obj, "name": "Название"} }
active_orders = {}

# ================= API VEXBOOST =================
class VexBoost:
    BASE_URL = "https://vexboost.ru/api/v2"

    @classmethod
    async def create_order(cls, service_id, link, quantity):
        params = {"action": "add", "service": service_id, "link": link, "quantity": quantity, "key": VEXBOOST_KEY}
        async with aiohttp.ClientSession() as session:
            async with session.get(cls.BASE_URL, params=params) as resp:
                data = await resp.json()
                return data.get("order")

    @classmethod
    async def check_status(cls, order_id):
        params = {"action": "status", "order": order_id, "key": VEXBOOST_KEY}
        async with aiohttp.ClientSession() as session:
            async with session.get(cls.BASE_URL, params=params) as resp:
                data = await resp.json()
                return data.get("status")

# ================= ФОНОВЫЕ ЗАДАЧИ =================

# 1. Автоподнятие лотов на FunPay
async def auto_bump_lots():
    while True:
        try:
           # === КОД ДЛЯ ПОДНЯТИЯ ЛОТОВ ===
            url = "https://funpay.com/lots/raise"
            
            # Берем твой токен FunPay из настроек хостинга
            cookies = {"phpsessid": os.getenv("FUNPAY_PHPSESSID")}
            
            # Притворяемся реальным браузером, чтобы FunPay не блокировал запрос
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "X-Requested-With": "XMLHttpRequest"
            }
            
            # ID категории твоих товаров (надо будет заменить на свой)
            data = {"game_id": 703} 

            async with aiohttp.ClientSession(cookies=cookies, headers=headers) as session:
                async with session.post(url, data=data) as resp:
                    if resp.status == 200:
                        print("✅ Лоты на FunPay успешно подняты!")
                    else:
                        print(f"⚠️ Ошибка поднятия лотов. Код: {resp.status}")
            # ===============================

# 2. Основной цикл проверки FunPay (Имитация логики)
async def funpay_worker():
    while True:
        try:
            # --- БЛОК 1: Проверка новых заказов и ссылок ---
            # (Тут должен быть парсер чатов FunPay)
            
            # --- БЛОК 2: Проверка завершения на VexBoost ---
            for fp_id, order_data in list(active_orders.items()):
                
                # Если заказ крутится на VexBoost
                if order_data["status"] == "in_progress":
                    status = await VexBoost.check_status(order_data["vex_id"])
                    
                    if status == "Completed":
                        active_orders[fp_id]["status"] = "waiting_confirm"
                        active_orders[fp_id]["time_completed"] = datetime.now()
                        
                        # Бот пишет на FunPay:
                        # "Всё готово! 🎉 Накрутка завершена. Пожалуйста, проверьте и подтвердите выполнение заказа."
                        print(f"Заказ {fp_id} выполнен. Ждем подтверждения.")

                # Если накрутка завершена, и мы ждем подтверждения от покупателя
                elif order_data["status"] == "waiting_confirm":
                    # (Тут проверяем статус заказа на самом FunPay. Если он Закрыт/Выполнен, 
                    # пишем: "Спасибо! Оставьте отзыв. Удаляем заказ из активных")
                    # active_orders.pop(fp_id)
                    
                    # Проверка на зависание (если прошел 1 час)
                    time_passed = datetime.now() - order_data["time_completed"]
                    if time_passed > timedelta(hours=1):
                        # Отправляем тебе в Telegram алерт
                        await bot.send_message(
                            ADMIN_ID, 
                            f"⚠️ **Ахтунг! Покупатель не подтверждает заказ.**\n"
                            f"Товар: {order_data['name']}\n"
                            f"Заказ FunPay: {fp_id}\nПрошел уже час!"
                        )
                        # Переводим статус, чтобы не спамить тебе каждый цикл
                        active_orders[fp_id]["status"] = "alerted"
                        
            await asyncio.sleep(10) # Проверяем каждые 10 секунд
            
        except Exception as e:
            print(f"Ошибка в цикле FunPay: {e}")
            await asyncio.sleep(10)


# ================= ЗАПУСК БОТА =================
async def main():
    print("🚀 Скрипт запущен! База товаров загружена.")
    print(f"Загружено товаров: {len(PRODUCTS_MAP)}")
    
    # Запускаем фоновые задачи
    asyncio.create_task(auto_bump_lots())
    asyncio.create_task(funpay_worker())
    
    # Запускаем телеграм-бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())