import asyncio
import logging
import sqlite3
import os
import json
import re
import subprocess
from datetime import datetime
from aiogram import Bot, Dispatcher, types, executor
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
import aiofiles
from aiohttp import web
import aiohttp

# Logging sozlash
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Database sozlash
DB_NAME = 'maker_bot.db'

# Admin ID (o'zgartiring)
ADMIN_ID = 7174828209

# Referral summa (default 300)
DEFAULT_REFERRAL_AMOUNT = 300   # <-- BU QATOR BO‘LISHI SHART!

# To'lov karta raqami
PAYMENT_CARD = "4790920024921400"

# Bot token (o'zgartiring)
# Tokenni xavfsiz o'qish va tozalash
BOT_TOKEN_RAW = os.getenv("BOT_TOKEN", "")
BOT_TOKEN = BOT_TOKEN_RAW.strip()

if not BOT_TOKEN:
    logger.critical("BOT_TOKEN topilmadi! .env yoki Environment Variables ni tekshiring.")
else:
    logger.info(f"BOT_TOKEN yuklandi: {BOT_TOKEN[:5]}...{BOT_TOKEN[-5:]}")


# Bot va Dispatcher yaratish
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)


# FSM States
class PaymentStates(StatesGroup):
    waiting_amount = State()
    waiting_screenshot = State()


class AdminStates(StatesGroup):
    waiting_channel = State()
    waiting_bot_file = State()
    waiting_bot_name = State()
    waiting_bot_price = State()
    waiting_run_command = State()
    waiting_user_id = State()
    waiting_topup_amount = State()
    waiting_referral_amount = State()
    waiting_reject_reason = State()


class BotCreationStates(StatesGroup):
    waiting_token = State()


class RegistrationStates(StatesGroup):
    waiting_name = State()


