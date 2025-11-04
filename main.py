import asyncio
import os
import logging
import re
from datetime import datetime
from collections import deque

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.sessions import StringSession
from dotenv import load_dotenv
from openai import OpenAI
from fastapi import FastAPI
import uvicorn

# ---------------- تحميل الإعدادات ----------------
load_dotenv()
PORT = int(os.getenv("PORT", 8080))  # 🚀 ضروري لعمل التطبيق على Railway

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")
SOURCE = os.getenv("SOURCE_CHANNEL")
SOURCE_2 = os.getenv("SOURCE_CHANNEL_2")
TARGET = os.getenv("TARGET_CHANNEL")
CONTROL_CHANNEL = os.getenv("CONTROL_CHANNEL")
TARGET_LANG = os.getenv("TARGET_LANG", "ar")
FIRST_SIGNATURE = os.getenv("SIGNATURE", "")
PUBLISH_INTERVAL = 600
MIN_VIEWS_FOR_NEXT = int(os.getenv("MIN_VIEWS_FOR_NEXT", 800))

# مفاتيح OpenAI
API_KEYS = os.getenv("OPENAI_API_KEYS", "").split(",")
if not API_KEYS or API_KEYS == [""]:
    raise ValueError("❌ لم يتم العثور على مفاتيح OpenAI في ملف .env")

# ---------------- إعدادات عامة ----------------
KEYWORDS_LIST = ["JUST IN", "MACRO", "$MACRO", "marco","FEDERAL","POWELL","powell", "TRUMP", "FED'S", "FED", "🔴"]
EMOJI_IMMEDIATE = "🚨"
EMOJI_SCHEDULED = "📝"
EMOJI_ALERT = "⚠️🚨"
CHANNEL_WATERMARK = "https://t.me/ForexNews24hours "

client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
translation_queue = deque()
posted_texts = set()
MAX_POSTED_HISTORY = 100
bot_active = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("bot_activity.log", "a", encoding="utf-8")]
)

# ---------------- OpenAI Manager ----------------
class OpenAIManager:
    def __init__(self, keys):
        self.keys = [k.strip() for k in keys if k.strip()]
        self.index = 0
    def get_client(self):
        key = self.keys[self.index]
        self.index = (self.index + 1) % len(self.keys)
        return OpenAI(api_key=key)

openai_manager = OpenAIManager(API_KEYS)

# ---------------- أدوات ----------------
def log_activity(task: str, message_id: int):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logging.info(f"[{now}] ({task}) -> نشر رسالة ID={message_id}")

def clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"http\S+|www\.\S+", "", text)
    text = re.sub(r"\$", "", text)
    text = re.sub(r"(\.{3,}|…+)$", "", text)
    return text.strip()

def clear_queue():
    translation_queue.clear()
    posted_texts.clear()
    logging.info("🧹 تم مسح قائمة الانتظار والمنشورات السابقة.")

# ---------------- ذكاء موحّد: ترجمة + تقييم ----------------
async def analyze_and_translate(text: str, target_lang: str, max_retries: int = 6, retry_delay: int = 5) -> dict:
    if not text:
        return {"impact": "⚪ تأثير محايد", "translation": ""}
    attempt = 0
    while attempt < max_retries:
        client_ai = openai_manager.get_client()
        try:
            response = client_ai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": (
                        "أنت محلل اقتصادي ومترجم محترف. "
                        "حلّل الخبر التالي، ثم أعد صياغته بالعربية بأسلوب اقتصادي مختصر ومهني. "
                        "أولاً، قدم تقييمًا للتأثير الاقتصادي من كلمتين إلى أربع فقط بخط عريض. "
                        "ثم ضع فاصلًا واضحًا بين التقييم والنص المترجم باستخدام الرمز '###'. "
                        "بعد ذلك اكتب الترجمة الاقتصادية الموجزة بالعربية مع استخدام الرموز المناسبة."
                    )},
                    {"role": "user", "content": text},
                ],
                temperature=0.3,
            )
            content = response.choices[0].message.content.strip()
            parts = content.split("###", 1)
            impact = parts[0].strip() if parts else "⚪ تأثير محايد"
            translation = parts[1].strip() if len(parts) > 1 else text
            return {"impact": impact, "translation": translation}
        except Exception as e:
            attempt += 1
            logging.warning(f"❌ محاولة {attempt} فشلت في التحليل والترجمة: {e}")
            await asyncio.sleep(retry_delay)
    logging.error("⚠️ فشل الذكاء الموحّد بعد عدة محاولات.")
    return {"impact": "⚪ تأثير محايد", "translation": text}

