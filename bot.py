import os
import re
import logging
import secrets
import string
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import os
from pathlib import Path

from dotenv import load_dotenv

import psycopg
from psycopg.rows import dict_row

from telegram import (
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    KeyboardButton,
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BotCommand,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    CallbackQueryHandler,
    filters,
)
from telegram.request import HTTPXRequest

load_dotenv(dotenv_path=Path(__file__).with_name(".env"), override=True)

# ----------------- LOGGING -----------------
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("ُTahsilatikBot")

# ----------------- CONFIG -----------------
TZ_NAME = "Asia/Tehran"

# محدودیتها:
TOTAL_QUESTION_LIMIT_NON_ACTIVE = 2  # کاربرِ فعالنشده: کلاً فقط ۱ سوال در کل عمر
DAILY_LIMIT_ACTIVE = 4  # کاربر فعال: روزی ۴ سوال

# محدودیت بانک سوال (برای کاربرانی که در طرح روزانه فعال نیستند)
QBANK_MAX_FILES_NON_ACTIVE = 3  # بعد از ۳ فایل بانک سوال، دسترسی قفل میشود

CHANNEL_CHAT_ID = "@TAHSILATIK_STUDY"
CHANNEL_LINK = "https://t.me/TAHSILATIK_STUDY"

# کانال عضویت اجباری (برای استفاده از ربات)
REQUIRED_CHANNEL_CHAT_ID = "@Tahsilatik"
REQUIRED_CHANNEL_LINK = "https://t.me/Tahsilatik"

# کانال محتوای رایگان (Private channel) -> عدد -100...
FREE_CONTENT_CHANNEL_ID = int(os.getenv("FREE_CONTENT_CHANNEL_ID", "-1003820381180"))
FREE_CONTENT_CHANNEL_ID = -1003820381180  # همونی که تو لاگ دیدی
# کانال بانک سوال (Private channel) -> عدد -100...
QBANK_CHANNEL_ID = int(os.getenv("QBANK_CHANNEL_ID", "-1003887252594"))
QBANK_CHANNEL_ID = -1003887252594  # chat_id کانال QBank_private

## ---------------- Registration (Daily Plan) Channel ----------------
# کانال پیامهای ثبتنام/تمدید (Private channel) -> عدد -100...
REGISTRATION_CHANNEL_ID = -1003860351492  # Registeration_Message

# شماره کارت (برای fallback؛ پیام اصلی پرداخت از کانال کپی میشود)
CARD_NUMBER = "6063 7310 6706 6036"

TAG_RE = re.compile(r"(#[A-Z0-9_]+)")
REG_TAG_RE = re.compile(r"#(REG_[A-Z0-9_]+)")
# --- National ID digit normalizer (Persian/Arabic -> English) ---
_DIGIT_TRANS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def to_en_digits(s: str) -> str:
    return (s or "").translate(_DIGIT_TRANS)


def normalize_national_id(s: str) -> str:
    # تبدیل ارقام فارسی/عربی به انگلیسی + حذف فاصله/خط تیره/هرچیز غیر عددی
    s = to_en_digits(s)
    s = re.sub(r"\D", "", s)
    return s


# برای جستجو در DB حتی اگر قبلاً فارسی ذخیره شده باشد
NID_TRANSLATE_SQL = "translate(national_id,'۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩','01234567890123456789')"

# Admin / Secretary chat_id (طبق حرف خودت)
ADMIN_CHAT_ID = 6203420995
ADMIN_CHAT_ID_2 = 1207601171
ADMIN_CHAT_IDS = {ADMIN_CHAT_ID} | ({ADMIN_CHAT_ID_2} if ADMIN_CHAT_ID_2 else set())

# Advisors (chat_id)
ADVISOR_MATH = 6012643756  # همچنین پشتیبانی به این آیدی میرود
ADVISOR_HUM = 101648381
ADVISOR_BIOCHEM = 1088863518
ADVISOR_IDS = {ADVISOR_MATH, ADVISOR_HUM, ADVISOR_BIOCHEM}

# ---------------- Buttons (Main) ----------------
BTN_SEND_QUESTION = "📝رفع اشکال درسی"
BTN_CONCOOR_COUNTDOWN = "⌛️روز شمار کنکور ۱۴۰۵"
BTN_SUPPORT = "‍👨‍💻پشتیبانی"
BTN_FREE_PLAN = "🎁 پلن رایگان یک روزه"
BTN_ORDER_DAILY = "✅ ثبت نام برنامه روزانه"

BTN_RENEW_MONTH = "🔁 تمدید برنامه روزانه"

# Registration plans
BTN_PLAN_NORMAL = "📌 طرح عادی"
BTN_PLAN_SPECIAL = "📌 طرح ویژه"
BTN_QBANK = "🗂️بانک سوال"

# Activation (student)
BTN_ACTIVATE = "🔓 فعالسازی (ثبت نام کرده ام)"

# Admin-only
BTN_ADMIN_ADD_STUDENT = "➕ ثبت دانش آموز روزانه"
BTN_ADMIN_REMOVE_STUDENT = "➖ حذف دانش آموز روزانه"
BTN_ADMIN_LIST_DAILY = "📋 لیست دانش آموزان روزانه"
BTN_ADMIN_EXTEND_DAILY = "🗓️ تمدید دانش آموز"

# Advisor-only buttons (ReplyKeyboard)

BTN_DONE = "تمام شد ✅"
BTN_CANCEL = "لغو ❌"

BTN_HOME = "🏠 منوی اصلی"
BTN_BACK = "🔙 بازگشت"

BTN_ACHIEVEMENTS = "🏆افتخارات و قبولی ها"

# ----------------- Question flow config -----------------
FIELDS = ["تجربی", "انسانی", "ریاضی"]
FIELDS_ADMIN = ["تجربی", "انسانی", "ریاضی", "هنر"]  # برای ثبت دانشآموز روزانه
GRADES = ["دهم", "یازدهم", "دوازدهم"]

LESSONS_BY_FIELD = {
    "انسانی": ["علوم و فنون ادبی", "فارسی"],
    "تجربی": ["ریاضی", "فیزیک", "زیست شناسی", "شیمی"],
    "ریاضی": ["حسابان", "فیزیک", "هندسه", "آمار و احتمال", "ریاضیات گسسته", "شیمی"],
    # هنر برای ارسال سوال درسی لازم نیست (فعلاً)
}

LESSON_TO_ADVISOR = {
    "ریاضی": ADVISOR_MATH,
    "حسابان": ADVISOR_MATH,
    "فیزیک": ADVISOR_MATH,
    "هندسه": ADVISOR_MATH,
    "ریاضیات گسسته": ADVISOR_MATH,
    "آمار و احتمال": ADVISOR_MATH,

    "فارسی": ADVISOR_HUM,
    "علوم و فنون ادبی": ADVISOR_HUM,

    "شیمی": ADVISOR_BIOCHEM,
    "زیست شناسی": ADVISOR_BIOCHEM,
}

# ----------------- Countdown config (Jalali) -----------------
CONCOOR_MATH_HUM_ART_LANG = (1405, 4, 11)
CONCOOR_EXPERIMENTAL = (1405, 4, 12)
CONCOOR_FARHANGIAN = (1405, 2, 17)  # ۱۷ اردیبهشت ۱۴۰۵

COUNTDOWN_OPTIONS = [
    "📌کنکور ریاضی (۱۱ تیر)",
    "📌کنکور انسانی (۱۱ تیر)",
    "📌کنکور تجربی (۱۲ تیر)",
    "📌کنکور فرهنگیان (۱۷ اردیبهشت)",
    "📌کنکور هنر (۱۱ تیر)",
    "📌کنکور زبان (۱۱ تیر)",
]


# ----------------- DB -----------------

def get_db_conn():
    return psycopg.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "5432")),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        row_factory=dict_row,
    )


def ensure_tables():
    sql = f"""
    CREATE TABLE IF NOT EXISTS question_submissions (
        id BIGSERIAL PRIMARY KEY,
        tg_chat_id BIGINT NOT NULL,
        day_date DATE NOT NULL,
        daily_no INT NOT NULL,
        field TEXT NOT NULL,
        grade TEXT NOT NULL,
        lesson TEXT NOT NULL,
        file_id TEXT NOT NULL,
        channel_msg_id BIGINT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (tg_chat_id, day_date, daily_no)
    );

    CREATE INDEX IF NOT EXISTS idx_question_submissions_day_chat
        ON question_submissions (day_date, tg_chat_id);

    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_name='question_submissions'
              AND column_name='channel_msg_id'
        ) THEN
            ALTER TABLE question_submissions ADD COLUMN channel_msg_id BIGINT NULL;
        END IF;
    END $$;


    CREATE TABLE IF NOT EXISTS free_content (
    id BIGSERIAL PRIMARY KEY,
    field TEXT,
    grade TEXT,
    content_type TEXT,
    channel_chat_id BIGINT,
    channel_message_id BIGINT,
    sort_order INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

    CREATE INDEX IF NOT EXISTS idx_free_content_field_grade_type
    ON public.free_content(field, grade, content_type);

    CREATE INDEX IF NOT EXISTS idx_free_content_sort
    ON public.free_content(sort_order);


    -- جدول دانشآموزهای روزانه + کد فعالسازی
    CREATE TABLE IF NOT EXISTS daily_students (
        id BIGSERIAL PRIMARY KEY,
        national_id TEXT NOT NULL UNIQUE,
        full_name TEXT NOT NULL,
        field TEXT NOT NULL,
        grade TEXT NOT NULL,
        activation_code TEXT NOT NULL UNIQUE,
        tg_chat_id BIGINT NULL UNIQUE,
        is_active BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        activated_at TIMESTAMPTZ NULL
    );

    CREATE INDEX IF NOT EXISTS idx_daily_students_active
        ON daily_students (is_active);

    CREATE INDEX IF NOT EXISTS idx_daily_students_chat
        ON daily_students (tg_chat_id);


    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_name='daily_students'
              AND column_name='expires_at'
        ) THEN
            ALTER TABLE daily_students ADD COLUMN expires_at TIMESTAMPTZ NULL;
        END IF;
    END $$;

    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_name='daily_students'
              AND column_name='phone'
        ) THEN
            ALTER TABLE daily_students ADD COLUMN phone TEXT NULL;
        END IF;
    END $$;

    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_name='daily_students'
              AND column_name='plan'
        ) THEN
            ALTER TABLE daily_students ADD COLUMN plan TEXT NULL;
        END IF;
    END $$;

    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_name='daily_students'
              AND column_name='reminder_3d_sent_at'
        ) THEN
            ALTER TABLE daily_students ADD COLUMN reminder_3d_sent_at TIMESTAMPTZ NULL;
        END IF;
    END $$;

    -- جدول پیامهای ثبتنام (کانال Registration_Message) بر اساس هشتگ
    CREATE TABLE IF NOT EXISTS registration_messages (
        tag TEXT PRIMARY KEY,
        channel_chat_id BIGINT NOT NULL,
        channel_message_id BIGINT NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS idx_registration_messages_updated_at
        ON registration_messages (updated_at);

    -- جدول درخواستهای ثبتنام/تمدید (برای دوام بعد از ریاستارت)
    CREATE TABLE IF NOT EXISTS registration_requests (
        tg_chat_id BIGINT PRIMARY KEY,
        full_name TEXT NOT NULL,
        national_id TEXT NOT NULL,
        field TEXT NOT NULL,
        grade TEXT NOT NULL,
        phone TEXT NOT NULL,
        plan TEXT NOT NULL,
        is_renew BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS idx_registration_requests_created_at
        ON registration_requests (created_at);

    -- جدول فایلهای بانک سوال (بر اساس هشتگ)
    CREATE TABLE IF NOT EXISTS qbank_files (
        tag TEXT PRIMARY KEY,
        channel_chat_id BIGINT NOT NULL,
        channel_message_id BIGINT NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS idx_qbank_files_updated_at
        ON qbank_files (updated_at);


    -- شمارنده استفاده از بانک سوال (برای کاربران غیر فعال)
    CREATE TABLE IF NOT EXISTS qbank_usage (
    tg_chat_id BIGINT PRIMARY KEY,
    used_count INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS idx_qbank_usage_updated_at
        ON qbank_usage (updated_at);


    """
    with get_db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()




def get_today_count_for_chat(tg_chat_id: int) -> int:
    sql = f"""
    SELECT COUNT(*) AS c
    FROM question_submissions
    WHERE tg_chat_id = %s
      AND day_date = (now() AT TIME ZONE '{TZ_NAME}')::date
    """
    with get_db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (tg_chat_id,))
            return int(cur.fetchone()["c"])

def has_question_in_last_7_days(tg_chat_id: int) -> bool:
    sql = """
    SELECT 1
    FROM question_submissions
    WHERE tg_chat_id = %s
      AND created_at >= NOW() - INTERVAL '7 days'
    LIMIT 1;
    """
    with get_db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (tg_chat_id,))
            return cur.fetchone() is not None

def get_total_count_for_chat(tg_chat_id: int) -> int:
    sql = """
          SELECT COUNT(*) AS c
          FROM question_submissions
          WHERE tg_chat_id = %s \
          """
    with get_db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (tg_chat_id,))
            return int(cur.fetchone()["c"])


def insert_submission(tg_chat_id: int, daily_no: int, field: str, grade: str, lesson: str, file_id: str):
    sql = f"""
    INSERT INTO question_submissions (tg_chat_id, day_date, daily_no, field, grade, lesson, file_id)
    VALUES (%s, (now() AT TIME ZONE '{TZ_NAME}')::date, %s, %s, %s, %s, %s)
    """
    with get_db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (tg_chat_id, daily_no, field, grade, lesson, file_id))
        conn.commit()


def set_channel_msg_id(tg_chat_id: int, daily_no: int, channel_msg_id: int):
    sql = f"""
    UPDATE question_submissions
    SET channel_msg_id = %s
    WHERE tg_chat_id = %s
      AND daily_no = %s
      AND day_date = (now() AT TIME ZONE '{TZ_NAME}')::date
    """
    with get_db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (channel_msg_id, tg_chat_id, daily_no))
        conn.commit()


def get_channel_msg_id_today(tg_chat_id: int, daily_no: int):
    sql = f"""
    SELECT channel_msg_id
    FROM question_submissions
    WHERE tg_chat_id = %s
      AND daily_no = %s
      AND day_date = (now() AT TIME ZONE '{TZ_NAME}')::date
    LIMIT 1
    """
    with get_db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (tg_chat_id, daily_no))
            row = cur.fetchone()
            return row["channel_msg_id"] if row else None


# ---- free_content table (قبلاً خودت ساختی) ----
def upsert_free_content(field: str, grade: str, content_type: str, channel_chat_id: int, channel_message_id: int,
                        sort_order: int = 1):
    sql = """
          INSERT INTO free_content (field, grade, content_type, channel_chat_id, channel_message_id, sort_order)
          VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (field, grade, content_type, sort_order)
    DO \
          UPDATE SET
              channel_chat_id = EXCLUDED.channel_chat_id, \
              channel_message_id = EXCLUDED.channel_message_id; \
          """
    with get_db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (field, grade, content_type, channel_chat_id, channel_message_id, sort_order))
        conn.commit()


def get_free_content(field: str, grade: str, content_type: str, sort_order: int = 1):
    sql = """
          SELECT channel_chat_id, channel_message_id
          FROM free_content
          WHERE field = %s \
            AND grade = %s \
            AND content_type = %s \
            AND sort_order = %s LIMIT 1; \
          """
    with get_db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (field, grade, content_type, sort_order))
            return cur.fetchone()


# ---- daily_students helpers ----
def is_student_active(tg_chat_id: int) -> bool:
    sql = "SELECT 1 FROM daily_students WHERE tg_chat_id=%s AND is_active=TRUE LIMIT 1;"
    with get_db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (tg_chat_id,))
            return cur.fetchone() is not None


def admin_create_activation_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    # 8 کاراکتر: مثل A1B2C3D4
    return "".join(secrets.choice(alphabet) for _ in range(8))


def admin_add_daily_student(full_name: str, national_id: str, field: str, grade: str) -> str:
    national_id = normalize_national_id(national_id)
    # اگر قبلاً وجود داشته باشد، همان کد را برگردان
    sql_check = f"SELECT activation_code FROM daily_students WHERE {NID_TRANSLATE_SQL}=%s LIMIT 1;"
    with get_db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql_check, (national_id,))
            row = cur.fetchone()
            if row and row.get("activation_code"):
                return row["activation_code"]

    # تولید کد یکتا
    for _ in range(10):
        code = admin_create_activation_code()
        try:
            sql_ins = """
                      INSERT INTO daily_students (national_id, full_name, field, grade, activation_code, is_active)
                      VALUES (%s, %s, %s, %s, %s, FALSE) \
                      """
            with get_db_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql_ins, (national_id, full_name, field, grade, code))
                conn.commit()
            return code
        except Exception:
            # احتمال تداخل کد
            continue

    raise RuntimeError("could not generate unique activation code")


def admin_remove_daily_student(national_id: str) -> bool:
    national_id = normalize_national_id(national_id)
    sql = f"DELETE FROM daily_students WHERE {NID_TRANSLATE_SQL}=%s;"
    with get_db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (national_id,))
            deleted = cur.rowcount
        conn.commit()
    return deleted > 0


def activate_student_by_code(code: str, tg_chat_id: int) -> str:
    """
    return status:
      OK
      CODE_NOT_FOUND
      CODE_USED
      CHAT_ALREADY_ACTIVE
    """
    if is_student_active(tg_chat_id):
        return "CHAT_ALREADY_ACTIVE"

    sql = """
          SELECT id, is_active, tg_chat_id
          FROM daily_students
          WHERE activation_code = %s LIMIT 1; \
          """
    with get_db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (code,))
            row = cur.fetchone()

    if not row:
        return "CODE_NOT_FOUND"

    if row["is_active"] or row["tg_chat_id"] is not None:
        return "CODE_USED"

    sql_upd = """
              UPDATE daily_students
              SET tg_chat_id=%s, \
                  is_active= TRUE, \
                  activated_at=NOW()
              WHERE id = %s; \
              """
    with get_db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql_upd, (tg_chat_id, row["id"]))
        conn.commit()
    return "OK"


# ---- qbank_files table ----
def upsert_qbank_file(tag: str, channel_chat_id: int, channel_message_id: int):
    """ذخیره/ویرایش پیام کانال برای یک هشتگ مشخص (Upsert). tag بدون # ذخیره میشود."""
    sql = """
          INSERT INTO qbank_files (tag, channel_chat_id, channel_message_id, updated_at)
          VALUES (%s, %s, %s, NOW()) ON CONFLICT (tag)
    DO \
          UPDATE SET
              channel_chat_id = EXCLUDED.channel_chat_id, \
              channel_message_id = EXCLUDED.channel_message_id, \
              updated_at = NOW(); \
          """
    with get_db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (tag, channel_chat_id, channel_message_id))
        conn.commit()


def get_qbank_file(tag: str):
    sql = """
          SELECT tag, channel_chat_id, channel_message_id
          FROM qbank_files
          WHERE tag = %s \
          """
    with get_db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (tag,))
            return cur.fetchone()


def delete_qbank_file(tag: str):
    sql = "DELETE FROM qbank_files WHERE tag = %s"
    with get_db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (tag,))
        conn.commit()


# ---- registration (daily plan self-register) messages ----
def upsert_registration_message(tag: str, channel_chat_id: int, channel_message_id: int):
    """ذخیره/ویرایش پیام کانال برای یک هشتگ مشخص (Upsert). tag بدون # ذخیره میشود."""
    sql = """
          INSERT INTO registration_messages (tag, channel_chat_id, channel_message_id, updated_at)
          VALUES (%s, %s, %s, NOW()) ON CONFLICT (tag)
          DO UPDATE SET
              channel_chat_id = EXCLUDED.channel_chat_id,
              channel_message_id = EXCLUDED.channel_message_id,
              updated_at = NOW();
          """
    with get_db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (tag, channel_chat_id, channel_message_id))
        conn.commit()


def get_registration_message(tag: str):
    sql = "SELECT tag, channel_chat_id, channel_message_id FROM registration_messages WHERE tag=%s LIMIT 1;"
    with get_db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (tag,))
            return cur.fetchone()


