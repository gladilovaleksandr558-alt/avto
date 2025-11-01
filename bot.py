import requests, hashlib, json, os, asyncio, logging
from bs4 import BeautifulSoup
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from config import BOT_TOKEN, CHECK_INTERVAL

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

class AdvertisementMonitor:
    def __init__(self):
        self.users_data = {}
        self.load_data()

    def load_data(self):
        if os.path.exists('users_data.json'):
            with open('users_data.json', 'r', encoding='utf-8') as f:
                self.users_data = json.load(f)

    def save_data(self):
        with open('users_data.json', 'w', encoding='utf-8') as f:
            json.dump(self.users_data, f, ensure_ascii=False, indent=2)

    def add_user_tracking(self, user_id, url):
        user_id = str(user_id)
        if user_id not in self.users_data:
            self.users_data[user_id] = {'tracking_urls': {}}
        url_hash = hashlib.md5(url.encode()).hexdigest()
        self.users_data[user_id]['tracking_urls'][url_hash] = {
            'url': url,
            'last_ads': [],
            'added_date': datetime.now().isoformat()
        }
        self.save_data()
        return url_hash

    def remove_user_tracking(self, user_id, url_hash):
        user_id = str(user_id)
        if user_id in self.users_data and url_hash in self.users_data[user_id]['tracking_urls']:
            del self.users_data[user_id]['tracking_urls'][url_hash]
            self.save_data()
            return True
        return False

    def get_ads_from_url(self, url):
        try:
            headers = {'User-Agent': 'Mozilla/5.0'}
            response = requests.get(url, headers=headers, timeout=30)
            soup = BeautifulSoup(response.content, 'html.parser')
            ads = []
            for ad in soup.select('[data-marker="item"]')[:10]:
                title = ad.find('h3') or ad.find('a', {'data-marker': 'item-title'})
                price = ad.find('span', {'data-marker': 'item-price'})
                link = ad.find('a', href=True)
                date_tag = ad.find('div', {'data-marker': 'item-date'})
                if date_tag:
                    date_text = date_tag.get_text(strip=True).lower()
                    if not any(x in date_text for x in ["сегодня", "только что"]):
                        continue
                if title and link:
                    full_link = link['href']
                    if full_link.startswith('/'):
                        full_link = 'https://www.avito.ru' + full_link
                    ads.append({
                        'title': title.get_text(strip=True),
                        'price': price.get_text(strip=True) if price else 'Цена не указана',
                        'link': full_link,
                        'hash': hashlib.md5((title.get_text(strip=True) + full_link).encode()).hexdigest()
                    })
            return ads
        except Exception as e:
            logging.error(f"Ошибка парсинга: {e}")
            return []

    def check_for_new_ads(self):
        new_ads_found = []
        for user_id, data in self.users_data.items():
            for url_hash, info in data['tracking_urls'].items():
                url = info['url']
                last_ads = info.get('last_ads', [])
                current_ads = self.get_ads_from_url(url)
                if not current_ads:
                    continue
                current_hashes = {ad['hash'] for ad in current_ads}
                last_hashes = {ad['hash'] for ad in last_ads}
                new_ads = [ad for ad in current_ads if ad['hash'] not in last_hashes]
                if new_ads:
                    info['last_ads'] = current_ads
                    new_ads_found.append({'user_id': user_id, 'url': url, 'new_ads': new_ads})
        if new_ads_found:
            self.save_data()
        return new_ads_found

monitor = AdvertisementMonitor()

# Telegram команды
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Отправь ссылку на страницу с объявлениями, и я буду присылать уведомления о новых!")

async def add_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Укажите ссылку после команды:\n/add https://example.com")
        return
    url = ' '.join(context.args)
    user_id = update.effective_user.id
    url_hash = monitor.add_user_tracking(user_id, url)
    ads = monitor.get_ads_from_url(url)
    monitor.users_data[str(user_id)]['tracking_urls'][url_hash]['last_ads'] = ads
    monitor.save_data()
    await update.message.reply_text(f"✅ Ссылка добавлена! Найдено новых объявлений: {len(ads)}")

async def list_tracking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    trackings = monitor.users_data.get(user_id, {}).get('tracking_urls', {})
    if not trackings:
        await update.message.reply_text("📭 У вас нет активных отслеживаний.")
        return
    msg = "📋 Ваши отслеживания:\n\n"
    for i, (url_hash, data) in enumerate(trackings.items(), 1):
        msg += f"{i}. {data['url']}\n📌 Объявлений: {len(data.get('last_ads', []))}\n🆔 ID: {url_hash[:8]}...\n\n"
    await update.message.reply_text(msg)

async def remove_tracking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Укажите ID отслеживания:\n/remove <id>")
        return
    user_id = str(update.effective_user.id)
    short_hash = context.args[0]
    full_hash = next((h for h in monitor.users_data.get(user_id, {}).get('tracking_urls', {}) if h.startswith(short_hash)), None)
    if full_hash and monitor.remove_user_tracking(user_id, full_hash):
        await update.message.reply_text("✅ Отслеживание удалено.")
    else:
        await update.message.reply_text("❌ Отслеживание не найдено.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text.startswith(('http://', 'https://')):
        user_id = update.effective_user.id
        url_hash = monitor.add_user_tracking(user_id, text)
        ads = monitor.get_ads_from_url(text)
        monitor.users_data[str(user_id)]['tracking_urls'][url_hash]['last_ads'] = ads
        monitor.save_data()
        await update.message.reply_text(f"✅ Ссылка добавлена! Найдено новых объявлений: {len(ads)}")
    else:
        await update.message.reply_text("Отправьте ссылку на страницу с объявлениями.")

# Цикл уведомлений
async def send_notifications(app):
    while True:
        new_ads = monitor.check_for_new_ads()
        for item in new_ads:
            user_id = int(item['user_id'])
            for ad in item['new_ads']:
                msg = f"📌 {ad['title']}\n💰 {ad['price']}\n🔗 {ad['link']}"
                try:
                    await app.bot.send_message(chat_id=user_id, text=msg)
                    await asyncio.sleep(1.5)
                except Exception as e:
                    logging.error(f"Ошибка отправки: {e}")
        await asyncio.sleep(CHECK_INTERVAL)

# Запуск бота
async def main():
    if not BOT_TOKEN:
        logging.error("❌ Переменная BOT_TOKEN не задана. Проверь config.py и Railway Variables.")
        return

    try:
        app = Application.builder().token(BOT_TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("add", add_url))
        app.add_handler(CommandHandler("list", list_tracking))
        app.add_handler(CommandHandler("remove", remove_tracking))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

        logging.info("🤖 Бот запущен и готов к работе.")
        await asyncio.gather(
            send_notifications(app),  # ✅ правильное имя функции
            app.run_polling()
        )
    except Exception as e:
        logging.error(f"❌ Ошибка запуска бота: {e}")

if __name__ == "__main__":
    asyncio.run(main())
