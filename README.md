# Maker Bot - Telegram Bot Yaratuvchi Bot

Bu bot foydalanuvchilarga boshqa Telegram botlar yaratish imkoniyatini beradi.

## O'rnatish

1. Kerakli paketlarni o'rnating:
```bash
pip install -r requirements.txt
```

2. `main.py` faylida quyidagilarni o'zgartiring:
   - `BOT_TOKEN` - o'zingizning bot token ni kiriting
   - `ADMIN_ID` - o'zingizning Telegram ID ni kiriting

3. Botni ishga tushiring:
```bash
python main.py
```

## Funksiyalar

### Foydalanuvchilar uchun:
- ✅ Majburiy obuna tekshiruvi
- 👥 Referral tizimi
- 💼 Asosiy kabinet va balans ko'rish
- 💳 Balans to'ldirish
- 🤖 Bot yaratish

### Admin uchun:
- ➕ Majburiy obuna qo'shish/olib tashlash
- 🤖 Bot qo'shish (.py fayl)
- 👥 Foydalanuvchilar statistikasi
- 💳 Balans to'ldirish
- 💰 Referral summani o'zgartirish

## Database

Bot SQLite database ishlatadi (`maker_bot.db`). Avtomatik yaratiladi.

## Bot Template Format

Admin panel orqali qo'shiladigan bot fayllari quyidagi formatda bo'lishi kerak:
- Bot token `BOT_TOKEN` o'zgaruvchisida yoki `YOUR_BOT_TOKEN` placeholder sifatida bo'lishi kerak
- Bot fayl `.py` formatida bo'lishi kerak
- Bot token avtomatik ravishda foydalanuvchi tomonidan kiritilgan token bilan almashtiriladi

Misol:
```python
BOT_TOKEN = "YOUR_BOT_TOKEN"
# yoki
BOT_TOKEN = "123456:ABC-DEF..."
```

## Eslatmalar

- Bot 24/7 ishlashi uchun server yoki VPS da ishga tushiring
- Admin ID ni to'g'ri kiriting (Telegram ID ni olish uchun @userinfobot dan foydalaning)
- Bot token ni xavfsiz saqlang
- To'lov karta raqami `PAYMENT_CARD` o'zgaruvchisida sozlangan

