import logging
import json
import os
from requests.auth import HTTPBasicAuth
import requests
import qrcode
from io import BytesIO

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ParseMode

TELEGRAM_BOT_TOKEN = "1670761293:AAHqm07ZGN4rixOtcbS23tI8SKMId78KpC0"
HESDA_API_KEY = "QqNKNBjPwUbQFfviUO"
HESDA_EMAIL = "fatkhurohmanofficial@gmail.com"
HESDA_PASSWORD = "12345678"
ADMIN_ID = 1485616701

USER_SALDO_FILE = "user_saldo.json"
USER_LIST_FILE = "user_list.json"
JASA_FEE = 2500

NAMA_PAKET_KUSTOM = {
    "ZVdMVXcyKzdJRlJERVdJc1hpVUhmQQ": "XL VIDIO (metode QRIS)",   
    "MTJLR28vN3VpUmxObFdHelZwRnVUUQ": "XL VIDIO (metode PULSA)",
    # ... paket lain
}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

(SELECT_PACKAGE, GET_PHONE, GET_OTP_CODE, CONFIRM_PURCHASE) = range(4)
(ADMIN_TOPUP_ID, ADMIN_TOPUP_AMOUNT) = range(10, 12)

# ---- Saldo User Management ----
def load_user_saldo():
    if not os.path.exists(USER_SALDO_FILE):
        with open(USER_SALDO_FILE, "w") as f:
            json.dump({}, f)
    with open(USER_SALDO_FILE) as f:
        return json.load(f)

def save_user_saldo(saldo_dict):
    with open(USER_SALDO_FILE, "w") as f:
        json.dump(saldo_dict, f)

def get_user_saldo(user_id):
    saldo_dict = load_user_saldo()
    return saldo_dict.get(str(user_id), 0)

def set_user_saldo(user_id, saldo):
    saldo_dict = load_user_saldo()
    saldo_dict[str(user_id)] = saldo
    save_user_saldo(saldo_dict)

def add_user_saldo(user_id, amount):
    saldo = get_user_saldo(user_id)
    set_user_saldo(user_id, saldo + amount)

def reduce_user_saldo(user_id, amount):
    saldo = get_user_saldo(user_id)
    if saldo >= amount:
        set_user_saldo(user_id, saldo - amount)
        return True
    return False

# ---- User List Management ----
def load_user_list():
    if not os.path.exists(USER_LIST_FILE):
        with open(USER_LIST_FILE, "w") as f:
            json.dump({}, f)
    with open(USER_LIST_FILE) as f:
        return json.load(f)

def save_user_list(user_dict):
    with open(USER_LIST_FILE, "w") as f:
        json.dump(user_dict, f)

def add_user_to_list(user_id, name):
    user_dict = load_user_list()
    user_dict[str(user_id)] = name
    save_user_list(user_dict)

def get_all_users():
    return load_user_list()

# ---- Hesda Store API ----
class HesdaAPI:
    BASE_URL = "https://api.hesda-store.com/v2"
    def __init__(self, api_key: str, email: str, password: str):
        self.api_key = api_key
        self.auth = HTTPBasicAuth(email, password)
    def _request(self, method: str, endpoint: str, params: dict = None, data: dict = None) -> dict:
        url = f"{self.BASE_URL}{endpoint}"
        response = requests.request(method, url, params=params, data=data, auth=self.auth, timeout=30)
        response.raise_for_status()
        resp_json = response.json()
        if resp_json.get("status") is True: return resp_json
        else: raise Exception(resp_json.get("message", "Terjadi kesalahan pada API."))
    def get_saldo(self) -> int:
        params = {'hesdastore': self.api_key}
        d = self._request("GET", "/saldo", params=params)
        return d.get("data", {}).get("saldo", 0)
    def get_paket_list(self, jenis: str) -> list:
        params = {"jenis": jenis, 'hesdastore': self.api_key}
        d = self._request("GET", "/list_paket", params=params)
        return d.get("data", [])
    def request_otp(self, no_hp: str) -> dict:
        payload = {"no_hp": no_hp, "metode": "OTP", 'hesdastore': self.api_key}
        return self._request("POST", "/get_otp", data=payload)
    def login_with_otp(self, auth_id: str, no_hp: str, otp_code: str) -> dict:
        payload = {"no_hp": no_hp, "metode": "OTP", "auth_id": auth_id, "kode_otp": otp_code, 'hesdastore': self.api_key}
        return self._request("POST", "/login_sms", data=payload)
    def beli_paket_otp(self, package_id: str, access_token: str, no_hp: str, price: int, payment_method: str = "QRIS") -> dict:
        payload = {
            "package_id": package_id,
            "access_token": access_token,
            "no_hp": no_hp,
            "uri": "package_purchase_otp",
            "payment_method": payment_method,
            "price_or_fee": str(price),
            "hesdastore": self.api_key,
            "url_callback": "https://yourdomain.com/callback"
        }
        logger.info(f"Payload beli_paket_otp: {payload}")
        return self._request("POST", "/beli/otp", data=payload)