def delete_registration_message(tag: str):
    sql = "DELETE FROM registration_messages WHERE tag=%s;"
    with get_db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (tag,))
        conn.commit()


# ---- registration requests (durable pending data) ----
def upsert_registration_request(*, tg_chat_id: int, full_name: str, national_id: str, field: str, grade: str, phone: str, plan: str, is_renew: bool):
    sql = """
    INSERT INTO registration_requests (tg_chat_id, full_name, national_id, field, grade, phone, plan, is_renew, created_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
    ON CONFLICT (tg_chat_id) DO UPDATE SET
        full_name=EXCLUDED.full_name,
        national_id=EXCLUDED.national_id,
        field=EXCLUDED.field,
        grade=EXCLUDED.grade,
        phone=EXCLUDED.phone,
        plan=EXCLUDED.plan,
        is_renew=EXCLUDED.is_renew,
        created_at=NOW();
    """
    with get_db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (tg_chat_id, full_name, national_id, field, grade, phone, plan, is_renew))
        conn.commit()


def get_registration_request(tg_chat_id: int):
    sql = "SELECT * FROM registration_requests WHERE tg_chat_id=%s LIMIT 1;"
    with get_db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (tg_chat_id,))
            return cur.fetchone()


def delete_registration_request(tg_chat_id: int):
    sql = "DELETE FROM registration_requests WHERE tg_chat_id=%s;"
    with get_db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (tg_chat_id,))
        conn.commit()


# ---- qbank usage (limit for non-active users) ----
def get_qbank_usage_count(tg_chat_id: int) -> int:
    sql = "SELECT used_count FROM qbank_usage WHERE tg_chat_id=%s;"
    with get_db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (tg_chat_id,))
            row = cur.fetchone()
            if not row:
                return 0
            return int(row.get("used_count") or 0)


def inc_qbank_usage_count(tg_chat_id: int) -> int:
    """Increment usage counter and return new value."""
    sql = """
          INSERT INTO qbank_usage (tg_chat_id, used_count, updated_at)
          VALUES (%s, 1, NOW()) ON CONFLICT (tg_chat_id)
    DO \
          UPDATE SET used_count = qbank_usage.used_count + 1, \
              updated_at = NOW() \
              RETURNING used_count; \
          """
    with get_db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (tg_chat_id,))
            row = cur.fetchone()
        conn.commit()
    return int(row.get("used_count") or 0) if row else 0

def qbank_used_in_last_7_days(tg_chat_id: int) -> bool:
    sql = """
    SELECT 1
    FROM qbank_usage
    WHERE tg_chat_id = %s
      AND updated_at >= NOW() - INTERVAL '7 days'
    LIMIT 1;
    """
    with get_db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (tg_chat_id,))
            return cur.fetchone() is not None


