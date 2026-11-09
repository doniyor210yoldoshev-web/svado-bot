# Marketplace Bot

Telegram marketplace bot (aiogram 3) with escrow, wallet, disputes, test mode and an
admin panel. Multi-language: **O'zbekcha / Русский / English**.

## Imkoniyatlar

- **Marketplace**: akkauntlar ro'yxati, sahifalash, qidiruv (akkaunt ID yoki sotuvchi kodi).
- **Escrow**: xaridor to'lovi vaqtincha ushlab turiladi, tasdiqlangach sotuvchiga o'tadi.
- **Komissiya**: default 8% (`COMMISSION_PERCENT`), sotuvchiga 92%.
- **Himoya**: tekshirish muddati (`CHECK_MINUTES`), kechikish jarimasi, avtomatik yakunlash.
- **Nizolar**: xaridor nizo ochadi, admin qaytarish yoki sotuvchiga o'tkazishni hal qiladi.
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
| `SUPPORT_USERNAME` | Yordam uchun username (@siz) | `""` |
| `DEFAULT_LANG` | `uz`, `ru`, `en` | `uz` |
| `COMMISSION_PERCENT` | Komissiya foizi | `8` |
| `CHECK_MINUTES` | Tekshirish muddati | `10` |
| `PENALTY_PERCENT` / `PENALTY_INTERVAL_MINUTES` | Jarima foizi va intervali | `10` / `10` |
| `AUTO_COMPLETE_MINUTES` | Avtomatik yakunlashgacha qo'shimcha vaqt | `60` |
| `MIN_PRICE`, `MIN_TOPUP`, `MIN_WITHDRAW`, `MAX_TOPUP` | Limitlar (so'm) | `1000`, `1000`, `5000`, `50000000` |
| `PAGE_SIZE` | Sahifadagi akkauntlar soni | `10` |
| `TEST_INITIAL_BALANCE` | Test rejimi boshlang'ich balansi | `1000000` |
| `TEST_MAX_ACCOUNTS` | Test rejimidagi akkauntlar limiti | `10` |

To'liq ro'yxat: `.env.example`.

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
balansi beriladi va tizim sotuvchisi (`tg_id = -1`) tomonidan 5 ta test akkaunti
qo'shiladi (narxi 10 000 so'm, login `test_<hex>`, parol `password123`).

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
```

## Xavfsizlik eslatmalari

- `.env` va `data/` **gitga qo'shilmaydi** (`.gitignore`).
- Token chatda oshkor bo'lgan bo'lsa, BotFather orqali **`/revoke`** qilib yangi token oling.
- Productionda `FERNET_KEY` ni xavfsiz joyda saqlang va zaxira nusxasini oling.
- PostgreSQL ulanishida SSL ishlatish tavsiya etiladi (`?sslmode=require`).
