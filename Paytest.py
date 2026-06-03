import os
import re
import logging
from typing import Optional, Tuple

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    KeyboardButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# =========================
# تنظیمات
# =========================
SECRETARY_CHAT_ID = 6012643756  # chat_id منشی (همون که دادی)

# شماره کارت (اینجا تغییرش بده)
CARD_NUMBER = "6037-7015-9823-2992"

# متن‌ها (دلخواه خودت تغییر بده)
WELCOME_TEXT = "سلام 👋\nبه ربات ثبت‌نام برنامه روزانه خوش اومدی."
REGISTER_INFO_TEXT = (
    "برای ثبت‌نام برنامه روزانه، چند تا سوال کوتاه ازت می‌پرسم.\n"
    "بعد از تکمیل اطلاعات، می‌تونی طرح عادی یا ویژه رو انتخاب کنی."
)

PLAN_NORMAL_TEXT = (
    "📌 *طرح عادی*\n"
    "توضیحات طرح عادی اینجا نوشته می‌شود.\n"
    "مثلاً: برنامه روزانه استاندارد + پشتیبانی محدود."
)

PLAN_SPECIAL_TEXT = (
    "⭐️ *طرح ویژه*\n"
    "توضیحات طرح ویژه اینجا نوشته می‌شود.\n"
    "مثلاً: برنامه اختصاصی + پشتیبانی کامل + بررسی روزانه."
)

PAYMENT_TEXT = (
    "💳 *پرداخت ریالی*\n"
    "لطفاً مبلغ موردنظر را به شماره کارت زیر واریز کنید:\n\n"
    f"`{CARD_NUMBER}`\n\n"
    "سپس روی دکمه «ارسال رسید پرداخت» بزن و عکس/فایل رسید را ارسال کن."
)

# =========================
# مراحل Conversation
# =========================
MENU, NAME, MAJOR, GRADE, PHONE, PLAN_SELECT, PLAN_FLOW, WAIT_RECEIPT = range(8)


def normalize_iran_phone(raw: str) -> Optional[str]:
    """
    قوانین:
    - قبول: 09xxxxxxxxx (11 رقم)
    - قبول: +98xxxxxxxxxx (کشور + 10 رقم)
    - (اختیاری) قبول: 989xxxxxxxxx (بدون +) -> تبدیل به +98...
    """
    if not raw:
        return None

    s = raw.strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")

    # اگر با 00 شروع شود، به + تبدیل کنیم
    if s.startswith("00"):
        s = "+" + s[2:]

    # 09xxxxxxxxx
    if re.fullmatch(r"09\d{9}", s):
        # نرمال‌سازی به فرمت بین‌المللی
        return "+98" + s[1:]  # 09... -> +989...

    # +98xxxxxxxxxx
    if re.fullmatch(r"\+98\d{10}", s):
        return s

    # 989xxxxxxxxx
    if re.fullmatch(r"98\d{10}", s):
        return "+" + s

    # 9xxxxxxxxx (گاهی از contact اینطور میاد) -> فرض موبایل ایران
    if re.fullmatch(r"9\d{9}", s):
        return "+98" + s

    return None