# ----------------- Jalali -> Gregorian (no extra libs) -----------------
def jalali_to_gregorian(jy: int, jm: int, jd: int) -> date:
    jy += 1595
    days = -355668 + (365 * jy) + ((jy // 33) * 8) + (((jy % 33) + 3) // 4)
    if jm < 7:
        days += (jm - 1) * 31
    else:
        days += ((jm - 7) * 30) + 186
    days += jd - 1

    gy = 400 * (days // 146097)
    days %= 146097

    if days > 36524:
        gy += 100 * ((days - 1) // 36524)
        days = (days - 1) % 36524
        if days >= 365:
            days += 1

    gy += 4 * (days // 1461)
    days %= 1461

    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365

    gd = days + 1
    leap = (gy % 4 == 0 and gy % 100 != 0) or (gy % 400 == 0)
    sal_a = [0, 31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 1
    while gm <= 12 and gd > sal_a[gm]:
        gd -= sal_a[gm]
        gm += 1

    return date(gy, gm, gd)


# ----------------- UI helpers -----------------
def make_keyboard(items, columns=2):
    rows, row = [], []
    for i, item in enumerate(items, 1):
        row.append(item)
        if i % columns == 0:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True)


def is_admin(chat_id: int) -> bool:
    return chat_id in ADMIN_CHAT_IDS


def is_abedini(chat_id: int) -> bool:
    return chat_id == ADMIN_CHAT_ID_2


def is_shiri(chat_id: int) -> bool:
    return chat_id == ADMIN_CHAT_ID


def is_advisor(chat_id: int) -> bool:
    return chat_id in ADVISOR_IDS


# ----------------- Jalali date helpers -----------------
def gregorian_to_jalali(gy: int, gm: int, gd: int):
    # Algorithm based on the Jalaali calendar conversion (commonly used implementation)
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    if gy > 1600:
        jy = 979
        gy -= 1600
    else:
        jy = 0
        gy -= 621
    gy2 = gy + 1 if gm > 2 else gy
    days = 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400 - 80 + gd + g_d_m[gm - 1]
    jy += 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm = 1 + days // 31
        jd = 1 + days % 31
    else:
        jm = 7 + (days - 186) // 30
        jd = 1 + (days - 186) % 30
    return jy, jm, jd


def jalali_to_gregorian(jy: int, jm: int, jd: int):
    if jy > 979:
        gy = 1600
        jy -= 979
    else:
        gy = 621
    days = 365 * jy + (jy // 33) * 8 + ((jy % 33) + 3) // 4 + 78 + jd
    if jm < 7:
        days += (jm - 1) * 31
    else:
        days += (jm - 7) * 30 + 186
    gy += 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        gy += 100 * ((days - 1) // 36524)
        days = (days - 1) % 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    sal_a = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    leap = (gy % 4 == 0 and gy % 100 != 0) or (gy % 400 == 0)
    if leap:
        sal_a[2] = 29
    gm = 1
    while gm <= 12 and gd > sal_a[gm]:
        gd -= sal_a[gm]
        gm += 1
    return gy, gm, gd


def format_jalali(dt: datetime) -> str:
    jy, jm, jd = gregorian_to_jalali(dt.year, dt.month, dt.day)
    return f"{jy:04d}/{jm:02d}/{jd:02d}"


def parse_jalali_date(s: str) -> datetime | None:
    s = (s or "").strip()
    m = re.match(r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2})$", s)
    if not m:
        return None
    jy, jm, jd = map(int, m.groups())
    try:
        gy, gm, gd = jalali_to_gregorian(jy, jm, jd)
        return datetime(gy, gm, gd, 0, 0, 0)
    except Exception:
        return None


def parse_free_tag(tag: str):
    """
    Expected tags:
    #P10R #Q11T #A12H #VOICE #ACH1
    """
    tag = tag.strip().lstrip("#")

    if tag == "VOICE":
        return ("ALL", "ALL", "voice", 1)

    m2 = re.fullmatch(r"ACH(\d+)", tag)
    if m2:
        sort_order = int(m2.group(1))
        return ("ALL", "ALL", "achievements", sort_order)

    m = re.fullmatch(r"([PQA])(10|11|12)([RTH])", tag)
    if not m:
        return None

    kind, grade, field_code = m.groups()

    content_type = {"P": "program", "Q": "quiz", "A": "answer_key"}[kind]
    grade_text = {"10": "دهم", "11": "یازدهم", "12": "دوازدهم"}[grade]
    field_text = {"R": "ریاضی", "T": "تجربی", "H": "انسانی"}[field_code]

    return (field_text, grade_text, content_type, 1)


def main_menu_keyboard(chat_id: int):
    # ادمین همزمان مشاور هم هست، پس همه دکمهها را داشته باشد
    if is_abedini(chat_id):
        return ReplyKeyboardMarkup(
            [
                [BTN_ADMIN_ADD_STUDENT, BTN_ADMIN_REMOVE_STUDENT],
                [BTN_ADMIN_LIST_DAILY, BTN_ADMIN_EXTEND_DAILY],
                [BTN_QBANK],
            ],
            resize_keyboard=True,
        )

    if is_shiri(chat_id):
        return ReplyKeyboardMarkup(
            [
                [BTN_ADMIN_ADD_STUDENT, BTN_ADMIN_REMOVE_STUDENT],
                [BTN_ADMIN_LIST_DAILY, BTN_ADMIN_EXTEND_DAILY],
            ],
            resize_keyboard=True,
        )

    if is_advisor(chat_id):
        return ReplyKeyboardMarkup(

            [
                [BTN_SEND_QUESTION, BTN_QBANK],
                [BTN_CONCOOR_COUNTDOWN, BTN_FREE_PLAN],
                [BTN_ACHIEVEMENTS, BTN_ORDER_DAILY],
                [BTN_SUPPORT, BTN_RENEW_MONTH],
                [BTN_ACTIVATE],
            ],

            resize_keyboard=True,
        )

    return ReplyKeyboardMarkup(

        [
            [BTN_SEND_QUESTION, BTN_QBANK],
            [BTN_CONCOOR_COUNTDOWN, BTN_FREE_PLAN],
            [BTN_ACHIEVEMENTS, BTN_ORDER_DAILY],
            [BTN_SUPPORT,BTN_RENEW_MONTH],
            [BTN_ACTIVATE],
        ],

        resize_keyboard=True,
    )


async def go_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    chat_id = update.effective_chat.id
    await update.message.reply_text("🏠 برگشتیم به منوی اصلی.", reply_markup=main_menu_keyboard(chat_id))
    return STATE_ACTION


# ----------------- Membership (Required Channel) -----------------
def join_required_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ عضویت در کانال", url=REQUIRED_CHANNEL_LINK)],
        [InlineKeyboardButton("🔄 عضو شدم (بررسی)", callback_data="CHK_JOIN")],
    ])


async def is_user_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if not user:
        return False
    try:
        m = await context.bot.get_chat_member(REQUIRED_CHANNEL_CHAT_ID, user.id)
        return m.status in ("member", "administrator", "creator")
    except Exception as e:
        logger.warning("get_chat_member failed: %s", e)
        return False


async def require_membership(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if await is_user_member(update, context):
        return True

    msg = "برای استفاده از خدمات ربات، ابتدا باید عضو کانال زیر شوید 👇"
    if update.message:
        await update.message.reply_text(msg, reply_markup=join_required_markup())
    elif update.callback_query:
        await update.callback_query.message.reply_text(msg, reply_markup=join_required_markup())
    return False


async def on_check_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not await require_membership(update, context):
        return STATE_ACTION
    context.user_data.clear()
    chat_id = q.message.chat_id
    await q.message.reply_text(
        "✅ عضویت تایید شد. حالا از منو استفاده کن:",
        reply_markup=main_menu_keyboard(chat_id),
    )


def parse_question_code(qcode: str):
    parts = qcode.split("-")
    if len(parts) != 2:
        return None
    if not parts[0].isdigit() or not parts[1].isdigit():
        return None
    target_chat_id = int(parts[0])
    daily_no = int(parts[1])
    if daily_no < 1:
        return None
    return target_chat_id, daily_no


# ----------------- Conversation States -----------------
(
    STATE_ACTION,
    STATE_FIELD,
    STATE_GRADE,
    STATE_LESSON,
    STATE_WAIT_PHOTO,
    STATE_COUNTDOWN_PICK,
    STATE_SUPPORT_WAIT,
    STATE_FREE_FIELD,
    STATE_FREE_GRADE,

    # activation (student)
    STATE_ACTIVATION_CODE,

    # admin add daily student
    STATE_ADMIN_STU_NAME,
    STATE_ADMIN_STU_NID,
    STATE_ADMIN_STU_FIELD,
    STATE_ADMIN_STU_GRADE,

    # admin remove daily student
    STATE_ADMIN_REMOVE_NID,

    # admin list daily students
    STATE_ADMIN_LIST_FIELD,
    STATE_ADMIN_LIST_GROUP,

    # admin extend daily student
    STATE_ADMIN_EXTEND_NID,
    STATE_ADMIN_EXTEND_PAYDATE,

    # qbank (bank soal)
    STATE_QB_GRADE,
    STATE_QB_MAJOR,
    STATE_QB_SUBJECT,
    STATE_QB_CHAPTER,

    # registration / renewal (daily plan self-register)
    STATE_REG_PLAN_SELECT,
    STATE_REG_NAME,
    STATE_REG_NID,
    STATE_REG_FIELD,
    STATE_REG_GRADE,
    STATE_REG_PHONE,
    STATE_REG_PAYMENT_WAIT_BTN,
    STATE_REG_WAIT_RECEIPT,

) = range(31)

# ----------------- QBANK (Bank Soal) Config -----------------
QB_GRADES = ["دهم", "یازدهم", "دوازدهم"]
QB_MAJORS = ["ریاضی", "تجربی", "انسانی"]


def qb_kb_menu():
    return ReplyKeyboardMarkup([[BTN_QBANK]], resize_keyboard=True)


def qb_kb_grades():
    return ReplyKeyboardMarkup([QB_GRADES, [BTN_BACK]], resize_keyboard=True, one_time_keyboard=True)


def qb_kb_majors():
    return ReplyKeyboardMarkup([QB_MAJORS, [BTN_BACK]], resize_keyboard=True, one_time_keyboard=True)


# درسها (زمینشناسی از همه تجربیها حذف شده)
QB_SUBJECTS = {
    "دوازدهم": {
        "ریاضی": [
            ["حسابان", "ریاضیات گسسته"],
            ["فیزیک", "هندسه"],
            ["شیمی", "دروس عمومی"],
            [BTN_BACK],
        ],
        "تجربی": [
            ["زیست شناسی", "شیمی"],
            ["فیزیک", "ریاضی"],
            ["دروس عمومی"],
            [BTN_BACK],
        ],
        "انسانی": [
            ["علوم و فنون", "فلسفه","جامعه شناسی"],
            ["تاریخ", "جغرافیا"],
            ["ریاضی", "عربی","دروس عمومی"],
            [BTN_BACK],
        ],
    },
    "یازدهم": {
        "ریاضی": [
            ["حسابان", "فیزیک"],
            ["شیمی", "هندسه"],
            ["آمار و احتمال", "دروس عمومی"],
            [BTN_BACK],
        ],
        "تجربی": [
            ["زیست شناسی", "شیمی"],
            ["فیزیک", "ریاضی"],
            ["دروس عمومی"],
            [BTN_BACK],
        ],
        "انسانی": [
            ["علوم و فنون","عربی", "ریاضی"],
            ["جامعه شناسی", "جغرافیا","تاریخ"],
            ["روانشناسی","فلسفه", "دروس عمومی"],
            [BTN_BACK],
        ],
    },
    "دهم": {
        "ریاضی": [
            ["ریاضی", "فیزیک"],
            ["هندسه", "شیمی"],
            ["دروس عمومی"],
            [BTN_BACK],
        ],
        "تجربی": [
            ["زیست شناسی", "شیمی"],
            ["فیزیک", "ریاضی"],
            ["دروس عمومی"],
            [BTN_BACK],
        ],
        "انسانی": [
            ["علوم و فنون","منطق", "جامعه شناسی"],
            ["تاریخ","عربی", "جغرافیا"],
            ["ریاضی", "اقتصاد","دروس عمومی"],
            [BTN_BACK],
        ],
    },
}

# ساختار: QB_CHAPTERS[grade][major][subject] = ["فصل 1: ...", ...]
# نکته: اگر برای یک درس فصلها را اضافه نکرده باشی، ربات پیام «هنوز ثبت نشده» میدهد.
QB_CHAPTERS = {
    "دهم": {
        "ریاضی": {
            "ریاضی": [
                "فصل 1: مجموعه، الگو، دنباله",
                "فصل 2: مثلثات",
                "فصل 3: توان‌های گویا و عبارات جبری",
                "فصل 4: معادله و نامعادله",
                "فصل 5: تابع",
                "فصل 6: شمارش، بدون شمارش",
                "فصل 7: آمار و احتمال",
            ],
            "فیزیک": [
                "فصل 1: فیزیک و اندازه‌گیری",
                "فصل 2: کار، انرژی و توان",
                "فصل 3: ویژگی‌های فیزیکی مواد",
                "فصل 4: دما و گرما",
                "فصل 5: ترمودینامیک",
            ],
            "هندسه": [
                "فصل 1: ترسیم‌های هندسی و استدلال",
                "فصل 2: قضیه تالس، تشابه و کاربردهای آن",
                "فصل 3: چندضلعی‌ها",
                "فصل 4: تجسم فضایی",
            ],
            "شیمی": [
                "فصل 1: کیهان زادگان، الفبای هستی",
                "فصل 2: ردپای گازها در زندگی",
                "فصل 3: آب، آهنگ زندگی",
            ],
        },
        "تجربی": {
            "ریاضی": [
                "فصل 1: مجموعه، الگو، دنباله",
                "فصل 2: مثلثات",
                "فصل 3: توان‌های گویا و عبارات جبری",
                "فصل 4: معادله و نامعادله",
                "فصل 5: تابع",
                "فصل 6: شمارش، بدون شمارش",
                "فصل 7: آمار و احتمال",
            ],
            "شیمی": [
                "فصل 1: کیهان زادگان، الفبای هستی",
                "فصل 2: ردپای گازها در زندگی",
                "فصل 3: آب، آهنگ زندگی",
            ],
            "فیزیک": [
                "فصل 1: فیزیک و اندازه‌گیری",
                "فصل 2: کار، انرژی و توان",
                "فصل 3: ویژگی‌های فیزیکی مواد",
                "فصل 4: دما و گرما",
            ],
            "زیست شناسی": [
                "فصل 1: زیست‌شناسی دیروز، امروز...",
                "فصل 2: گوارش و جذب مواد",
                "فصل 3: تبادلات گازی",
                "فصل 4: گردش مواد در بدن",
                "فصل 5: دفع مواد",
                "فصل 6: از یاخته تا گیاه",
                "فصل 7: جذب و انتقال مواد در گیاهان",
            ],
        },
        "انسانی": {
            "علوم و فنون": [
                "درس 1: مبانی تحلیل متن",
                "درس 2: سازه‌ها و عوامل تاثیرگذار در شعر فارسی",
                "درس 3: واژآرایی، واژه‌آرایی",
                "درس 4: تاریخ ادبیات پیش از اسلام",
                "درس 5: هماهنگی پاره‌های کلام",
                "درس 6: سجع و انواع آن",
                "درس 7: سبک و سبک‌شناسی",
                "درس 8: وزن شعر فارسی",
                "درس 9: موازنه و ترصیع",
                "درس 10: زبان و ادبیات در سده‌های 5 و 6 و ویژگی‌های سبکی",
                "درس 11: قافیه",
                "درس 12: جناس و انواع آن",
            ],

            "جامعه شناسی": [
                "درس 1: کنش‌های ما",
                "درس 2: پدیده‌های اجتماعی",
                "درس 3: جهان اجتماعی",
                "درس 4: اجزا و لایه های جهان اجتماعی",
                "درس 5: جهان های اجتماعی",
                "درس 6: پیامد های جهان اجتماعی",
                "درس 7: ارزیابی جهان های اجتماعی",
                "درس 8: هویت ",
                "درس 9: باز تولید هویت اجتماعی",
                "درس 10: تغییرات هویت اجتماعی",
                "درس 11: تحولات هویتی جهان اجتماعی(درونی)",
                "درس 12: تحولات هویتی جهان اجتماعی(بیرونی)",
                "درس 13: هویت ایرانی 1",
                "درس 14: هویت ایرانی 2",
                "درس 15: هویت ایرانی 3",
                "درس 16: هویت ایرانی 4",
            ],

            "تاریخ": [

                "درس 1: تاریخ یا تاریخ‌نگاری",
                "درس 2: تاریخ؛ زمان و مکان",
                "درس 3: باستان‌شناسی در جست‌وجوی میراث فرهنگی",
                "درس 4: پیدایش تمدن بین‌النهرین و مصر",
                "درس 5: هند و چین",
                "درس 6: یونان و روم",
                "درس 7: مطالعه و کاوش در گذشته‌های دور",
                "درس 8: میراث تمدن ایرانی",
                "درس 9: از ورود آریایی‌ها تا پایان هخامنشیان",
                "درس 10: اشکانیان و ساسانیان",
                "درس 11: آیین کشورداری",
                "درس 12: جامعه و خانواده",
                "درس 13: اقتصاد و معیشت",
                "درس 14: دین و اعتقادات",
                "درس 15: زبان، علم و آموزش",
                "درس 16: هنر و معماری",
            ],

            "جغرافیا": [
                "درس 1: جغرافیای علمی برای زندگی بهتر",
                "درس 2: روش مطالعه و پژوهش در جغرافیا",
                "درس 3: موقعیت جغرافیایی ایران",
                "درس 4: ناهمواری‌های ایران",
                "درس 5: آب و هوای ایران",
                "درس 6: منابع آب ایران",
                "درس 7: ویژگی‌های جمعیت ایران",
                "درس 8: تقسیمات کشوری ایران",
                "درس 9: سکونتگاه‌های ایران",
                "درس 10: توان‌های اقتصادی ایران",
            ],

            "اقتصاد": [
                "درس 1: کسب و کار و کارآفرینی",
                "درس 2: انتخاب نوع کسب و کار",
                "درس 3: اصول انتخاب درست",
                "درس 4: مرز امکانات تولید",
                "درس 5: بازار چیست و چگونه عمل می‌کند؟",
                "درس 6: نقش دولت در اقتصاد چیست؟",
                "درس 7: تجارت بین‌الملل",
                "درس 8: رکود، بیکاری و فقر",
                "درس 9: تورم و کاهش قدرت خرید",
                "درس 10: مقاوم‌سازی اقتصاد",
                "درس 11: رشد و پیشرفت اقتصادی",
                "درس 12: بودجه‌بندی",
                "درس 13: تصمیم‌گیری در مخارج",
                "درس 14: پس‌انداز و سرمایه‌گذاری",
            ],
            "ریاضی": [
                "فصل 1:معادله درجه دو",
                "فصل 2:تابع",
                "فصل 3: داده های آماری",
                "فصل 4:نمایش داده ها",
            ],
            "عربی": [
                "درس 1",
                "درس 2",
                "درس 3",
                "درس 4",
                "درس 5",
                "درس 6",
                "درس 7",
                "درس 8",
            ],
            "منطق": [
                "درس 1: منطق ترازوی اندیشه",
                "درس 2: لفظ و معنا",
                "درس 3: مفهوم و مصداق",
                "درس 4: اقسام و شرایط تعریف",
                "درس 5: اقسام استدلال استقرایی",
                "درس 6: قضیه حملی",
                "درس 7: احکام قضایا",
                "درس 8: قیاس اقترانی",
                "درس 9: قضیه شرطی و قیاس استثنایی",
                "درس 10: سنجشگری در تفکر",
            ],
        }

    },

    "یازدهم": {
        "ریاضی": {
            "حسابان": [
                "فصل 1: جبر و معادله",
                "فصل 2: تابع",
                "فصل 3: توابع نمایی و لگاریتمی",
                "فصل 4: مثلثات",
                "فصل 5: حد و پیوستگی",
            ],
            "فیزیک": [
                "فصل 1: الکتریسیته ساکن",
                "فصل 2: جریان الکتریکی و مدارهای جریان مستقیم",
                "فصل 3: مغناطیس",
                "فصل 4: القای الکترومغناطیس و جریان متناوب",
            ],
            "شیمی": [
                "فصل 1: قدر هدیه‌های زمینی را بدانیم",
                "فصل 2: در پی غذای سالم",
                "فصل 3: پوشاک، نیازی پایان‌ناپذیر",
            ],
            "هندسه": [
                "فصل 1: دایره",
                "فصل 2: تبدیل‌های هندسی و کاربردها",
                "فصل 3: روابط طولی در مثلث",
            ],
            "آمار و احتمال": [
                "فصل 1: آشنایی با مبانی ریاضیات",
                "فصل 2: احتمال",
                "فصل 3: آمار توصیفی",
                "فصل 4: آمار استنباطی",
            ],
        },

        "تجربی": {
            "شیمی": [
                "فصل 1: قدر هدیه‌های زمینی را بدانیم",
                "فصل 2: در پی غذای سالم",
                "فصل 3: پوشاک، نیازی پایان‌ناپذیر",
            ],
            "ریاضی": [
                "فصل 1: هندسه تحلیلی و جبر",
                "فصل 2: هندسه",
                "فصل 3: تابع",
                "فصل 4: مثلثات",
                "فصل 5: توابع نمایی و لگاریتمی",
                "فصل 6: حد و پیوستگی",
                "فصل 7: آمار و احتمال",
            ],
            "فیزیک": [
                "فصل 1: الکتریسیته ساکن",
                "فصل 2: جریان الکتریکی و جریان مستقیم",
                "فصل 3: مغناطیس و القای الکترومغناطیس",
            ],
            "زیست شناسی": [
                "فصل 1: تنظیم عصبی",
                "فصل 2: حواس",
                "فصل 3: دستگاه حرکتی",
                "فصل 4: تنظیم شیمیایی",
                "فصل 5: ایمنی",
                "فصل 6: تقسیم یاخته",
                "فصل 7: تولید مثل",
                "فصل 8: تولید مثل نهان‌دانگان",
                "فصل 9: پاسخ گیاهان به محرک‌ها",
            ],
        },

        "انسانی": {
            "عربی": [
                "درس 1",
                "درس 2",
                "درس 3",
                "درس 4",
                "درس 5",
                "درس 6",
                "درس 7",
            ],
            "جامعه شناسی": [

                "درس 1: جهان فرهنگی",
                "درس 2: فرهنگ جهانی",
                "درس 3: نمونه های فرهنگ جهانی 1",
                "درس 4: نمونه های فرهنگ جهانی 2",
                "درس 5: باور ها و ارزش های بنیادین فرهنگ غرب",
                "درس 6: چگونگی تکوین فرهنگ معاصر غرب",
                "درس 7: جامعه جهانی",
                "درس 8: تحولات نظام جهانی",
                "درس 9: جهان دو قطبی",
                "درس 10: جنگ ها و تقابل های جهانی",
                "درس 11: بحران های اقتصادی و زیست محیطی",
                "درس 12:بحران های معرفتی و معنوی",
                "درس 13: سرآغاز بیداری اسلامی",
                "درس 14: انقلاب اسلامی ایران ، نقطه عطف بیداری",
                "درس 15: افق بیداری اسلامی",
            ],
            "روانشناسی": [
                "درس 1: تعریف و روش مورد مطالعه",
                "درس 2: روانشناسی رشد",
                "درس 3: احساس، توجه، ادراک",
                "درس 4: حافظه و علل فراموشی",
                "درس 5: تفکر 1 - حل مسئله",
                "درس 6: تفکر 2 - تصمیم‌گیری",
                "درس 7: انگیزه و نگرش",
                "درس 8: روانشناسی سلامت",
            ],
            "علوم و فنون": [
                "درس 1: تاریخ ادبیات قرن‌های 7 و 8 و 9",
                "درس 2: پایه‌های آوایی",
                "درس 3: تشبیه",
                "درس 4: سبک‌شناسی قرن‌های 7 و 8 و 9",
                "درس 5: پایه‌های آوایی همسان (1)",
                "درس 6: مجاز",
                "درس 7: تاریخ ادبیات قرن‌های 10 و 11",
                "درس 8: پایه‌های آوایی همسان (2)",
                "درس 9: استعاره",
                "درس 10: سبک‌شناسی قرن‌های 10 و 11",
                "درس 11: پایه‌های آوایی همسان دو لختی",
                "درس 12: کنایه",
            ],
            "فلسفه": [
                "درس 1: چیستی فلسفه",
                "درس 2: ریشه و شاخه‌های فلسفه",
                "درس 3: فلسفه و زندگی",
                "درس 4: آغاز تاریخی فلسفه",
                "درس 5: زندگی بر اساس اندیشه",
                "درس 6: امکان شناخت",
                "درس 7: ابزارهای شناخت",
                "درس 8: نگاهی به تاریخچه معرفت",
                "درس 9: چیستی انسان (1)",
                "درس 10: چیستی انسان (2)",
                "درس 11: انسان، موجود اخلاق‌گرا",
            ],
            "تاریخ": [
                "درس 1: منابع پژوهش در تاریخ اسلام و ایران اسلامی",
                "درس 2: روش پژوهش در تاریخ؛ بررسی و سنجش اعتبار شواهد و مدارک",
                "درس 3: اسلام در مکه",
                "درس 4: امت و حکومت نبوی در مدینه",
                "درس 5: تثبیت و گسترش اسلام در دوران خلفای نخستین",
                "درس 6: امویان بر مسند قدرت ",
                "درس 7: جهان اسلام در عصر خلافت عباسی",
                "درس 8: اسلام در ایران",
                "درس 9: ظهور و گسترش تمدن ایرانی اسلامی",
                "درس 10: ایران در دوران غزنوی سلجوقی و خوارزمشاهی",
                "درس 11: حکومت‌ها، جامعه و اقتصاد در عصر مغول-تیموری",
                "درس 12: فرهنگ وهنر در عصر مغول-تیموری",
                "درس 13: تحولات سیاسی و اقتصادی ایران در دوره صفوی",
                "درس 14: فرهنگ و تمدن در دوره صفوی",
                "درس 15: قرون وسطا",
                "درس 16: رنسانس و عصر جدید",
            ],
            "جغرافیا": [

                "درس 1: معنا و مفهوم ناحیه",
                "درس 2: انسان و ناحیه",
                "درس 3: نواحی آب و هوایی",
                "درس 4: ناهمواری‌ها و اشکال زمین",
                "درس 5: نواحی زیستی",
                "درس 6: نواحی فرهنگی",
                "درس 7: نواحی اقتصادی (کشاورزی و صنعت)",
                "درس 8: نواحی اقتصادی (تجارت و اقتصاد جهانی)",
                "درس 9: معنا و مفهوم ناحیه سیاسی",
                "درس 10: کشور، یک ناحیه سیاسی",
                "درس 11: ژئوپلیتیک",
            ],
            "ریاضی": [
                "فصل1:آشنایی با منطق و استدلال",
                "فصل2:تابع",
                "فصل3:آمار",
            ]
        },
    },

    "دوازدهم": {
        "ریاضی": {
            "حسابان": [
                "فصل 1: تابع",
                "فصل 2: مثلثات",
                "فصل 3: حدهای متناهی و حد در بی‌نهایت",
                "فصل 4: مشتق",
                "فصل 5: کاربردهای مشتق",
            ],
            "ریاضیات گسسته": [
                "فصل 1: نظریه اعداد",
                "فصل 2: گراف و مدل‌سازی",
                "فصل 3: ترکیبیات",
            ],
            "فیزیک": [
                "فصل 1: حرکت بر خط راست",
                "فصل 2: دینامیک و حرکت دایره‌ای",
                "فصل 3: نوسان و موج",
                "فصل 4: برهم‌کنش‌های موج",
                "فصل 5: آشنایی با فیزیک اتمی",
                "فصل 6: آشنایی با فیزیک هسته‌ای",
            ],
            "شیمی": [
                "فصل 1: مولکول‌ها در خدمت تندرستی",
                "فصل 2: آسایش و رفاه در سایه شیمی",
                "فصل 3: شیمی جلوه‌ای از هنر، زیبایی و ماندگاری",
                "فصل 4: شیمی، راهی به سوی آینده روشن‌تر",
            ],
            "هندسه": [
                "فصل 1: ماتریس و کاربردها",
                "فصل 2: آشنایی با مقاطع مخروطی",
                "فصل 3: بردارها",
            ],
        },

        "تجربی": {
            "شیمی": [
                "فصل 1: مولکول‌ها در خدمت تندرستی",
                "فصل 2: آسایش و رفاه در سایه شیمی",
                "فصل 3: شیمی جلوه‌ای از هنر، زیبایی و ماندگاری",
                "فصل 4: شیمی، راهی به سوی آینده روشن‌تر",
            ],
            "ریاضی": [
                "فصل 1: تابع",
                "فصل 2: مثلثات",
                "فصل 3: حد بی‌نهایت، حد در بی‌نهایت",
                "فصل 4: مشتق",
                "فصل 5: کاربرد مشتق",
                "فصل 6: هندسه",
                "فصل 7: احتمال",
            ],
            "فیزیک": [
                "فصل 1: حرکت بر خط راست",
                "فصل 2: دینامیک",
                "فصل 3: نوسان و امواج",
                "فصل 4: آشنایی با فیزیک اتمی و فیزیک هسته‌ای",
            ],
            "زیست شناسی": [
                "فصل 1: مولکول‌های اطلاعاتی",
                "فصل 2: جریان اطلاعات در یاخته",
                "فصل 3: انتقال اطلاعات در نسل‌ها",
                "فصل 4: تغییر در اطلاعات وراثتی",
                "فصل 5: از ماده به انرژی",
                "فصل 6: از انرژی به ماده",
                "فصل 7: فناوری‌های نوین زیستی",
                "فصل 8: رفتارهای جانوران",
            ],
        },

        "انسانی": {
            "عربی": [
                "درس 1",
                "درس 2",
                "درس 3",
                "درس 4",
                "درس 5",
            ],
            "تاریخ": [
                "درس 1: تاریخ‌نگاری و منابع دوره معاصر",
                "درس 2: ایران و جهان در آستانه دوره معاصر",
                "درس 3: سیاست و حکومت در عصر قاجار (از آغاز تا پایان سلطنت ناصرالدین شاه)",
                "درس 4: اوضاع اجتماعی، اقتصادی و فرهنگی عصر قاجار",
                "درس 5: نهضت مشروطه ایران",
                "درس 6: جنگ جهانی اول و ایران",
                "درس 7: ایران در دوره حکومت رضاشاه",
                "درس 8: جنگ جهانی دوم و جهان پس از آن",
                "درس 9: نهضت ملی شدن صنعت نفت ایران",
                "درس 10: انقلاب اسلامی",
                "درس 11: استقرار و تثبیت نظام جمهوری اسلامی",
                "درس 12: جنگ تحمیلی و دفاع مقدس",
            ],
            "علوم و فنون": [

                "درس 1: تاریخ ادبیات قرن‌های 12 و 13",
                "درس 2: پایه‌های آوایی ناهمسان",
                "درس 3: مراعات نظیر، تلمیح و تضمین",
                "درس 4: سبک‌شناسی قرن‌های 12 و 13",
                "درس 5: اختیارات شاعری (1): زبانی",
                "درس 6: لف و نشر، تضاد و متناقض‌نما",
                "درس 7: تاریخ ادبیات قرن 14",
                "درس 8: اختیارات شاعری (2): زبانی",
                "درس 9: اغراق، ایهام، ایهام تناسب",
                "درس 10: سبک‌شناسی دوره معاصر و انقلاب اسلامی",
                "درس 11: وزن در شعر نیمایی",
                "درس 12: حسن تعلیل، حسن آمیزی و اسلوب معادله",
            ],



            "جامعه شناسی": [
                "درس 1: ذخیره دانشی",
                "درس 2: علوم اجتماعی",
                "درس 3: نظم اجتماعی",
                "درس 4: کنش اجتماعی",
                "درس 5: معنای زندگی",
                "درس 6: قدرت اجتماعی",
                "درس 7: نابرابری اجتماعی",
                "درس 8: سیاست هویت",
                "درس 9: پیشینه علوم اجتماعی در جهان اسلام",
                "درس 10: افق علوم اجتماعی در جهان اسلام",
            ],

            "فلسفه": [
                "درس 1: هستی و چیستی",
                "درس 2: جهان ممکنات",
                "درس 3: جهان علی و معلولی",
                "درس 4: کدام تصویر از جهان؟",
                "درس 5: خدا در فلسفه (1)",
                "درس 6: خدا در فلسفه (2)",
                "درس 7: عقل در فلسفه (1)",
                "درس 8: عقل در فلسفه (2)",
                "درس 9: آغاز فلسفه در جهان اسلام",
                "درس 10: دوره میانی",
                "درس 11: دوران متأخر",
                "درس 12: حکمت معاصر",
            ],

            "جغرافیا": [
                "درس 1: شهرها و روستاها",
                "درس 2: مدیریت شهر و روستا",
                "درس 3: ویژگی‌ها و انواع شیوه‌های حمل و نقل",
                "درس 4: مدیریت حمل و نقل",
                "درس 5: ویژگی‌ها و انواع مخاطرات طبیعی",
                "درس 6: مدیریت مخاطرات طبیعی",
            ],

            "ریاضی": [
                "فصل 1:آمار و احتمالات",
                "فصل 2:الگو های خطی",
                "فصل3:الگو های غیر خطی",
            ]
        },
    },
}



def qb_kb_subjects(grade: str, major: str):
    return ReplyKeyboardMarkup(QB_SUBJECTS[grade][major], resize_keyboard=True, one_time_keyboard=True)


def qb_valid_subjects_set(grade: str, major: str):
    return {s for row in QB_SUBJECTS[grade][major] for s in row if s not in (BTN_BACK, BTN_HOME)}


def qb_kb_chapters(chapters: list[str]):
    rows = []
    for i in range(0, len(chapters), 2):
        rows.append(chapters[i:i + 2])
    rows.append([BTN_BACK])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True)


def qb_get_chapters(grade: str, major: str, subject: str):
    return QB_CHAPTERS.get(grade, {}).get(major, {}).get(subject)


# --- Hashtag helpers (ASCII only) ---
QB_GRADE_CODE = {"دهم": "10", "یازدهم": "11", "دوازدهم": "12"}
QB_MAJOR_CODE = {"ریاضی": "M", "تجربی": "T", "انسانی": "H"}

QB_SUBJECT_CODE = {
    "حسابان": "HESABAN",
    "ریاضیات گسسته": "DISCRETE",
    "فیزیک": "PHYSICS",
    "هندسه": "GEOMETRY",
    "شیمی": "CHEMISTRY",
    "دروس عمومی": "OMUMI",
    "زیست شناسی": "BIOLOGY",
    "ریاضی": "MATH",
    "آمار و احتمال": "STAT",
    "علوم و فنون": "OLoomFonon",
    "جامعه شناسی": "JAME",
    "تاریخ": "TARIKH",
    "جغرافیا": "JOGH",
    "فلسفه": "FALSAFE",
    "روانشناسی": "RAVAN",
    "منطق": "MANTEGH",
    "اقتصاد": "EGHTESAD",
    "عربی": "ARABIC",
}

QB_CHAPTER_NO_RE = re.compile(r"(?:فصل|درس)\s*(\d+)", re.UNICODE)


def qb_make_tag(grade: str, major: str, subject: str, chapter_label: str) -> str:
    """خروجی: مثل QB_12_M_HESABAN_01 (بدون #)\n\nchapter_label مثال: 'فصل 1: تابع'"""
    g = QB_GRADE_CODE.get(grade, "00")
    m = QB_MAJOR_CODE.get(major, "X")
    s = QB_SUBJECT_CODE.get(subject, "SUBJ")
    mm = QB_CHAPTER_NO_RE.search(chapter_label or "")
    chap_no = int(mm.group(1)) if mm else 0
    return f"QB_{g}_{m}_{s}_{chap_no:02d}"


QB_TAG_RE = re.compile(r"#(QB_[A-Za-z0-9_]+)")


def extract_qb_tags(text: str) -> list[str]:
    if not text:
        return []
    return QB_TAG_RE.findall(text)


def extract_reg_tags(text: str) -> list[str]:
    if not text:
        return []
    return REG_TAG_RE.findall(text)


# ----------------- Start / Action -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()

    if not await require_membership(update, context):
        return STATE_ACTION

    chat_id = update.effective_chat.id
    await update.message.reply_text(
        "سلام! 👋\nیکی از گزینه های زیر را انتخاب کنید:",
        reply_markup=main_menu_keyboard(chat_id),
    )
    return STATE_ACTION



# ----------------- Registration / Renewal Flow (Daily Plan) -----------------
REG_TAG_START_1 = "REG_START_1"
REG_TAG_PLAN_NORMAL = "REG_PLAN_NORMAL"
REG_TAG_PLAN_SPECIAL = "REG_PLAN_SPECIAL"
REG_TAG_PAYMENT_NORMAL = "REG_PAYMENT_NORMAL"
REG_TAG_PAYMENT_SPECIAL = "REG_PAYMENT_SPECIAL"
REG_TAG_PAYMENT_CARD_DEPRECATED = "REG_PAYMENT_CARD"  # (اختیاری/قدیمی)
REG_TAG_SUCCESS = "REG_SUCCESS"
REG_TAG_EXPIRE_NOTICE = "REG_EXPIRE_NOTICE"
REG_TAG_EXPIRE_REMINDER_3D = "REG_EXPIRE_REMINDER_3D"
REG_TAG_RENEW_SUCCESS = "#REG_RENEW_SUCCESS"

def reg_kb_plans() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[BTN_PLAN_NORMAL, BTN_PLAN_SPECIAL], [BTN_BACK]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def reg_kb_fields() -> ReplyKeyboardMarkup:
    # ترتیب موردنظر شما: ریاضی / تجربی / انسانی
    return ReplyKeyboardMarkup(
        [["ریاضی", "تجربی", "انسانی"], [BTN_BACK]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def reg_kb_grades() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [GRADES,  [BTN_BACK]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def reg_kb_phone() -> ReplyKeyboardMarkup:
    # کاربر میتواند هم شماره را دستی تایپ کند، هم با این دکمه Contact را ارسال کند.
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📱 ارسال شماره تماس (خودکار)", request_contact=True)], [BTN_BACK]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def reg_kb_nav() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([[BTN_BACK, BTN_HOME]], resize_keyboard=True)


def reg_inline_continue(user_chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("ادامه ثبت نام", callback_data=f"REG_CONT|{user_chat_id}")]]
    )


def reg_inline_receipt(user_chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("ارسال رسید پرداخت", callback_data=f"REG_RECEIPT|{user_chat_id}")]]
    )


def reg_inline_approve(user_chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ تایید", callback_data=f"REG_APPROVE|{user_chat_id}")]]
    )


def _normalize_iran_phone(raw: str) -> str | None:
    """پذیرش ورودی دستی یا Contact تلگرام با فرمتهای رایج ایران.

    قبول میکند:
    - 09xxxxxxxxx  (۱۱ رقم)
    - +98xxxxxxxxxx (کد کشور + ۱۰ رقم)
    - 98xxxxxxxxxx  (بدون +)
    - 9xxxxxxxxx    (بدون صفر اول؛ خروجی 09... میشود)
    - 0098xxxxxxxxxx
    """
    s = to_en_digits(raw or "").strip()
    s = s.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")

    # 00... -> +...
    if s.startswith("00"):
        s = "+" + s[2:]

    digits = re.sub(r"\D", "", s)

    # اگر 00 در digits باقی مانده باشد
    if digits.startswith("00"):
        digits = digits[2:]

    if re.fullmatch(r"09\d{9}", digits):
        return digits

    # شماره بدون صفر اول که بعضی وقتها از contact میآید
    if re.fullmatch(r"9\d{9}", digits):
        return "0" + digits

    # 98 + 10 digits
    if re.fullmatch(r"98\d{10}", digits):
        return "+" + digits

    # حالت +98... (با +) را هم قبول کنیم
    if re.fullmatch(r"\+98\d{10}", s):
        return s

    return None


async def _copy_reg_message(context: ContextTypes.DEFAULT_TYPE, *, to_chat_id: int, tag: str,
                            reply_markup: InlineKeyboardMarkup | None = None) -> None:
    row = get_registration_message(tag)
    if row:
        try:
            await context.bot.copy_message(
                chat_id=to_chat_id,
                from_chat_id=row["channel_chat_id"],
                message_id=row["channel_message_id"],
                reply_markup=reply_markup,
            )
            return
        except Exception as e:
            logger.exception("copy reg message failed tag=%s: %s", tag, e)

    # fallback اگر پیام کانال هنوز ذخیره نشده باشد
    if tag in (REG_TAG_PAYMENT_NORMAL, REG_TAG_PAYMENT_SPECIAL, REG_TAG_PAYMENT_CARD_DEPRECATED):
        # fallback؛ بهتر است پیامهای پرداخت را داخل کانال با هشتگهای مربوطه ثبت کنید
        await context.bot.send_message(
            chat_id=to_chat_id,
            text=f"💳 شماره کارت برای پرداخت:\n{CARD_NUMBER}\n\n⚠️ پیام پرداخت برای #{tag} در کانال ثبت نشده است.",
            reply_markup=reply_markup,
        )
    else:
        await context.bot.send_message(
            chat_id=to_chat_id,
            text=f"⚠️ پیام کانال برای #{tag} هنوز ثبت نشده است.",
            reply_markup=reply_markup,
        )


async def reg_send_summary_and_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """After collecting required info, show summary and payment message (plan-specific)."""
    chat_id = update.effective_chat.id

    plan = (context.user_data.get("reg_plan") or "").strip()
    is_renew = bool(context.user_data.get("reg_is_renew", False))

    full_name = (context.user_data.get("reg_full_name") or "").strip() or "نامشخص"
    national_id = (context.user_data.get("reg_national_id") or "").strip() or "نامشخص"
    field = (context.user_data.get("reg_field") or "").strip() or "نامشخص"
    grade = (context.user_data.get("reg_grade") or "").strip() or "نامشخص"
    phone = (context.user_data.get("reg_phone") or "").strip() or "نامشخص"

    # ذخیره موقت درخواست برای دوام بعد از ریاستارت (همیشه یک رکورد داشته باشیم)
    try:
        upsert_registration_request(
            tg_chat_id=chat_id,
            full_name=full_name,
            national_id=national_id,
            field=field,
            grade=grade,
            phone=phone,
            plan=plan,
            is_renew=is_renew,
        )
    except Exception as e:
        logger.exception("upsert registration_request failed: %s", e)

    header = "🔁 درخواست تمدید یک ماهه" if is_renew else "✅ درخواست ثبت نام برنامه روزانه"
    summary = (
        f"{header}\n\n"
        f"👤 نام و نام خانوادگی: {full_name}\n"
        f"🆔 کد ملی: {national_id}\n"
        f"📚 رشته: {field}\n"
        f"🏫 پایه: {grade}\n"
        f"📞 شماره تماس: {phone}\n"
        f"🧩 طرح: {plan}\n\n"
        "حالا طبق پیام پرداخت زیر، واریز را انجام دهید و سپس روی «ارسال رسید پرداخت» بزنید."
    )
    await update.message.reply_text(summary, reply_markup=reg_kb_nav())

    # پیام پرداخت از کانال + دکمه ارسال رسید (بر اساس طرح)
    payment_tag = (REG_TAG_PAYMENT_NORMAL if plan == BTN_PLAN_NORMAL else REG_TAG_PAYMENT_SPECIAL)
    # اگر پیام پلن هنوز در کانال ثبت نشده بود، به تگ قدیمی (اختیاری) برگرد
    if not get_registration_message(payment_tag) and get_registration_message(REG_TAG_PAYMENT_CARD_DEPRECATED):
        payment_tag = REG_TAG_PAYMENT_CARD_DEPRECATED

    await _copy_reg_message(
        context,
        to_chat_id=chat_id,
        tag=payment_tag,
        reply_markup=reg_inline_receipt(chat_id),
    )
    return STATE_REG_PAYMENT_WAIT_BTN


async def reg_start(update: Update, context: ContextTypes.DEFAULT_TYPE, *, is_renew: bool) -> int:
    if not await require_membership(update, context):
        return STATE_ACTION

    # فقط دادههای رجیستر را ریست میکنیم
    context.user_data.pop("reg_plan", None)
    context.user_data.pop("reg_full_name", None)
    context.user_data.pop("reg_national_id", None)
    context.user_data.pop("reg_field", None)
    context.user_data.pop("reg_grade", None)
    context.user_data.pop("reg_phone", None)

    context.user_data["reg_is_renew"] = bool(is_renew)

    chat_id = update.effective_chat.id

    # برای تمدید: پیام شروع (#REG_START_1) نمایش داده نشود
    if not is_renew:
        await _copy_reg_message(context, to_chat_id=chat_id, tag=REG_TAG_START_1)

    await update.message.reply_text(
        "یکی از طرح ها را انتخاب کنید:",
        reply_markup=reg_kb_plans(),
    )
    return STATE_REG_PLAN_SELECT



async def reg_pick_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    text_in = (update.message.text or "").strip()

    if text_in in (BTN_HOME, BTN_BACK):
        return await go_home(update, context)

    if text_in not in (BTN_PLAN_NORMAL, BTN_PLAN_SPECIAL):
        await update.message.reply_text("لطفاً یکی از طرح ها را انتخاب کنید:", reply_markup=reg_kb_plans())
        return STATE_REG_PLAN_SELECT

    context.user_data["reg_plan"] = text_in

    tag = REG_TAG_PLAN_NORMAL if text_in == BTN_PLAN_NORMAL else REG_TAG_PLAN_SPECIAL
    await _copy_reg_message(context, to_chat_id=chat_id, tag=tag, reply_markup=reg_inline_continue(chat_id))
    return STATE_REG_PLAN_SELECT


async def reg_on_continue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q:
        return STATE_REG_PLAN_SELECT
    await q.answer()

    try:
        _, uid_str = (q.data or "").split("|", 1)
        uid = int(uid_str)
    except Exception:
        return STATE_REG_PLAN_SELECT

    if q.from_user and q.from_user.id != uid:
        await q.answer("این دکمه برای شما نیست.", show_alert=True)
        return STATE_REG_PLAN_SELECT

    context.user_data["reg_user_id"] = uid

    # اگر تمدید است، فقط کد ملی میگیریم
    if bool(context.user_data.get("reg_is_renew", False)):
        await q.message.reply_text("🆔 لطفاً کد ملی ۱۰ رقمی خود را وارد کنید:", reply_markup=reg_kb_nav())
        return STATE_REG_NID

    # ثبتنام جدید: نام و نام خانوادگی + سایر اطلاعات
    await q.message.reply_text("لطفاً نام و نام خانوادگی خود را وارد کنید:", reply_markup=reg_kb_nav())
    return STATE_REG_NAME



async def reg_get_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text_in = (update.message.text or "").strip()

    if text_in == BTN_HOME:
        return await go_home(update, context)

    if text_in == BTN_BACK:
        await update.message.reply_text("یکی از طرح ها را انتخاب کنید:", reply_markup=reg_kb_plans())
        return STATE_REG_PLAN_SELECT

    if len(text_in) < 3:
        await update.message.reply_text("نام و نام خانوادگی معتبر نیست. دوباره وارد کنید:", reply_markup=reg_kb_nav())
        return STATE_REG_NAME

    context.user_data["reg_full_name"] = text_in
    await update.message.reply_text("کد ملی ۱۰ رقمی خود را وارد کنید:", reply_markup=reg_kb_nav())
    return STATE_REG_NID


async def reg_get_nid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text_in = (update.message.text or "").strip()

    if text_in == BTN_HOME:
        return await go_home(update, context)

    is_renew = bool(context.user_data.get("reg_is_renew", False))

    if text_in == BTN_BACK:
        if is_renew:
            await update.message.reply_text("یکی از طرح ها را انتخاب کنید:", reply_markup=reg_kb_plans())
            return STATE_REG_PLAN_SELECT
        await update.message.reply_text("لطفاً نام و نام خانوادگی خود را وارد کنید:", reply_markup=reg_kb_nav())
        return STATE_REG_NAME

    nid = normalize_national_id(text_in)
    if not re.fullmatch(r"\d{10}", nid):
        await update.message.reply_text("❌ کد ملی نامعتبر است. لطفاً فقط یک کد ملی ۱۰ رقمی وارد کنید:", reply_markup=reg_kb_nav())
        return STATE_REG_NID

    context.user_data["reg_national_id"] = nid

    # تمدید: فقط با کد ملی، اطلاعات از دیتابیس خوانده میشود و مستقیم به پرداخت میرویم
    if is_renew:
        stu = None
        try:
            stu = _get_daily_student_by_nid_full(nid)
        except Exception as e:
            logger.exception("_get_daily_student_by_nid_full failed: %s", e)

        if not stu:
            await update.message.reply_text(
                "❌ این کد ملی در سیستم پیدا نشد.\n"
                "اگر قصد شروع دارید، لطفاً «✅ ثبت نام برنامه روزانه» را بزنید.",
                reply_markup=main_menu_keyboard(update.effective_chat.id),
            )
            return STATE_ACTION

        # پر کردن دادهها از DB (بدون سوال اضافه)
        context.user_data["reg_full_name"] = (stu.get("full_name") or "").strip()
        context.user_data["reg_field"] = (stu.get("field") or "").strip()
        context.user_data["reg_grade"] = (stu.get("grade") or "").strip()
        context.user_data["reg_phone"] = (stu.get("phone") or "نامشخص").strip()

        return await reg_send_summary_and_payment(update, context)

    # ثبتنام جدید: ادامه مراحل
    await update.message.reply_text("رشته خود را انتخاب کنید:", reply_markup=reg_kb_fields())
    return STATE_REG_FIELD



async def reg_get_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text_in = (update.message.text or "").strip()

    if text_in == BTN_HOME:
        return await go_home(update, context)

    if text_in == BTN_BACK:
        await update.message.reply_text("کد ملی ۱۰ رقمی خود را وارد کنید:", reply_markup=reg_kb_nav())
        return STATE_REG_NID

    if text_in not in FIELDS:
        await update.message.reply_text("لطفاً یکی از رشته ها را انتخاب کنید:", reply_markup=reg_kb_fields())
        return STATE_REG_FIELD

    context.user_data["reg_field"] = text_in
    await update.message.reply_text("پایه خود را انتخاب کنید:", reply_markup=reg_kb_grades())
    return STATE_REG_GRADE


async def reg_get_grade(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text_in = (update.message.text or "").strip()

    if text_in == BTN_HOME:
        return await go_home(update, context)

    if text_in == BTN_BACK:
        await update.message.reply_text("رشته خود را انتخاب کنید:", reply_markup=reg_kb_fields())
        return STATE_REG_FIELD

    if text_in not in GRADES:
        await update.message.reply_text("لطفاً یکی از پایه ها را انتخاب کنید:", reply_markup=reg_kb_grades())
        return STATE_REG_GRADE

    context.user_data["reg_grade"] = text_in
    await update.message.reply_text(
        "📞 لطفاً شماره تماس را ارسال کنید.\n"
        "میتوانید روی دکمه «ارسال شماره تماس (خودکار)» بزنید یا شماره را دستی تایپ کنید (09xxxxxxxxx یا +98xxxxxxxxxx):",
        reply_markup=reg_kb_phone(),
    )
    return STATE_REG_PHONE


async def reg_get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # هم ورودی دستی (text) و هم ارسال خودکار شماره (contact) را قبول میکنیم
    raw_in = ""
    if update.message.contact:
        raw_in = update.message.contact.phone_number or ""
    else:
        raw_in = (update.message.text or "").strip()

    if raw_in == BTN_HOME:
        return await go_home(update, context)

    if raw_in == BTN_BACK:
        await update.message.reply_text("پایه خود را انتخاب کنید:", reply_markup=reg_kb_grades())
        return STATE_REG_GRADE

    if not raw_in:
        await update.message.reply_text(
            "لطفاً شماره تماس را ارسال کنید یا دستی تایپ کنید:",
            reply_markup=reg_kb_phone(),
        )
        return STATE_REG_PHONE

    phone = _normalize_iran_phone(raw_in)
    if not phone:
        await update.message.reply_text(
            "❌ شماره تماس نامعتبر است. مثل 09123456789 یا +989123456789 وارد کنید:",
            reply_markup=reg_kb_phone(),
        )
        return STATE_REG_PHONE

    context.user_data["reg_phone"] = phone

    return await reg_send_summary_and_payment(update, context)



async def reg_payment_nav(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """در مرحله پرداخت، کاربر ممکن است دکمههای بازگشت/خانه را بزند."""
    text_in = (update.message.text or "").strip()

    if text_in == BTN_HOME:
        return await go_home(update, context)

    if text_in == BTN_BACK:
        # برای تمدید: برگشت به کد ملی
        if bool(context.user_data.get("reg_is_renew", False)):
            await update.message.reply_text("🆔 لطفاً کد ملی ۱۰ رقمی خود را وارد کنید:", reply_markup=reg_kb_nav())
            return STATE_REG_NID

        # ثبتنام جدید: برگشت به اصلاح شماره تماس
        await update.message.reply_text(
            "📞 لطفاً شماره تماس را ارسال کنید یا دستی تایپ کنید:",
            reply_markup=reg_kb_phone(),
        )
        return STATE_REG_PHONE

    await update.message.reply_text(
        "برای ادامه، روی دکمه «ارسال رسید پرداخت» زیر پیام پرداخت بزنید.",
        reply_markup=reg_kb_nav(),
    )
    return STATE_REG_PAYMENT_WAIT_BTN



async def reg_on_receipt_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q:
        return STATE_REG_PAYMENT_WAIT_BTN
    await q.answer()

    try:
        _, uid_str = (q.data or "").split("|", 1)
        uid = int(uid_str)
    except Exception:
        return STATE_REG_PAYMENT_WAIT_BTN

    if q.from_user and q.from_user.id != uid:
        await q.answer("این دکمه برای شما نیست.", show_alert=True)
        return STATE_REG_PAYMENT_WAIT_BTN

    await q.message.reply_text("📎 لطفاً عکس یا فایل رسید پرداخت را همینجا ارسال کنید.", reply_markup=reg_kb_nav())
    return STATE_REG_WAIT_RECEIPT


async def reg_receive_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id

    # ناوبری
    if update.message.text:
        t = update.message.text.strip()
        if t == BTN_HOME:
            return await go_home(update, context)
        if t == BTN_BACK:
            # برگشت به مرحله پرداخت (برای اینکه دوباره روی «ارسال رسید پرداخت» بزند)
            plan = context.user_data.get("reg_plan", "")
            if not plan:
                req = get_registration_request(chat_id)
                if req:
                    plan = req.get("plan", "")
                    # برای اینکه stateهای بعدی هم کار کنند
                    context.user_data["reg_plan"] = plan
                    context.user_data["reg_full_name"] = req.get("full_name", "")
                    context.user_data["reg_national_id"] = req.get("national_id", "")
                    context.user_data["reg_field"] = req.get("field", "")
                    context.user_data["reg_grade"] = req.get("grade", "")
                    context.user_data["reg_phone"] = req.get("phone", "")
                    context.user_data["reg_is_renew"] = bool(req.get("is_renew", False))

            payment_tag = (REG_TAG_PAYMENT_NORMAL if plan == BTN_PLAN_NORMAL else REG_TAG_PAYMENT_SPECIAL)
            if not get_registration_message(payment_tag) and get_registration_message(REG_TAG_PAYMENT_CARD_DEPRECATED):
                payment_tag = REG_TAG_PAYMENT_CARD_DEPRECATED

            await _copy_reg_message(
                context,
                to_chat_id=chat_id,
                tag=payment_tag,
                reply_markup=reg_inline_receipt(chat_id),
            )
            await update.message.reply_text("برای ارسال رسید، روی دکمه «ارسال رسید پرداخت» بزنید.", reply_markup=reg_kb_nav())
            return STATE_REG_PAYMENT_WAIT_BTN

    photo = update.message.photo[-1] if update.message.photo else None
    doc = update.message.document if update.message.document else None
    if not photo and not doc:
        await update.message.reply_text("لطفاً فقط عکس یا فایل رسید ارسال کنید.", reply_markup=reg_kb_nav())
        return STATE_REG_WAIT_RECEIPT

    full_name = context.user_data.get("reg_full_name", "")
    national_id = context.user_data.get("reg_national_id", "")
    field = context.user_data.get("reg_field", "")
    grade = context.user_data.get("reg_grade", "")
    phone = context.user_data.get("reg_phone", "")
    plan = context.user_data.get("reg_plan", "")
    is_renew = bool(context.user_data.get("reg_is_renew", False))

    # اگر به هر دلیلی user_data خالی بود، از جدول درخواستها بازیابی کنیم
    if not (full_name and national_id and field and grade and phone and plan):
        req = get_registration_request(chat_id)
        if req:
            full_name = full_name or req.get("full_name", "")
            national_id = national_id or req.get("national_id", "")
            field = field or req.get("field", "")
            grade = grade or req.get("grade", "")
            phone = phone or req.get("phone", "")
            plan = plan or req.get("plan", "")
            is_renew = bool(req.get("is_renew", is_renew))

    # دوباره در DB ذخیره میکنیم که ادمین همیشه داده را داشته باشد
    try:
        upsert_registration_request(
            tg_chat_id=chat_id,
            full_name=full_name,
            national_id=national_id,
            field=field,
            grade=grade,
            phone=phone,
            plan=plan,
            is_renew=is_renew,
        )
    except Exception as e:
        logger.exception("upsert registration_request failed (receipt): %s", e)

    caption = (
        "🧾 رسید پرداخت\n"
        f"{'🔁 تمدید یکماهه' if is_renew else '✅ ثبتنام جدید'}\n\n"
        f"👤 نام و نام خانوادگی: {full_name}\n"
        f"🆔 کد ملی: {national_id}\n"
        f"📚 رشته: {field}\n"
        f"🏫 پایه: {grade}\n"
        f"📞 شماره تماس: {phone}\n"
        f"🧩 طرح: {plan}\n\n"
        f"User Chat ID: {chat_id}"
    )

    markup = reg_inline_approve(chat_id)

    for admin_id in ADMIN_CHAT_IDS:
        try:
            if photo:
                await context.bot.send_photo(
                    chat_id=admin_id,
                    photo=photo.file_id,
                    caption=caption,
                    reply_markup=markup,
                )
            else:
                await context.bot.send_document(
                    chat_id=admin_id,
                    document=doc.file_id,
                    caption=caption,
                    reply_markup=markup,
                )
        except Exception as e:
            logger.exception("send receipt to admin failed admin=%s: %s", admin_id, e)

    await update.message.reply_text(
        "✅ رسید شما برای ادمین ارسال شد. بعد از تایید، پیام موفقیت برای شما ارسال میشود.",
        reply_markup=main_menu_keyboard(chat_id),
    )

    # پاکسازی موقت (درخواست داخل DB هست)
    for k in ["reg_plan", "reg_full_name", "reg_national_id", "reg_field", "reg_grade", "reg_phone", "reg_is_renew"]:
        context.user_data.pop(k, None)

    return STATE_ACTION


async def on_reg_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    await q.answer()

    if q.from_user and q.from_user.id not in ADMIN_CHAT_IDS:
        await q.answer("شما اجازه تایید ندارید.", show_alert=True)
        return

    try:
        _, uid_str = (q.data or "").split("|", 1)
        user_chat_id = int(uid_str)
    except Exception:
        await q.answer("داده تایید نامعتبر است.", show_alert=True)
        return

    req = get_registration_request(user_chat_id)
    if not req:
        await q.answer("درخواست در دیتابیس پیدا نشد (ممکن است ربات ریاستارت شده باشد).", show_alert=True)
        return

    # اطمینان: اگر این tg_chat_id روی رکورد دیگری است، خالیاش کنیم تا conflict نخوریم
    try:
        sql_clear = "UPDATE daily_students SET tg_chat_id=NULL WHERE tg_chat_id=%s AND {nid_sql}<>%s;".format(
            nid_sql=NID_TRANSLATE_SQL
        )
        with get_db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql_clear, (user_chat_id, req["national_id"]))
            conn.commit()
    except Exception:
        pass

    # upsert/activate/extend
    try:
        activation_code = admin_create_activation_code()

        # اگر تمدید باشد: به expires_at فعلی 30 روز اضافه کن (حتی اگر هنوز اعتبار دارد)
        # اگر ثبت‌نام باشد: از NOW حساب کن
        sql_upsert = f"""
        INSERT INTO daily_students (
            national_id, full_name, field, grade, phone, plan,
            activation_code, tg_chat_id, is_active, activated_at,
            expires_at, reminder_3d_sent_at
        )
        VALUES (
            %s, %s, %s, %s, %s, %s,
            %s, %s, TRUE, NOW(),
            NOW() + INTERVAL '30 days', NULL
        )
        ON CONFLICT (national_id) DO UPDATE SET
            full_name = EXCLUDED.full_name,
            field = EXCLUDED.field,
            grade = EXCLUDED.grade,
            phone = EXCLUDED.phone,
            plan = EXCLUDED.plan,
            tg_chat_id = EXCLUDED.tg_chat_id,
            is_active = TRUE,
            activated_at = NOW(),
            expires_at = CASE
                WHEN %s THEN COALESCE(daily_students.expires_at, NOW()) + INTERVAL '30 days'
                ELSE NOW() + INTERVAL '30 days'
            END,
            reminder_3d_sent_at = NULL;
        """

        with get_db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql_upsert,
                    (
                        req["national_id"],
                        req["full_name"],
                        req["field"],
                        req["grade"],
                        req["phone"],
                        req["plan"],
                        activation_code,
                        user_chat_id,
                        bool(req.get("is_renew", False)),  # <-- مهم: تعیین تمدید یا ثبت‌نام
                    ),
                )
            conn.commit()
    except Exception as e:
        logger.exception("approve upsert failed: %s", e)
        await q.answer("خطا در دیتابیس.", show_alert=True)
        return

    # حذف درخواست
    try:
        delete_registration_request(user_chat_id)
    except Exception:
        pass

    # پیام موفقیت برای کاربر (از کانال)
    # پیام موفقیت برای کاربر
    if req.get("is_renew"):
        await context.bot.send_message(
            chat_id=user_chat_id,
            text="✅ تمدید شما به مدت ۳۰ روز با موفقیت انجام شد"
        )
    else:
        try:
            await _copy_reg_message(context, to_chat_id=user_chat_id, tag=REG_TAG_SUCCESS)
        except Exception:
            await context.bot.send_message(chat_id=user_chat_id, text="✅ ثبت‌نام شما با موفقیت انجام شد.")
    try:
        await context.bot.send_message(
            chat_id=user_chat_id,
            text="🏠 بازگشت به منوی اصلی.",
            reply_markup=main_menu_keyboard(user_chat_id),
        )
    except Exception:
        pass

    # بهروزرسانی پیام ادمین (اختیاری)
    try:
        if q.message:
            if q.message.caption:
                await q.edit_message_caption(caption=q.message.caption + "\n\n✅ تایید شد", reply_markup=None)
            else:
                await q.edit_message_text(text="✅ تایید شد", reply_markup=None)
    except Exception:
        pass

async def registration_expiry_job(context: ContextTypes.DEFAULT_TYPE):
    """هر ساعت: expires_at را تنظیم میکند، منقضیها را غیرفعال میکند و بعد از ۵ روز حذف میکند."""
    # 1) برای فعالهایی که expires_at ندارند (مثلاً فعالسازی با کد)، expires_at را ست کنیم
    try:
        sql_fill = """
        UPDATE daily_students
        SET expires_at = activated_at + INTERVAL '30 days'
        WHERE is_active=TRUE AND expires_at IS NULL AND activated_at IS NOT NULL;
        """
        with get_db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql_fill)
            conn.commit()
    except Exception as e:
        logger.exception("expiry fill failed: %s", e)


    # 1.5) reminder: 3 روز مانده به پایان اعتبار (فقط یکبار در هر دوره)
    try:
        sql_remind3 = '''
        SELECT tg_chat_id
        FROM daily_students
        WHERE tg_chat_id IS NOT NULL
          AND is_active=TRUE
          AND expires_at IS NOT NULL
          AND expires_at > NOW()
          AND expires_at <= NOW() + INTERVAL '3 days'
          AND reminder_3d_sent_at IS NULL;
        '''
        with get_db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql_remind3)
                rows = cur.fetchall() or []
        for r in rows:
            uid = int(r["tg_chat_id"])
            sent_ok = False
            try:
                await _copy_reg_message(context, to_chat_id=uid, tag=REG_TAG_EXPIRE_REMINDER_3D)
                sent_ok = True
            except Exception:
                try:
                    await context.bot.send_message(
                        chat_id=uid,
                        text="⏰ فقط ۳ روز تا پایان اعتبار برنامه روزانه باقی مانده. برای جلوگیری از قطع دسترسی، همین امروز تمدید کنید.",
                    )
                    sent_ok = True
                except Exception:
                    sent_ok = False

            if sent_ok:
                try:
                    with get_db_conn() as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                "UPDATE daily_students SET reminder_3d_sent_at=NOW() WHERE tg_chat_id=%s AND is_active=TRUE;",
                                (uid,),
                            )
                        conn.commit()
                except Exception:
                    pass
    except Exception as e:
        logger.exception("remind 3d failed: %s", e)

    # 2) expire: is_active -> false و پیام هشدار
    try:
        sql_expired = """
        SELECT tg_chat_id
        FROM daily_students
        WHERE tg_chat_id IS NOT NULL
          AND is_active=TRUE
          AND expires_at IS NOT NULL
          AND expires_at <= NOW();
        """
        with get_db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql_expired)
                rows = cur.fetchall() or []
        for r in rows:
            uid = int(r["tg_chat_id"])
            try:
                with get_db_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute("UPDATE daily_students SET is_active=FALSE WHERE tg_chat_id=%s;", (uid,))
                    conn.commit()
            except Exception:
                pass

            try:
                await _copy_reg_message(context, to_chat_id=uid, tag=REG_TAG_EXPIRE_NOTICE)
            except Exception:
                try:
                    await context.bot.send_message(
                        chat_id=uid,
                        text="⏳ اعتبار یک ماهه شما تمام شد. لطفاً حداکثر تا ۵ روز دیگر تمدید کنید.",
                    )
                except Exception:
                    pass
    except Exception as e:
        logger.exception("expiry check failed: %s", e)

    # 3) delete after 5 days past expiry (فقط کسانی که expires_at دارند)
    try:
        sql_list = f"""
        SELECT
            tg_chat_id,
            full_name,
            {NID_TRANSLATE_SQL} AS national_id,
            field,
            grade,
            phone,
            plan
        FROM daily_students
        WHERE is_active=FALSE
          AND expires_at IS NOT NULL
          AND expires_at <= NOW() - INTERVAL '5 days';
        """
        with get_db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql_list)
                rows = cur.fetchall() or []

        # قبل از حذف، به ادمینها و کاربر اطلاع بده
        for r in rows:
            uid = r.get("tg_chat_id")
            full_name = r.get("full_name") or "نامشخص"
            national_id = r.get("national_id") or "نامشخص"
            field = r.get("field") or "نامشخص"
            grade = r.get("grade") or "نامشخص"
            phone = r.get("phone") or "نامشخص"
            plan = r.get("plan") or "نامشخص"

            admin_text = (
                "🗑️ حذف از طرح برنامه روزانه\n\n"
                f"👤 نام و نام خانوادگی: {full_name}\n"
                f"🆔 کد ملی: {national_id}\n"
                f"📚 رشته: {field}\n"
                f"🏫 پایه: {grade}\n"
                f"🧩 طرح: {plan}\n"
                f"📞 شماره تماس: {phone}\n"
                f"User Chat ID: {uid if uid is not None else 'NULL'}\n\n"
                "علت: عدم تمدید تا ۵ روز بعد از پایان اعتبار"
            )
            for admin_id in ADMIN_CHAT_IDS:
                try:
                    await context.bot.send_message(chat_id=admin_id, text=admin_text)
                except Exception:
                    pass

            if uid:
                try:
                    await context.bot.send_message(
                        chat_id=int(uid),
                        text=(
                            "❌ شما برای ماه جدید تمدید نکردید و امکان دریافت برنامه رو ندارید.\n"
                            "اگر میخواهید ادامه دهید لطفا دکمه ثبت نام برنامه روزانه رو بزنید."
                        ),
                        reply_markup=main_menu_keyboard(int(uid)),
                    )
                except Exception:
                    pass

        # سپس حذف واقعی از دیتابیس
        sql_del = """
        DELETE FROM daily_students
        WHERE is_active=FALSE
          AND expires_at IS NOT NULL
          AND expires_at <= NOW() - INTERVAL '5 days';
        """
        with get_db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql_del)
            conn.commit()
    except Exception as e:
        logger.exception("expiry delete failed: %s", e)


