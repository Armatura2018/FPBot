import asyncio
import aiohttp
import os
import json
import re
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

# ================= НАСТРОЙКИ (С хостинга) =================
TG_TOKEN = os.getenv("TG_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID")) if os.getenv("ADMIN_ID") else 0
VEXBOOST_KEY = os.getenv("VEXBOOST_KEY")
FUNPAY_PHPSESSID = os.getenv("FUNPAY_PHPSESSID")

bot = Bot(token=TG_TOKEN)
dp = Dispatcher()

# Базовый User-Agent для маскировки под браузер
BASE_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# ИСПРАВЛЕНО: Теперь кука строго КАПСОМ, как на твоем скриншоте!
FUNPAY_COOKIES = {"PHPSESSID": FUNPAY_PHPSESSID}

# ================= БАЗЫ ДАННЫХ =================
def load_products():
    try:
        with open("products.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        print("❌ КРИТИЧЕСКАЯ ОШИБКА: Не удалось прочитать products.json!", flush=True)
        return {}

PRODUCTS_MAP = load_products()
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

# ================= ФОНОВЫЕ ЗАДАЧИ FUNPAY =================

# 1. Функция автоподнятия лотов
async def auto_bump_lots():
    while True:
        try:
            url = "https://funpay.com/lots/raise"
            # ПОДСТАВЬ СВОЙ ID КАТЕГОРИИ (Например, 123 для Telegram услуг)
            data = {"game_id": 703} 
            
            # Для POST-запросов имитации клика заголовок XMLHttpRequest обязателен
            headers = {
                "User-Agent": BASE_USER_AGENT,
                "X-Requested-With": "XMLHttpRequest"
            }
            
            async with aiohttp.ClientSession(cookies=FUNPAY_COOKIES, headers=headers) as session:
                async with session.post(url, data=data) as resp:
                    if resp.status == 200:
                        print("✅ Кнопка 'Поднять лоты' успешно нажита автоматикой!", flush=True)
                    else:
                        print(f"⚠️ Предупреждение: Не удалось поднять лоты. Статус: {resp.status}", flush=True)
            
            await asyncio.sleep(3600) # Проверяем раз в час
        except Exception as e:
            print(f"❌ Ошибка в модуле автоподнятия лотов: {e}", flush=True)
            await asyncio.sleep(60)


# 2. Функция отправки сообщения в чат FunPay
async def funpay_send_message(node_id, text):
    url = "https://funpay.com/chat/message"
    data = {"node": node_id, "last_id": 0, "content": text}
    headers = {
        "User-Agent": BASE_USER_AGENT,
        "X-Requested-With": "XMLHttpRequest"
    }
    async with aiohttp.ClientSession(cookies=FUNPAY_COOKIES, headers=headers) as session:
        async with session.post(url, data=data) as resp:
            return resp.status == 200


# 3. Основной движок автоматизации
async def funpay_worker():
    print("🤖 Поток мониторинга FunPay запущен и слушает сайт...", flush=True)
    
    while True:
        try:
            # Для обычного просмотра страниц заголовки должны быть чистыми, как у обычного человека
            headers = {"User-Agent": BASE_USER_AGENT}
            
            async with aiohttp.ClientSession(cookies=FUNPAY_COOKIES, headers=headers) as session:
                # Шаг 1: Заходим на страницу продаж
                async with session.get("https://funpay.com/orders/trade") as resp:
                    if resp.status != 200:
                        print(f"⚠️ Ошибка доступа к FunPay (Продажи): {resp.status}. Проверь FUNPAY_PHPSESSID!", flush=True)
                        await asyncio.sleep(15)
                        continue
                    
                    html = await resp.text()
                    
                    # Ищем все ссылки на оплаченные заказы
                    orders = re.findall(r'href="https://funpay\.com/orders/([A-Z0-9]+)/".*?tc-status-paid', html, re.DOTALL)
                    
                    for fp_id in orders:
                        if fp_id not in active_orders:
                            # Мы нашли новый оплаченный заказ! Заходим внутрь заказа за деталями
                            async with session.get(f"https://funpay.com/orders/{fp_id}/") as order_resp:
                                order_html = await order_resp.text()
                                
                                node_match = re.search(r'data-node="(\d+)"', order_html)
                                title_match = re.search(r'<div class="text-bold">.*?</div>.*?<div>(.*?)</div>', order_html, re.DOTALL)
                                
                                if node_match:
                                    node_id = node_match.group(1)
                                    product_name = title_match.group(1).strip() if title_match else "Неизвестный товар"
                                    
                                    active_orders[fp_id] = {
                                        "status": "waiting_link",
                                        "node_id": node_id,
                                        "name": product_name,
                                        "vex_id": None,
                                        "time_completed": None
                                    }
                                    
                                    print(f"📦 Новый заказ {fp_id}: {product_name}. Запрашиваю ссылку...", flush=True)
                                    await funpay_send_message(node_id, "Здравствуйте! Вашу оплату вижу. Пожалуйста, пришлите ссылку на ваш канал/пост/профиль для выполнения накрутки.")

            # Шаг 2: Обработка чатов для активных заказов
            for fp_id, order_data in list(active_orders.items()):
                node_id = order_data["node_id"]
                
                url = f"https://funpay.com/chat/?node={node_id}"
                async with aiohttp.ClientSession(cookies=FUNPAY_COOKIES, headers=headers) as session:
                    async with session.get(url) as chat_resp:
                        chat_html = await chat_resp.text()
                        
                        messages = re.findall(r'<div class="msg-text">(.*?)</div>', chat_html)
                        if not messages:
                            continue
                        
                        last_msg = messages[-1].strip().lower()
                        
                        if order_data["status"] == "waiting_link" and ("http" in last_msg or "t.me" in last_msg):
                            link = messages[-1].strip()
                            service_id = PRODUCTS_MAP.get(order_data["name"])
                            
                            if service_id:
                                print(f"🔗 Ссылка получена. Запускаю накрутку на VexBoost (ID услуги: {service_id})...", flush=True)
                                vex_id = await VexBoost.create_order(service_id, link, 1000)
                                
                                if vex_id:
                                    active_orders[fp_id]["status"] = "in_progress"
                                    active_orders[fp_id]["vex_id"] = vex_id
                                    await funpay_send_message(node_id, "Отлично, ссылка принята! Заказ автоматически передан в работу на сервер. Ожидайте выполнения.")
                                else:
                                    print(f"❌ Ошибка баланса или API на VexBoost для заказа {fp_id}", flush=True)
                            else:
                                print(f"⚠️ Товар '{order_data['name']}' не найден в products.json! Проверь названия.", flush=True)

                        elif "отмен" in last_msg and order_data["status"] == "in_progress":
                            active_orders[fp_id]["status"] = "canceling_confirm"
                            await funpay_send_message(node_id, "Вы уверены, что хотите отменить заказ? Если да, напишите слово 'ДА' в ответном сообщении.")

                        elif last_msg == "да" and order_data["status"] == "canceling_confirm":
                            active_orders[fp_id]["status"] = "waiting_admin_decision"
                            await funpay_send_message(node_id, "Запрос на отмену отправлен администратору. Ожидайте решения.")
                            
                            kb = InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="✅ Разрешить отмену", callback_data=f"can_y_{fp_id}_{order_data['vex_id']}")],
                                [InlineKeyboardButton(text="❌ Отказать", callback_data=f"can_n_{fp_id}_{order_data['vex_id']}")]
                            ])
                            await bot.send_message(
                                ADMIN_ID,
                                f"⚠️ **Запрос отмены!**\nЗаказ: {order_data['name']}\nFunPay ID: {fp_id}\nVexBoost ID: {order_data['vex_id']}",
                                reply_markup=kb
                            )

            # Шаг 3: Мониторинг выполнения накрутки на VexBoost
            for fp_id, order_data in list(active_orders.items()):
                if order_data["status"] == "in_progress" and order_data["vex_id"]:
                    status = await VexBoost.check_status(order_data["vex_id"])
                    
                    if status == "Completed":
                        active_orders[fp_id]["status"] = "waiting_confirm"
                        active_orders[fp_id]["time_completed"] = datetime.now()
                        await funpay_send_message(order_data["node_id"], "✅ Накрутка успешно завершена! Пожалуйста, проверьте результат, подтвердите выполнение заказа на сайте и оставьте отзыв.")
                        print(f"🎉 Заказ {fp_id} выполнен на VexBoost. Ждем подтверждения от покупателя.", flush=True)

                elif order_data["status"] == "waiting_confirm":
                    time_passed = datetime.now() - order_data["time_completed"]
                    if time_passed > timedelta(hours=1):
                        await bot.send_message(ADMIN_ID, f"⏰ Покупатель заказа {fp_id} (`{order_data['name']}`) не подтверждает выполнение уже больше часа!")
                        active_orders[fp_id]["status"] = "alerted"

            await asyncio.sleep(15)

        except Exception as e:
            print(f"💥 Ошибка в главном цикле воркера: {e}", flush=True)
            await asyncio.sleep(15)

