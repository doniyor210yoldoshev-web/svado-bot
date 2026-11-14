# Marketplace Bot

Telegram marketplace bot (aiogram 3) with escrow, wallet, disputes, test mode and an
admin panel. Multi-language: **O'zbekcha / Русский / English**.

## Imkoniyatlar

- **Marketplace**: akkauntlar ro'yxati, sahifalash, qidiruv (akkaunt ID yoki sotuvchi kodi).
- **Escrow**: xaridor to'lovi vaqtincha ushlab turiladi, tasdiqlangach sotuvchiga o'tadi.
- **Komissiya**: default 7% (`COMMISSION_PERCENT`), sotuvchiga 93%.
- **Himoya**: 5 daqiqalik to'lov oynasi, tekshirish muddati (`CHECK_MINUTES`), kechikish jarimasi, avtomatik yakunlash.
- **Nizolar**: xaridor nizo ochadi, sotuvchi HA/YO'Q javob beradi, tizim avtomatik hal qiladi (admin aralashmaydi).
- **Hamyon**: balans to'ldirish (karta + chek), yechib olish so'rovlari, tarix.
- **Test rejimi**: admin istalgan foydalanuvchini test rejimiga o'tkazadi; test balansi va
  test akkauntlari real balansga ta'sir qilmaydi.
- **Admin panel**: statistika, to'lovlar, yechib olishlar, nizolar, sotuvlar, akkauntlar,
  foydalanuvchilar, balansni qo'lda o'zgartirish, broadcast, audit log.
- **Xavfsizlik**: login/parollar Fernet bilan shifrlanadi, parollar PBKDF2 bilan hashlanadi,
  barcha foydalanuvchi matni HTML-escape qilinadi, anti-flood middleware.

## Talablar

- Python 3.11+ (lokalda 3.14 da tekshirilgan)
- PostgreSQL (production) yoki SQLite (lokal, avtomatik)
- Telegram bot token (BotFather)

## Lokal ishga tushirish

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

Copy-Item .env.example .env
# .env faylini ochib BOT_TOKEN va ADMIN_IDS ni to'ldiring