async def action_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()

    if not await require_membership(update, context):
        return STATE_ACTION

    if text == BTN_BACK:
        return await go_home(update, context)

    if text == BTN_HOME:
        return await go_home(update, context)

    if text == BTN_QBANK:
        # شروع بانک سوال
        context.user_data.pop("qb_grade", None)
        context.user_data.pop("qb_major", None)
        context.user_data.pop("qb_subject", None)
        context.user_data.pop("qb_chapter", None)
        await update.message.reply_text("پایه تحصیلیات رو انتخاب کن:", reply_markup=qb_kb_grades())
        return STATE_QB_GRADE

    # -------- admin buttons --------
    if text == BTN_ADMIN_ADD_STUDENT:
        if not is_admin(chat_id):
            await update.message.reply_text("این گزینه فقط برای ادمین فعال است.")
            return STATE_ACTION
        await update.message.reply_text("نام و نام خانوادگی دانش آموز را وارد کن:",
                                        reply_markup=ReplyKeyboardMarkup([[BTN_BACK]], resize_keyboard=True))
        return STATE_ADMIN_STU_NAME

    if text == BTN_ADMIN_REMOVE_STUDENT:
        if not is_admin(chat_id):
            await update.message.reply_text("این گزینه فقط برای ادمین فعال است.")
            return STATE_ACTION
        await update.message.reply_text("کد ملی دانش آموز را وارد کن تا حذف شود:",
                                        reply_markup=ReplyKeyboardMarkup([[BTN_BACK]], resize_keyboard=True))
        return STATE_ADMIN_REMOVE_NID

    if text == BTN_ADMIN_LIST_DAILY:
        if not is_admin(chat_id):
            await update.message.reply_text("این گزینه فقط برای ادمین فعال است.")
            return STATE_ACTION

        kb = [
            ["ریاضی", "تجربی", "انسانی"],
            ["هنر", "زبان"],
            ["همه دانشآموزان"],
            [BTN_BACK],
        ]

        await update.message.reply_text(
            "لیست دانش آموزان روزانه\n\nابتدا رشته را انتخاب کنید:",
            reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True, one_time_keyboard=True),
        )

        context.user_data["admin_list_page"] = 0
        context.user_data["admin_list_field"] = None
        context.user_data["admin_list_group"] = None

        return STATE_ADMIN_LIST_FIELD

    if text == BTN_ADMIN_EXTEND_DAILY:
        if not is_admin(chat_id):
            await update.message.reply_text("این گزینه فقط برای ادمین فعال است.")
            return STATE_ACTION
        await update.message.reply_text(
            "کد ملی دانش آموز را وارد کنید:",
            reply_markup=ReplyKeyboardMarkup([[BTN_BACK]], resize_keyboard=True),
        )
        context.user_data.pop("admin_extend_nid", None)
        return STATE_ADMIN_EXTEND_NID

    # -------- activation --------
    if text == BTN_ACTIVATE:
        await update.message.reply_text(
            "کد فعالسازی را وارد کن (مثال: A1B2C3D4):",
            reply_markup=ReplyKeyboardMarkup([[BTN_BACK]], resize_keyboard=True),
        )
        return STATE_ACTIVATION_CODE

    # -------- support --------
    if text == BTN_SUPPORT:
        await update.message.reply_text(
            "اگر سوال یا موردی هست میتوانید به آیدی @tahsilatik_C پیام دهید.",
            reply_markup=main_menu_keyboard(chat_id),
        )
        return STATE_ACTION

    # -------- countdown --------
    if text == BTN_CONCOOR_COUNTDOWN:
        kb = list(make_keyboard(COUNTDOWN_OPTIONS, columns=1).keyboard) + [[BTN_BACK]]
        await update.message.reply_text(
            "نوع کنکور خود را انتخاب کنید:",
            reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True, one_time_keyboard=True),
        )
        return STATE_COUNTDOWN_PICK

    # -------- free plan --------
    if text == BTN_FREE_PLAN:
        kb = list(make_keyboard(FIELDS, columns=3).keyboard) + [[BTN_BACK]]
        await update.message.reply_text(
            "برای پلن رایگان 🎁\nرشتهات رو انتخاب کن:",
            reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True, one_time_keyboard=True),
        )
        return STATE_FREE_FIELD

    if text == BTN_ACHIEVEMENTS:
        sent_any = False

        for i in range(1, 201):  # سقف زیاد، ولی ارسال فقط تا جایی که دیتابیس دارد
            row = get_free_content(field="ALL", grade="ALL", content_type="achievements", sort_order=i)
            if not row:
                break

            await context.bot.copy_message(
                chat_id=update.effective_chat.id,
                from_chat_id=row["channel_chat_id"],
                message_id=row["channel_message_id"],
            )
            sent_any = True

        if not sent_any:
            await update.message.reply_text("⚠️ هنوز عکسهای افتخارات در کانال ثبت نشده اند.")

        return await start(update, context)

    # -------- send question --------
    if text == BTN_SEND_QUESTION:
        # اگر فعال باشد: روزانه ۴ تا، اگر فعال نباشد: کلاً ۱ تا
        try:
            active = is_student_active(chat_id)
        except Exception as e:
            logger.exception("DB error is_student_active: %s", e)
            await update.message.reply_text("خطا در اتصال به دیتابیس. لطفاً بعداً تلاش کنید.")
            return STATE_ACTION

        try:
            if not active:
                if has_question_in_last_7_days(chat_id):
                    await update.message.reply_text(
                        "⚠️ شما در ۷ روز گذشته یک‌بار رفع اشکال ارسال کرده‌اید.\n"
                        "هفته بعد دوباره می‌توانید ارسال کنید.\n\n"
                        "✅ اگر در طرح برنامه روزانه ثبت‌نام کنید، محدودیت ندارید."
                    )
                    return STATE_ACTION
            # اگر active باشد: هیچ محدودیتی ندارد
        except Exception as e:
            logger.exception("DB error in weekly question limit: %s", e)
            await update.message.reply_text("خطا در دیتابیس. لطفاً بعداً تلاش کنید.")
            return STATE_ACTION

        kb = list(make_keyboard(FIELDS, columns=3).keyboard) + [[BTN_BACK]]
        await update.message.reply_text(
            "لطفا رشته تحصیلی خود را انتخاب کنید:",
            reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True, one_time_keyboard=True),
        )
        return STATE_FIELD

    # -------- advisor helper --------
    # if text == BTN_SEND_ANSWER and is_advisor(chat_id):
    #     await update.message.reply_text(
    #         "برای پاسخ، زیر همان سوال روی دکمه «پاسخ به این سوال ✅» بزنید.",
    #         reply_markup=ReplyKeyboardMarkup([[BTN_BACK]], resize_keyboard=True),
    #     )
    #     return STATE_ACTION

    # -------- order daily / renew (daily plan registration) --------
    if text == BTN_ORDER_DAILY:
        # ادمینها مثل قبل
        if chat_id in ADMIN_CHAT_IDS:
            await update.message.reply_text(
                "✅ درخواست شما ثبت شد.\n\n"
                "برای تکمیل ثبت نام و پرداخت، همین الان به منشی پیام بده:\n"
                "👉 @tahsilatik_C",
                reply_markup=main_menu_keyboard(chat_id),
            )
            context.user_data.clear()
            return STATE_ACTION

        return await reg_start(update, context, is_renew=False)

    if text == BTN_RENEW_MONTH:
        if chat_id in ADMIN_CHAT_IDS:
            await update.message.reply_text("این گزینه مخصوص کاربران است.", reply_markup=main_menu_keyboard(chat_id))
            return STATE_ACTION
        return await reg_start(update, context, is_renew=True)

    await update.message.reply_text("لطفاً یکی از دکمه ها را انتخاب کنید.")
    return STATE_ACTION