# ================= ОБРАБОТКА КНОПОК В ТЕЛЕГРАМЕ =================
@dp.callback_query(F.data.startswith("can_"))
async def tg_callback(callback: CallbackQuery):
    _, decision, fp_id, vex_id = callback.data.split("_")
    order_data = active_orders.get(fp_id)
    
    if not order_data:
        await callback.answer("Заказ не найден в текущей памяти бота.")
        return

    if decision == "y":
        await funpay_send_message(order_data["node_id"], "Администратор одобрил отмену. Деньги возвращены на ваш баланс FunPay.")
        active_orders.pop(fp_id, None)
        await callback.message.edit_text(f"✅ Ты одобрил отмену заказа {fp_id}. Верни деньги на сайте вручную.")
    else:
        active_orders[fp_id]["status"] = "in_progress"
        await funpay_send_message(order_data["node_id"], "В отмене заказа отказано, так как услуга уже запущена на серверах и не может быть остановлена.")
        await callback.message.edit_text(f"❌ Ты отказал в отмене заказа {fp_id}.")

# ================= ЗАПУСК СКРИПТА =================
async def main():
    print("🚀 Скрипт запущен! Логирование активировано.", flush=True)
    asyncio.create_task(auto_bump_lots())
    asyncio.create_task(funpay_worker())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