def main_menu_keyboard():
    return ReplyKeyboardMarkup(
        [["ثبت نام برنامه روزانه"]],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def majors_keyboard():
    return ReplyKeyboardMarkup(
        [["ریاضی", "تجربی", "انسانی"]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def grades_keyboard():
    return ReplyKeyboardMarkup(
        [["دهم", "یازدهم", "دوازدهم"]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def plans_keyboard():
    return ReplyKeyboardMarkup(
        [["طرح عادی", "طرح ویژه"]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def phone_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("ارسال شماره تماس 📱", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def pay_inline_keyboard():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("پرداخت ریالی", callback_data="pay")]]
    )


def send_receipt_inline_keyboard():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("ارسال رسید پرداخت", callback_data="receipt")]]
    )


def approve_inline_keyboard(user_chat_id: int):
    # callback_data محدودیت طول دارد، پس کوتاه نگه می‌داریم
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ تایید", callback_data=f"approve|{user_chat_id}")]]
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(WELCOME_TEXT, reply_markup=main_menu_keyboard())
    return MENU


async def start_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(REGISTER_INFO_TEXT)
    await update.message.reply_text(
        "✅ لطفاً *نام و نام خانوادگی* خود را وارد کنید:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return NAME


async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = (update.message.text or "").strip()
    if len(name) < 3:
        await update.message.reply_text("لطفاً نام و نام خانوادگی را درست وارد کنید.")
        return NAME

    context.user_data["full_name"] = name
    await update.message.reply_text(
        "📚 رشته خود را انتخاب کنید:", reply_markup=majors_keyboard()
    )
    return MAJOR


async def get_major(update: Update, context: ContextTypes.DEFAULT_TYPE):
    major = (update.message.text or "").strip()
    if major not in ["ریاضی", "تجربی", "انسانی"]:
        await update.message.reply_text("لطفاً یکی از گزینه‌ها را انتخاب کنید.")
        return MAJOR

    context.user_data["major"] = major
    await update.message.reply_text("🏫 لطفاً پایه خود را انتخاب کنید:", reply_markup=grades_keyboard())
    return GRADE


async def get_grade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    grade = (update.message.text or "").strip()
    if grade not in ["دهم", "یازدهم", "دوازدهم"]:
        await update.message.reply_text("لطفاً یکی از گزینه‌ها را انتخاب کنید.")
        return GRADE

    context.user_data["grade"] = grade
    await update.message.reply_text(
        "📞 لطفاً شماره تماس خود را ارسال کنید.\n"
        "می‌توانید از دکمه زیر استفاده کنید یا دستی وارد کنید (09... یا +98...):",
        reply_markup=phone_keyboard(),
    )
    return PHONE


async def get_phone_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    phone_raw = contact.phone_number if contact else ""
    normalized = normalize_iran_phone(phone_raw)

    if not normalized:
        await update.message.reply_text(
            "شماره تماس معتبر نیست. لطفاً دوباره ارسال کنید (09... یا +98...).",
            reply_markup=phone_keyboard(),
        )
        return PHONE

    context.user_data["phone"] = normalized
    await update.message.reply_text(
        "✅ اطلاعات اولیه ثبت شد.\n\nحالا یکی از طرح‌ها را انتخاب کنید:",
        reply_markup=plans_keyboard(),
    )
    return PLAN_SELECT


async def get_phone_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone_raw = (update.message.text or "").strip()
    normalized = normalize_iran_phone(phone_raw)

    if not normalized:
        await update.message.reply_text(
            "شماره تماس معتبر نیست.\n"
            "فرمت‌های درست: 09xxxxxxxxx یا +98xxxxxxxxxx\n"
            "لطفاً دوباره وارد کنید:",
            reply_markup=phone_keyboard(),
        )
        return PHONE

    context.user_data["phone"] = normalized
    await update.message.reply_text(
        "✅ اطلاعات اولیه ثبت شد.\n\nحالا یکی از طرح‌ها را انتخاب کنید:",
        reply_markup=plans_keyboard(),
    )
    return PLAN_SELECT


async def choose_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    plan = (update.message.text or "").strip()
    if plan not in ["طرح عادی", "طرح ویژه"]:
        await update.message.reply_text("لطفاً یکی از طرح‌ها را انتخاب کنید.")
        return PLAN_SELECT

    context.user_data["plan"] = plan

    if plan == "طرح عادی":
        text = PLAN_NORMAL_TEXT
    else:
        text = PLAN_SPECIAL_TEXT

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    await update.message.reply_text(
        "برای ادامه روی دکمه زیر بزنید:",
        reply_markup=pay_inline_keyboard(),
    )
    return PLAN_FLOW


async def pay_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    await query.message.reply_text(
        PAYMENT_TEXT,
        parse_mode="Markdown",
        reply_markup=send_receipt_inline_keyboard(),
    )
    return PLAN_FLOW


async def receipt_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    await query.message.reply_text(
        "📎 لطفاً *عکس یا فایل رسید پرداخت* را همینجا ارسال کنید.",
        parse_mode="Markdown",
    )
    return WAIT_RECEIPT


async def receive_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # رسید می‌تواند photo یا document باشد
    user_chat_id = update.effective_chat.id

    full_name = context.user_data.get("full_name", "نامشخص")
    major = context.user_data.get("major", "نامشخص")
    grade = context.user_data.get("grade", "نامشخص")
    phone = context.user_data.get("phone", "نامشخص")
    plan = context.user_data.get("plan", "نامشخص")

    caption = (
        "🧾 *رسید پرداخت جدید*\n\n"
        f"👤 نام و نام خانوادگی: {full_name}\n"
        f"📚 رشته: {major}\n"
        f"🏫 پایه: {grade}\n"
        f"📞 شماره تماس: `{phone}`\n"
        f"🧩 طرح انتخابی: {plan}\n\n"
        f"User Chat ID: `{user_chat_id}`"
    )

    # ارسال به منشی + دکمه تایید
    markup = approve_inline_keyboard(user_chat_id)

    try:
        if update.message.photo:
            file_id = update.message.photo[-1].file_id
            await context.bot.send_photo(
                chat_id=SECRETARY_CHAT_ID,
                photo=file_id,
                caption=caption,
                parse_mode="Markdown",
                reply_markup=markup,
            )
        elif update.message.document:
            file_id = update.message.document.file_id
            await context.bot.send_document(
                chat_id=SECRETARY_CHAT_ID,
                document=file_id,
                caption=caption,
                parse_mode="Markdown",
                reply_markup=markup,
            )
        else:
            await update.message.reply_text("لطفاً فقط عکس یا فایل رسید ارسال کنید.")
            return WAIT_RECEIPT
    except Exception as e:
        logger.exception("Failed sending receipt to secretary: %s", e)
        await update.message.reply_text("خطا در ارسال رسید. لطفاً دوباره تلاش کنید.")
        return WAIT_RECEIPT

    await update.message.reply_text(
        "✅ رسید شما برای بررسی ارسال شد.\n"
        "بعد از تایید منشی، پیام موفقیت برای شما ارسال می‌شود.",
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END


async def approve_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # فقط منشی اجازه تایید دارد
    if update.effective_user.id != SECRETARY_CHAT_ID:
        await query.answer("شما اجازه تایید ندارید.", show_alert=True)
        return

    data = query.data  # approve|<user_chat_id>
    try:
        _, user_chat_id_str = data.split("|", 1)
        user_chat_id = int(user_chat_id_str)
    except Exception:
        await query.answer("خطا در داده تایید.", show_alert=True)
        return

    # پیام به کاربر
    await context.bot.send_message(
        chat_id=user_chat_id,
        text="🎉 ثبت‌نام شما با موفقیت انجام شد.\nاگر سوالی دارید پیام بدید.",
        reply_markup=main_menu_keyboard(),
    )

    # بروزرسانی پیام منشی (اختیاری)
    try:
        if query.message.caption:
            new_caption = query.message.caption + "\n\n✅ *وضعیت:* تایید شد"
            await query.edit_message_caption(
                caption=new_caption,
                parse_mode="Markdown",
                reply_markup=None,
            )
        else:
            await query.edit_message_text("✅ تایید شد", reply_markup=None)
    except Exception:
        # اگر ادیت نشد مشکلی نیست
        pass


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("فرآیند لغو شد.", reply_markup=main_menu_keyboard())
    return ConversationHandler.END


def build_app(token: str):
    app = ApplicationBuilder().token(token).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MENU: [
                MessageHandler(filters.Regex(r"^ثبت نام برنامه روزانه$"), start_registration),
            ],
            NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_name),
            ],
            MAJOR: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_major),
            ],
            GRADE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_grade),
            ],
            PHONE: [
                MessageHandler(filters.CONTACT, get_phone_contact),
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone_text),
            ],
            PLAN_SELECT: [
                MessageHandler(filters.Regex(r"^(طرح عادی|طرح ویژه)$"), choose_plan),
            ],
            PLAN_FLOW: [
                CallbackQueryHandler(pay_handler, pattern=r"^pay$"),
                CallbackQueryHandler(receipt_button_handler, pattern=r"^receipt$"),
            ],
            WAIT_RECEIPT: [
                MessageHandler(filters.PHOTO | filters.Document.ALL, receive_receipt),
                MessageHandler(filters.ALL & ~filters.COMMAND, receive_receipt),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
        allow_reentry=True,
    )

    # Conversation + تایید منشی
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(approve_handler, pattern=r"^approve\|"))

    return app


def main():
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN را در Environment Variable تنظیم کنید.")

    app = build_app(token)
    # Polling (طبق خواسته شما)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()