# ----------------- Activation flow -----------------

# ----------------- QBANK (Bank Soal) Flow -----------------
async def qb_grade_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()

    if text == BTN_HOME:
        return await go_home(update, context)

    if text == BTN_BACK:
        # از صفحه انتخاب پایه برمیگردیم منوی اصلی
        return await go_home(update, context)

    if text not in QB_GRADES:
        await update.message.reply_text("پایه نامعتبره. یکی از گزینه ها رو انتخاب کن:", reply_markup=qb_kb_grades())
        return STATE_QB_GRADE

    context.user_data["qb_grade"] = text
    await update.message.reply_text("رشته تحصیلی ات رو انتخاب کن:", reply_markup=qb_kb_majors())
    return STATE_QB_MAJOR


async def qb_major_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()

    if text == BTN_HOME:
        return await go_home(update, context)

    if text == BTN_BACK:
        await update.message.reply_text("پایه تحصیلیات رو دوباره انتخاب کن:", reply_markup=qb_kb_grades())
        return STATE_QB_GRADE

    if text not in QB_MAJORS:
        await update.message.reply_text("رشته نامعتبره. یکی از گزینه ها رو انتخاب کن:", reply_markup=qb_kb_majors())
        return STATE_QB_MAJOR

    context.user_data["qb_major"] = text
    grade = context.user_data["qb_grade"]
    await update.message.reply_text("درس مورد نظر رو انتخاب کن:", reply_markup=qb_kb_subjects(grade, text))
    return STATE_QB_SUBJECT