class Database:
    def __init__(self):
        self.conn = sqlite3.connect(DB_NAME, check_same_thread=False)
        self.init_db()

    def init_db(self):
        cursor = self.conn.cursor()

        # Foydalanuvchilar jadvali
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                phone_number TEXT,
                balance REAL DEFAULT 0,
                referral_code TEXT UNIQUE,
                referred_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Qo'shimcha ustunlar qo'shish
        for col in ['full_name TEXT', 'phone_number TEXT', 'referral_bonus_paid INTEGER DEFAULT 0']:
            try:
                cursor.execute(f'ALTER TABLE users ADD COLUMN {col}')
            except sqlite3.OperationalError:
                pass

        # Majburiy obunalar
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS mandatory_subscriptions (
                channel_id TEXT PRIMARY KEY,
                channel_username TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Botlar
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bots (
                bot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_name TEXT,
                bot_file_path TEXT,
                run_command TEXT,
                price REAL,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        try:
            cursor.execute('ALTER TABLE bots ADD COLUMN run_command TEXT')
        except sqlite3.OperationalError:
            pass

        # Foydalanuvchi botlari
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_bots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                bot_token TEXT,
                bot_id INTEGER,
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                payment_date TIMESTAMP,
                days_left INTEGER DEFAULT 30,
                FOREIGN KEY (bot_id) REFERENCES bots(bot_id)
            )
        ''')
        
        # Oylik to'lov ustunlarini qo'shish
        for col in ['payment_date TIMESTAMP', 'days_left INTEGER DEFAULT 30']:
            try:
                cursor.execute(f'ALTER TABLE user_bots ADD COLUMN {col}')
            except sqlite3.OperationalError:
                pass

        # To'lovlar
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount REAL,
                screenshot_path TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Banned foydalanuvchilar
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS banned_users (
                user_id INTEGER PRIMARY KEY,
                banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                reason TEXT
            )
        ''')

        # Sozlamalar
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')

        # Default referral summasi
        cursor.execute('''
            INSERT OR IGNORE INTO settings (key, value) VALUES ('referral_amount', ?)
        ''', (str(DEFAULT_REFERRAL_AMOUNT),))

        self.conn.commit()

    # ==================== FOYDALANUVCHI ====================
    def get_user(self, user_id):
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        return cursor.fetchone()

    def create_user(self, user_id, username, full_name=None, phone_number=None, referral_code=None, referred_by=None):
        cursor = self.conn.cursor()
        try:
            cursor.execute('''
                INSERT INTO users (user_id, username, full_name, phone_number, referral_code, referred_by)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (user_id, username, full_name, phone_number, referral_code, referred_by))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def update_user_name(self, user_id, full_name):
        cursor = self.conn.cursor()
        cursor.execute('UPDATE users SET full_name = ? WHERE user_id = ?', (full_name, user_id))
        self.conn.commit()

    def update_user_phone(self, user_id, phone_number):
        cursor = self.conn.cursor()
        cursor.execute('UPDATE users SET phone_number = ? WHERE user_id = ?', (phone_number, user_id))
        self.conn.commit()

    def get_user_name(self, user_id):
        cursor = self.conn.cursor()
        cursor.execute('SELECT full_name FROM users WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        return result[0] if result and result[0] else None

    def get_user_phone(self, user_id):
        cursor = self.conn.cursor()
        cursor.execute('SELECT phone_number FROM users WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        return result[0] if result and result[0] else None

    def update_balance(self, user_id, amount):
        cursor = self.conn.cursor()
        cursor.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (amount, user_id))
        self.conn.commit()

    def get_balance(self, user_id):
        cursor = self.conn.cursor()
        cursor.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        return float(result[0]) if result and result[0] is not None else 0.0

    # ==================== REFERRAL ====================
    def get_referral_amount(self):
        cursor = self.conn.cursor()
        cursor.execute('SELECT value FROM settings WHERE key = ?', ('referral_amount',))
        result = cursor.fetchone()
        if result and result[0]:
            try:
                return float(result[0])
            except:
                return DEFAULT_REFERRAL_AMOUNT
        return DEFAULT_REFERRAL_AMOUNT

    def set_referral_amount(self, amount):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO settings (key, value) VALUES ('referral_amount', ?)
        ''', (str(amount),))
        self.conn.commit()

    def get_referral_code(self, user_id):
        cursor = self.conn.cursor()
        cursor.execute('SELECT referral_code FROM users WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        if result and result[0]:
            return result[0]
        code = f"REF{user_id}"
        cursor.execute('UPDATE users SET referral_code = ? WHERE user_id = ?', (code, user_id))
        self.conn.commit()
        return code

    def get_referrals_count(self, user_id):
        cursor = self.conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM users WHERE referred_by = ?', (user_id,))
        result = cursor.fetchone()
        return result[0] if result else 0

    def is_referral_bonus_paid(self, user_id):
        """Referral bonus berilganini tekshirish"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT referral_bonus_paid FROM users WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        if result and len(result) > 0:
            return bool(result[0])
        return False

    def mark_referral_bonus_paid(self, user_id):
        """Referral bonus berilganini belgilash"""
        cursor = self.conn.cursor()
        cursor.execute('UPDATE users SET referral_bonus_paid = 1 WHERE user_id = ?', (user_id,))
        self.conn.commit()

    def get_user_bots(self, user_id):
        """Foydalanuvchi yaratgan botlarni olish"""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT ub.id, ub.user_id, ub.bot_token, ub.bot_id, ub.status, ub.created_at,
                   ub.payment_date, ub.days_left,
                   b.bot_name, b.bot_file_path, b.run_command, b.price
            FROM user_bots ub
            LEFT JOIN bots b ON ub.bot_id = b.bot_id
            WHERE ub.user_id = ?
            ORDER BY ub.created_at DESC
        ''', (user_id,))
        return cursor.fetchall()

    def get_user_bot(self, user_bot_id):
        """Foydalanuvchi botini ID bo'yicha olish"""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT ub.id, ub.user_id, ub.bot_token, ub.bot_id, ub.status, ub.created_at,
                   ub.payment_date, ub.days_left,
                   b.bot_name, b.bot_file_path, b.run_command, b.price
            FROM user_bots ub
            LEFT JOIN bots b ON ub.bot_id = b.bot_id
            WHERE ub.id = ?
        ''', (user_bot_id,))
        return cursor.fetchone()

    def update_user_bot_status(self, user_bot_id, status):
        """Foydalanuvchi bot statusini yangilash"""
        cursor = self.conn.cursor()
        cursor.execute('UPDATE user_bots SET status = ? WHERE id = ?', (status, user_bot_id))
        self.conn.commit()

    def delete_user_bot(self, user_bot_id):
        """Foydalanuvchi botini o'chirish"""
        cursor = self.conn.cursor()
        cursor.execute('DELETE FROM user_bots WHERE id = ?', (user_bot_id,))
        self.conn.commit()

    # ==================== BOTLAR ====================
    def get_bots(self):
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM bots')
        rows = cursor.fetchall()
        result = []
        for row in rows:
            row_list = list(row)
            row_list[4] = float(row_list[4]) if row_list[4] is not None else 0.0
            result.append(tuple(row_list))
        return result

    def get_bot(self, bot_id):
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM bots WHERE bot_id = ?', (bot_id,))
        row = cursor.fetchone()
        if row:
            row_list = list(row)
            row_list[4] = float(row_list[4]) if row_list[4] is not None else 0.0
            return tuple(row_list)
        return None

    def add_bot(self, bot_name, bot_file_path, run_command, price):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO bots (bot_name, bot_file_path, run_command, price)
            VALUES (?, ?, ?, ?)
        ''', (bot_name, bot_file_path, run_command, price))
        self.conn.commit()
        return cursor.lastrowid

    def add_user_bot(self, user_id, bot_token, bot_id):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO user_bots (user_id, bot_token, bot_id) VALUES (?, ?, ?)
        ''', (user_id, bot_token, bot_id))
        self.conn.commit()
        return cursor.lastrowid

    def add_payment(self, user_id, amount, screenshot_path):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO payments (user_id, amount, screenshot_path) VALUES (?, ?, ?)
        ''', (user_id, amount, screenshot_path))
        self.conn.commit()
        return cursor.lastrowid

    # ==================== STATISTIKA ====================
    def get_total_users(self):
        cursor = self.conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM users')
        result = cursor.fetchone()
        return result[0] if result else 0

    def get_all_users(self):
        cursor = self.conn.cursor()
        cursor.execute('SELECT user_id, username, full_name, phone_number, balance, referral_code, referred_by, created_at FROM users ORDER BY created_at DESC')
        return cursor.fetchall()

    def get_active_users(self, days=30):
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT COUNT(DISTINCT user_id) FROM user_bots
            WHERE created_at >= datetime('now', ? || ' days')
        ''', (f'-{days}',))
        result = cursor.fetchone()
        return result[0] if result else 0

    def get_total_bots(self):
        cursor = self.conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM user_bots')
        result = cursor.fetchone()
        return result[0] if result else 0

    # ==================== OBUNALAR ====================
    def get_mandatory_subscriptions(self):
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM mandatory_subscriptions')
        return cursor.fetchall()

    def add_mandatory_subscription(self, channel_id, channel_username):
        cursor = self.conn.cursor()
        try:
            cursor.execute('INSERT INTO mandatory_subscriptions (channel_id, channel_username) VALUES (?, ?)', (channel_id, channel_username))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def remove_mandatory_subscription(self, channel_id):
        cursor = self.conn.cursor()
        cursor.execute('DELETE FROM mandatory_subscriptions WHERE channel_id = ?', (channel_id,))
        self.conn.commit()

    # ==================== TO'LOVLAR ====================
    def get_pending_payments(self):
        """Kutilayotgan to'lovlarni olish"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM payments WHERE status = ? ORDER BY created_at DESC', ('pending',))
        return cursor.fetchall()

    def get_payment(self, payment_id):
        """To'lovni olish"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM payments WHERE id = ?', (payment_id,))
        return cursor.fetchone()

    def update_payment_status(self, payment_id, status):
        """To'lov holatini yangilash"""
        cursor = self.conn.cursor()
        cursor.execute('UPDATE payments SET status = ? WHERE id = ?', (status, payment_id))
        self.conn.commit()

    # ==================== BANNED USERS ====================
    def is_banned(self, user_id):
        """Foydalanuvchi ban qilinganini tekshirish"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM banned_users WHERE user_id = ?', (user_id,))
        return cursor.fetchone() is not None

    def ban_user(self, user_id, reason=None):
        """Foydalanuvchini ban qilish"""
        cursor = self.conn.cursor()
        try:
            cursor.execute('INSERT INTO banned_users (user_id, reason) VALUES (?, ?)', (user_id, reason))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def unban_user(self, user_id):
        """Foydalanuvchini unban qilish"""
        cursor = self.conn.cursor()
        cursor.execute('DELETE FROM banned_users WHERE user_id = ?', (user_id,))
        self.conn.commit()

    # ==================== OYLIK TO'LOV ====================
    def update_user_bot_payment(self, user_bot_id, days_left):
        """Foydalanuvchi botining to'lov muddatini yangilash"""
        cursor = self.conn.cursor()
        cursor.execute('''
            UPDATE user_bots 
            SET payment_date = CURRENT_TIMESTAMP, days_left = ?
            WHERE id = ?
        ''', (days_left, user_bot_id))
        self.conn.commit()

    def get_user_bots_with_expiring_payments(self, days_threshold=5):
        """To'lov muddati tugayotgan botlarni olish"""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT ub.*, b.bot_name
            FROM user_bots ub
            LEFT JOIN bots b ON ub.bot_id = b.bot_id
            WHERE ub.days_left <= ? AND ub.days_left > 0 AND ub.status = 'active'
            ORDER BY ub.days_left ASC
        ''', (days_threshold,))
        return cursor.fetchall()

    def get_all_user_bots_for_payment_check(self):
        """Oylik to'lov tekshiruvi uchun barcha botlarni olish"""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT ub.*, b.bot_name
            FROM user_bots ub
            LEFT JOIN bots b ON ub.bot_id = b.bot_id
            WHERE ub.status = 'active'
        ''')
        return cursor.fetchall()

    def decrease_days_left(self):
        """Barcha botlar uchun qolgan kunlarni kamaytirish"""
        cursor = self.conn.cursor()
        cursor.execute('UPDATE user_bots SET days_left = days_left - 1 WHERE days_left > 0 AND status = "active"')
        self.conn.commit()

    def disable_expired_bots(self):
        """Muddati o'tgan botlarni o'chirish"""
        cursor = self.conn.cursor()
        cursor.execute('''
            UPDATE user_bots 
            SET status = 'expired', days_left = 0
            WHERE days_left <= 0 AND status = 'active'
        ''')
        self.conn.commit()
        return cursor.rowcount


# Database instance (oxirida)
db = Database()


async def check_subscription(user_id):
    """Majburiy obunalarni tekshirish"""
    subscriptions = db.get_mandatory_subscriptions()
    if not subscriptions:
        return True

    for sub in subscriptions:
        channel_id = sub[0]
        try:
            member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
            if member.status not in ['member', 'administrator', 'creator']:
                return False
        except Exception as e:
            logger.error(f"Error checking subscription: {e}")
            return False
    return True


async def require_subscription(message_or_callback):
    """Majburiy obuna tekshiruvini qaytarish - agar obuna bo'lmagan bo'lsa False"""
    if isinstance(message_or_callback, types.Message):
        user_id = message_or_callback.from_user.id
        is_subscribed = await check_subscription(user_id)
        if not is_subscribed:
            subscriptions = db.get_mandatory_subscriptions()
            keyboard_buttons = []
            for sub in subscriptions:
                channel_username = sub[1] or sub[0]
                keyboard_buttons.append([InlineKeyboardButton(
                    f"📢 {channel_username} ga obuna bo'lish",
                    url=f"https://t.me/{channel_username.replace('@', '')}"
                )])
            keyboard_buttons.append([InlineKeyboardButton("✅ Obuna bo'ldim", callback_data="check_subscription")])
            keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
            await message_or_callback.answer(
                "❌ Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling:",
                reply_markup=keyboard
            )
            return False
        return True
    elif isinstance(message_or_callback, types.CallbackQuery):
        user_id = message_or_callback.from_user.id
        is_subscribed = await check_subscription(user_id)
        if not is_subscribed:
            subscriptions = db.get_mandatory_subscriptions()
            keyboard_buttons = []
            for sub in subscriptions:
                channel_username = sub[1] or sub[0]
                keyboard_buttons.append([InlineKeyboardButton(
                    f"📢 {channel_username} ga obuna bo'lish",
                    url=f"https://t.me/{channel_username.replace('@', '')}"
                )])
            keyboard_buttons.append([InlineKeyboardButton("✅ Obuna bo'ldim", callback_data="check_subscription")])
            keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
            await message_or_callback.message.answer(
                "❌ Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling:",
                reply_markup=keyboard
            )
            await message_or_callback.answer("Iltimos, avval obuna bo'ling!", show_alert=True)
            return False
        return True
    return True


async def show_main_menu(message: types.Message):
    """Asosiy menu ko'rsatish"""
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    keyboard.add(
        types.KeyboardButton("👥 Referral chaqirish"),
        types.KeyboardButton("💼 Asosiy kabinet"),
        types.KeyboardButton("🤖 Bot yaratish"),
        types.KeyboardButton("🤖 Mening botlarim")
    )

    text = "🤖 Maker Bot ga xush kelibsiz!\n\n"
    text += "Quyidagi funksiyalardan foydalaning:"

    await message.answer(text, reply_markup=keyboard)


@dp.message_handler(commands=['start'])
async def start_handler(message: types.Message, state: FSMContext):
    """Start command handler"""
    user_id = message.from_user.id
    username = message.from_user.username or ""
    
    # Banned tekshiruvi
    if db.is_banned(user_id):
        await message.answer("❌ Siz botdan foydalanish huquqidan mahrum qilingansiz!")
        return

    # Foydalanuvchini yaratish
    user = db.get_user(user_id)
    if not user:
        # Referral kodini tekshirish
        referred_by = None
        args = message.text.split()[1:] if len(message.text.split()) > 1 else []
        if args:
            referral_code = args[0]
            cursor = db.conn.cursor()
            cursor.execute('SELECT user_id FROM users WHERE referral_code = ?', (referral_code,))
            ref_user = cursor.fetchone()
            if ref_user:
                referred_by = ref_user[0]

        # Foydalanuvchini yaratish (telefon raqamisiz)
        db.create_user(user_id, username, referred_by=referred_by)

        # Telefon raqamini so'rash
        await RegistrationStates.waiting_name.set()
        await state.update_data(referred_by=referred_by)

        # Contact button yaratish
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        keyboard.add(types.KeyboardButton("📱 Telefon raqamini ulashish", request_contact=True))

        await message.answer("👋 Salom! Botdan foydalanish uchun telefon raqamingizni ulashing:", reply_markup=keyboard)
        return

    # Agar foydalanuvchi mavjud bo'lsa, telefon raqamini tekshirish
    user_phone = db.get_user_phone(user_id)
    if not user_phone:
        # Telefon raqami kiritilmagan, telefon raqamini so'rash
        await RegistrationStates.waiting_name.set()

        # Contact button yaratish
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        keyboard.add(types.KeyboardButton("📱 Telefon raqamini ulashish", request_contact=True))

        await message.answer("👋 Botdan foydalanish uchun telefon raqamingizni ulashing:", reply_markup=keyboard)
        return

    # Ism mavjud, majburiy obunani tekshirish
    await check_subscription_and_continue(message, state, user_id)


async def check_subscription_and_continue(message: types.Message, state: FSMContext, user_id: int):
    """Majburiy obunani tekshirish va davom etish"""
    # Majburiy obunani tekshirish
    is_subscribed = await check_subscription(user_id)

    if not is_subscribed:
        subscriptions = db.get_mandatory_subscriptions()
        keyboard_buttons = []
        for sub in subscriptions:
            channel_username = sub[1] or sub[0]
            keyboard_buttons.append([InlineKeyboardButton(
                f"📢 {channel_username} ga obuna bo'lish",
                url=f"https://t.me/{channel_username.replace('@', '')}"
            )])
        keyboard_buttons.append([InlineKeyboardButton("✅ Obuna bo'ldim", callback_data="check_subscription")])

        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        await message.answer(
            "👋 Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling:",
            reply_markup=keyboard
        )
        return

    # Obuna bo'lgan, referral bonusni berish (faqat birinchi marta obuna bo'lganda)
    user = db.get_user(user_id)
    if user:
        # referred_by ni to'g'ri indexdan olish
        # users jadvali: user_id(0), username(1), full_name(2), phone_number(3), balance(4), referral_code(5), referred_by(6), created_at(7), referral_bonus_paid(8)
        referred_by = None
        if len(user) > 6:
            referred_by = user[6]
        
        # Referral bonusni faqat birinchi marta obuna bo'lganda berish
        if referred_by and not db.is_referral_bonus_paid(user_id):
            referral_amount = db.get_referral_amount()
            
            # Balansga qo'shish
            old_balance = db.get_balance(referred_by)
            db.update_balance(referred_by, referral_amount)
            new_balance = db.get_balance(referred_by)
            
            # Referral bonus berilganini belgilash
            db.mark_referral_bonus_paid(user_id)
            
            logger.info(f"Referral bonus berildi: user_id={user_id}, referred_by={referred_by}, amount={referral_amount}, old_balance={old_balance}, new_balance={new_balance}")

            # Referral bergan foydalanuvchiga xabar
            try:
                user_name = db.get_user_name(user_id) or message.from_user.username or "Foydalanuvchi"
                username_display = f"@{message.from_user.username}" if message.from_user.username else user_name
                await bot.send_message(
                    chat_id=referred_by,
                    text=f"🎉 Yangi referal!\n\n"
                         f"👤 {username_display} botga qo'shildi va obuna bo'ldi\n"
                         f"💰 Balansingizga {referral_amount} so'm qo'shildi\n"
                         f"💵 Joriy balans: {new_balance} so'm"
                )
            except Exception as e:
                # Chat topilmasa yoki foydalanuvchi botni bloklagan bo'lsa, xatoni log qilamiz
                error_msg = str(e)
                if "Chat not found" in error_msg or "chat not found" in error_msg.lower():
                    logger.warning(
                        f"Referral xabarini yuborib bo'lmadi: Foydalanuvchi {referred_by} botni bloklagan yoki chat topilmadi")
                else:
                    logger.error(f"Error sending referral message: {e}")

    # Asosiy menu
    await show_main_menu(message)


@dp.message_handler(state=RegistrationStates.waiting_name, content_types=['contact'])
async def process_phone_contact(message: types.Message, state: FSMContext):
    """Telefon raqamini contact orqali qabul qilish"""
    user_id = message.from_user.id

    if message.contact:
        phone_number = message.contact.phone_number
        # + belgisini qo'shish agar yo'q bo'lsa
        if not phone_number.startswith('+'):
            phone_number = '+' + phone_number

        # Telefon raqamini saqlash
        db.update_user_phone(user_id, phone_number)

        # Ismni ham saqlash (agar contact da mavjud bo'lsa)
        if message.contact.first_name:
            full_name = message.contact.first_name
            if message.contact.last_name:
                full_name += ' ' + message.contact.last_name
            db.update_user_name(user_id, full_name)

        # Keyboard ni olib tashlash
        remove_keyboard = types.ReplyKeyboardRemove()
        await message.answer("✅ Telefon raqamingiz qabul qilindi!", reply_markup=remove_keyboard)

        # Majburiy obunani tekshirish
        await state.finish()
        await check_subscription_and_continue(message, state, user_id)
    else:
        # Contact yuborilmagan
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        keyboard.add(types.KeyboardButton("📱 Telefon raqamini ulashish", request_contact=True))
        await message.answer("❌ Iltimos, telefon raqamingizni ulashing:", reply_markup=keyboard)


@dp.message_handler(commands=['admin'])
async def admin_handler(message: types.Message):
    """Admin panel"""
    if message.from_user.id != ADMIN_ID:
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("➕ Majburiy obuna qo'shish", callback_data="admin_add_sub")],
        [InlineKeyboardButton("➖ Majburiy obunani olib tashlash", callback_data="admin_remove_sub")],
        [InlineKeyboardButton("🤖 Bot qo'shish", callback_data="admin_add_bot")],
        [InlineKeyboardButton("👥 Foydalanuvchilar", callback_data="admin_users")],
        [InlineKeyboardButton("📊 Aktiv foydalanuvchilar", callback_data="admin_active_users")],
        [InlineKeyboardButton("👤 Umumiy foydalanuvchilar", callback_data="admin_total_users")],
        [InlineKeyboardButton("🤖 Botlar", callback_data="admin_bots")],
        [InlineKeyboardButton("💳 Balans to'ldirish", callback_data="admin_topup")],
        [InlineKeyboardButton("💰 Referral summani o'zgartirish", callback_data="admin_change_referral")]
    ])
    await message.answer("🔐 Admin panel", reply_markup=keyboard)


@dp.callback_query_handler(lambda c: c.data == "check_subscription")
async def check_subscription_callback(callback_query: types.CallbackQuery, state: FSMContext):
    """Obuna tekshirish callback"""
    await callback_query.answer()
    user_id = callback_query.from_user.id

    is_subscribed = await check_subscription(user_id)
    if is_subscribed:
        # Obuna bo'lgan, referral bonusni berish (faqat birinchi marta obuna bo'lganda)
        user = db.get_user(user_id)
        if user:
            # referred_by ni to'g'ri indexdan olish
            referred_by = None
            if len(user) > 6:
                referred_by = user[6]
            
            # Referral bonusni faqat birinchi marta obuna bo'lganda berish
            if referred_by and not db.is_referral_bonus_paid(user_id):
                referral_amount = db.get_referral_amount()
                
                # Balansga qo'shish
                old_balance = db.get_balance(referred_by)
                db.update_balance(referred_by, referral_amount)
                new_balance = db.get_balance(referred_by)
                
                # Referral bonus berilganini belgilash
                db.mark_referral_bonus_paid(user_id)
                
                logger.info(f"Referral bonus berildi (callback): user_id={user_id}, referred_by={referred_by}, amount={referral_amount}, old_balance={old_balance}, new_balance={new_balance}")

                # Referral bergan foydalanuvchiga xabar
                try:
                    user_name = db.get_user_name(user_id) or callback_query.from_user.username or "Foydalanuvchi"
                    username_display = f"@{callback_query.from_user.username}" if callback_query.from_user.username else user_name
                    await bot.send_message(
                        chat_id=referred_by,
                        text=f"🎉 Yangi referal!\n\n"
                             f"👤 {username_display} botga qo'shildi va obuna bo'ldi\n"
                             f"💰 Balansingizga {referral_amount} so'm qo'shildi\n"
                             f"💵 Joriy balans: {new_balance} so'm"
                    )
                except Exception as e:
                    # Chat topilmasa yoki foydalanuvchi botni bloklagan bo'lsa, xatoni log qilamiz
                    error_msg = str(e)
                    if "Chat not found" in error_msg or "chat not found" in error_msg.lower():
                        logger.warning(
                            f"Referral xabarini yuborib bo'lmadi: Foydalanuvchi {referred_by} botni bloklagan yoki chat topilmadi")
                    else:
                        logger.error(f"Error sending referral message: {e}")

        await show_main_menu(callback_query.message)
    else:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton("🔄 Qayta tekshirish", callback_data="check_subscription")
        ]])
        await callback_query.message.edit_text(
            "❌ Siz hali obuna bo'lmadingiz. Iltimos, barcha kanallarga obuna bo'ling va qayta urinib ko'ring.",
            reply_markup=keyboard
        )


@dp.message_handler(lambda message: message.text == "🔙 Asosiy menu")
async def main_menu_handler(message: types.Message):
    """Asosiy menu handler"""
    if not await require_subscription(message):
        return
    await show_main_menu(message)


@dp.callback_query_handler(lambda c: c.data == "main_menu")
async def main_menu_callback(callback_query: types.CallbackQuery):
    """Asosiy menu callback (inline button uchun)"""
    await callback_query.answer()
    await show_main_menu(callback_query.message)


@dp.message_handler(lambda message: message.text == "👥 Referral chaqirish")
async def referral_handler(message: types.Message):
    """Referral handler"""
    if not await require_subscription(message):
        return
    
    user_id = message.from_user.id

    referral_code = db.get_referral_code(user_id)
    referrals_count = db.get_referrals_count(user_id)
    referral_amount = db.get_referral_amount()
    bot_username = (await bot.get_me()).username

    text = f"👥 Referral tizimi\n\n"
    text += f"📝 Sizning referral kodingiz: `{referral_code}`\n"
    text += f"👤 Jami chaqirganlar: {referrals_count}\n"
    text += f"💰 Har bir referral uchun: {referral_amount} so'm\n"
    text += f"🔗 Referral havola:\n"
    text += f"`https://t.me/{bot_username}?start={referral_code}`\n\n"
    text += "Do'stlaringizni taklif qiling va har bir chaqirgan do'stingiz uchun pul oling!"

    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    keyboard.add(types.KeyboardButton("🔙 Asosiy menu"))

    await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")


@dp.message_handler(lambda message: message.text == "💼 Asosiy kabinet")
async def cabinet_handler(message: types.Message):
    """Kabinet handler"""
    if not await require_subscription(message):
        return
    
    user_id = message.from_user.id

    balance = db.get_balance(user_id)
    referrals_count = db.get_referrals_count(user_id)

    text = f"💼 Asosiy kabinet\n\n"
    text += f"💰 Balans: {balance} so'm\n"
    text += f"👥 Referrallar: {referrals_count}\n"

    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    keyboard.add(
        types.KeyboardButton("💳 Balans to'ldirish"),
        types.KeyboardButton("🔙 Asosiy menu")
    )

    await message.answer(text, reply_markup=keyboard)


@dp.message_handler(lambda message: message.text == "💳 Balans to'ldirish")
async def topup_balance_handler(message: types.Message, state: FSMContext):
    """Balans to'ldirish handler"""
    if not await require_subscription(message):
        return
    
    user_id = message.from_user.id
    
    text = f"💳 Balans to'ldirish\n\n"
    text += f"Karta raqami: `{PAYMENT_CARD}`\n"
    text += f"To'lov summasini kiriting (so'm):\n\n"
    text += "To'lov qilganingizdan keyin skrinshot yuboring."

    await PaymentStates.waiting_amount.set()

    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    keyboard.add(types.KeyboardButton("🔙 Bekor qilish"))

    await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")


@dp.message_handler(state=PaymentStates.waiting_amount)
async def process_payment_amount(message: types.Message, state: FSMContext):

    # BEKOR QILISH
    if message.text == "🔙 Bekor qilish":
        await message.answer(
            "Bekor qilindi✅",
            reply_markup=await show_main_menu(message)
        )
        await state.finish()
        return

    """To'lov summasini qabul qilish"""
    try:
        amount = float(message.text)
    except ValueError:
        await message.answer("❌ Noto'g'ri summa! Iltimos, raqam kiriting.")
        return

    await state.update_data(amount=amount)
    await PaymentStates.waiting_screenshot.set()

    await message.answer(
        f"✅ Summa qabul qilindi: {amount} so'm\n\n"
        "Endi to'lov skrinshotini yuboring:"
    )


@dp.message_handler(state=PaymentStates.waiting_screenshot, content_types=['photo'])
async def process_payment_screenshot(message: types.Message, state: FSMContext):
    """To'lov skrinshotini qabul qilish"""
    user_id = message.from_user.id
    data = await state.get_data()
    amount = data.get('amount', 0)

    # Skrinshotni saqlash
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    screenshot_path = f"payments/{user_id}_{datetime.now().timestamp()}.jpg"
    os.makedirs("payments", exist_ok=True)
    await file.download(destination_file=screenshot_path)

    # To'lovni bazaga qo'shish
    payment_id = db.add_payment(user_id, amount, screenshot_path)

    # Adminga xabar yuborish inline tugmalar bilan
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"approve_payment_{payment_id}")],
        [InlineKeyboardButton("❌ Rad etish", callback_data=f"reject_payment_{payment_id}")]
    ])
    
    try:
        await bot.send_photo(
            chat_id=ADMIN_ID,
            photo=photo.file_id,
            caption=f"💳 Yangi to'lov so'rovi\n\n"
                    f"👤 Foydalanuvchi ID: {user_id}\n"
                    f"👤 Username: @{message.from_user.username or 'N/A'}\n"
                    f"💰 Summa: {amount} so'm",
            reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"To'lov xabarini yuborishda xatolik: {e}")
        # Agar rasm yuborib bo'lmasa, faqat matn yuborish
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=f"💳 Yangi to'lov so'rovi\n\n"
                 f"👤 Foydalanuvchi ID: {user_id}\n"
                 f"👤 Username: @{message.from_user.username or 'N/A'}\n"
                 f"💰 Summa: {amount} so'm",
            reply_markup=keyboard
        )

    await state.finish()
    await message.answer(
        "✅ To'lov so'rovi adminga yuborildi. Tez orada balansingiz to'ldiriladi."
    )


@dp.message_handler(lambda message: message.text == "🤖 Bot yaratish")
async def create_bot_handler(message: types.Message):
    """Bot yaratish handler"""
    if not await require_subscription(message):
        return
    
    user_id = message.from_user.id
    bots = db.get_bots()
    if not bots:
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
        keyboard.add(types.KeyboardButton("🔙 Asosiy menu"))
        await message.answer(
            "❌ Hozircha mavjud botlar yo'q.",
            reply_markup=keyboard
        )
        return

    balance = db.get_balance(user_id)
    text = f"🤖 Bot yaratish\n\n💰 Sizning balansingiz: {balance} so'm\n\nQuyidagi botlardan birini tanlang:\n\n"
    keyboard_buttons = []
    for bot_data in bots:
        bot_id = bot_data[0]
        bot_name = bot_data[1]
        price = bot_data[4]  # endi bu har doim float

        text += f"• {bot_name} — {price:,.0f} so'm\n".replace(',', ' ')  # chiroyli format
        keyboard_buttons.append([InlineKeyboardButton(
            f"🤖 {bot_name} — {price:,.0f} so'm".replace(',', ' '),
            callback_data=f"select_bot_{bot_id}"
        )])

    keyboard_buttons.append([InlineKeyboardButton("🔙 Asosiy menu", callback_data="main_menu")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    await message.answer(text, reply_markup=keyboard)


@dp.message_handler(lambda message: message.text == "🤖 Mening botlarim")
async def my_bots_handler(message: types.Message):
    """Mening botlarim handler"""
    if not await require_subscription(message):
        return
    
    user_id = message.from_user.id
    user_bots = db.get_user_bots(user_id)
    
    if not user_bots:
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
        keyboard.add(types.KeyboardButton("🔙 Asosiy menu"))
        await message.answer(
            "🤖 Siz hali hech qanday bot yaratmadingiz.\n\nBot yaratish uchun '🤖 Bot yaratish' tugmasini bosing.",
            reply_markup=keyboard
        )
        return
    
    text = "🤖 Mening botlarim\n\n"
    keyboard_buttons = []
    
    for bot_data in user_bots:
        # bot_data: id, user_id, bot_token, bot_id, status, created_at, payment_date, days_left, bot_name, bot_file_path, run_command, price
        bot_id = bot_data[0]
        bot_name = bot_data[8] or "Noma'lum bot"
        status = bot_data[4] or "active"
        status_emoji = "🟢" if status == "active" else "🔴"
        status_text = "Ishlamoqda" if status == "active" else "To'xtatilgan"
        
        text += f"{status_emoji} {bot_name} - {status_text}\n"
        keyboard_buttons.append([InlineKeyboardButton(
            f"{status_emoji} {bot_name}",
            callback_data=f"my_bot_{bot_id}"
        )])
    
    keyboard_buttons.append([InlineKeyboardButton("🔙 Asosiy menu", callback_data="main_menu")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    
    await message.answer(text, reply_markup=keyboard)


@dp.callback_query_handler(lambda c: c.data.startswith("my_bot_"))
async def my_bot_detail_callback(callback_query: types.CallbackQuery):
    """Foydalanuvchi botini batafsil ko'rish"""
    if not await require_subscription(callback_query):
        return
    
    await callback_query.answer()
    user_id = callback_query.from_user.id
    bot_id = int(callback_query.data.split("_")[2])
    
    bot_data = db.get_user_bot(bot_id)
    if not bot_data or bot_data[1] != user_id:  # bot_data[1] = user_id
        await callback_query.answer("Bot topilmadi!", show_alert=True)
        return
    
    # bot_data: id, user_id, bot_token, bot_id, status, created_at, payment_date, days_left, bot_name, bot_file_path, run_command, price
    bot_name = bot_data[8] or "Noma'lum bot"
    status = bot_data[4] or "active"
    status_emoji = "🟢" if status == "active" else "🔴"
    status_text = "Ishlamoqda" if status == "active" else "To'xtatilgan"
    created_at = bot_data[5] or "N/A"
    
    text = f"🤖 {bot_name}\n\n"
    text += f"📊 Holat: {status_emoji} {status_text}\n"
    text += f"📅 Yaratilgan: {created_at}\n\n"
    text += "Quyidagi amallardan birini tanlang:"
    
    keyboard_buttons = []
    if status != "active":
        keyboard_buttons.append([InlineKeyboardButton("▶️ Ishga tushirish", callback_data=f"start_bot_{bot_id}")])
    keyboard_buttons.append([InlineKeyboardButton("🗑 O'chirish", callback_data=f"delete_my_bot_{bot_id}")])
    keyboard_buttons.append([InlineKeyboardButton("🔙 Orqaga", callback_data="my_bots_list")])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    await callback_query.message.edit_text(text, reply_markup=keyboard)


@dp.callback_query_handler(lambda c: c.data == "my_bots_list")
async def my_bots_list_callback(callback_query: types.CallbackQuery):
    """Mening botlarim ro'yxatini ko'rsatish"""
    if not await require_subscription(callback_query):
        return
    
    await callback_query.answer()
    user_id = callback_query.from_user.id
    user_bots = db.get_user_bots(user_id)
    
    if not user_bots:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton("🔙 Asosiy menu", callback_data="main_menu")
        ]])
        await callback_query.message.edit_text(
            "🤖 Siz hali hech qanday bot yaratmadingiz.",
            reply_markup=keyboard
        )
        return
    
    text = "🤖 Mening botlarim\n\n"
    keyboard_buttons = []
    
    for bot_data in user_bots:
        bot_id = bot_data[0]
        bot_name = bot_data[8] or "Noma'lum bot"
        status = bot_data[4] or "active"
        status_emoji = "🟢" if status == "active" else "🔴"
        status_text = "Ishlamoqda" if status == "active" else "To'xtatilgan"
        
        text += f"{status_emoji} {bot_name} - {status_text}\n"
        keyboard_buttons.append([InlineKeyboardButton(
            f"{status_emoji} {bot_name}",
            callback_data=f"my_bot_{bot_id}"
        )])
    
    keyboard_buttons.append([InlineKeyboardButton("🔙 Asosiy menu", callback_data="main_menu")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    
    await callback_query.message.edit_text(text, reply_markup=keyboard)


@dp.callback_query_handler(lambda c: c.data.startswith("start_bot_"))
async def start_bot_callback(callback_query: types.CallbackQuery):
    """Botni ishga tushirish"""
    if not await require_subscription(callback_query):
        return
    
    await callback_query.answer()
    user_id = callback_query.from_user.id
    bot_id = int(callback_query.data.split("_")[2])
    
    bot_data = db.get_user_bot(bot_id)
    if not bot_data or bot_data[1] != user_id:
        await callback_query.answer("Bot topilmadi!", show_alert=True)
        return
    
    # Botni ishga tushirish
    bot_name = bot_data[8] or "Noma'lum bot"
    bot_file_path = bot_data[9]
    run_command = bot_data[10] or "python main.py"
    bot_token = bot_data[2]
    
    try:
        import zipfile, shutil, time, sys, os, re
        from datetime import datetime
        
        # Bot papkasini topish
        user_bot_dir = None
        for root, dirs, files in os.walk("user_bots"):
            for dir_name in dirs:
                if f"bot_{user_id}_{bot_data[3]}_" in dir_name:  # bot_data[3] = bot_id
                    user_bot_dir = os.path.join(root, dir_name)
                    break
            if user_bot_dir:
                break
        
        if not user_bot_dir or not os.path.exists(user_bot_dir):
            await callback_query.answer("Bot papkasi topilmadi!", show_alert=True)
            return
        
        # main.py topish
        main_py = None
        for root, _, files in os.walk(user_bot_dir):
            for file in files:
                if file.lower() in ['main.py', 'bot.py', 'start.py', 'index.py', 'app.py']:
                    main_py = os.path.join(root, file)
                    break
            if main_py:
                break
        
        if not main_py or not os.path.exists(main_py):
            await callback_query.answer("Bot fayli topilmadi!", show_alert=True)
            return
        
        # Log fayl
        log_file = os.path.join(user_bot_dir, "log.txt")
        
        # Botni ishga tushirish
        env = os.environ.copy()
        env["MAKER_USER_ID"] = str(user_id)
        
        subprocess.Popen(
            [sys.executable, main_py],
            cwd=user_bot_dir,
            env=env,
            stdout=open(log_file, "a", encoding="utf-8"),
            stderr=open(log_file, "a", encoding="utf-8"),
            creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
        )
        
        # Statusni yangilash
        db.update_user_bot_status(bot_id, "active")
        
        await callback_query.answer("Bot ishga tushirildi!", show_alert=True)
        
        # Qayta botlar ro'yxatini ko'rsatish
        await my_bots_list_callback(callback_query)
        
    except Exception as e:
        logger.error(f"Botni ishga tushirishda xatolik: {e}")
        await callback_query.answer(f"Xatolik: {str(e)[:100]}", show_alert=True)


@dp.callback_query_handler(lambda c: c.data.startswith("delete_my_bot_"))
async def delete_my_bot_callback(callback_query: types.CallbackQuery):
    """Foydalanuvchi botini o'chirish"""
    if not await require_subscription(callback_query):
        return
    
    await callback_query.answer()
    user_id = callback_query.from_user.id
    bot_id = int(callback_query.data.split("_")[3])
    
    bot_data = db.get_user_bot(bot_id)
    if not bot_data or bot_data[1] != user_id:
        await callback_query.answer("Bot topilmadi!", show_alert=True)
        return
    
    # Botni o'chirish
    db.delete_user_bot(bot_id)
    
    # Bot papkasini o'chirish (ixtiyoriy)
    try:
        import shutil
        for root, dirs, files in os.walk("user_bots"):
            for dir_name in dirs:
                if f"bot_{user_id}_{bot_data[3]}_" in dir_name:
                    bot_dir = os.path.join(root, dir_name)
                    shutil.rmtree(bot_dir, ignore_errors=True)
                    break
    except:
        pass
    
    await callback_query.answer("Bot o'chirildi!", show_alert=True)
    
    # Qayta botlar ro'yxatini ko'rsatish
    await my_bots_list_callback(callback_query)


@dp.callback_query_handler(lambda c: c.data.startswith("select_bot_"))
async def select_bot_callback(callback_query: types.CallbackQuery, state: FSMContext):
    """Bot tanlash callback"""
    if not await require_subscription(callback_query):
        return
    
    await callback_query.answer()
    user_id = callback_query.from_user.id

    bot_id = int(callback_query.data.split("_")[2])
    bot_data = db.get_bot(bot_id)
    if not bot_data:
        await callback_query.answer("Bot topilmadi!", show_alert=True)
        return

    bot_id_db, bot_name, _, run_command, price = bot_data[0], bot_data[1], bot_data[2], bot_data[3], bot_data[4]
    balance = db.get_balance(user_id)

    if balance < price:
        await callback_query.answer(
            f"❌ Balansingiz yetmadi!\n\nBot narxi: {price} so'm\nSizning balansingiz: {balance} so'm",
            show_alert=True
        )
        return

    await state.update_data(selected_bot_id=bot_id_db)
    await BotCreationStates.waiting_token.set()

    text = f"🤖 {bot_name}\n\n"
    text += f"💰 Narx: {price} so'm\n"
    text += f"💵 Sizning balansingiz: {balance} so'm\n\n"
    text += "Bot tokenini yuboring:"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton("🔙 Orqaga", callback_data="create_bot")
    ]])
    await callback_query.message.edit_text(text, reply_markup=keyboard)


@dp.callback_query_handler(lambda c: c.data == "create_bot")
async def create_bot_callback(callback_query: types.CallbackQuery):
    """Bot yaratish menyusiga qaytish"""
    if not await require_subscription(callback_query):
        return
    
    await callback_query.answer()
    user_id = callback_query.from_user.id
    bots = db.get_bots()
    
    if not bots:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton("🔙 Asosiy menu", callback_data="main_menu")
        ]])
        await callback_query.message.edit_text(
            "❌ Hozircha mavjud botlar yo'q.",
            reply_markup=keyboard
        )
        return
    
    balance = db.get_balance(user_id)
    text = f"🤖 Bot yaratish\n\n💰 Sizning balansingiz: {balance} so'm\n\nQuyidagi botlardan birini tanlang:\n\n"
    keyboard_buttons = []
    for bot_data in bots:
        bot_id = bot_data[0]
        bot_name = bot_data[1]
        price = bot_data[4]
        
        text += f"• {bot_name} — {price:,.0f} so'm\n".replace(',', ' ')
        keyboard_buttons.append([InlineKeyboardButton(
            f"🤖 {bot_name} — {price:,.0f} so'm".replace(',', ' '),
            callback_data=f"select_bot_{bot_id}"
        )])
    
    keyboard_buttons.append([InlineKeyboardButton("🔙 Asosiy menu", callback_data="main_menu")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    await callback_query.message.edit_text(text, reply_markup=keyboard)


@dp.message_handler(state=BotCreationStates.waiting_token)
async def process_bot_token(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    # Majburiy obunani tekshirish
    if not await require_subscription(message):
        await state.finish()
        return
    
    data = await state.get_data()
    bot_id = data.get('selected_bot_id')

    if not bot_id:
        await message.answer("Xatolik yuz berdi! Qaytadan boshlang.")
        await state.finish()
        return

    bot_data = db.get_bot(bot_id)
    if not bot_data:
        await message.answer("Bot topilmadi!")
        await state.finish()
        return

    bot_name = bot_data[1]
    bot_file_path = bot_data[2]
    price = float(bot_data[4])
    token = message.text.strip()

    if db.get_balance(user_id) < price:
        await message.answer("Balansingiz yetarli emas!")
        await state.finish()
        return

    db.update_balance(user_id, -price)

    try:
        import zipfile, shutil, time, sys, os, re
        from datetime import datetime

        timestamp = int(time.time())
        user_bot_dir = f"user_bots/bot_{user_id}_{bot_id}_{timestamp}"
        os.makedirs(user_bot_dir, exist_ok=True)

        # ZIP extract
        if bot_file_path.lower().endswith('.zip'):
            with zipfile.ZipFile(bot_file_path, 'r') as z:
                z.extractall(user_bot_dir)
        else:
            shutil.copy2(bot_file_path, user_bot_dir)

        # Token almashtirish
        for root, _, files in os.walk(user_bot_dir):
            for file in files:
                if file.endswith('.py'):
                    path = os.path.join(root, file)
                    try:
                        async with aiofiles.open(path, 'r', encoding='utf-8') as f:
                            content = await f.read()
                        content = content.replace("YOUR_BOT_TOKEN", token)
                        content = re.sub(r'(BOT_TOKEN|API_TOKEN|token|API_KEY)\s*[:=]\s*[\'"].*?[\'"]', f'\\1 = "{token}"', content, flags=re.IGNORECASE)
                        async with aiofiles.open(path, 'w', encoding='utf-8') as f:
                            await f.write(content)
                    except Exception as e:
                        logger.warning(f"Token xato {path}: {e}")

        # main.py topish
        main_py = None
        for root, _, files in os.walk(user_bot_dir):
            for file in files:
                if file.lower() in ['main.py', 'bot.py', 'start.py', 'index.py', 'app.py']:
                    main_py = os.path.join(root, file)
                    break
            if main_py:
                break
        if not main_py:
            for root, _, files in os.walk(user_bot_dir):
                for file in files:
                    if file.endswith('.py'):
                        main_py = os.path.join(root, file)
                        break
                if main_py:
                    break

        if not main_py or not os.path.exists(main_py):
            await message.answer("Bot faylida .py topilmadi!")
            await state.finish()
            return

        # Yo'lni 100% to'g'rilash — ikki marta qo'shilmaydi!
        main_py = os.path.abspath(main_py)
        user_bot_dir_abs = os.path.abspath(user_bot_dir)

        if user_bot_dir_abs.replace('\\', '/') in main_py.replace('\\', '/'):
            parts = main_py.replace('\\', '/').split(user_bot_dir_abs.replace('\\', '/'))
            main_py = user_bot_dir_abs + parts[-1].replace('/', os.sep)

        # Oxirgi tekshiruv
        if not os.path.isfile(main_py):
            await message.answer("main.py topilmadi — yo‘l buzildi!")
            await state.finish()
            return

        # Log
        log_file = os.path.join(user_bot_dir, "log.txt")
        with open(log_file, "w", encoding="utf-8") as f:
            f.write(f"Bot ishga tushirildi: {datetime.now()}\n")
            f.write(f"Python: {sys.executable}\n")
            f.write(f"To'g'rilangan yo'l: {main_py}\n")
            f.write(f"Token: {token[:10]}...{token[-4:]}\n\n")

        # ==== YANGI QO‘SHILGAN QISM — FOYDALANUVCHINI AVTO ADMIN QILISH ====
        env = os.environ.copy()
        env["MAKER_USER_ID"] = str(user_id)   # <--- Bu yerga foydalanuvchi ID si yoziladi
        # ====================================================================

        # Ishga tushirish (env bilan — endi bot ichida os.getenv("MAKER_USER_ID") ishlaydi)
        subprocess.Popen(
            [sys.executable, main_py],
            cwd=user_bot_dir,
            env=env,  # <--- YANGI: muhit o‘zgaruvchilari uzatilmoqda
            stdout=open(log_file, "a", encoding="utf-8"),
            stderr=open(log_file, "a", encoding="utf-8"),
            creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
        )

        db.add_user_bot(user_id, token, bot_id)

        await message.answer(
            f"Bot muvaffaqiyatli yaratildi va ISHGA TUSHDI!\n\n"
            f"Bot: {bot_name}\n"
            f"To'landi: {price:,.0f} so'm\n"
            f"Qoldiq: {db.get_balance(user_id):,.0f} so'm\n"
            f"Papka: <code>{user_bot_dir}</code>\n"
            f"Log: <code>{log_file}</code>\n\n"
            f"Agar ishlamasa — log.txt ni oching!",
            parse_mode="HTML"
        )
        await state.finish()

    except Exception as e:
        logger.error(f"BOT YARATISH XATOSI (user {user_id}): {e}")
        await message.answer(f"Xatolik:\n<code>{str(e)[:500]}</code>", parse_mode="HTML")
        await state.finish()


@dp.message_handler(commands=['logs'])
async def cmd_view_logs(message: types.Message):
    """Foydalanuvchi o'zining bot loglarini ko'rishi uchun"""
    user_id = message.from_user.id
    
    # Foydalanuvchining botini topish
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT bot_id FROM user_bots WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,))
    res = cursor.fetchone()
    conn.close()
    
    if not res:
        await message.answer("Sizda hali bot yo'q!")
        return
        
    # Oxirgi bot papkasini topish
    import glob
    bot_dirs = sorted(glob.glob(f"user_bots/bot_{user_id}_*"), reverse=True)
    if not bot_dirs:
        await message.answer("Bot loglari topilmadi!")
        return
        
    log_file = os.path.join(bot_dirs[0], "log.txt")
    if os.path.exists(log_file):
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                logs = f.read()[-1000:] 
            await message.answer(f"📋 **Bot loglari (oxirgi 1000 belgi):**\n\n<code>{logs}</code>", parse_mode="HTML")
        except Exception as e:
            await message.answer(f"Log o'qishda xato: {e}")
    else:
        await message.answer("Log fayli hali yaratilmagan.")

# Admin callbacks
@dp.callback_query_handler(lambda c: c.data == "admin_add_sub")
async def admin_add_sub_callback(callback_query: types.CallbackQuery, state: FSMContext):
    """Admin: Majburiy obuna qo'shish"""
    if callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("Siz admin emassiz!", show_alert=True)
        return

    await callback_query.answer()
    await AdminStates.waiting_channel.set()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton("🔙 Orqaga", callback_data="admin_panel")
    ]])
    await callback_query.message.edit_text(
        "Kanal username ni kiriting (masalan: @channel_username):",
        reply_markup=keyboard
    )


@dp.message_handler(state=AdminStates.waiting_channel)
async def process_admin_channel(message: types.Message, state: FSMContext):
    """Admin: Kanal username qabul qilish"""
    if message.from_user.id != ADMIN_ID:
        return

    channel_username = message.text.strip()
    if not channel_username.startswith('@'):
        channel_username = '@' + channel_username

    try:
        # Kanalni tekshirish
        chat = await bot.get_chat(channel_username)
        channel_id = str(chat.id)

        if db.add_mandatory_subscription(channel_id, channel_username):
            await message.answer(f"✅ Kanal qo'shildi: {channel_username}")
        else:
            await message.answer("❌ Kanal allaqachon qo'shilgan!")

        await state.finish()
    except Exception as e:
        await message.answer(f"❌ Xatolik: {str(e)}")
        await state.finish()


@dp.callback_query_handler(lambda c: c.data == "admin_remove_sub")
async def admin_remove_sub_callback(callback_query: types.CallbackQuery):
    """Admin: Majburiy obunani olib tashlash"""
    if callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("Siz admin emassiz!", show_alert=True)
        return

    await callback_query.answer()

    subscriptions = db.get_mandatory_subscriptions()
    if not subscriptions:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton("🔙 Orqaga", callback_data="admin_panel")
        ]])
        await callback_query.message.edit_text(
            "Majburiy obunalar mavjud emas.",
            reply_markup=keyboard
        )
        return

    keyboard_buttons = []
    for sub in subscriptions:
        channel_id, channel_username = sub[0], sub[1]
        keyboard_buttons.append([InlineKeyboardButton(
            f"❌ {channel_username or channel_id}",
            callback_data=f"admin_remove_sub_{channel_id}"
        )])
    keyboard_buttons.append([InlineKeyboardButton("🔙 Orqaga", callback_data="admin_panel")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    await callback_query.message.edit_text("Olib tashlash uchun kanalni tanlang:", reply_markup=keyboard)


@dp.callback_query_handler(lambda c: c.data.startswith("admin_remove_sub_"))
async def admin_remove_sub_confirm_callback(callback_query: types.CallbackQuery):
    """Admin: Kanalni olib tashlash"""
    if callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("Siz admin emassiz!", show_alert=True)
        return

    await callback_query.answer("Kanal olib tashlandi!", show_alert=True)

    channel_id = callback_query.data.replace("admin_remove_sub_", "")
    db.remove_mandatory_subscription(channel_id)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton("🔙 Orqaga", callback_data="admin_panel")
    ]])
    await callback_query.message.edit_text(
        "✅ Kanal muvaffaqiyatli olib tashlandi!",
        reply_markup=keyboard
    )


@dp.callback_query_handler(lambda c: c.data == "admin_add_bot")
async def admin_add_bot_callback(callback_query: types.CallbackQuery, state: FSMContext):
    """Admin: Bot qo'shish"""
    if callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("Siz admin emassiz!", show_alert=True)
        return

    await callback_query.answer()
    await AdminStates.waiting_bot_file.set()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton("🔙 Orqaga", callback_data="admin_panel")
    ]])
    await callback_query.message.edit_text(
        "Bot faylini yuboring (zip, py yoki boshqa fayllar):",
        reply_markup=keyboard
    )


@dp.message_handler(state=AdminStates.waiting_bot_file, content_types=['document'])
async def process_admin_bot_file(message: types.Message, state: FSMContext):
    """Admin: Bot faylini qabul qilish"""
    if message.from_user.id != ADMIN_ID:
        return

    if message.document:
        # Barcha fayl turlarini qabul qilish
        file_name = message.document.file_name

        # Faylni saqlash
        file = await bot.get_file(message.document.file_id)
        bot_file_path = f"bot_templates/{file_name}"
        os.makedirs("bot_templates", exist_ok=True)
        await file.download(destination_file=bot_file_path)

        await state.update_data(bot_file_path=bot_file_path, file_name=file_name)
        await AdminStates.waiting_bot_name.set()

        await message.answer("✅ Bot fayli qabul qilindi!\n\nBot nomini kiriting:")
    else:
        await message.answer("❌ Iltimos, fayl yuboring!")


@dp.message_handler(state=AdminStates.waiting_bot_name)
async def process_admin_bot_name(message: types.Message, state: FSMContext):
    """Admin: Bot nomini qabul qilish"""
    if message.from_user.id != ADMIN_ID:
        return

    bot_name = message.text.strip()
    await state.update_data(bot_name=bot_name)
    await AdminStates.waiting_bot_price.set()

    await message.answer("✅ Bot nomi qabul qilindi!\n\nBot narxini kiriting (so'm):")


@dp.message_handler(state=AdminStates.waiting_bot_price)
async def process_admin_bot_price(message: types.Message, state: FSMContext):
    """Admin: Bot narxini qabul qilish"""
    if message.from_user.id != ADMIN_ID:
        return

    try:
        price = float(message.text.strip())
        data = await state.get_data()
        bot_name = data.get('bot_name')
        bot_file_path = data.get('bot_file_path')

        await state.update_data(price=price)
        await AdminStates.waiting_run_command.set()

        await message.answer(
            "✅ Bot narxi qabul qilindi!\n\n"
            "Botni qanday qilib ishga tushirish kerak?\n"
            "Masalan:\n"
            "- `python bot.py`\n"
            "- `python -m bot`\n"
            "- `node bot.js`\n"
            "- `python main.py`\n\n"
            "Run command ni kiriting:"
        )
    except ValueError:
        await message.answer("❌ Noto'g'ri narx! Iltimos, raqam kiriting.")


@dp.message_handler(state=AdminStates.waiting_run_command)
async def process_admin_run_command(message: types.Message, state: FSMContext):
    """Admin: Run command qabul qilish"""
    if message.from_user.id != ADMIN_ID:
        return

    run_command = message.text.strip()
    data = await state.get_data()
    bot_name = data.get('bot_name')
    bot_file_path = data.get('bot_file_path')
    price = data.get('price')

    db.add_bot(bot_name, bot_file_path, run_command, price)

    await message.answer(
        f"✅ Bot qo'shildi!\n\n"
        f"🤖 Nomi: {bot_name}\n"
        f"💰 Narxi: {price} so'm\n"
        f"▶️ Run command: {run_command}"
    )

    await state.finish()


@dp.callback_query_handler(lambda c: c.data == "admin_users")
async def admin_users_callback(callback_query: types.CallbackQuery):
    """Admin: Foydalanuvchilar"""
    if callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("Siz admin emassiz!", show_alert=True)
        return

    await callback_query.answer()

    # Foydalanuvchilar ro'yxatini olish
    users = db.get_all_users()

    # .txt fayl yaratish
    users_text = "FOYDALANUVCHILAR RO'YXATI\n"
    users_text += "=" * 70 + "\n\n"
    users_text += f"{'№':<5} {'ID':<12} {'Ism':<20} {'Username':<20} {'Telefon':<15} {'Balans':<10} {'Sana':<20}\n"
    users_text += "-" * 120 + "\n"

    for idx, user in enumerate(users, 1):
        user_id = user[0]
        username = user[1] or "N/A"
        full_name = user[2] or "N/A"
        phone_number = user[3] or "N/A"
        balance = user[4] or 0
        created_at = user[7] or "N/A"

        # Sana formatini qisqartirish
        if created_at != "N/A":
            try:
                dt = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
                created_at = dt.strftime("%d.%m.%Y %H:%M")
            except:
                pass

        users_text += f"{idx:<5} {user_id:<12} {full_name[:18]:<20} {username[:18]:<20} {phone_number[:13]:<15} {balance:<10.2f} {created_at:<20}\n"

    users_text += "\n" + "=" * 70 + "\n"
    users_text += f"Jami foydalanuvchilar: {len(users)}"

    # Faylni saqlash
    filename = f"users_list_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    os.makedirs("temp", exist_ok=True)
    filepath = f"temp/{filename}"

    async with aiofiles.open(filepath, 'w', encoding='utf-8') as f:
        await f.write(users_text)

    # Faylni yuborish
    try:
        document = types.InputFile(filepath)
        await bot.send_document(
            chat_id=callback_query.from_user.id,
            document=document,
            caption=f"👥 Foydalanuvchilar ro'yxati\n\nJami: {len(users)} ta foydalanuvchi"
        )

        # Faylni o'chirish
        os.remove(filepath)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton("🔙 Orqaga", callback_data="admin_panel")
        ]])
        await callback_query.message.edit_text(
            f"✅ Foydalanuvchilar ro'yxati yuborildi!\n\nJami: {len(users)} ta foydalanuvchi",
            reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"Error sending users file: {e}")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton("🔙 Orqaga", callback_data="admin_panel")
        ]])
        await callback_query.message.edit_text(
            f"❌ Xatolik yuz berdi: {str(e)}",
            reply_markup=keyboard
        )


@dp.callback_query_handler(lambda c: c.data == "admin_active_users")
async def admin_active_users_callback(callback_query: types.CallbackQuery):
    """Admin: Aktiv foydalanuvchilar"""
    if callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("Siz admin emassiz!", show_alert=True)
        return

    await callback_query.answer()
    active = db.get_active_users()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton("🔙 Orqaga", callback_data="admin_panel")
    ]])
    await callback_query.message.edit_text(
        f"📊 Aktiv foydalanuvchilar (30 kun): {active}",
        reply_markup=keyboard
    )


@dp.callback_query_handler(lambda c: c.data == "admin_total_users")
async def admin_total_users_callback(callback_query: types.CallbackQuery):
    """Admin: Umumiy foydalanuvchilar"""
    if callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("Siz admin emassiz!", show_alert=True)
        return

    await callback_query.answer()
    total = db.get_total_users()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton("🔙 Orqaga", callback_data="admin_panel")
    ]])
    await callback_query.message.edit_text(
        f"👤 Umumiy foydalanuvchilar: {total}",
        reply_markup=keyboard
    )


@dp.callback_query_handler(lambda c: c.data == "admin_bots")
async def admin_bots_callback(callback_query: types.CallbackQuery):
    """Admin: Botlar"""
    if callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("Siz admin emassiz!", show_alert=True)
        return

    await callback_query.answer()
    total = db.get_total_bots()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton("🔙 Orqaga", callback_data="admin_panel")
    ]])
    await callback_query.message.edit_text(
        f"🤖 Jami yaratilgan botlar: {total}",
        reply_markup=keyboard
    )


@dp.callback_query_handler(lambda c: c.data == "admin_topup")
async def admin_topup_callback(callback_query: types.CallbackQuery, state: FSMContext):
    """Admin: Balans to'ldirish"""
    if callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("Siz admin emassiz!", show_alert=True)
        return

    await callback_query.answer()
    await AdminStates.waiting_user_id.set()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton("🔙 Orqaga", callback_data="admin_panel")
    ]])
    await callback_query.message.edit_text(
        "Foydalanuvchi ID sini kiriting:",
        reply_markup=keyboard
    )






@dp.callback_query_handler(lambda c: c.data == "admin_change_referral")
async def admin_change_referral_callback(callback_query: types.CallbackQuery, state: FSMContext):
    """Admin: Referral summani o'zgartirish"""
    if callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("Siz admin emassiz!", show_alert=True)
        return

    await callback_query.answer()
    current_amount = db.get_referral_amount()
    await AdminStates.waiting_referral_amount.set()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton("🔙 Orqaga", callback_data="admin_panel")
    ]])
    await callback_query.message.edit_text(
        f"Joriy referral summa: {current_amount} so'm\n\nYangi summani kiriting:",
        reply_markup=keyboard
    )


@dp.message_handler(state=AdminStates.waiting_referral_amount)
async def process_admin_referral_amount(message: types.Message, state: FSMContext):
    """Admin: Referral summani qabul qilish"""
    if message.from_user.id != ADMIN_ID:
        return

    try:
        amount = float(message.text.strip())
        db.set_referral_amount(amount)

        await message.answer(f"✅ Referral summa o'zgartirildi: {amount} so'm")

        await state.finish()
    except ValueError:
        await message.answer("❌ Noto'g'ri summa! Iltimos, raqam kiriting.")


@dp.callback_query_handler(lambda c: c.data == "admin_delete_bot")
async def admin_delete_bot_list(callback_query: types.CallbackQuery):
    if callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("Siz admin emassiz!", show_alert=True)
        return
    await callback_query.answer()
    bots = db.get_bots()
    if not bots:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton("Orqaga", callback_data="admin_panel")]])
        await callback_query.message.edit_text("Hozircha botlar yo'q.", reply_markup=keyboard)
        return

    keyboard_buttons = []
    for bot in bots:
        bot_id, bot_name, _, _, price = bot[0], bot[1], bot[2], bot[3], bot[4]
        keyboard_buttons.append([InlineKeyboardButton(
            f"{bot_name} — {price:,.0f} so'm".replace(',', ' '),
            callback_data=f"delete_bot_{bot_id}"
        )])
    keyboard_buttons.append([InlineKeyboardButton("Orqaga", callback_data="admin_panel")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    await callback_query.message.edit_text("O‘chirish uchun botni tanlang:", reply_markup=keyboard)


@dp.callback_query_handler(lambda c: c.data.startswith("delete_bot_"))
async def admin_delete_bot_confirm(callback_query: types.CallbackQuery):
    if callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("Siz admin emassiz!", show_alert=True)
        return

    bot_id = int(callback_query.data.split("_")[2])
    bot = db.get_bot(bot_id)
    if not bot:
        await callback_query.answer("Bot topilmadi!", show_alert=True)
        return

    # Bazadan o'chirish
    cursor = db.conn.cursor()
    cursor.execute('DELETE FROM bots WHERE bot_id = ?', (bot_id,))
    db.conn.commit()

    # Faylni o'chirish
    try:
        if os.path.exists(bot[2]):
            os.remove(bot[2])
    except:
        pass

    await callback_query.answer("Bot muvaffaqiyatli o‘chirildi!", show_alert=True)
    await callback_query.message.edit_text(
        f"Bot o‘chirildi: {bot[1]}\n\nAdmin panelga qaytish uchun pastdagi tugmani bosing.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton("Admin panel", callback_data="admin_panel")]])
    )


@dp.callback_query_handler(lambda c: c.data == "admin_panel")
async def admin_panel_callback(callback_query: types.CallbackQuery):
    if callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("Siz admin emassiz!", show_alert=True)
        return
    await callback_query.answer()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("➕ Majburiy obuna qo'shish", callback_data="admin_add_sub")],
        [InlineKeyboardButton("➖ Majburiy obunani olib tashlash", callback_data="admin_remove_sub")],
        [InlineKeyboardButton("🤖 Bot qo'shish", callback_data="admin_add_bot")],
        [InlineKeyboardButton("🗑 Bot o'chirish", callback_data="admin_delete_bot")],
        [InlineKeyboardButton("💳 To'lovlarni ko'rish", callback_data="admin_payments")],
        [InlineKeyboardButton("👥 Foydalanuvchilar", callback_data="admin_users")],
        [InlineKeyboardButton("📊 Aktiv foydalanuvchilar", callback_data="admin_active_users")],
        [InlineKeyboardButton("👤 Umumiy foydalanuvchilar", callback_data="admin_total_users")],
        [InlineKeyboardButton("🤖 Botlar", callback_data="admin_bots")],
        [InlineKeyboardButton("💳 Balans to'ldirish", callback_data="admin_topup")],
        [InlineKeyboardButton("💰 Referral summani o'zgartirish", callback_data="admin_change_referral")]
    ])
    await callback_query.message.edit_text("🔐 Admin panel", reply_markup=keyboard)


# Admin: To'lovlarni ko'rish
@dp.callback_query_handler(lambda c: c.data == "admin_payments")
async def admin_payments_callback(callback_query: types.CallbackQuery):
    """Admin: To'lovlarni ko'rish"""
    if callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("Siz admin emassiz!", show_alert=True)
        return
    
    await callback_query.answer()
    payments = db.get_pending_payments()
    
    if not payments:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton("🔙 Orqaga", callback_data="admin_panel")
        ]])
        await callback_query.message.edit_text(
            "Kutilayotgan to'lovlar yo'q.",
            reply_markup=keyboard
        )
        return
    
    # Birinchi to'lovni ko'rsatish
    payment = payments[0]
    payment_id, user_id, amount, screenshot_path, status, created_at = payment[0], payment[1], payment[2], payment[3], payment[4], payment[5]
    
    user = db.get_user(user_id)
    username = user[1] if user else "N/A"
    
    text = f"💳 To'lov #{payment_id}\n\n"
    text += f"👤 Foydalanuvchi: @{username} (ID: {user_id})\n"
    text += f"💰 Summa: {amount} so'm\n"
    text += f"📅 Sana: {created_at}\n"
    text += f"📊 Holat: {status}"
    
    keyboard_buttons = [
        [InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"approve_payment_{payment_id}")],
        [InlineKeyboardButton("❌ Rad etish", callback_data=f"reject_payment_{payment_id}")],
        [InlineKeyboardButton("🔙 Orqaga", callback_data="admin_panel")]
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    
    if screenshot_path and os.path.exists(screenshot_path):
        try:
            with open(screenshot_path, 'rb') as photo:
                await callback_query.message.delete()
                await bot.send_photo(
                    chat_id=callback_query.from_user.id,
                    photo=photo,
                    caption=text,
                    reply_markup=keyboard
                )
        except:
            await callback_query.message.edit_text(text, reply_markup=keyboard)
    else:
        await callback_query.message.edit_text(text, reply_markup=keyboard)


@dp.callback_query_handler(lambda c: c.data.startswith("approve_payment_"))
async def approve_payment_callback(callback_query: types.CallbackQuery):
    """Admin: To'lovni tasdiqlash"""
    if callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("Siz admin emassiz!", show_alert=True)
        return
    
    await callback_query.answer()
    payment_id = int(callback_query.data.split("_")[2])
    payment = db.get_payment(payment_id)
    
    if not payment:
        await callback_query.answer("To'lov topilmadi!", show_alert=True)
        return
    
    user_id = payment[1]
    amount = payment[2]
    
    # To'lovni tasdiqlash
    db.update_payment_status(payment_id, "approved")
    db.update_balance(user_id, amount)
    
    # Xabarni yangilash
    try:
        await callback_query.message.edit_caption(
            caption=f"✅ To'lov tasdiqlandi!\n\n"
                    f"👤 Foydalanuvchi ID: {user_id}\n"
                    f"💰 Summa: {amount} so'm\n"
                    f"💵 Yangi balans: {db.get_balance(user_id)} so'm"
        )
    except:
        pass
    
    # Foydalanuvchiga xabar
    try:
        await bot.send_message(
            chat_id=user_id,
            text=f"✅ To'lovingiz tasdiqlandi!\n\n💰 Summa: {amount} so'm\n💵 Joriy balans: {db.get_balance(user_id)} so'm"
        )
    except:
        pass
    
    await callback_query.answer("To'lov tasdiqlandi!", show_alert=True)


@dp.callback_query_handler(lambda c: c.data.startswith("reject_payment_"))
async def reject_payment_callback(callback_query: types.CallbackQuery, state: FSMContext):
    """Admin: To'lovni rad etish"""
    if callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("Siz admin emassiz!", show_alert=True)
        return
    
    await callback_query.answer()
    payment_id = int(callback_query.data.split("_")[2])
    payment = db.get_payment(payment_id)
    
    if not payment:
        await callback_query.answer("To'lov topilmadi!", show_alert=True)
        return
    
    # State ga payment_id ni saqlash
    await state.update_data(payment_id=payment_id, user_id=payment[1], amount=payment[2])
    await AdminStates.waiting_reject_reason.set()
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton("🔙 Bekor qilish", callback_data="admin_panel")
    ]])
    
    # Xabar rasm yoki matn bo'lishi mumkin
    try:
        if callback_query.message.photo:
            await callback_query.message.edit_caption(
                caption="❌ To'lovni rad etish\n\nRad etish sababini kiriting:",
                reply_markup=keyboard
            )
        else:
            await callback_query.message.edit_text(
                "❌ To'lovni rad etish\n\nRad etish sababini kiriting:",
                reply_markup=keyboard
            )
    except Exception as e:
        # Agar xabarni yangilab bo'lmasa, yangi xabar yuborish
        await callback_query.message.answer(
            "❌ To'lovni rad etish\n\nRad etish sababini kiriting:",
            reply_markup=keyboard
        )


@dp.message_handler(state=AdminStates.waiting_reject_reason)
async def process_reject_reason(message: types.Message, state: FSMContext):
    """Admin: Rad etish sababini qabul qilish"""
    if message.from_user.id != ADMIN_ID:
        return
    
    data = await state.get_data()
    payment_id = data.get('payment_id')
    user_id = data.get('user_id')
    amount = data.get('amount')
    reason = message.text.strip()
    
    # To'lovni rad etish
    db.update_payment_status(payment_id, "rejected")
    
    # Foydalanuvchiga xabar
    try:
        await bot.send_message(
            chat_id=user_id,
            text=f"❌ To'lovingiz rad etildi.\n\n"
                 f"💰 Summa: {amount} so'm\n"
                 f"📝 Sabab: {reason}\n\n"
                 f"Iltimos, qayta urinib ko'ring yoki admin bilan bog'laning."
        )
    except:
        pass
    
    await message.answer(f"✅ To'lov rad etildi va foydalanuvchiga xabar yuborildi.")
    await state.finish()


@dp.message_handler(state=AdminStates.waiting_topup_amount)
async def process_admin_topup_amount(message: types.Message, state: FSMContext):
    """Admin: To'ldirish summasini qabul qilish"""
    if message.from_user.id != ADMIN_ID:
        return
    
    try:
        amount = float(message.text.strip())
        data = await state.get_data()
        target_user_id = data.get('target_user_id')
        
        db.update_balance(target_user_id, amount)
        await message.answer(
            f"✅ Balans to'ldirildi!\n\n"
            f"👤 Foydalanuvchi ID: {target_user_id}\n"
            f"💰 Summa: {amount} so'm\n"
            f"💵 Yangi balans: {db.get_balance(target_user_id)} so'm"
        )
        try:
            await bot.send_message(
                chat_id=target_user_id,
                text=f"✅ Sizning balansingiz {amount} so'm ga to'ldirildi!\n\n💵 Joriy balans: {db.get_balance(target_user_id)} so'm"
            )
        except:
            pass
        
        await state.finish()
    except ValueError:
        await message.answer("❌ Noto'g'ri raqam! Iltimos, raqam kiriting.")


# Admin komandalar
@dp.message_handler(commands=['message_all_user'])
async def message_all_user_handler(message: types.Message, state: FSMContext):
    """Admin: Barcha foydalanuvchilarga xabar yuborish"""
    if message.from_user.id != ADMIN_ID:
        return
    
    await message.answer("Xabarni kiriting (barcha foydalanuvchilarga yuboriladi):")
    await state.update_data(is_broadcast=True)
    await AdminStates.waiting_user_id.set()  # Reuse state for message


@dp.message_handler(state=AdminStates.waiting_user_id)
async def process_admin_broadcast_message(message: types.Message, state: FSMContext):
    """Admin: Broadcast xabarni qabul qilish"""
    if message.from_user.id != ADMIN_ID:
        return
    
    data = await state.get_data()
    is_broadcast = data.get('is_broadcast', False)
    
    if is_broadcast:
        # Broadcast xabar
        text = message.text
        users = db.get_all_users()
        sent = 0
        failed = 0
        
        for user in users:
            user_id = user[0]
            try:
                await bot.send_message(chat_id=user_id, text=text)
                sent += 1
            except:
                failed += 1
        
        await message.answer(
            f"✅ Xabar yuborildi!\n\n"
            f"✅ Muvaffaqiyatli: {sent}\n"
            f"❌ Xatolik: {failed}\n"
            f"📊 Jami: {len(users)}"
        )
        await state.finish()
    else:
        # Balans to'ldirish
        try:
            target_user_id = int(message.text.strip())
            await state.update_data(target_user_id=target_user_id)
            await AdminStates.waiting_topup_amount.set()
            await message.answer("✅ Foydalanuvchi ID qabul qilindi!\n\nTo'ldirish summasini kiriting (so'm):")
        except ValueError:
            await message.answer("❌ Noto'g'ri ID! Iltimos, raqam kiriting.")


@dp.message_handler(commands=['ban'])
async def ban_user_handler(message: types.Message):
    """Admin: Foydalanuvchini ban qilish"""
    if message.from_user.id != ADMIN_ID:
        return
    
    try:
        user_id = int(message.text.split()[1])
        db.ban_user(user_id, "Admin buyrug'i")
        await message.answer(f"✅ Foydalanuvchi {user_id} ban qilindi!")
    except (IndexError, ValueError):
        await message.answer("❌ Noto'g'ri format! Masalan: /ban 123456789")



async def on_startup(dp):
    # Web serverni ishga tushirish (Render uchun)
    app = web.Application()
    async def handle_ping(request):
        return web.Response(text="Alive")
    
    app.router.add_get('/', handle_ping)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"Web server started on port {port}")

    # Keep-alive background taskini ishga tushirish
    asyncio.create_task(keep_alive_ping())

async def keep_alive_ping():
    """Bot o'ziga o'zi ping yuborib turishi uchun funksiya"""
    url = os.getenv("RENDER_EXTERNAL_URL")
    if not url:
        # Agar bu o'zgaruvchi yo'q bo'lsa (localda), ishlamaydi
        if os.getenv("RENDER"): # Faqat Renderda log yozish
            logger.warning("RENDER_EXTERNAL_URL topilmadi, keep-alive ishlamaydi.")
        return

    logger.info(f"Keep-alive ping boshlandi: {url}")
    while True:
        await asyncio.sleep(300)  # 5 daqiqa (300 soniya)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        logger.info(f"Self-ping muvaffaqiyatli: {url}")
                    else:
                        logger.warning(f"Self-ping xatosi. Status: {response.status}")
        except Exception as e:
            logger.error(f"Self-ping ulanish xatosi: {e}")


if __name__ == '__main__':
    logger.info("Bot ishga tushmoqda...")
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