python main.py
```

Birinchi ishga tushirishda `data/` papkasi va SQLite bazasi avtomatik yaratiladi.
`FERNET_KEY` bo'sh bo'lsa `data/fernet.key` avtomatik generatsiya qilinadi.

## Muhim environment o'zgaruvchilari

| O'zgaruvchi | Tavsif | Default |
| --- | --- | --- |
| `BOT_TOKEN` | BotFather tokeni (majburiy) | — |
| `ADMIN_IDS` | Admin Telegram ID lari, vergul bilan | — |
| `DATABASE_URL` | PostgreSQL URL; bo'sh bo'lsa SQLite | `""` |
| `DB_PATH` | SQLite fayl yo'li | `data/market.db` |
| `FSM_DB_PATH` | FSM SQLite fayli | `data/fsm.db` |
| `FERNET_KEY` | Login/parol shifrlash kaliti | avtomatik |
| `PAYMENT_CARD` / `PAYMENT_HOLDER` | To'lov kartasi ma'lumotlari | — |
| `HAMYON_API_URL` / `HAMYON_SHOP_ID` / `HAMYON_SHOP_KEY` | HamyonAPI sozlamalari (@HamyonAPIBot dan) | `https://hamyon-api.uz` / — |
| `SUPPORT_USERNAME` | Yordam uchun username (@siz) | `""` |
| `DEFAULT_LANG` | `uz`, `ru`, `en` | `uz` |
| `COMMISSION_PERCENT` | Komissiya foizi | `7` |
| `CHECK_MINUTES` | Tekshirish muddati | `10` |
| `PENALTY_PERCENT` / `PENALTY_INTERVAL_MINUTES` | Jarima foizi va intervali | `10` / `10` |
| `AUTO_COMPLETE_MINUTES` | Avtomatik yakunlashgacha qo'shimcha vaqt | `60` |
| `MIN_PRICE`, `MIN_TOPUP`, `MIN_WITHDRAW`, `MAX_TOPUP` | Limitlar (so'm) | `1000`, `1000`, `5000`, `50000000` |
| `PAGE_SIZE` | Sahifadagi akkauntlar soni | `10` |
| `TEST_INITIAL_BALANCE` | Test rejimi boshlang'ich balansi | `1000000` |
| `TEST_MAX_ACCOUNTS` | Test rejimidagi akkauntlar limiti | `10` |
| `SEED_TEST_ACCOUNTS` | Ishga tushishda test e'lonlarini yaratish | `1` |

To'liq ro'yxat: `.env.example`.

## Click (click.uz) to'lovini ulash

1. `merchant.click.uz` kabinetida quyidagi URL larni kiriting (domeningiz bilan):
   - **Prepare URL**: `https://<domen>/click/prepare`
   - **Complete URL**: `https://<domen>/click/complete`
2. `.env` faylida `PAYMENT_PROVIDER=click` qilib, kabinetdan olingan qiymatlarni yozing:
   `CLICK_SERVICE_ID`, `CLICK_MERCHANT_ID`, `CLICK_MERCHANT_USER_ID`,
   `CLICK_SECRET_KEY`. `PUBLIC_BASE_URL` ga ochiq HTTPS manzilni yozing
   (masalan `https://marketplace-bot.onrender.com`).
3. Server `PORT` muhit o'zgaruvchisida ishlashi kerak (Render buni avtomatik beradi).
4. To'lov oqimi: botda summa kiritiladi -> Click to'lov havolasi beriladi ->
   Click `prepare`/`complete` yuboradi -> balans avtomatik to'ldiriladi va
   foydalanuvchiga xabar boradi.

Imzo (MD5 `sign_string`) har bir so'rovda tekshiriladi; `complete`da Click xatosi
(`error < 0`) bo'lsa to'lov bekor qilinadi. Click to'lovlari admin panelning
"To'lovlar" ro'yxatida ko'rinmaydi (ular avtomatik tasdiqlanadi).

## HamyonAPI (hamyon-api.uz) to'lovini ulash

1. Telegram'da [@HamyonAPIBot](https://t.me/HamyonAPIBot) ga `/start` yuboring va
   olingan `shop_id` va `shop_key` ni `.env` ga yozing.
2. Kartangiz xabarnomalarini ulang: HUMO uchun [@HumoCardBot](https://t.me/HumoCardBot),
   UZCARD uchun [@CardXabarBot](https://t.me/CardXabarBot).
3. HamyonAPI paneli (bot ichidagi WebApp) yoki bot sozlamalarida callback URL
   larini kiriting (domeningiz bilan):
   - **Prepare URL**: `https://<domen>/hamyon/prepare`
   - **Complete URL**: `https://<domen>/hamyon/complete`
4. `.env` faylida `PAYMENT_PROVIDER=hamyon` qilib, `HAMYON_SHOP_ID` va
   `HAMYON_SHOP_KEY` ni to'ldiring. `PUBLIC_BASE_URL` ga ochiq HTTPS manzilni
   yozing. Server `PORT` muhit o'zgaruvchisida ishlashi kerak.
5. To'lov oqimi: botda summa kiritiladi -> HamyonAPI dan karta raqami olinadi ->
   mijoz xuddi shu kartaga summani o'tkazadi -> pul tushishi bilan (5–30 s)
   balans avtomatik to'ladi va foydalanuvchiga xabar boradi.

Imzo har bir callbackda tekshiriladi: `md5(shop_id + payment_id + amount +
shop_key)`. Callbacklar bir necha marta qayta yuborilishi mumkin, shuning uchun
balans `payment_id` bo'yicha bir marta (idempotent) to'ldiriladi. To'lov 5 daqiqa
ichida tushmasa avtomatik `cancel` keladi. Bir do'konda bir vaqtda bir xil
summadagi ikkita ochiq to'lov bo'lishi mumkin emas — shuning uchun summa
zarur bo'lsa 1–2 so'mga o'zgartiriladi (mijozga ko'rsatilgan summa o'sha).
HamyonAPI to'lovlari admin panelning "To'lovlar" ro'yxatida ko'rinmaydi (ular
avtomatik tasdiqlanadi).

## Hamyon tizimi

Har bir foydalanuvchining alohida hamyoni bor: joriy balans, jami kirim/chiqim,
kutilayotgan to'lovlar, tranzaksiyalar tarixi va to'lov holati. Hamyon ekraniga
**👛 Hamyonim** tugmasi orqali kiriladi.

- **Balansni to'ldirish** `MIN_TOPUP`–`MAX_TOPUP` oralig'ida; har bir to'lov
  yaratilganda `PAY-XXXXXXXX...` ko'rinishidagi unikal `public_id` beriladi.
  Balans faqat admin yoki rasmiy provayder callback'i to'lovni tasdiqlagach
  yangilanadi.
- **Hamyondan to'lash**: mahsulot sahifasida balans yetarli bo'lsa
  «👛 Hamyondan to'lash» tugmasi chiqadi. Balans atomar yechiladi, buyurtma
  yaratiladi va login-parol ochiladi. Balans yetmasa xarid bekor qilinadi.
- **Xavfsizlik**: Click/HamyonAPI imzolari tekshiriladi; bir to'lov faqat bir
  marta hisobga olinadi (`topups.status` bo'yicha shartli `UPDATE`); barcha
  balans o'zgarishlari bitta database transaction ichida bajariladi.
  `transactions.reference` unikal, foydalanuvchi faqat o'z tranzaksiyasini
  ko'ra oladi.
- **Jadvallar**: mavjud `users`/`transactions`/`topups`/`withdrawals`ga
  qo'shimcha ravishda `wallets` (trigger orqali `users.balance` bilan sinxron),
  `wallet_audit_logs` (admin aralashuvlari) yaratiladi. Migratsiyalar
  `CREATE TABLE IF NOT EXISTS` va `ALTER TABLE ADD COLUMN` bilan xavfsiz —
  eski ma'lumotlar saqlanadi.
- **Admin panel**: 💰 Hamyonlar (barcha balanslar), 🧾 Tranzaksiyalar,
  📝 Hamyon auditi, foydalanuvchi kartasidagi «Balansni o'zgartirish» va
  «Pulni qaytarish» amallari. Har bir amal `wallet_audit_logs` va umumiy
  `audit_logs`da qoladi.

### Ma'lum cheklovlar

- HamyonAPI da **pulni kartaga qaytarish (refund) API si yo'q**. Nizo xaridor
  foydasiga hal bo'lganda summa botdagi ichki balansga qaytariladi; kartaga
  qaytarishni qo'lda amalga oshirish kerak.
- Akkaunt **egasini API orqali tekshirish xizmati mavjud emas**. Sotuvchi akkaunt
  o'ziga tegishli ekanini o'zi tasdiqlaydi; bot login/parolni faqat to'lov
  tasdiqlangandan keyin ko'rsatadi.
- Nizoni admin hal qilmaydi: sotuvchi 15 daqiqa ichida `HA`/`YO'Q` javob beradi,
  javob kelmasa tizim avtomatik qaytaradi.

## Render'ga deploy qilish

1. Render'da **PostgreSQL** yarating va `DATABASE_URL` ni oling.
2. **Web Service** yarating (repo bilan bog'lang).
   - Build command: `pip install -r requirements.txt`
   - Start command: `python main.py`
   - Health Check Path: `/health`
3. Environment bo'limida `BOT_TOKEN`, `ADMIN_IDS`, `DATABASE_URL`, `FERNET_KEY`,
   `PAYMENT_CARD`, `PAYMENT_HOLDER` va boshqa kerakli qiymatlarni kiriting.
4. Deploydan so'ng `https://<service>.onrender.com/health` javobi
   `{"status":"ok","database":"ok",...}` bo'lishi kerak.

> Agar Web Service port scan muammosi bersa, xizmat turini **Background Worker** ga
> o'zgartiring (start command bir xil). `/health` baribir ishlayveradi.

`FERNET_KEY` ni productionda doimiy saqlang: kalit o'zgarsa, mavjud login/parollarni
deshifrlash imkoni bo'lmaydi. Kalit yaratish:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Admin panel

1. `ADMIN_IDS` ga o'z Telegram ID ingizni qo'shing.
2. Botda `/start` → **Admin panel**.
3. Bo'limlar: statistika, to'lovlar, yechib olishlar, nizolar, sotuvlar, akkauntlar,
   foydalanuvchilar, broadcast, audit, test rejimi.

Test rejimini yoqish uchun admin panel → **Test rejimi**. Yoqilganda foydalanuvchiga test
balansi beriladi va tizim sotuvchisi (`tg_id = -1`) tomonidan 10 ta arzon test akkaunti
avtomatik qo'shiladi (narxi 1 000 so'm, login `test_<display_id>`, parol `password123`).
"Test akkauntlar yaratish" tugmasi bilan qo'shimcha qo'shish mumkin.

## Loyiha tuzilishi

```
config.py            # env sozlamalari
main.py              # entrypoint: db, bot, routerlar, scheduler
health.py            # Render uchun /health HTTP server
utils.py             # formatlash, parsing, escaping
states.py            # FSM holatlari
database/            # dual-dialect db (SQLite/PostgreSQL), models, FSM storage
services/            # biznes-logika: users, marketplace, payment, security, ...
locales/             # uz.json, ru.json, en.json
keyboards/           # inline va reply klaviaturalar
middlewares/         # throttling, user yuklash
handlers/            # start, menu, seller, accounts, search, wallet, admin
```

## Tekshirish

```powershell
.\.venv\Scripts\python.exe -m ruff check . --exclude .venv
.\.venv\Scripts\python.exe -m compileall -q -x "\.venv" .
.\.venv\Scripts\python.exe -m pytest
```

Testlar izolyatsiyalangan SQLite bazasida ishlaydi (`.env` dagi haqiqiy bazaga
tegmaydi). Hamyon uchun alohida `tests/test_wallet.py` mavjud: to'ldirish,
muvaffaqiyatli/muvaffaqiyatsiz xarid, takroriy webhook, bir vaqtda bir nechta
to'lov, refund, balans yetmasligi va ruxsatsiz tranzaksiyaga kirish tekshiriladi.
CI `.github/workflows/ci.yml` orqali har bir push va PR da ruff, compile va
pytest avtomatik tekshiriladi.

## Docker

```powershell
docker compose up --build -d
docker compose logs -f bot
```

`docker-compose.yml` `.env` faylini o'qiydi va `data/` papkasini `bot_data`
volume ichida saqlaydi. Health endpoint: `http://localhost:8080/health`.

## Xavfsizlik eslatmalari

- `.env` va `data/` **gitga qo'shilmaydi** (`.gitignore`).
- Token chatda oshkor bo'lgan bo'lsa, BotFather orqali **`/revoke`** qilib yangi token oling.
- Productionda `FERNET_KEY` ni xavfsiz joyda saqlang va zaxira nusxasini oling.
- PostgreSQL ulanishida SSL ishlatish tavsiya etiladi (`?sslmode=require`).