async def qb_subject_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    grade = context.user_data.get("qb_grade")
    major = context.user_data.get("qb_major")

    if text == BTN_HOME:
        return await go_home(update, context)

    if text == BTN_BACK:
        await update.message.reply_text("رشته رو دوباره انتخاب کن:", reply_markup=qb_kb_majors())
        return STATE_QB_MAJOR

    valid = qb_valid_subjects_set(grade, major)
    if text not in valid:
        await update.message.reply_text("لطفاً یکی از دکمه های درسها رو انتخاب کن:",
                                        reply_markup=qb_kb_subjects(grade, major))
        return STATE_QB_SUBJECT

    context.user_data["qb_subject"] = text

    chapters = qb_get_chapters(grade, major, text)
    if not chapters:
        await update.message.reply_text(
            "برای این درس هنوز بانک سوالی ثبت نشده است\n"
            ,
            reply_markup=qb_kb_subjects(grade, major)
        )
        return STATE_QB_SUBJECT

    await update.message.reply_text("فصل یا درس مورد نظر رو انتخاب کن:", reply_markup=qb_kb_chapters(chapters))
    return STATE_QB_CHAPTER


async def qb_chapter_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()

    grade = context.user_data.get("qb_grade")
    major = context.user_data.get("qb_major")
    subject = context.user_data.get("qb_subject")

    chapters = qb_get_chapters(grade, major, subject) or []

    # 🔙 بازگشت
    if text == BTN_BACK:
        await update.message.reply_text("درس رو دوباره انتخاب کن:", reply_markup=qb_kb_subjects(grade, major))
        return STATE_QB_SUBJECT

    # انتخاب معتبر فصل
    if text not in chapters:
        await update.message.reply_text(
            "لطفاً یکی از فصلها رو از دکمه ها انتخاب کن:",
            reply_markup=qb_kb_chapters(chapters)
        )
        return STATE_QB_CHAPTER

    # tag برای این فصل
    tag = qb_make_tag(grade, major, subject, text)  # بدون #
    context.user_data["qb_chapter"] = text

    # فایل ثبت شده؟
    rec = get_qbank_file(tag)
    if not rec:
        await update.message.reply_text(
            f"❌ بانک سوال مربوط به «{subject}» - «{text}» هنوز ثبت نشده است.\n\n"
            ,
            reply_markup=qb_kb_chapters(chapters)
        )
        return STATE_QB_CHAPTER

    # محدودیت استفاده: فقط برای کسانی که در طرح روزانه «فعال» نیستند
    user_chat_id = update.effective_chat.id
    is_active = is_student_active(user_chat_id)

    if (not is_active) and (not is_abedini(user_chat_id)):
        if qbank_used_in_last_7_days(user_chat_id):
            await update.message.reply_text(
                "⚠️ شما در ۷ روز گذشته یک‌بار از بانک سوال استفاده کرده‌اید.\n"
                "هفته بعد دوباره می‌توانید فایل دریافت کنید.\n\n"
                "✅ اگر در طرح برنامه روزانه ثبت‌نام کنید، محدودیت ندارید.",
                reply_markup=qb_kb_chapters(chapters),
            )
            return STATE_QB_CHAPTER

    # ارسال فایل
    try:
        await context.bot.copy_message(
            chat_id=user_chat_id,
            from_chat_id=rec["channel_chat_id"],
            message_id=rec["channel_message_id"],
        )

        # ✅ فقط بعد از ارسال موفق، شمارنده افزایش پیدا کند (فقط برای غیر فعالها)
        if not is_active:
            inc_qbank_usage_count(user_chat_id)

    except Exception as e:
        # اگر پیام از کانال پاک شده باشد، اینجا خطا میخورد
        logger.warning("copy_message failed for tag=%s: %s", tag, e)

        # پاکسازی رکورد (اختیاری)
        delete_qbank_file(tag)

        await update.message.reply_text(
            "⚠️ فایل این فصل داخل کانال پیدا نشد (احتمالاً پیام حذف شده).\n"
            f"لطفاً دوباره فایل رو در کانال با هشتگ #{tag} بفرست ✅",
            reply_markup=qb_kb_chapters(chapters)
        )
        return STATE_QB_CHAPTER

    await update.message.reply_text("اگر فصل دیگهای میخوای انتخاب کن 👇", reply_markup=qb_kb_chapters(chapters))
    return STATE_QB_CHAPTER


async def activation_receive_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if text == BTN_BACK:
        return await go_home(update, context)

    if text in (BTN_BACK, BTN_HOME):
        return await go_home(update, context)

    code = text.upper().replace(" ", "")
    if not re.fullmatch(r"[A-Z0-9]{6,12}", code):
        await update.message.reply_text("کد نامعتبره. دوباره کد فعالسازی را وارد کن:")
        return STATE_ACTIVATION_CODE

    try:
        status = activate_student_by_code(code=code, tg_chat_id=update.effective_chat.id)
    except Exception as e:
        logger.exception("Activate failed: %s", e)
        await update.message.reply_text("خطا در دیتابیس. لطفاً بعداً تلاش کن.")
        return await start(update, context)

    if status == "OK":
        await update.message.reply_text(
            "✅ فعالسازی با موفقیت انجام شد!\n از این پس برای استفاده از خدمات ربات محدودیتی ندارید"
            ,
            reply_markup=main_menu_keyboard(update.effective_chat.id),
        )
    elif status == "CODE_NOT_FOUND":
        await update.message.reply_text("❌ چنین کدی پیدا نشد. دوباره تلاش کن یا از ادمین کد صحیح بگیر.")
    elif status == "CODE_USED":
        await update.message.reply_text("❌ این کد قبلاً استفاده شده. اگر مشکل داری به ادمین پیام بده.")
    elif status == "CHAT_ALREADY_ACTIVE":
        await update.message.reply_text("✅ اکانت شما قبلاً فعال شده است.",
                                        reply_markup=main_menu_keyboard(update.effective_chat.id))
    else:
        await update.message.reply_text("خطای نامشخص. دوباره تلاش کن.")

    return STATE_ACTION


# ----------------- Admin: add daily student -----------------
async def admin_stu_get_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update.effective_chat.id):
        return await start(update, context)

    text = (update.message.text or "").strip()

    if text == BTN_BACK:
        return await start(update, context)
    if len(text) < 3:
        await update.message.reply_text("نام معتبر وارد کن:")
        return STATE_ADMIN_STU_NAME

    context.user_data["adm_stu_name"] = text
    await update.message.reply_text("کد ملی دانش آموز را وارد کن:")
    return STATE_ADMIN_STU_NID


async def admin_stu_get_nid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update.effective_chat.id):
        return await start(update, context)

    nid = normalize_national_id(update.message.text or "")
    if not re.fullmatch(r"\d{10}", nid):
        await update.message.reply_text("❌ کد ملی نامعتبر است. لطفاً فقط یک کد ملی ۱۰ رقمی وارد کنید:")
        return STATE_ADMIN_STU_NID

    context.user_data["adm_stu_nid"] = nid

    kb = list(make_keyboard(FIELDS_ADMIN, columns=2).keyboard) + [[BTN_BACK]]
    await update.message.reply_text(
        "رشته را انتخاب کن:",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True, one_time_keyboard=True),
    )
    return STATE_ADMIN_STU_FIELD


async def admin_stu_get_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update.effective_chat.id):
        return await start(update, context)

    text = (update.message.text or "").strip()

    if text == BTN_BACK:
        await update.message.reply_text("کد ملی دانش آموز را وارد کن:",
                                        reply_markup=ReplyKeyboardMarkup([[BTN_BACK]], resize_keyboard=True))
        return STATE_ADMIN_STU_NID

    if text == BTN_BACK:
        await update.message.reply_text("نام و نام خانوادگی دانش آموز را وارد کن:",
                                        reply_markup=ReplyKeyboardMarkup([[BTN_BACK]], resize_keyboard=True))
        return STATE_ADMIN_STU_NAME
    if text in (BTN_BACK, BTN_HOME):
        return await go_home(update, context)

    if text not in FIELDS_ADMIN:
        await update.message.reply_text("یکی از گزینه ها را انتخاب کن:", reply_markup=make_keyboard(FIELDS_ADMIN, 2))
        return STATE_ADMIN_STU_FIELD

    context.user_data["adm_stu_field"] = text

    kb = list(make_keyboard(GRADES, columns=3).keyboard) + [[BTN_BACK]]
    await update.message.reply_text(
        "پایه را انتخاب کن:",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True, one_time_keyboard=True),
    )
    return STATE_ADMIN_STU_GRADE


async def admin_stu_get_grade(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update.effective_chat.id):
        return await start(update, context)

    text = (update.message.text or "").strip()

    if text == BTN_BACK:
        kb = list(make_keyboard(FIELDS_ADMIN, columns=3).keyboard) + [[BTN_BACK]]
        await update.message.reply_text("رشته دانش آموز را انتخاب کن:",
                                        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True,
                                                                         one_time_keyboard=True))
        return STATE_ADMIN_STU_FIELD
    if text in (BTN_BACK, BTN_HOME):
        return await go_home(update, context)

    if text not in GRADES:
        await update.message.reply_text("یکی از پایه ها را انتخاب کن:", reply_markup=make_keyboard(GRADES, 3))
        return STATE_ADMIN_STU_GRADE

    full_name = context.user_data.get("adm_stu_name", "-")
    nid = context.user_data.get("adm_stu_nid", "-")
    field = context.user_data.get("adm_stu_field", "-")
    grade = text

    try:
        code = admin_add_daily_student(full_name=full_name, national_id=nid, field=field, grade=grade)
    except Exception as e:
        logger.exception("admin_add_daily_student failed: %s", e)
        await update.message.reply_text("❌ خطا در دیتابیس. دوباره تلاش کن.",
                                        reply_markup=main_menu_keyboard(update.effective_chat.id))
        context.user_data.clear()
        return STATE_ACTION

    # نمایش کد به ادمین تا برای دانشآموز ارسال کند
    await update.message.reply_text(
        "✅ دانشآموز ثبت شد.\n\n"
        f"نام: {full_name}\n"
        f"کدملی: {nid}\n"
        f"رشته: {field}\n"
        f"پایه: {grade}\n\n"
        f"🔑 کد فعالسازی: `{code}`\n\n"
        "این کد را برای دانشآموز بفرست تا داخل ربات روی «✅ فعالسازی» بزند و کد را وارد کند.",
        reply_markup=main_menu_keyboard(update.effective_chat.id),
        parse_mode="Markdown",
    )

    context.user_data.clear()
    return STATE_ACTION


# ----------------- Admin: remove daily student -----------------
async def admin_remove_student(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update.effective_chat.id):
        return await start(update, context)

    # متن ورودی
    raw_text = (update.message.text or "").strip()

    # دکمه بازگشت
    if raw_text == BTN_BACK:
        return await start(update, context)

    # نرمالسازی کد ملی (تبدیل فارسی/عربی به انگلیسی + حذف غیرعدد)
    nid = normalize_national_id(raw_text)

    # چک ۱۰ رقمی بودن
    if not re.fullmatch(r"\d{10}", nid):
        await update.message.reply_text("❌ کد ملی نامعتبر است. لطفاً یک کد ملی ۱۰ رقمی وارد کن:")
        return STATE_ADMIN_REMOVE_NID

    try:
        stu = _get_daily_student_by_nid(nid)
        ok = admin_remove_daily_student(national_id=nid)
    except Exception as e:
        logger.exception("admin_remove_daily_student failed: %s", e)
        await update.message.reply_text(
            "❌ خطا در دیتابیس",
            reply_markup=main_menu_keyboard(update.effective_chat.id),
        )
        return STATE_ACTION

    if ok:
        name = stu["full_name"] if stu else ""
        await update.message.reply_text(
            f"✅ {name} از طرح دانش آموز روزانه حذف شد.",
            reply_markup=main_menu_keyboard(update.effective_chat.id),
        )
    else:
        await update.message.reply_text(
            "⚠️ با این کد ملی کسی پیدا نشد.",
            reply_markup=main_menu_keyboard(update.effective_chat.id),
        )

    return STATE_ACTION


# ----------------- Admin: list daily students (filtered + pagination) -----------------
DAILY_LIST_PAGE_SIZE = 10


def _admin_daily_students_count(field_filter: str | None, group_filter: str) -> int:
    where = []
    params = []
    if field_filter:
        where.append("field=%s")
        params.append(field_filter)
    if group_filter == "konkoori":
        where.append("grade=%s")
        params.append("دوازدهم")
    else:
        where.append("grade<>%s")
        params.append("دوازدهم")
    wh = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"SELECT COUNT(*) AS c FROM daily_students {wh};"
    with get_db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            return int(cur.fetchone()["c"])


def _admin_daily_students_page(field_filter: str | None, group_filter: str, limit: int, offset: int):
    where = []
    params = []
    if field_filter:
        where.append("field=%s")
        params.append(field_filter)
    if group_filter == "konkoori":
        where.append("grade=%s")
        params.append("دوازدهم")
    else:
        where.append("grade<>%s")
        params.append("دوازدهم")
    wh = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"""
    SELECT full_name, national_id, grade, field, created_at
    FROM daily_students
    {wh}
    ORDER BY created_at DESC, id DESC
    LIMIT %s OFFSET %s;
    """
    params.extend([limit, offset])
    with get_db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            return cur.fetchall()


async def _send_admin_daily_list(update: Update, context: ContextTypes.DEFAULT_TYPE, *, as_edit: bool = False):
    field_filter = context.user_data.get("admin_list_field")  # None = all
    group_filter = context.user_data.get("admin_list_group")  # "base" | "konkoori"
    page = int(context.user_data.get("admin_list_page", 0) or 0)

    total = _admin_daily_students_count(field_filter, group_filter)
    max_page = max(0, (total - 1) // DAILY_LIST_PAGE_SIZE) if total else 0
    if page > max_page:
        page = max_page
        context.user_data["admin_list_page"] = page

    rows = _admin_daily_students_page(field_filter, group_filter, DAILY_LIST_PAGE_SIZE, page * DAILY_LIST_PAGE_SIZE)

    field_title = field_filter if field_filter else "همه رشتهها"
    group_title = "کنکوری (دوازدهم)" if group_filter == "konkoori" else "پایه (دهم/یازدهم)"
    header = f"📋 لیست دانشآموزان روزانه\nرشته: {field_title} | گروه: {group_title}\nصفحه {page + 1} از {max_page + 1}\n\n"

    if not rows:
        body = "هیچ دانش آموزی با این فیلتر پیدا نشد."
    else:
        parts = []
        for i, r in enumerate(rows, start=page * DAILY_LIST_PAGE_SIZE + 1):
            created_at = r["created_at"]
            if isinstance(created_at, datetime) and created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=ZoneInfo(TZ_NAME))
            expiry = created_at + timedelta(days=30)
            nid_disp = normalize_national_id(r.get("national_id", ""))
            parts.append(
                f"{i}) {r['full_name']} | کدملی: {nid_disp} | پایه: {r['grade']} | رشته: {r['field']} | پایان: {format_jalali(expiry)}"
            )
        body = "\n".join(parts)

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ قبلی", callback_data="DLST:PREV"))
    if page < max_page:
        nav_row.append(InlineKeyboardButton("➡️ بعدی", callback_data="DLST:NEXT"))

    kb = []
    if nav_row:
        kb.append(nav_row)
    kb.append([InlineKeyboardButton("🔙 بازگشت", callback_data="DLST:BACK_GROUP")])
    markup = InlineKeyboardMarkup(kb)

    text_msg = header + body

    if as_edit and update.callback_query:
        await update.callback_query.edit_message_text(text_msg, reply_markup=markup)
    else:
        if update.message:
            await update.message.reply_text(text_msg, reply_markup=markup)
        elif update.callback_query:
            await update.callback_query.message.reply_text(text_msg, reply_markup=markup)


async def admin_list_pick_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update.effective_chat.id):
        return await start(update, context)

    text_in = (update.message.text or "").strip()
    if text_in == BTN_BACK:
        return await start(update, context)

    fields_allowed = {"ریاضی", "تجربی", "انسانی", "هنر", "زبان", "همه دانش آموزان"}
    if text_in not in fields_allowed:
        kb = [["ریاضی", "تجربی", "انسانی"], ["هنر", "زبان"], ["همه دانش آموزان"], [BTN_BACK]]
        await update.message.reply_text(
            "لطفاً یکی از گزینه ها را انتخاب کنید:",
            reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True, one_time_keyboard=True),
        )
        return STATE_ADMIN_LIST_FIELD

    context.user_data["admin_list_field"] = None if text_in == "همه دانش آموزان" else text_in

    kb2 = [["پایه (دهم/یازدهم)", "کنکوری"], [BTN_BACK]]
    await update.message.reply_text(
        "حالا گروه را انتخاب کنید:",
        reply_markup=ReplyKeyboardMarkup(kb2, resize_keyboard=True, one_time_keyboard=True),
    )
    return STATE_ADMIN_LIST_GROUP


async def admin_list_pick_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update.effective_chat.id):
        return await start(update, context)

    text_in = (update.message.text or "").strip()
    if text_in == BTN_BACK:
        kb = [["ریاضی", "تجربی", "انسانی"], ["هنر", "زبان"], ["همه دانش آموزان"], [BTN_BACK]]
        await update.message.reply_text(
            "ابتدا رشته را انتخاب کنید:",
            reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True, one_time_keyboard=True),
        )
        return STATE_ADMIN_LIST_FIELD

    if text_in not in ("پایه (دهم/یازدهم)", "کنکوری"):
        kb2 = [["پایه (دهم/یازدهم)", "کنکوری"], [BTN_BACK]]
        await update.message.reply_text(
            "لطفاً یکی از گزینه ها را انتخاب کنید:",
            reply_markup=ReplyKeyboardMarkup(kb2, resize_keyboard=True, one_time_keyboard=True),
        )
        return STATE_ADMIN_LIST_GROUP

    context.user_data["admin_list_group"] = "konkoori" if text_in == "کنکوری" else "base"
    context.user_data["admin_list_page"] = 0
    await _send_admin_daily_list(update, context)
    return STATE_ACTION


async def on_admin_daily_list_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not is_admin(q.message.chat_id):
        return

    action = (q.data or "")
    if action == "DLST:BACK_GROUP":
        kb2 = [["پایه (دهم/یازدهم)", "کنکوری"], [BTN_BACK]]
        await q.message.reply_text(
            "حالا گروه را انتخاب کنید:",
            reply_markup=ReplyKeyboardMarkup(kb2, resize_keyboard=True, one_time_keyboard=True),
        )
        return

    page = int(context.user_data.get("admin_list_page", 0) or 0)
    if action == "DLST:PREV":
        page = max(0, page - 1)
    elif action == "DLST:NEXT":
        page = page + 1

    context.user_data["admin_list_page"] = page
    await _send_admin_daily_list(update, context, as_edit=True)


# ----------------- Admin: extend daily student -----------------
def _get_daily_student_by_nid(nid: str):
    nid = normalize_national_id(nid)
    sql = f"SELECT full_name, national_id, grade, field, created_at FROM daily_students WHERE {NID_TRANSLATE_SQL}=%s LIMIT 1;"
    with get_db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (nid,))
            return cur.fetchone()