# ---- Paket XL VIDIO di urutan atas ----
def sort_xl_vidio_first(packages):
    vidio_qris = "ZVdMVXcyKzdJRlJERVdJc1hpVUhmQQ"
    vidio_pulsa = "MTJLR28vN3VpUmxObFdHelZwRnVUUQ"
    vidio_items = []
    other_items = []
    for pkg in packages:
        if pkg.get('package_id') in [vidio_pulsa, vidio_qris]:
            vidio_items.append(pkg)
        else:
            other_items.append(pkg)
    ordered = [pkg for pkg in vidio_items if pkg.get('package_id') == vidio_pulsa] + \
              [pkg for pkg in vidio_items if pkg.get('package_id') == vidio_qris] + \
              other_items
    return ordered

def build_paginated_keyboard(packages: list, page: int, callback_prefix: str, user_id=None) -> InlineKeyboardMarkup:
    PER_PAGE = 5
    buttons = []
    start = page * PER_PAGE
    end = start + PER_PAGE
    for pkg in packages[start:end]:
        package_id = pkg.get('package_id')
        original_name = pkg.get('package_name_show', 'Tanpa Nama')
        nama_paket = NAMA_PAKET_KUSTOM.get(package_id, original_name)
        if len(nama_paket) > 50:
             nama_paket = nama_paket[:47] + "..."
        harga_asli = pkg.get('harga_int', 0)
        harga_user = harga_asli + JASA_FEE
        harga_text = f"Rp {harga_user:,.0f}".replace(",", ".")
        callback_data = f"{callback_prefix}:{package_id}:{harga_user}:{harga_asli}"
        buttons.append([InlineKeyboardButton(f"{nama_paket} - {harga_text}", callback_data=callback_data)])
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("« Sebelumnya", callback_data=f"page_{callback_prefix}:{page-1}"))
    if end < len(packages):
        nav_buttons.append(InlineKeyboardButton("Selanjutnya »", callback_data=f"page_{callback_prefix}:{page+1}"))
    if nav_buttons:
        buttons.append(nav_buttons)
    buttons.append([InlineKeyboardButton("« Kembali ke Menu Utama", callback_data="main_menu")])
    return InlineKeyboardMarkup(buttons)

def build_main_menu(user_id):
    keyboard = [
        [InlineKeyboardButton("💰 Cek Saldo", callback_data="menu_saldo")],
        [InlineKeyboardButton("🛍️ Beli Paket", callback_data="menu_beli_paket")]
    ]
    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("⚡ Topup Saldo User", callback_data="menu_topup")])
        keyboard.append([InlineKeyboardButton("👥 Pelanggan", callback_data="menu_pelanggan")])
    else:
        keyboard.append([InlineKeyboardButton("➕ Top Up Saldo", callback_data="menu_user_topup")])
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    # Simpan user ke database pelanggan
    add_user_to_list(user.id, user.first_name)
    await update.message.reply_html(
        f"👋 Halo, {user.mention_html()}!\n\nSelamat datang di bot VADD STORE!",
        reply_markup=build_main_menu(user.id)
    )

async def main_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Anda kembali ke Menu Utama. Silakan pilih lagi:", reply_markup=build_main_menu(query.from_user.id))
    context.user_data.clear()
    return ConversationHandler.END