# ---------------- تنسيق المنشور ----------------
async def format_final_text(text: str, emoji: str, attention=False) -> str:
    text_upper = text.upper()
    client_ai = openai_manager.get_client()

    # 🟥 الحالة 1: MACRO + ACTUAL
    if "MACRO" in text_upper and "ACTUAL" in text_upper:
        try:
            response = client_ai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": (
                        "أنت محرر أخبار اقتصادية محترف. "
                        "استخرج البيانات الخاصة بالإصدار الاقتصادي "
                        "مثل الدولة، المؤشر، درجة الأهمية، والقيم (السابق، التقدير، الحالي). "
                        "ثم اعرضها ضمن القالب التالي بالعربية:\n\n"
                        "🔴 صدر الآن :\n\n"
                        "💠 {اسم الدولة مع العلم}\n"
                        "🔵 {اسم الحدث أو المؤشر}\n\n"
                        "🔖 درجة الأهمية\n\n"
                        "🕒 السابق :\n"
                        "🕞 التقدير :\n"
                        "🕓 الحالي :\n\n"
                        "👈 النتيجة : تحليل احترافي يشرح دلالات الأرقام وتأثيرها المحتمل على الأسواق أو العملة."
                    )},
                    {"role": "user", "content": text},
                ],
                temperature=0.5,
            )
            translation = response.choices[0].message.content.strip()
        except Exception as e:
            logging.warning(f"⚠️ فشل في معالجة MACRO+ACTUAL: {e}")
            translation = text

        return f"{translation}\n\n{FIRST_SIGNATURE}\n\n{CHANNEL_WATERMARK}"[:4000]

    # 🟨 الحالة 2: MACRO فقط
    elif "MACRO" in text_upper:
        try:
            response = client_ai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": (
                        "أنت محلل اقتصادي محترف. "
                        "قم بتحليل الخبر أو البيانات التالية تحليلاً اقتصاديًا احترافيًا بالعربية."
                    )},
                    {"role": "user", "content": text},
                ],
                temperature=0.7,
            )
            translation = response.choices[0].message.content.strip()
        except Exception as e:
            logging.warning(f"⚠️ فشل في التحليل الحر (MACRO فقط): {e}")
            translation = text

        return f"{translation}\n\n{FIRST_SIGNATURE}\n\n{CHANNEL_WATERMARK}"[:4000]

    # 🟩 باقي الحالات
    else:
        result = await analyze_and_translate(text, TARGET_LANG)
        header_attention = f"{EMOJI_ALERT} **إنتباه:**\n\n" if attention else ""
        return (
            f"{header_attention}{result['impact']}\n\n"
            f"{emoji} {result['translation']}\n\n"
            f"{FIRST_SIGNATURE}\n\n{CHANNEL_WATERMARK}"
        )[:4000]

# ---------------- إرسال الرسائل ----------------
async def forward_or_send(message, caption: str, task_name=""):
    text_signature = caption.strip()
    if text_signature in posted_texts:
        logging.info(f"❌ تم تجاهل الرسالة ID={message.id} لأنها مكررة")
        return
    posted_texts.add(text_signature)
    if len(posted_texts) > MAX_POSTED_HISTORY:
        posted_texts.pop()
    try:
        sent = await client.send_message(TARGET, caption, link_preview=False)
        log_activity(task_name, message.id)
        return sent
    except FloodWaitError as fe:
        logging.warning(f"⏳ Flood wait: الانتظار {fe.seconds} ثانية...")
        await asyncio.sleep(fe.seconds + 1)
        return await client.send_message(TARGET, caption, link_preview=False)
    except Exception:
        logging.exception("Error while sending message")