def _get_daily_student_by_nid_full(nid: str):
    """Fetch full daily_student record by national_id (normalized)."""
    nid = normalize_national_id(nid)
    sql = f"""
    SELECT
        full_name,
        national_id,
        grade,
        field,
        phone,
        plan,
        tg_chat_id,
        is_active,
        activated_at,
        expires_at
    FROM daily_students
    WHERE {NID_TRANSLATE_SQL}=%s
    LIMIT 1;
    """
    with get_db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (nid,))
            return cur.fetchone()


def _update_daily_student_created_at(nid: str, new_created_at: datetime) -> bool:
    nid = normalize_national_id(nid)
    sql = f"UPDATE daily_students SET created_at=%s WHERE {NID_TRANSLATE_SQL}=%s;"
    with get_db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (new_created_at, nid))
            ok = cur.rowcount > 0
        conn.commit()
    return ok


async def admin_extend_get_nid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update.effective_chat.id):
        return await start(update, context)

    text_in = (update.message.text or "").strip()
    if text_in == BTN_BACK:
        return await start(update, context)

    nid = normalize_national_id(text_in)
    if not re.fullmatch(r"\d{10}", nid):
        await update.message.reply_text("❌ کد ملی نامعتبر است. لطفاً فقط یک کد ملی ۱۰ رقمی وارد کنید:")
        return STATE_ADMIN_EXTEND_NID

    stu = _get_daily_student_by_nid(nid)
    if not stu:
        await update.message.reply_text("⚠️ با این کد ملی کسی پیدا نشد. دوباره وارد کن:")
        return STATE_ADMIN_EXTEND_NID

    context.user_data["admin_extend_nid"] = nid
    context.user_data["admin_extend_name"] = stu["full_name"]

    await update.message.reply_text(
        "تاریخ آخرین پرداخت را وارد کنید (مثال: 1404/11/18):",
        reply_markup=ReplyKeyboardMarkup([[BTN_BACK]], resize_keyboard=True),
    )
    return STATE_ADMIN_EXTEND_PAYDATE


async def admin_extend_get_paydate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update.effective_chat.id):
        return await start(update, context)

    text_in = (update.message.text or "").strip()
    if text_in == BTN_BACK:
        await update.message.reply_text(
            "کد ملی دانش آموز را وارد کنید:",
            reply_markup=ReplyKeyboardMarkup([[BTN_BACK]], resize_keyboard=True),
        )
        return STATE_ADMIN_EXTEND_NID

    dt = parse_jalali_date(text_in)
    if not dt:
        await update.message.reply_text("فرمت تاریخ نامعتبر است. مثل 1404/11/18 وارد کنید:")
        return STATE_ADMIN_EXTEND_PAYDATE

    tz = ZoneInfo(TZ_NAME)
    dt = dt.replace(tzinfo=tz)
    nid = context.user_data.get("admin_extend_nid")
    full_name = context.user_data.get("admin_extend_name", "")

    try:
        ok = _update_daily_student_created_at(nid, dt)
    except Exception as e:
        logger.exception("extend update failed: %s", e)
        await update.message.reply_text("❌ خطا در دیتابیس.", reply_markup=main_menu_keyboard(update.effective_chat.id))
        return STATE_ACTION

    if not ok:
        await update.message.reply_text("⚠️ با این کد ملی کسی پیدا نشد.",
                                              reply_markup=main_menu_keyboard(update.effective_chat.id))
        return STATE_ACTION

    expiry = dt + timedelta(days=30)
    await update.message.reply_text(
        f"✅ تمدید دانشآموز {full_name} با موفقیت به مدت ۳۰ روز انجام شد.\n"
        f"تاریخ پایان اعتبار: {format_jalali(expiry)}",
        reply_markup=main_menu_keyboard(update.effective_chat.id),
    )
    context.user_data.pop("admin_extend_nid", None)
    context.user_data.pop("admin_extend_name", None)
    return STATE_ACTION


# ----------------- Countdown -----------------
async def countdown_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if text in (BTN_BACK, BTN_HOME):
        return await go_home(update, context)

    choice = text
    if choice not in COUNTDOWN_OPTIONS:
        kb = list(make_keyboard(COUNTDOWN_OPTIONS, 1).keyboard) + [[BTN_BACK]]
        await update.message.reply_text(
            "لطفاً یکی از گزینه ها را انتخاب کنید:",
            reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True, one_time_keyboard=True),
        )
        return STATE_COUNTDOWN_PICK

    if "فرهنگیان" in choice:
        jy, jm, jd = CONCOOR_FARHANGIAN
        exam_hour, exam_min = 8, 0
    elif "تجربی" in choice:
        jy, jm, jd = CONCOOR_EXPERIMENTAL
        exam_hour, exam_min = 8, 0
    else:
        jy, jm, jd = CONCOOR_MATH_HUM_ART_LANG
        if ("هنر" in choice) or ("زبان" in choice):
            exam_hour, exam_min = 14, 0
        else:
            exam_hour, exam_min = 8, 0

    gy, gm, gd = jalali_to_gregorian(jy, jm, jd)  # این تابع tuple برمیگردونه
    tz = ZoneInfo("Asia/Tehran")
    now = datetime.now(tz)

    exam_dt = datetime(
        gy,
        gm,
        gd,
        exam_hour,
        exam_min,
        tzinfo=tz,
    )

    delta = exam_dt - now
    total_seconds = int(delta.total_seconds())

    if total_seconds > 0:
        days = total_seconds // 86400
        rem = total_seconds % 86400
        hours = rem // 3600
        rem %= 3600
        minutes = rem // 60

        msg = (
            f"⏳ تا {choice} دقیقاً "
            f"{days} روز و {hours} ساعت و {minutes} دقیقه باقی مانده است.\n"
            f"🕘 زمان آزمون: {exam_hour:02d}:{exam_min:02d} (به وقت تهران)"
        )
    elif -60 <= total_seconds <= 0:
        msg = f"🔥 همین الان زمان {choice} است! موفق باشی 💪"
    else:
        passed = abs(total_seconds)
        days = passed // 86400
        rem = passed % 86400
        hours = rem // 3600
        rem %= 3600
        minutes = rem // 60

        msg = (
            f"✅ تاریخ {choice} گذشته است.\n"
            f"⏱️ {days} روز و {hours} ساعت و {minutes} دقیقه قبل"
        )

    await update.message.reply_text(msg)
    return await start(update, context)


# ----------------- Support (user -> admin) -----------------
async def support_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if text == BTN_BACK:
        return await go_home(update, context)

    user = update.effective_user
    chat_id = update.effective_chat.id

    full_name = user.full_name if user else "-"
    username = f"@{user.username}" if user and user.username else "ندارد"

    header_text = (
        "📩 پیام پشتیبانی جدید\n"
        f"از: {full_name}\n"
        f"chat_id: {chat_id}\n"
        f"username: {username}"
    )

    support_markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton("✍️ پاسخ به این کاربر", callback_data=f"SUPR:{chat_id}")]]
    )

    try:
        header_msg = await context.bot.send_message(
            chat_id=ADVISOR_MATH,
            text=header_text,
            reply_markup=support_markup,
        )
        await context.bot.copy_message(
            chat_id=ADVISOR_MATH,
            from_chat_id=chat_id,
            message_id=update.message.message_id,
            reply_to_message_id=header_msg.message_id,
        )
    except Exception as e:
        logger.exception("Support send failed: %s", e)

    await update.message.reply_text("✅ پیام شما به پشتیبانی ارسال شد. به زودی پاسخ داده میشود.")
    return await start(update, context)


# ----------------- Support reply (admin -> user) -----------------
async def on_support_reply_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    admin_chat_id = query.message.chat_id
    if admin_chat_id != ADVISOR_MATH:
        await query.message.reply_text("این دکمه فقط برای پشتیبانی فعال است.")
        return

    data = (query.data or "").strip()
    if not data.startswith("SUPR:"):
        return

    target_str = data.replace("SUPR:", "", 1).strip()
    if not target_str.isdigit():
        await query.message.reply_text("chat_id کاربر نامعتبر است.")
        return

    target_chat_id = int(target_str)

    context.user_data["support_collecting"] = True
    context.user_data["support_target_chat_id"] = target_chat_id
    context.user_data["support_pending_msgs"] = []

    keyboard = ReplyKeyboardMarkup([[BTN_DONE, BTN_CANCEL]], resize_keyboard=True)
    await query.message.reply_text(
        "✅ حالت پاسخ پشتیبانی فعال شد.\n"
        f"کاربر هدف: {target_chat_id}\n\n"
        "هر تعداد پیام خواستی بفرست (متن/عکس/ویس/ویدیو/فایل و ...)\n"
        "وقتی تمام شد «تمام شد ✅» را بزن.",
        reply_markup=keyboard,
    )


async def support_admin_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    admin_chat_id = update.effective_chat.id
    if admin_chat_id != ADVISOR_MATH:
        return

    if not context.user_data.get("support_collecting"):
        return

    text = (update.message.text or "").strip()

    if text == BTN_CANCEL:
        context.user_data.clear()
        await update.message.reply_text("لغو شد.", reply_markup=ReplyKeyboardRemove())
        return

    if text == BTN_DONE:
        pending = context.user_data.get("support_pending_msgs", [])
        target_chat_id = context.user_data.get("support_target_chat_id")

        if not pending:
            await update.message.reply_text("هنوز پاسخی ارسال نکردی. حداقل یک پیام بفرست.")
            return

        if not target_chat_id:
            await update.message.reply_text("خطا: کاربر هدف مشخص نیست. دوباره دکمه «پاسخ به این کاربر» را بزن.")
            return

        try:
            await context.bot.send_message(
                chat_id=target_chat_id,
                text="✅ پاسخ پشتیبانی برای شما ارسال شد:",
            )
        except Exception as e:
            logger.warning("Support notify user failed: %s", e)

        ok = True
        for mid in pending:
            try:
                await context.bot.copy_message(
                    chat_id=target_chat_id,
                    from_chat_id=admin_chat_id,
                    message_id=mid,
                )
            except Exception as e:
                ok = False
                logger.warning("Support copy to user failed: %s", e)

        if ok:
            await update.message.reply_text("✅ پاسخ پشتیبانی با موفقیت برای کاربر ارسال شد.",
                                            reply_markup=ReplyKeyboardRemove())
        else:
            await update.message.reply_text(
                "⚠️ بخشی از پیامها به کاربر ارسال نشد (ممکن است ربات را بلاک کرده باشد).",
                reply_markup=ReplyKeyboardMarkup([[BTN_BACK]], resize_keyboard=True),
            )

        context.user_data.clear()
        return

    context.user_data.setdefault("support_pending_msgs", []).append(update.message.message_id)
    await update.message.reply_text("✅ دریافت شد. ادامه بده یا «تمام شد ✅» را بزن.")


# ----------------- Question flow -----------------
async def choose_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if text in (BTN_BACK, BTN_HOME):
        return await go_home(update, context)

    field = text
    if field not in FIELDS:
        await update.message.reply_text("لطفاً یکی از رشتهها را انتخاب کنید:", reply_markup=make_keyboard(FIELDS, 3))
        return STATE_FIELD

    context.user_data["field"] = field
    kb = list(make_keyboard(GRADES, columns=3).keyboard) + [[BTN_BACK]]
    await update.message.reply_text(
        "لطفا پایه تحصیلی خود را انتخاب کنید:",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True, one_time_keyboard=True),
    )
    return STATE_GRADE


async def choose_grade(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if text == BTN_BACK:
        kb = list(make_keyboard(FIELDS, columns=3).keyboard) + [[BTN_BACK]]
        await update.message.reply_text(
            "لطفا رشته تحصیلی خود را انتخاب کنید:",
            reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True, one_time_keyboard=True),
        )
        return STATE_FIELD

    if text == BTN_HOME:
        return await go_home(update, context)

    if text == BTN_QBANK:
        # شروع بانک سوال
        context.user_data.pop("qb_grade", None)
        context.user_data.pop("qb_major", None)
        context.user_data.pop("qb_subject", None)
        context.user_data.pop("qb_chapter", None)
        await update.message.reply_text("پایه تحصیلیات رو انتخاب کن:", reply_markup=qb_kb_grades())
        return STATE_QB_GRADE

    grade = text
    if grade not in GRADES:
        await update.message.reply_text("لطفاً یکی از پایه ها را انتخاب کنید:", reply_markup=make_keyboard(GRADES, 3))
        return STATE_GRADE

    context.user_data["grade"] = grade
    field = context.user_data.get("field")
    lessons = LESSONS_BY_FIELD.get(field, [])

    kb = list(make_keyboard(lessons, columns=2).keyboard) + [[BTN_BACK]]
    await update.message.reply_text(
        "لطفا درس مربوطه را انتخاب کنید:",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True, one_time_keyboard=True),
    )
    return STATE_LESSON


async def choose_lesson(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if text == BTN_BACK:
        kb = list(make_keyboard(GRADES, columns=3).keyboard) + [[BTN_BACK]]
        await update.message.reply_text(
            "لطفا پایه تحصیلی خود را انتخاب کنید:",
            reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True, one_time_keyboard=True),
        )
        return STATE_GRADE

    if text == BTN_HOME:
        return await go_home(update, context)

    if text == BTN_QBANK:
        # شروع بانک سوال
        context.user_data.pop("qb_grade", None)
        context.user_data.pop("qb_major", None)
        context.user_data.pop("qb_subject", None)
        context.user_data.pop("qb_chapter", None)
        await update.message.reply_text("پایه تحصیلیات رو انتخاب کن:", reply_markup=qb_kb_grades())
        return STATE_QB_GRADE

    lesson = text
    field = context.user_data.get("field")
    allowed = LESSONS_BY_FIELD.get(field, [])

    if lesson not in allowed:
        await update.message.reply_text(
            "لطفاً یکی از درسهای نمایش داده شده را انتخاب کنید:",
            reply_markup=make_keyboard(allowed, 2),
        )
        return STATE_LESSON

    context.user_data["lesson"] = lesson
    await update.message.reply_text(
        "لطفا سوال خود را به شکل فایل عکس ارسال کنید 📷",
        reply_markup=ReplyKeyboardMarkup([[BTN_BACK]], resize_keyboard=True),
    )
    return STATE_WAIT_PHOTO


async def receive_question_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if text == BTN_BACK:
        field = context.user_data.get("field")
        lessons = LESSONS_BY_FIELD.get(field, [])
        kb = list(make_keyboard(lessons, columns=2).keyboard) + [[BTN_BACK]]
        await update.message.reply_text(
            "لطفا درس مربوطه را انتخاب کنید:",
            reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True, one_time_keyboard=True),
        )
        return STATE_LESSON

    if text == BTN_HOME:
        return await go_home(update, context)

    if text == BTN_QBANK:
        # شروع بانک سوال
        context.user_data.pop("qb_grade", None)
        context.user_data.pop("qb_major", None)
        context.user_data.pop("qb_subject", None)
        context.user_data.pop("qb_chapter", None)
        await update.message.reply_text("پایه تحصیلیات رو انتخاب کن:", reply_markup=qb_kb_grades())
        return STATE_QB_GRADE

    tg_chat_id = update.effective_chat.id


    # limit check again (برای امنیت) - منطق جدید: غیر فعال هفته‌ای ۱بار، فعال بدون محدودیت
    try:
        active = is_student_active(tg_chat_id)
        if not active:
            if has_question_in_last_7_days(tg_chat_id):
                await update.message.reply_text(
                    "⚠️ شما در ۷ روز گذشته یک‌بار رفع اشکال ارسال کرده‌اید.\n"
                    "هفته بعد دوباره می‌توانید ارسال کنید.\n\n"
                    "✅ اگر در طرح برنامه روزانه ثبت‌نام کنید، محدودیت ندارید."
                )
                context.user_data.clear()
                return STATE_ACTION
        # اگر active باشد: هیچ محدودیتی ندارد
    except Exception as e:
        logger.exception("DB error in receive_question_photo weekly limit: %s", e)
        await update.message.reply_text("خطا در دیتابیس. لطفاً بعداً تلاش کنید.")
        context.user_data.clear()
        return STATE_ACTION

    field = context.user_data.get("field", "-")
    grade = context.user_data.get("grade", "-")
    lesson = context.user_data.get("lesson", "-")

    file_id = None
    is_photo = False

    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        is_photo = True
    elif update.message.document and update.message.document.mime_type and update.message.document.mime_type.startswith(
            "image/"):
        file_id = update.message.document.file_id
        is_photo = False

    if not file_id:
        await update.message.reply_text("لطفاً فقط عکس ارسال کنید (Photo یا فایل عکس).")
        return STATE_WAIT_PHOTO

    try:
        used_today = get_today_count_for_chat(tg_chat_id)
    except Exception as e:
        logger.exception("DB error: %s", e)
        await update.message.reply_text("خطا در اتصال به دیتابیس. لطفاً بعداً تلاش کنید.")
        context.user_data.clear()
        return STATE_ACTION

    # daily_no برای کاربر فعال تا ۴؛ برای غیر فعال هم تا ۱ (چون در کل فقط ۱ سوال دارد)
    daily_no = used_today + 1
    question_code = f"{tg_chat_id}-{daily_no}"

    try:
        insert_submission(tg_chat_id=tg_chat_id, daily_no=daily_no, field=field, grade=grade, lesson=lesson,
                          file_id=file_id)
    except Exception as e:
        logger.exception("DB insert error: %s", e)
        await update.message.reply_text("خطا در ذخیره در دیتابیس. لطفاً دوباره تلاش کنید.")
        context.user_data.clear()
        return STATE_ACTION

    user_caption = (
        f"رشته: {field}\n"
        f"پایه: {grade}\n"
        f"درس: {lesson}\n\n"
        f"کد سوال شما: {question_code}\n"
        f"پاسخ سوال شما حداکثر تا ۲۴ ساعت ضبط میشود و در کانال زیر قرار میگیرد:\n"
        f"{CHANNEL_LINK}"
    )

    advisor_caption = (
        f"رشته: {field}\n"
        f"پایه: {grade}\n"
        f"درس: {lesson}\n\n"
        f"مشاوریار عزیز لطفا به سوال کاربر ({tg_chat_id})\n"
        f"کد سوال: {question_code}\n"
        f"حداکثر تا ۲۴ ساعت پاسخ دهید."
    )

    channel_caption = (
        f"📌 سوال جدید\n\n"
        f"رشته: {field}\n"
        f"پایه: {grade}\n"
        f"درس: {lesson}\n\n"
        f"کد سوال: {question_code}"
    )

    advisor_markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton("پاسخ به این سوال ✅", callback_data=f"ANS:{question_code}")]]
    )

    advisor_id = LESSON_TO_ADVISOR.get(lesson)
    if advisor_id:
        try:
            if is_photo:
                await context.bot.send_photo(chat_id=advisor_id, photo=file_id, caption=advisor_caption,
                                             reply_markup=advisor_markup)
            else:
                await context.bot.send_document(chat_id=advisor_id, document=file_id, caption=advisor_caption,
                                                reply_markup=advisor_markup)
        except Exception as e:
            logger.exception("Advisor send failed: %s", e)

    # send question to channel + store msg_id
    try:
        if is_photo:
            sent = await context.bot.send_photo(chat_id=CHANNEL_CHAT_ID, photo=file_id, caption=channel_caption)
        else:
            sent = await context.bot.send_document(chat_id=CHANNEL_CHAT_ID, document=file_id, caption=channel_caption)

        try:
            set_channel_msg_id(tg_chat_id=tg_chat_id, daily_no=daily_no, channel_msg_id=sent.message_id)
        except Exception as e:
            logger.exception("DB set_channel_msg_id failed: %s", e)
    except Exception as e:
        logger.exception("Channel send question failed: %s", e)

    # back to student
    if is_photo:
        await update.message.reply_photo(photo=file_id, caption=user_caption)
    else:
        await update.message.reply_document(document=file_id, caption=user_caption)

    context.user_data.clear()
    return STATE_ACTION


