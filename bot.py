import requests, hashlib, json, os, asyncio, logging
from bs4 import BeautifulSoup
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from config import BOT_TOKEN
import nest_asyncio

nest_asyncio.apply()
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

CHECK_INTERVAL = 300  # 5 минут

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
            'seen_hashes': [],
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
                image_tag = ad.find('img')
                image_url = image_tag['src'] if image_tag and image_tag.has_attr('src') else None

                if title and link:
                    full_link = link['href']
                    if full_link.startswith('/'):
                        full_link = 'https://www.avito.ru' + full_link
                    hash_input = title.get_text(strip=True) + full_link + (price.get_text(strip=True) if price else '')
                    ad_hash = hashlib.md5(hash_input.encode()).hexdigest()
                    ads.append({
                        'title': title.get_text(strip=True),
                        'price': price.get_text(strip=True) if price else 'Цена не указана',
                        'link': full_link,
                        'image': image_url,
                        'hash': ad_hash
                    })
            logging.info(f"🔍 Найдено {len(ads)} объявлений на странице {url}")
            return ads
        except Exception as e:
            logging.error(f"Ошибка парсинга: {e}")
            return []

    def check_for_new_ads(self):
        new_ads_found = []
        for user_id, data in self.users_data.items():
            for url_hash, info in data['tracking_urls'].items():
                url = info['url']
                seen_hashes = set(info.get('seen_hashes', []))
                current_ads = self.get_ads_from_url(url)
                fresh_ads = [ad for ad in current_ads if ad['hash'] not in seen_hashes]
                logging.info(f"🆕 Найдено {len(fresh_ads)} новых объявлений для {user_id}")
                if fresh_ads:
                    info['seen_hashes'].extend(ad['hash'] for ad in fresh_ads)
                    new_ads_found.append({'user_id': user_id, 'new_ads': fresh_ads})
        if new_ads_found:
            self.save_data()
        return new_ads_found

monitor = AdvertisementMonitor()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Отправь ссылку на Avito, и я буду присылать новые объявления каждые 5 минут!")

async def add_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Укажите ссылку после команды:\n/add https://avito.ru/...")
        return
    url = ' '.join(context.args)
    user_id = update.effective_user.id
    url_hash = monitor.add_user_tracking(user_id, url)
    await update.message.reply_text(f"✅ Ссылка добавлена! Я начну отслеживать новые объявления.")

async def list_tracking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    trackings = monitor.users_data.get(user_id, {}).get('tracking_urls', {})
    if not trackings:
        await update.message.reply_text("📭 У вас нет активных отслеживаний.")
        return
    msg = "📋 Ваши отслеживания:\n\n"
    for i, (url_hash, data) in enumerate(trackings.items(), 1):
        msg += f"{i}. {data['url']}\n🆔 ID: {url_hash[:8]}...\n\n"
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
        await update.message.reply_text(f"✅ Ссылка добавлена! Я начну отслеживать новые объявления.")
    else:
        await update.message.reply_text("Отправьте ссылку на страницу с объявлениями Avito.")

async def send_notifications(app):
    while True:
        new_ads = monitor.check_for_new_ads()
        for item in new_ads:
            user_id = int(item['user_id'])
            for ad in item['new_ads']:
                msg = (
                    f"<b>{ad['title']}</b>\n"
                    f"💰 {ad['price']}\n"
                    f"<a href='{ad['link']}'>Открыть объявление</a>"
                )
                try:
                    if ad.get('image'):
                        await app.bot.send_photo(
                            chat_id=user_id,
                            photo=ad['image'],
                            caption=msg,
                            parse_mode="HTML"
                        )
                    else:
                        await app.bot.send_message(
                            chat_id=user_id,
                            text=msg,
                            parse_mode="HTML"
                        )
                    await asyncio.sleep(1.5)
                except Exception as e:
                    logging.error(f"Ошибка отправки: {e}")
        await asyncio.sleep(CHECK_INTERVAL)

async def main():
    if not BOT_TOKEN:
        logging.error("❌ BOT_TOKEN не задан. Проверь config.py или Railway Variables.")
        return

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add_url))
    app.add_handler(CommandHandler("list", list_tracking))
    app.add_handler(CommandHandler("remove", remove_tracking))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logging.info("🤖 Бот запущен и готов к работе.")
    asyncio.create_task(send_notifications(app))
    await app.run_polling()

if __name__ == "__main__":
    import sys
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            logging.warning("⚠️ Цикл уже запущен. Пропускаем повторный запуск.")
        else:
            loop.run_until_complete(main())
    except RuntimeError as e:
        logging.error(f"❌ Ошибка запуска бота: {e}")
    except Exception as e:
        logging.error(f"❌ Неожиданная ошибка: {e}")