async def saldo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if user_id == ADMIN_ID:
        await query.edit_message_text(text="⏳ Mengambil saldo backend (admin)...")
        try:
            api = HesdaAPI(HESDA_API_KEY, HESDA_EMAIL, HESDA_PASSWORD)
            saldo = api.get_saldo()
            saldo_formatted = f"Rp {saldo:,.0f}".replace(",", ".")
            text = f"✅ Saldo backend (Hesda Store Anda): <b>{saldo_formatted}</b>"
        except Exception as e:
            logger.error(f"Gagal mengambil saldo backend: {e}")
            text = f"❌ Terjadi kesalahan:\n\n<code>{e}</code>"
    else:
        saldo_user = get_user_saldo(user_id)
        saldo_formatted = f"Rp {saldo_user:,.0f}".replace(",", ".")
        text = f"✅ Saldo Anda: <b>{saldo_formatted}</b>\n\nSaldo ini digunakan untuk beli paket melalui bot."
    keyboard = [[InlineKeyboardButton("« Kembali", callback_data="main_menu")]]
    await query.edit_message_text(text=text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

async def user_topup_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("🔗 Link Top Up Tripay (coming soon)", url="https://tripay.co.id")],
        [InlineKeyboardButton("« Kembali ke Menu Utama", callback_data="main_menu")]
    ]
    await query.edit_message_text(
        "Untuk topup saldo, klik tombol di bawah. Setelah pembayaran, saldo akan otomatis masuk ke akun Anda (fitur coming soon).",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def pelanggan_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    users = get_all_users()
    if users:
        text = "<b>Daftar Pelanggan:</b>\n\n"
        for uid, name in users.items():
            saldo = get_user_saldo(uid)
            saldo_formatted = f"Rp {saldo:,.0f}".replace(",", ".")
            text += f"• <b>{name}</b> | <code>{uid}</code> | Saldo: <b>{saldo_formatted}</b>\n"
    else:
        text = "<i>Belum ada pelanggan yang menggunakan bot.</i>"
    keyboard = [[InlineKeyboardButton("« Kembali ke Menu Utama", callback_data="main_menu")]]
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

async def beli_paket_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    keyboard = [[InlineKeyboardButton("🔫 Tembak Paket XL", callback_data="list_paket:otp:0")], [InlineKeyboardButton("« Kembali", callback_data="main_menu")]]
    await query.edit_message_text("Silakan pilih jenis paket yang ingin Anda beli:", reply_markup=InlineKeyboardMarkup(keyboard))

async def list_paket_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if query.data.startswith("page_"):
        parts = query.data.split(":")
        callback_prefix = parts[0].replace("page_", "")
        page = int(parts[1])
        jenis = "otp" if "otp" in callback_prefix else "nonotp"
    else:
        _, jenis, page_str = query.data.split(":")
        page = int(page_str)
        callback_prefix = "buy_otp" if jenis == "otp" else "buy_nonotp"

    if 'packages' not in context.user_data or context.user_data.get('jenis') != jenis:
        await query.edit_message_text(text=f"⏳ Mengambil daftar paket {jenis}...")
        api = HesdaAPI(HESDA_API_KEY, HESDA_EMAIL, HESDA_PASSWORD)
        packages = api.get_paket_list(jenis)
        packages = sort_xl_vidio_first(packages)
        context.user_data['packages'] = packages
        context.user_data['jenis'] = jenis
    else:
        packages = context.user_data['packages']
    if not packages:
        await query.edit_message_text(f"Tidak ada paket untuk jenis '{jenis}'.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Kembali", callback_data="menu_beli_paket")]]))
        return
    keyboard = build_paginated_keyboard(packages, page, callback_prefix, user_id)
    title = "Tembak Paket XL" if jenis == "otp" else "Paket"
    await query.edit_message_text(f"👇 Silakan pilih salah satu **{title}**:", reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

async def show_package_description(query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE) -> str:
    _, package_id, harga_user, harga_asli = query.data.split(":")
    context.user_data['package_id'] = package_id
    context.user_data['harga_user'] = int(harga_user)
    context.user_data['harga_asli'] = int(harga_asli)
    all_packages = context.user_data.get('packages', [])
    selected_package = next((pkg for pkg in all_packages if pkg.get('package_id') == package_id), None)
    if not selected_package:
        await query.edit_message_text("❌ Paket tidak ditemukan. Silakan coba lagi.", reply_markup=build_main_menu(query.from_user.id))
        return ConversationHandler.END
    nama = selected_package.get('package_name_show', 'N/A')
    harga = f"Rp {int(harga_user):,.0f}".replace(",", ".")
    deskripsi_raw = selected_package.get('package_description_show', 'Tidak ada deskripsi.')
    deskripsi = deskripsi_raw.replace('\r\n', '\n').strip() if deskripsi_raw else 'Tidak ada deskripsi.'
    text = (f"<b>Anda memilih:</b>\n{nama}\n\n<b>Harga:</b> {harga}\n\n<b>Deskripsi:</b>\n{deskripsi}\n\n--------------------\n")
    return text

async def select_package(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    description_text = await show_package_description(query, context)
    final_text = description_text + "➡️ Masukkan <b>Nomor HP XL/Axis</b> Anda untuk meminta kode OTP:"
    await query.edit_message_text(final_text, parse_mode=ParseMode.HTML)
    return GET_PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    no_hp = update.message.text
    context.user_data['no_hp'] = no_hp
    await update.message.reply_text(f"⏳ Meminta kode OTP untuk <b>{no_hp}</b>...", parse_mode=ParseMode.HTML)
    try:
        api = HesdaAPI(HESDA_API_KEY, HESDA_EMAIL, HESDA_PASSWORD)
        result = api.request_otp(no_hp)
        auth_id = result.get('data', {}).get('auth_id')
        if not auth_id: raise Exception("Gagal mendapatkan auth_id dari API.")
        context.user_data['auth_id'] = auth_id
        await update.message.reply_text("📲 Kode OTP telah dikirim. Masukkan kode OTP yang Anda terima:")
        return GET_OTP_CODE
    except Exception as e:
        logger.error(f"Gagal meminta OTP: {e}")
        await update.message.reply_html(f"❌ Gagal meminta OTP:\n\n<code>{e}</code>", reply_markup=build_main_menu(update.effective_user.id))
        context.user_data.clear()
        return ConversationHandler.END

async def get_otp_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    otp_code = update.message.text
    await update.message.reply_text("🔐 Memverifikasi kode OTP...", parse_mode=ParseMode.HTML)
    try:
        api = HesdaAPI(HESDA_API_KEY, HESDA_EMAIL, HESDA_PASSWORD)
        login_result = api.login_with_otp(
            auth_id=context.user_data['auth_id'],
            no_hp=context.user_data['no_hp'],
            otp_code=otp_code
        )
        access_token = login_result.get('data', {}).get('access_token')
        if not access_token:
            raise Exception("Gagal mendapatkan access_token setelah verifikasi OTP.")
        context.user_data['access_token'] = access_token
        harga_user = context.user_data['harga_user']
        harga_formatted = f"Rp {harga_user:,.0f}".replace(",", ".")
        confirmation_text = (
            f"✅ Berhasil login dengan OTP!\n\n"
            f"<b>Lanjutkan tembak?</b>\n"
            f"Setelah Anda klik lanjutkan, <b>{harga_formatted}</b> akan terpotong dari saldo Anda sebagai jasa tembak."
        )
        keyboard = [
            [InlineKeyboardButton("✔️ Lanjut Tembak", callback_data="otp_confirm_purchase")],
            [InlineKeyboardButton("❌ Batal", callback_data="main_menu")]
        ]
        await update.message.reply_html(confirmation_text, reply_markup=InlineKeyboardMarkup(keyboard))
        return CONFIRM_PURCHASE
    except Exception as e:
        logger.error(f"Gagal verifikasi OTP: {e}")
        await update.message.reply_html(f"❌ Gagal verifikasi OTP:\n\n<code>{e}</code>", reply_markup=build_main_menu(update.effective_user.id))
        context.user_data.clear()
        return ConversationHandler.END

async def process_purchase_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    harga_user = context.user_data['harga_user']
    harga_asli = context.user_data['harga_asli']
    await query.edit_message_text("⏳ Memproses pembelian akhir...", parse_mode=ParseMode.HTML)
    # Cek saldo user cukup
    if user_id != ADMIN_ID:
        saldo_user = get_user_saldo(user_id)
        if saldo_user < harga_user:
            await query.edit_message_text(
                f"❌ Saldo Anda kurang.\nHarga paket: Rp {harga_user:,.0f}\nSaldo Anda: Rp {saldo_user:,.0f}\nSilakan topup saldo.",
                parse_mode=ParseMode.HTML,
                reply_markup=build_main_menu(user_id)
            )
            context.user_data.clear()
            return ConversationHandler.END
        reduce_user_saldo(user_id, harga_user)  # Potong saldo user

    # Proses ke Hesda Store
    try:
        api = HesdaAPI(HESDA_API_KEY, HESDA_EMAIL, HESDA_PASSWORD)
        beli_result = api.beli_paket_otp(
            package_id=context.user_data['package_id'],
            access_token=context.user_data['access_token'],
            no_hp=context.user_data['no_hp'],
            price=harga_asli  # harga backend
        )
        message = beli_result.get('message', 'Transaksi berhasil.')
        result_text = f"✅ **Pembelian Berhasil Diproses**\n\n{message}"
        reply_markup = build_main_menu(user_id)
        if beli_result.get('data', {}).get('is_qris', False):
            qris_code = beli_result['data']['qris_data']['qr_code']
            qr_img = qrcode.make(qris_code)
            bio = BytesIO()
            bio.name = 'qris.png'
            qr_img.save(bio, 'PNG')
            bio.seek(0)
            await query.message.reply_photo(
                photo=bio,
                caption=(
                    "✅ Pembelian Berhasil Diproses\n\n"
                    "Silakan scan/upload QR di bawah ke E-Wallet/Mbanking Anda dan segera lakukan pembayaran!\n"
                    "Abaikan jika sudah melakukan pembayaran."
                )
            )
            result_text = None
        if beli_result.get('data', {}).get('have_deeplink', False):
            deeplink = beli_result['data']['deeplink_data']['deeplink_url']
            if result_text:
                result_text += f"\n\nAtau bayar dengan DANA melalui link di bawah."
            else:
                result_text = "Atau bayar dengan DANA melalui link di bawah."
            reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("Bayar dengan DANA", url=deeplink)], [InlineKeyboardButton("Selesai", callback_data="main_menu")]])
    except Exception as e:
        logger.error(f"Gagal dalam pembelian akhir OTP: {e}")
        result_text = f"❌ Gagal memproses pembelian:\n\n<code>{e}</code>"
        reply_markup = build_main_menu(user_id)
    if result_text:
        await query.edit_message_text(result_text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
    else:
        await query.edit_message_text("Setelah pembayaran, klik 'Selesai' untuk kembali ke menu.", reply_markup=reply_markup)
    context.user_data.clear()
    return ConversationHandler.END

# ---- Admin Topup Saldo User (Manual) ----
async def topup_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Masukkan ID Telegram user yang ingin ditopup saldo (balas pesan ini dengan ID):")
    context.user_data.clear()
    return ADMIN_TOPUP_ID

async def topup_step_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # Step 1: Input ID
    if 'target_id' not in context.user_data:
        try:
            target_id = int(update.message.text.strip())
            context.user_data['target_id'] = target_id
            users = get_all_users()
            user_name = users.get(str(target_id), None)
            if user_name:
                await update.message.reply_text(f"User ditemukan: <b>{user_name}</b> (ID: <code>{target_id}</code>)\nMasukkan jumlah saldo yang ingin ditambahkan (dalam rupiah):", parse_mode=ParseMode.HTML)
            else:
                await update.message.reply_text(f"User dengan ID <code>{target_id}</code> belum pernah menggunakan bot. Tetap lanjutkan topup? Masukkan jumlah saldo yang ingin ditambahkan (dalam rupiah):", parse_mode=ParseMode.HTML)
            return ADMIN_TOPUP_AMOUNT
        except:
            await update.message.reply_text("ID tidak valid. Masukkan angka ID Telegram user.")
            return ADMIN_TOPUP_ID
    # Step 2: Input Jumlah Saldo
    else:
        try:
            amount = int(update.message.text.strip())
            target_id = context.user_data['target_id']
            add_user_saldo(target_id, amount)
            users = get_all_users()
            user_name = users.get(str(target_id), "-")
            await update.message.reply_text(
                f"✅ Berhasil menambahkan saldo Rp {amount:,.0f} ke user <b>{user_name}</b> (ID: <code>{target_id}</code>).",
                parse_mode=ParseMode.HTML
            )
            context.user_data.clear()
            return ConversationHandler.END
        except:
            await update.message.reply_text("Jumlah saldo tidak valid. Masukkan angka.")
            return ADMIN_TOPUP_AMOUNT

def main() -> None:
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # ConversationHandler khusus topup saldo user (entry: tombol, step: input ID lalu jumlah)
    topup_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(topup_menu, pattern="^menu_topup$")],
        states={
            ADMIN_TOPUP_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, topup_step_handler)],
            ADMIN_TOPUP_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, topup_step_handler)],
        },
        fallbacks=[CallbackQueryHandler(main_menu_handler, pattern="^main_menu$")],
        allow_reentry=True
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(main_menu_handler, pattern="^main_menu$"))
    application.add_handler(CallbackQueryHandler(saldo_handler, pattern="^menu_saldo$"))
    application.add_handler(CallbackQueryHandler(beli_paket_menu, pattern="^menu_beli_paket$"))
    application.add_handler(CallbackQueryHandler(list_paket_handler, pattern="^list_paket:"))
    application.add_handler(CallbackQueryHandler(list_paket_handler, pattern="^page_"))
    application.add_handler(CallbackQueryHandler(user_topup_menu_handler, pattern="^menu_user_topup$", block=False))
    application.add_handler(CallbackQueryHandler(pelanggan_menu_handler, pattern="^menu_pelanggan$", block=False))
    application.add_handler(topup_conv)
    # Handler untuk transaksi paket, dsb tetap...

    logger.info("Bot multi saldo VADD berjalan...")
    application.run_polling()

if __name__ == "__main__":
    main()