# ---------------- قناة التحكم ----------------
@client.on(events.NewMessage(chats=CONTROL_CHANNEL))
async def control_handler(event):
    global bot_active
    text = event.raw_text.strip().lower()
    if "تفعيل" in text:
        bot_active = True
        await event.reply("✅ تم تفعيل البوت بنجاح.")
    elif "ايقاف" in text:
        bot_active = False
        clear_queue()
        await event.reply("⛔ تم إيقاف البوت ومسح المنشورات.")
    else:
        await event.reply("⚙️ استخدم الأوامر:\n- تفعيل البوت\n- ايقاف البوت")

# ---------------- القناة الأولى ----------------
@client.on(events.NewMessage(chats=SOURCE))
async def handler_source1(event):
    global bot_active
    if not bot_active:
        return
    message = event.message
    if message.action:
        return
    text = message.message or ""
    text_lower = text.lower()
    if any(keyword.lower() in text_lower for keyword in KEYWORDS_LIST):
        cleaned = clean_text(text)
        final_text = await format_final_text(cleaned, EMOJI_IMMEDIATE)
        await forward_or_send(message, final_text, "نشر فوري")
    else:
        translation_queue.append(message)

# ---------------- القناة الثانية ----------------
@client.on(events.NewMessage(chats=SOURCE_2))
async def handler_source2(event):
    global bot_active
    if not bot_active:
        return
    message = event.message
    if message.action:
        return
    text = clean_text(message.message or "")
    client_ai = openai_manager.get_client()
    try:
        response = client_ai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": (
                    "أنت مترجم اقتصادي محترف. ترجم النص التالي ترجمة دقيقة "
                    "ثم أضف فقرة تحليلية قصيرة تشرح بإيجاز تأثيره على السوق."
                )},
                {"role": "user", "content": text},
            ],
            temperature=0.4,
        )
        translated = response.choices[0].message.content.strip()
    except Exception as e:
        logging.warning(f"⚠️ فشل في الترجمة الحرفية: {e}")
        translated = text
    final_text = f"{EMOJI_ALERT} **بيانات هامة:**\n\n{translated}\n\n{FIRST_SIGNATURE}\n\n{CHANNEL_WATERMARK}"
    await forward_or_send(message, final_text[:4000], "نشر فوري — القناة الثانية")

# ---------------- النشر المجدول ----------------
async def publisher():
    global bot_active
    last_post_id = None
    while True:
        if not bot_active:
            await asyncio.sleep(5)
            continue
        if translation_queue:
            if last_post_id:
                try:
                    last_post = await client.get_messages(TARGET, ids=last_post_id)
                    views = last_post.views or 0
                    while views < MIN_VIEWS_FOR_NEXT:
                        await asyncio.sleep(60)
                        last_post = await client.get_messages(TARGET, ids=last_post_id)
                        views = last_post.views or 0
                except Exception:
                    pass
            message = translation_queue.popleft()
            cleaned = clean_text(message.message or "")
            final_text = await format_final_text(cleaned, EMOJI_SCHEDULED)
            sent = await forward_or_send(message, final_text, "نشر مجدول")
            if sent:
                last_post_id = sent.id
        await asyncio.sleep(PUBLISH_INTERVAL)

# ---------------- خادم FASTAPI (لمنع السكون) ----------------
app = FastAPI()

@app.get("/")
async def root():
    return {"status": "✅ Bot is running on Railway", "active": bot_active}

# ---------------- التشغيل ----------------
async def main():
    await client.start()
    me = await client.get_me()
    logging.info(f"✅ تسجيل الدخول باسم: {me.username or me.first_name}")
    logging.info("📡 البوت جاهز — في انتظار أمر التفعيل.")
    asyncio.create_task(publisher())
    config = uvicorn.Config(app, host="0.0.0.0", port=PORT, log_level="warning")
    server = uvicorn.Server(config)
    await asyncio.gather(server.serve(), client.run_until_disconnected())

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("🛑 تم إيقاف البوت يدوياً.")