# ----------------- Answer button & advisor flow -----------------
async def on_answer_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    advisor_chat_id = query.message.chat_id
    if not is_advisor(advisor_chat_id):
        await query.message.reply_text("این دکمه فقط برای مشاورهاست.")
        return

    data = (query.data or "").strip()
    if not data.startswith("ANS:"):
        return

    qcode = data.replace("ANS:", "", 1).strip()
    parsed = parse_question_code(qcode)
    if not parsed:
        await query.message.reply_text("کد سوال نامعتبر است.")
        return

    target_chat_id, daily_no = parsed

    context.user_data["advisor_collecting"] = True
    context.user_data["pending_admin_msgs"] = []
    context.user_data["answer_target"] = {
        "target_chat_id": target_chat_id,
        "daily_no": daily_no,
        "question_code": qcode,
    }

    keyboard = ReplyKeyboardMarkup([[BTN_DONE, BTN_CANCEL]], resize_keyboard=True)
    await query.message.reply_text(
        f"مشاوریار عزیز ✅\n"
        f"پاسخ های مربوط به این سوال را ارسال کنید (میتواند چند پیام باشد).\n"
        f"کد سوال: {qcode}\n"
        f"وقتی تمام شد «تمام شد ✅» را بزنید.",
        reply_markup=keyboard,
    )


async def advisor_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    chat_id = update.effective_chat.id
    if not is_advisor(chat_id):
        return

    if not context.user_data.get("advisor_collecting"):
        return

    text = (update.message.text or "").strip()

    if text == BTN_CANCEL:
        context.user_data.clear()
        await update.message.reply_text("لغو شد.", reply_markup=ReplyKeyboardRemove())
        return

    if text == BTN_DONE:
        pending = context.user_data.get("pending_admin_msgs", [])
        target = context.user_data.get("answer_target")

        if not pending:
            await update.message.reply_text("هنوز پاسخی ارسال نکردی. حداقل یک پیام بفرست.")
            return

        if not target:
            await update.message.reply_text("خطا: هدف سوال مشخص نیست. دوباره روی «پاسخ به این سوال» بزن.")
            return

        target_chat_id = int(target["target_chat_id"])
        daily_no = int(target["daily_no"])
        question_code = target["question_code"]
        advisor_chat_id = update.effective_chat.id

        # notify student
        try:
            await context.bot.send_message(
                chat_id=target_chat_id,
                text=(
                    f"✅ پاسخ سوال شما آماده شد.\n"
                    f"کد سوال: {question_code}\n"
                    f"همچنین در کانال زیر هم قرار میگیرد:\n{CHANNEL_LINK}"
                ),
            )
        except Exception as e:
            logger.warning("Student notify failed: %s", e)

        # reply to channel question
        try:
            channel_q_msg_id = get_channel_msg_id_today(target_chat_id, daily_no)
        except Exception as e:
            logger.exception("DB get_channel_msg_id_today failed: %s", e)
            channel_q_msg_id = None

        channel_answer_header_id = None
        try:
            if channel_q_msg_id:
                header = await context.bot.send_message(
                    chat_id=CHANNEL_CHAT_ID,
                    text=f"✅ پاسخ سوال با کد: {question_code}",
                    reply_to_message_id=channel_q_msg_id,
                )
            else:
                header = await context.bot.send_message(
                    chat_id=CHANNEL_CHAT_ID,
                    text=f"✅ پاسخ سوال با کد: {question_code}",
                )
            channel_answer_header_id = header.message_id
        except Exception as e:
            logger.exception("Channel header send failed: %s", e)

        for mid in pending:
            # to student
            try:
                await context.bot.copy_message(
                    chat_id=target_chat_id,
                    from_chat_id=advisor_chat_id,
                    message_id=mid,
                )
            except Exception as e:
                logger.warning("Student copy failed: %s", e)

            # to channel (reply to header)
            try:
                if channel_answer_header_id:
                    await context.bot.copy_message(
                        chat_id=CHANNEL_CHAT_ID,
                        from_chat_id=advisor_chat_id,
                        message_id=mid,
                        reply_to_message_id=channel_answer_header_id,
                    )
                else:
                    await context.bot.copy_message(
                        chat_id=CHANNEL_CHAT_ID,
                        from_chat_id=advisor_chat_id,
                        message_id=mid,
                    )
            except Exception as e:
                logger.exception("Channel copy failed: %s", e)

        await update.message.reply_text("✅ پاسخ ها برای دانش آموز ارسال شد و در کانال هم ثبت شد.",
                                        reply_markup=ReplyKeyboardRemove())
        context.user_data.clear()
        return

    context.user_data.setdefault("pending_admin_msgs", []).append(update.message.message_id)
    await update.message.reply_text("✅ دریافت شد. ادامه بده یا «تمام شد ✅» را بزن.")


# ----------------- FREE CONTENT CHANNEL LISTENER -----------------
HASHTAG_RE = re.compile(r"#([A-Za-z0-9_]+)")


def _extract_hashtag(text: str):
    if not text:
        return None
    m = HASHTAG_RE.search(text)
    return m.group(1) if m else None


async def on_free_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    post = update.channel_post
    if not post:
        return
    if post.chat_id != FREE_CONTENT_CHANNEL_ID:
        return

    raw_text = (post.text or post.caption or "")
    tag = _extract_hashtag(raw_text)
    if tag:
        logger.info("FREE CHANNEL TAG FOUND -> #%s | message_id=%s", tag, post.message_id)
    else:
        logger.info("FREE CHANNEL POST RECEIVED (no tag) | message_id=%s | has_caption=%s",
                    post.message_id, bool(post.caption))


async def on_free_channel_edited_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    post = update.edited_channel_post
    if not post:
        return
    if post.chat_id != FREE_CONTENT_CHANNEL_ID:
        return

    raw_text = (post.text or post.caption or "")
    tag = _extract_hashtag(raw_text)
    if tag:
        logger.info("FREE CHANNEL EDITED TAG FOUND -> #%s | message_id=%s", tag, post.message_id)
    else:
        logger.info("FREE CHANNEL EDITED POST (no tag) | message_id=%s", post.message_id)


# ----------------- QBANK Channel Listeners -----------------
async def on_qbank_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg:
        return
    if msg.chat_id != QBANK_CHANNEL_ID:
        return

    text = (msg.text or msg.caption or "")
    tags = extract_qb_tags(text)
    if not tags:
        return

    # اگر داخل متن #DEL باشد، رکورد حذف میشود (اختیاری)
    if "#DEL" in text:
        for tag in tags:
            delete_qbank_file(tag)
            logger.info("QBANK deleted tag=%s (by #DEL)", tag)
        return

    for tag in tags:
        upsert_qbank_file(tag, msg.chat_id, msg.message_id)
        logger.info("QBANK upsert tag=%s chat_id=%s msg_id=%s", tag, msg.chat_id, msg.message_id)


async def on_qbank_channel_edited_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.edited_channel_post
    if not msg:
        return
    if msg.chat_id != QBANK_CHANNEL_ID:
        return

    text = (msg.text or msg.caption or "")
    tags = extract_qb_tags(text)
    if not tags:
        return

    if "#DEL" in text:
        for tag in tags:
            delete_qbank_file(tag)
            logger.info("QBANK deleted tag=%s (by #DEL edit)", tag)
        return

    for tag in tags:
        upsert_qbank_file(tag, msg.chat_id, msg.message_id)
        logger.info("QBANK upsert (edited) tag=%s chat_id=%s msg_id=%s", tag, msg.chat_id, msg.message_id)


# ----------------- Registration Channel Listeners -----------------
async def on_registration_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg:
        return
    if msg.chat_id != REGISTRATION_CHANNEL_ID:
        return

    text = (msg.text or msg.caption or "")
    tags = extract_reg_tags(text)
    if not tags:
        return

    if "#DEL" in text:
        for tag in tags:
            delete_registration_message(tag)
            logger.info("REG deleted tag=%s (by #DEL)", tag)
        return

    for tag in tags:
        upsert_registration_message(tag, msg.chat_id, msg.message_id)
        logger.info("REG upsert tag=%s chat_id=%s msg_id=%s", tag, msg.chat_id, msg.message_id)


async def on_registration_channel_edited_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.edited_channel_post
    if not msg:
        return
    if msg.chat_id != REGISTRATION_CHANNEL_ID:
        return

    text = (msg.text or msg.caption or "")
    tags = extract_reg_tags(text)
    if not tags:
        return

    if "#DEL" in text:
        for tag in tags:
            delete_registration_message(tag)
            logger.info("REG deleted tag=%s (by #DEL edit)", tag)
        return

    for tag in tags:
        upsert_registration_message(tag, msg.chat_id, msg.message_id)
        logger.info("REG upsert (edited) tag=%s chat_id=%s msg_id=%s", tag, msg.chat_id, msg.message_id)


async def free_content_channel_listener(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg:
        return

    chat = update.effective_chat
    if not chat or chat.id != FREE_CONTENT_CHANNEL_ID:
        return

    text = (msg.text or "") + "\n" + (msg.caption or "")
    tags = TAG_RE.findall(text)

    if not tags:
        return

    for tag in tags:
        parsed = parse_free_tag(tag)
        if not parsed:
            continue

        field, grade, content_type, sort_order = parsed
        try:
            upsert_free_content(
                field=field,
                grade=grade,
                content_type=content_type,
                channel_chat_id=chat.id,
                channel_message_id=msg.message_id,
                sort_order=sort_order,
            )
            logger.info(
                f"FREE TAG SAVED -> {tag} | type={content_type} field={field} grade={grade} msg_id={msg.message_id}")
        except Exception as e:
            logger.exception("Free content upsert failed: %s", e)


async def free_pick_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if text in (BTN_BACK, BTN_HOME):
        return await go_home(update, context)

    if text not in FIELDS:
        await update.message.reply_text("لطفاً یکی از رشته ها را انتخاب کنید:", reply_markup=make_keyboard(FIELDS, 3))
        return STATE_FREE_FIELD

    context.user_data["free_field"] = text
    kb = list(make_keyboard(GRADES, columns=3).keyboard) + [[BTN_BACK]]
    await update.message.reply_text(
        "حالا پایه ات رو انتخاب کن:",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True, one_time_keyboard=True),
    )
    return STATE_FREE_GRADE


async def free_pick_grade(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if text == BTN_BACK:
        kb = list(make_keyboard(FIELDS, columns=3).keyboard) + [[BTN_BACK]]
        await update.message.reply_text(
            "برای پلن رایگان، رشته ات رو انتخاب کن:",
            reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True, one_time_keyboard=True),
        )
        return STATE_FREE_FIELD

    if text == BTN_HOME:
        return await go_home(update, context)

    if text == BTN_QBANK:
        # شروع بانک سوال
        context.user_data.pop("qb_grade", None)
        context.user_data.pop("qb_major", None)
        context.user_data.pop("qb_subject", None)
        context.user_data.pop("qb_chapter", None)
        await update.message.reply_text("پایه تحصیلیات رو انتخاب کن:", reply_markup=qb_kb_grades())
        return STATE_QB_GRADE

    if text not in GRADES:
        await update.message.reply_text("لطفاً یکی از پایه ها را انتخاب کنید:", reply_markup=make_keyboard(GRADES, 3))
        return STATE_FREE_GRADE

    field = context.user_data.get("free_field")
    grade = text
    if not field:
        await update.message.reply_text("خطا: رشته مشخص نیست. دوباره پلن رایگان را بزن.")
        return await start(update, context)

    # 1) برنامه
    row = get_free_content(field=field, grade=grade, content_type="program", sort_order=1)
    if row:
        await context.bot.copy_message(
            chat_id=update.effective_chat.id,
            from_chat_id=row["channel_chat_id"],
            message_id=row["channel_message_id"],
        )
    else:
        await update.message.reply_text("⚠️ برنامهی این پایه/رشته هنوز در کانال تعریف نشده.")

    # 2) کوییز
    row = get_free_content(field=field, grade=grade, content_type="quiz", sort_order=1)
    if row:
        await context.bot.copy_message(
            chat_id=update.effective_chat.id,
            from_chat_id=row["channel_chat_id"],
            message_id=row["channel_message_id"],
        )
    else:
        await update.message.reply_text("⚠️ کوییز این پایه/رشته هنوز تعریف نشده.")

    # 3) پاسخنامه
    row = get_free_content(field=field, grade=grade, content_type="answer_key", sort_order=1)
    if row:
        await context.bot.copy_message(
            chat_id=update.effective_chat.id,
            from_chat_id=row["channel_chat_id"],
            message_id=row["channel_message_id"],
        )
    else:
        await update.message.reply_text("⚠️ پاسخنامه این پایه/رشته هنوز تعریف نشده.")

    # 4) ویس عمومی
    row = get_free_content(field="ALL", grade="ALL", content_type="voice", sort_order=1)
    if row:
        await context.bot.copy_message(
            chat_id=update.effective_chat.id,
            from_chat_id=row["channel_chat_id"],
            message_id=row["channel_message_id"],
        )

    await update.message.reply_text(
        "✅ پلن رایگان ارسال شد. لطفا به ویس بالا گوش کنید.\nاگر دوست داشتی میتونی روی << ✅ثبت نام برنامه روزانه >> کلیک کنی و تا یک ماه از این طرح استفاده کنی.")
    context.user_data.pop("free_field", None)
    return await start(update, context)


# ----------------- misc -----------------
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("لغو شد. برای شروع دوباره /start را بزنید.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"chat_id: {update.effective_chat.id}")


async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("pong ✅")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error: %s", context.error)


async def free_channel_capture(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # اینجا لازم نیست (قبلاً listener داریم)
    return


# ----------------- main -----------------
async def post_init(application: Application):
    # برای نمایش دکمه آبی Menu و لیست دستورات
    await application.bot.set_my_commands([
        BotCommand("start", "شروع ربات"),
        BotCommand("menu", "نمایش منوی اصلی"),
    ])

    # بررسی انقضا / حذف بعد از ۵ روز (هر ساعت)
    if application.job_queue:
        application.job_queue.run_repeating(registration_expiry_job, interval=3600, first=60)


def main():
    token = os.getenv("BOT_TOKEN")
    logger.info("BOT_ID=%s | TOKEN_LAST6=%s", token.split(":")[0], token[-6:])
    if not token:
        raise RuntimeError("BOT_TOKEN در فایل .env تنظیم نشده است.")

    ensure_tables()

    request = HTTPXRequest(
        connect_timeout=60.0,
        read_timeout=60.0,
        write_timeout=60.0,
        pool_timeout=60.0,
    )

    app = Application.builder().token(token).request(request).post_init(post_init).build()
    app.add_error_handler(error_handler)

    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("ping", ping))

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start), CommandHandler("menu", start)],
        states={
            STATE_ACTION: [
                CallbackQueryHandler(on_check_join, pattern=r"^CHK_JOIN$"),
                # Admin daily students list pagination (next/prev/back) uses inline buttons.
                # If not handled here, Telegram keeps the button in a perpetual "loading" state.
                CallbackQueryHandler(on_admin_daily_list_nav, pattern=r"^DLST:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, action_select),
            ],

            # registration / renewal (daily plan)
            STATE_REG_PLAN_SELECT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, reg_pick_plan),
                CallbackQueryHandler(reg_on_continue, pattern=r"^REG_CONT\|"),
            ],
            STATE_REG_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_get_name)],
            STATE_REG_NID: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_get_nid)],
            STATE_REG_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_get_field)],
            STATE_REG_GRADE: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_get_grade)],
            STATE_REG_PHONE: [
                MessageHandler(filters.CONTACT, reg_get_phone),
                MessageHandler(filters.TEXT & ~filters.COMMAND, reg_get_phone),
            ],
            STATE_REG_PAYMENT_WAIT_BTN: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, reg_payment_nav),
                CallbackQueryHandler(reg_on_receipt_button, pattern=r"^REG_RECEIPT\|"),
            ],
            STATE_REG_WAIT_RECEIPT: [
                MessageHandler((filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND, reg_receive_receipt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, reg_receive_receipt),
            ],

            # qbank
            STATE_QB_GRADE: [MessageHandler(filters.TEXT & ~filters.COMMAND, qb_grade_handler)],
            STATE_QB_MAJOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, qb_major_handler)],
            STATE_QB_SUBJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, qb_subject_handler)],
            STATE_QB_CHAPTER: [MessageHandler(filters.TEXT & ~filters.COMMAND, qb_chapter_handler)],

            STATE_ACTIVATION_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, activation_receive_code)],

            STATE_ADMIN_STU_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_stu_get_name)],
            STATE_ADMIN_STU_NID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_stu_get_nid)],
            STATE_ADMIN_STU_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_stu_get_field)],
            STATE_ADMIN_STU_GRADE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_stu_get_grade)],
            STATE_ADMIN_REMOVE_NID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_remove_student)],
            STATE_ADMIN_LIST_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_list_pick_field)],
            STATE_ADMIN_LIST_GROUP: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_list_pick_group)],
            STATE_ADMIN_EXTEND_NID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_extend_get_nid)],
            STATE_ADMIN_EXTEND_PAYDATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_extend_get_paydate)],

            STATE_COUNTDOWN_PICK: [MessageHandler(filters.TEXT & ~filters.COMMAND, countdown_pick)],
            STATE_SUPPORT_WAIT: [MessageHandler(filters.ALL & ~filters.COMMAND, support_receive)],
            STATE_FREE_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, free_pick_field)],
            STATE_FREE_GRADE: [MessageHandler(filters.TEXT & ~filters.COMMAND, free_pick_grade)],

            STATE_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_field)],
            STATE_GRADE: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_grade)],
            STATE_LESSON: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_lesson)],
            STATE_WAIT_PHOTO: [
                MessageHandler((filters.PHOTO | filters.Document.IMAGE) & ~filters.COMMAND, receive_question_photo),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_question_photo),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(conv)

    app.add_handler(CallbackQueryHandler(on_answer_button, pattern=r"^ANS:"))
    app.add_handler(CallbackQueryHandler(on_support_reply_button, pattern=r"^SUPR:"))

    # تایید ادمین برای ثبتنام/تمدید
    app.add_handler(CallbackQueryHandler(on_reg_approve, pattern=r"^REG_APPROVE\|"))

    # group=1: جمعآوری پیامهای مشاور (پاسخ سوال)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, advisor_router), group=1)

    # group=2: جمعآوری پیامهای پشتیبانی
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, support_admin_router), group=2)

    # شنونده پستهای کانال خصوصی FreeContent
    app.add_handler(
        MessageHandler(filters.Chat(FREE_CONTENT_CHANNEL_ID) & filters.UpdateType.CHANNEL_POST, on_free_channel_post))
    app.add_handler(MessageHandler(filters.Chat(FREE_CONTENT_CHANNEL_ID) & filters.UpdateType.EDITED_CHANNEL_POST,
                                   on_free_channel_edited_post))

    # شنونده پستهای کانال خصوصی QBANK
    app.add_handler(
        MessageHandler(filters.Chat(QBANK_CHANNEL_ID) & filters.UpdateType.CHANNEL_POST, on_qbank_channel_post))
    app.add_handler(MessageHandler(filters.Chat(QBANK_CHANNEL_ID) & filters.UpdateType.EDITED_CHANNEL_POST,
                                   on_qbank_channel_edited_post))

    # شنونده پستهای کانال خصوصی Registration_Message
    app.add_handler(
        MessageHandler(filters.Chat(REGISTRATION_CHANNEL_ID) & filters.UpdateType.CHANNEL_POST, on_registration_channel_post))
    app.add_handler(MessageHandler(filters.Chat(REGISTRATION_CHANNEL_ID) & filters.UpdateType.EDITED_CHANNEL_POST,
                                   on_registration_channel_edited_post))

    app.add_handler(
        MessageHandler(filters.Chat(FREE_CONTENT_CHANNEL_ID) & filters.ALL, free_content_channel_listener),
        group=10
    )
    app.add_handler(
        MessageHandler(filters.Chat(FREE_CONTENT_CHANNEL_ID) & filters.UpdateType.EDITED_MESSAGE,
                       free_content_channel_listener),
        group=10
    )

    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, free_channel_capture))
    app.add_handler(MessageHandler(filters.UpdateType.EDITED_CHANNEL_POST, free_channel_capture))

    logger.info("Bot is running...")


    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()