import logging
import json
import os
import random
import traceback
import csv
import io
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ConversationHandler, filters, ContextTypes

# --- CẤU HÌNH ---
# Ưu tiên lấy TOKEN/ADMIN_ID từ file config.json (không dính vào code, an toàn hơn
# khi chia sẻ/upload code lên đâu đó). Nếu chưa có file config.json, tự tạo từ giá trị dưới
# rồi lần sau chỉ cần sửa trong config.json, không cần sửa code nữa.
CONFIG_FILE = 'config.json'
_DEFAULT_TOKEN = "8665912079:AAF40ASx3CvRSSgrPagKVML_txyWInOkb6g"
_DEFAULT_ADMIN_ID = 123456789  # <-- Nếu chưa có config.json, THAY SỐ NÀY bằng ID Telegram của mày

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
            cfg.setdefault('admin_ids', [cfg.get('admin_id', _DEFAULT_ADMIN_ID)])
    else:
        cfg = {'token': _DEFAULT_TOKEN, 'admin_id': _DEFAULT_ADMIN_ID, 'admin_ids': [_DEFAULT_ADMIN_ID]}
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    return cfg

def save_config():
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(_config, f, ensure_ascii=False, indent=2)

_config = load_config()
TOKEN = os.environ.get('BOT_TOKEN', _config.get('token', _DEFAULT_TOKEN))
ADMIN_ID = int(os.environ.get('ADMIN_ID', _config.get('admin_id', _DEFAULT_ADMIN_ID)))  # chủ bot (owner) — người duy nhất được thêm/xóa admin phụ
ADMIN_IDS = set(_config.get('admin_ids', [ADMIN_ID]))
ADMIN_IDS.add(ADMIN_ID)

# --- GHI LOG RA FILE (giúp truy lỗi khi bot crash mà không thấy màn hình) ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot_error.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# --- DỮ LIỆU SẢN PHẨM MẶC ĐỊNH (chỉ dùng khi chạy lần đầu, chưa có file lưu trữ) ---
DEFAULT_PRODUCTS = {
    '1': {
        'name': 'Full code bot ff',
        'price': 800000,
        'description': 'làm lag lập team5 bật hd7',
        'stock': 9999,
        'image': 'https://picsum.photos/400/400?random=1'
    },
    '2': {
        'name': 'file mod nhân vật',
        'price': 30000,
        'description': 'antiban cao',
        'stock': 9999,
        'image': 'https://picsum.photos/400/400?random=2'
    },
    '3': {
        'name': 'aimlock adr',
        'price': 160000,
        'description': 'kéo là bám đầu 160k=1ob 220k=2ob 330k=vv',
        'stock': 9316,
        'image': 'https://picsum.photos/400/400?random=3'
    }
}

# --- LƯU DỮ LIỆU ---
DATA_FILE = 'shop_data.json'

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            loaded = json.load(f)
            loaded.setdefault('products', json.loads(json.dumps(DEFAULT_PRODUCTS)))
            loaded.setdefault('product_counter', max([int(k) for k in loaded['products'].keys()] + [0]))
            loaded.setdefault('discounts', {})
            return loaded
    return {
        'carts': {},
        'orders': [],
        'order_counter': 0,
        'products': json.loads(json.dumps(DEFAULT_PRODUCTS)),
        'product_counter': max([int(k) for k in DEFAULT_PRODUCTS.keys()] + [0]),
        'discounts': {}
    }

def save_data(data):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

data = load_data()
carts = data['carts']
orders = data['orders']
PRODUCTS = data['products']
DISCOUNTS = data['discounts']

def next_product_id():
    data['product_counter'] += 1
    return str(data['product_counter'])

def format_price(price):
    return f"{price:,.0f}đ".replace(',', '.')

def calc_discount(total, code):
    """Trả về (số tiền được giảm, tổng tiền sau giảm). Nếu mã không hợp lệ trả về (0, total)"""
    d = DISCOUNTS.get(code.upper())
    if not d:
        return 0, total
    if d.get('limit') is not None and d.get('used', 0) >= d['limit']:
        return 0, total
    if d['type'] == 'percent':
        amount = total * d['value'] / 100
    else:
        amount = d['value']
    amount = min(amount, total)
    return amount, total - amount

async def notify_admins(context, text, reply_markup=None, parse_mode='Markdown'):
    """Gửi tin nhắn cho TẤT CẢ admin (không chỉ chủ bot)"""
    for aid in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=aid, text=text, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception:
            pass

async def safe_edit_text(query, text, reply_markup=None, parse_mode='Markdown'):
    """Sửa tin nhắn thành text, tự xử lý trường hợp tin nhắn gốc là ảnh (không edit_message_text được)"""
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception:
        try:
            await query.message.delete()
        except Exception:
            pass
        await query.message.chat.send_message(text, reply_markup=reply_markup, parse_mode=parse_mode)

# --- XỬ LÝ THANH TOÁN TỰ ĐỘNG ---
async def process_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý thanh toán tự động qua bank/QR"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    cart = carts.get(str(user_id), {})
    
    if not cart:
        await query.answer("❌ Giỏ hàng trống!", show_alert=True)
        return
    
    # Tính tổng tiền
    total = sum(item['price'] * item['quantity'] for item in cart.values())
    
    # Áp mã giảm giá nếu khách đã nhập
    discount_amount = 0
    discount_code = context.user_data.get('discount_code')
    if discount_code:
        discount_amount, total = calc_discount(total, discount_code)
        if discount_amount > 0:
            d = DISCOUNTS.get(discount_code)
            d['used'] = d.get('used', 0) + 1
        else:
            discount_code = None
        context.user_data.pop('discount_code', None)
    
    # Tạo mã đơn hàng
    data['order_counter'] += 1
    order_id = f"DH{datetime.now().strftime('%d%m%y')}{data['order_counter']:04d}"
    
    # Lưu đơn hàng
    order = {
        'order_id': order_id,
        'user_id': user_id,
        'user_name': query.from_user.first_name,
        'items': cart.copy(),
        'total': total,
        'discount_code': discount_code,
        'discount_amount': discount_amount,
        'status': 'Chờ thanh toán',
        'date': datetime.now().strftime('%d/%m/%Y %H:%M'),
        'payment_method': query.data.split('_')[1] if '_' in query.data else 'bank',
        'stock_deducted': False
    }
    orders.append(order)
    save_data(data)
    
    discount_line = f"🎟️ Mã **{discount_code}**: -{format_price(discount_amount)}\n" if discount_code else ""
    # Tạo tin nhắn đơn hàng + QR thanh toán
    message = f"""✅ **ĐẶT HÀNG THÀNH CÔNG!**

📋 **Mã đơn:** `{order_id}`
{discount_line}💰 **Tổng tiền:** {format_price(total)}

🏦 **Thông tin thanh toán:**
Ngân hàng: **Mbbank**
Số TK: **0356228641**
Chủ TK: **DANG ANH NGHIA**
Nội dung: `{order_id}`

📌 **QR Code thanh toán:** *(Bot tự tạo)*
"""
    
    # Tạo QR code (dùng thư viện qrcode nếu cài)
    try:
        import qrcode
        from io import BytesIO
        
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(f"Bank: Mbbank\nAccount: 0356228641\nAmount: {total}\nContent: {order_id}")
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        
        bio = BytesIO()
        img.save(bio, 'PNG')
        bio.seek(0)
        
        await safe_edit_text(query, message, parse_mode='Markdown')
        await context.bot.send_photo(
            chat_id=user_id,
            photo=bio,
            caption="📱 Quét QR để thanh toán nhanh!"
        )
    except Exception:
        await safe_edit_text(
            query,
            message + "\n\n⚠️ Chưa cài thư viện QR, vui lòng chuyển khoản thủ công.",
            parse_mode='Markdown'
        )
    
    # Nút xác nhận đã thanh toán
    keyboard = [
        [InlineKeyboardButton("✅ Tôi đã chuyển khoản", callback_data=f"confirm_{order_id}")],
        [InlineKeyboardButton("❌ Hủy đơn hàng", callback_data=f"cancel_{order_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(
        chat_id=user_id,
        text="Sau khi chuyển khoản, nhấn nút bên dưới để xác nhận:",
        reply_markup=reply_markup
    )
    
    # Xóa giỏ hàng
    carts[str(user_id)] = {}
    save_data(data)
    
    # Gửi thông báo cho admin
    admin_msg = f"🆕 **ĐƠN HÀNG MỚI!**\n\n"
    admin_msg += f"📋 Mã: {order_id}\n"
    admin_msg += f"👤 Khách: {query.from_user.first_name} (ID: {user_id})\n"
    for pid, item in order['items'].items():
        admin_msg += f"📦 {item['name']} x{item['quantity']}\n"
    admin_msg += f"💰 Tổng: {format_price(total)}\n"
    admin_msg += f"📅 Ngày: {order['date']}"
    
    admin_keyboard = [
        [InlineKeyboardButton("✅ Duyệt đơn (đã nhận tiền)", callback_data=f"admin|orderstatus|{order_id}|paid")],
        [InlineKeyboardButton("❌ Hủy đơn", callback_data=f"admin|orderstatus|{order_id}|cancelled")]
    ]
    await notify_admins(context, admin_msg, reply_markup=InlineKeyboardMarkup(admin_keyboard))

# --- XÁC NHẬN THANH TOÁN ---
async def confirm_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    order_id = query.data.split('_')[1]
    
    # Tìm đơn hàng
    for order in orders:
        if order['order_id'] == order_id:
            if order['status'] == 'Chờ thanh toán':
                order['status'] = 'Đã thanh toán'
                save_data(data)
                
                await query.edit_message_text(
                    f"✅ **XÁC NHẬN THANH TOÁN THÀNH CÔNG!**\n\n"
                    f"📋 Mã đơn: `{order_id}`\n"
                    f"⏳ Đang xử lý đơn hàng...\n"
                    f"📦 Vui lòng liên hệ admin!"
                )
                
                # Thông báo admin
                await notify_admins(context, f"✅ Đơn hàng {order_id} đã được khách xác nhận đã chuyển khoản!")
            else:
                await query.answer(f"⚠️ Đơn hàng đang ở trạng thái: {order['status']}", show_alert=True)
            return
    
    await query.answer("❌ Không tìm thấy đơn hàng!", show_alert=True)

# --- HỦY ĐƠN HÀNG ---
async def cancel_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    order_id = query.data.split('_')[1]
    
    for order in orders:
        if order['order_id'] == order_id:
            if order['status'] == 'Chờ thanh toán':
                order['status'] = 'Đã hủy'
                save_data(data)
                await query.edit_message_text(f"❌ Đã hủy đơn hàng `{order_id}`")
            return

# --- TỰ ĐỘNG HỦY ĐƠN TREO QUÁ LÂU (chạy định kỳ) ---
PENDING_ORDER_TIMEOUT_HOURS = 2

async def auto_cancel_pending_orders(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now()
    cancelled_ids = []
    
    for order in orders:
        if order['status'] != 'Chờ thanh toán':
            continue
        try:
            order_time = datetime.strptime(order['date'], '%d/%m/%Y %H:%M')
        except ValueError:
            continue
        if now - order_time > timedelta(hours=PENDING_ORDER_TIMEOUT_HOURS):
            order['status'] = 'Đã hủy'
            cancelled_ids.append(order['order_id'])
            try:
                await context.bot.send_message(
                    chat_id=order['user_id'],
                    text=f"⏰ Đơn hàng `{order['order_id']}` đã tự động bị hủy do quá "
                         f"{PENDING_ORDER_TIMEOUT_HOURS} tiếng chưa xác nhận thanh toán.",
                    parse_mode='Markdown'
                )
            except Exception:
                pass
    
    if cancelled_ids:
        save_data(data)
        await notify_admins(context, f"⏰ Đã tự động hủy {len(cancelled_ids)} đơn quá hạn: {', '.join(cancelled_ids)}")

# --- XEM SẢN PHẨM ---
async def view_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    keyboard = []
    for pid, product in PRODUCTS.items():
        status = "✅" if product['stock'] > 0 else "❌"
        keyboard.append([InlineKeyboardButton(
            f"{status} {product['name']} — {format_price(product['price'])}",
            callback_data=f"product_{pid}"
        )])
    
    keyboard.append([InlineKeyboardButton("🔍 Tìm sản phẩm", callback_data="search_product")])
    keyboard.append([InlineKeyboardButton("🛒 Xem giỏ hàng", callback_data="view_cart")])
    keyboard.append([InlineKeyboardButton("🏠 Trang chủ", callback_data="back_menu")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    total_products = len(PRODUCTS)
    message = (
        "🛍️ **DANH SÁCH SẢN PHẨM**\n"
        "━━━━━━━━━━━━━━━\n"
        f"📦 Tổng cộng: {total_products} sản phẩm\n\n"
        "👇 Chọn 1 sản phẩm để xem chi tiết:"
    )
    
    await safe_edit_text(query, message, reply_markup=reply_markup, parse_mode='Markdown')

# --- TÌM KIẾM SẢN PHẨM ---
SEARCH_KEYWORD = 300

async def search_product_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit_text(query, "🔍 Gõ từ khóa cần tìm (tên hoặc mô tả sản phẩm):\n\n(gõ /cancel để hủy)", parse_mode=None)
    return SEARCH_KEYWORD

async def search_product_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyword = update.message.text.strip().lower()
    results = {
        pid: p for pid, p in PRODUCTS.items()
        if keyword in p['name'].lower() or keyword in p['description'].lower()
    }
    
    if not results:
        keyboard = [[InlineKeyboardButton("🛍️ Xem tất cả sản phẩm", callback_data="view_products")]]
        await update.message.reply_text(
            f"❌ Không tìm thấy sản phẩm nào khớp với \"{keyword}\".",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ConversationHandler.END
    
    keyboard = []
    for pid, product in results.items():
        status = "✅" if product['stock'] > 0 else "❌"
        keyboard.append([InlineKeyboardButton(
            f"{status} {product['name']} — {format_price(product['price'])}",
            callback_data=f"product_{pid}"
        )])
    keyboard.append([InlineKeyboardButton("🛍️ Xem tất cả sản phẩm", callback_data="view_products")])
    
    await update.message.reply_text(
        f"🔍 Tìm thấy {len(results)} sản phẩm khớp với \"{keyword}\":",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ConversationHandler.END

# --- CHI TIẾT SẢN PHẨM ---
async def product_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    product_id = query.data.split('_')[1]
    product = PRODUCTS.get(product_id)
    
    if not product:
        await query.edit_message_text("❌ Sản phẩm không tồn tại!")
        return
    
    # Reset số lượng chọn về 1 mỗi lần vào lại trang chi tiết
    context.user_data[f'qty_{product_id}'] = 1
    
    await render_product_detail(update, context, product_id, edit=True)

def build_product_keyboard(product_id, qty):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➖", callback_data=f"qtydec_{product_id}"),
            InlineKeyboardButton(f"Số lượng: {qty}", callback_data="noop"),
            InlineKeyboardButton("➕", callback_data=f"qtyinc_{product_id}")
        ],
        [InlineKeyboardButton("🛒 Mua ngay", callback_data=f"buy_{product_id}")],
        [InlineKeyboardButton(f"➕ Thêm vào giỏ (x{qty})", callback_data=f"add_{product_id}")],
        [InlineKeyboardButton("🔙 Quay lại danh sách", callback_data="view_products")],
        [InlineKeyboardButton("🏠 Trang chủ", callback_data="back_menu")]
    ])

async def render_product_detail(update, context, product_id, edit=False):
    query = update.callback_query
    product = PRODUCTS.get(product_id)
    qty = context.user_data.get(f'qty_{product_id}', 1)
    status = "✅ Còn hàng" if product['stock'] > 0 else "❌ Hết hàng"
    
    message = f"""🛍️ **{product['name']}**
━━━━━━━━━━━━━━━
💰 **Giá:** {format_price(product['price'])}
📦 **Tồn kho:** {product['stock']} ({status})
📝 **Mô tả:** {product['description']}
━━━━━━━━━━━━━━━
👇 Chọn số lượng rồi thêm vào giỏ, hoặc mua ngay"""
    
    keyboard = build_product_keyboard(product_id, qty)
    
    if edit:
        # Xóa tin nhắn cũ (danh sách) và gửi thẻ sản phẩm có ảnh cho đẹp
        try:
            await query.message.delete()
        except:
            pass
        await context.bot.send_photo(
            chat_id=query.from_user.id,
            photo=product['image'],
            caption=message,
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
    else:
        # Chỉ cập nhật lại caption/keyboard (khi bấm +/-)
        try:
            await query.edit_message_caption(caption=message, reply_markup=keyboard, parse_mode='Markdown')
        except:
            await query.edit_message_text(message, reply_markup=keyboard, parse_mode='Markdown')

# --- TĂNG / GIẢM SỐ LƯỢNG ---
async def qty_change(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    action, product_id = query.data.split('_')
    product = PRODUCTS.get(product_id)
    
    if not product:
        await query.answer("❌ Sản phẩm không tồn tại!", show_alert=True)
        return
    
    qty = context.user_data.get(f'qty_{product_id}', 1)
    if action == 'qtyinc':
        if qty < product['stock']:
            qty += 1
        else:
            await query.answer("⚠️ Đã đạt số lượng tồn kho tối đa!", show_alert=True)
    elif action == 'qtydec':
        qty = max(1, qty - 1)
    
    context.user_data[f'qty_{product_id}'] = qty
    await query.answer()
    await render_product_detail(update, context, product_id, edit=False)

# --- THÊM VÀO GIỎ ---
async def add_to_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    product_id = query.data.split('_')[1]
    product = PRODUCTS.get(product_id)
    
    if not product or product['stock'] <= 0:
        await query.answer("❌ Sản phẩm đã hết!", show_alert=True)
        return
    
    qty = context.user_data.get(f'qty_{product_id}', 1)
    user_id = str(query.from_user.id)
    cart = carts.setdefault(user_id, {})
    
    if product_id in cart:
        cart[product_id]['quantity'] += qty
    else:
        cart[product_id] = {
            'name': product['name'],
            'price': product['price'],
            'quantity': qty
        }
    save_data(data)
    
    await query.answer(f"✅ Đã thêm {qty}x {product['name']} vào giỏ!", show_alert=True)

# --- NÚT KHÔNG LÀM GÌ (hiển thị số lượng) ---
async def noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()

# --- MUA NGAY (Bỏ qua giỏ hàng) ---
async def buy_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    product_id = query.data.split('_')[1]
    product = PRODUCTS.get(product_id)
    
    if not product or product['stock'] <= 0:
        await query.answer("❌ Sản phẩm đã hết!", show_alert=True)
        return
    
    # Tạo giỏ hàng tạm (dùng đúng số lượng người dùng đã chọn ở trang chi tiết)
    qty = context.user_data.get(f'qty_{product_id}', 1)
    user_id = str(query.from_user.id)
    carts[user_id] = {
        product_id: {
            'name': product['name'],
            'price': product['price'],
            'quantity': qty
        }
    }
    save_data(data)
    
    # Chuyển sang thanh toán
    await process_payment(update, context)

# --- QUẢN LÝ SẢN PHẨM (ADMIN) ---
ADD_NAME, ADD_PRICE, ADD_DESC, ADD_STOCK, ADD_IMAGE = range(5)
EDIT_VALUE = 0

FIELD_LABELS = {
    'name': 'tên sản phẩm',
    'price': 'giá (chỉ nhập số, VD: 150000)',
    'description': 'mô tả',
    'stock': 'số lượng tồn kho (chỉ nhập số)',
    'image': 'link ảnh (URL)'
}

def is_admin(user_id):
    return user_id in ADMIN_IDS

def is_owner(user_id):
    return user_id == ADMIN_ID

async def add_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("⛔ Chỉ chủ bot mới dùng được lệnh này!")
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Cú pháp: /addadmin <ID Telegram>")
        return
    new_id = int(context.args[0])
    ADMIN_IDS.add(new_id)
    _config['admin_ids'] = list(ADMIN_IDS)
    save_config()
    await update.message.reply_text(f"✅ Đã thêm admin: `{new_id}`", parse_mode='Markdown')

async def del_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("⛔ Chỉ chủ bot mới dùng được lệnh này!")
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Cú pháp: /deladmin <ID Telegram>")
        return
    rm_id = int(context.args[0])
    if rm_id == ADMIN_ID:
        await update.message.reply_text("⛔ Không thể xóa chủ bot!")
        return
    ADMIN_IDS.discard(rm_id)
    _config['admin_ids'] = list(ADMIN_IDS)
    save_config()
    await update.message.reply_text(f"✅ Đã xóa admin: `{rm_id}`", parse_mode='Markdown')

async def list_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("⛔ Chỉ chủ bot mới dùng được lệnh này!")
        return
    lines = [f"👑 {aid} (chủ bot)" if aid == ADMIN_ID else f"👤 {aid}" for aid in ADMIN_IDS]
    await update.message.reply_text("📋 **Danh sách admin:**\n" + "\n".join(lines), parse_mode='Markdown')

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mở panel quản lý — dùng chung cho cả nút bấm và lệnh /admin"""
    query = update.callback_query
    user_id = query.from_user.id if query else update.effective_user.id
    
    if not is_admin(user_id):
        text = "⛔ Mày không có quyền dùng chức năng này!"
        if query:
            await query.answer(text, show_alert=True)
        else:
            await update.message.reply_text(text)
        return
    
    keyboard = []
    for pid, p in PRODUCTS.items():
        status = "✅" if p['stock'] > 0 else "❌"
        keyboard.append([InlineKeyboardButton(f"{status} {p['name']}", callback_data=f"admin|edit|{pid}")])
    keyboard.append([InlineKeyboardButton("➕ Thêm sản phẩm mới", callback_data="admin|add")])
    keyboard.append([InlineKeyboardButton("📋 Quản lý đơn hàng", callback_data="admin|orders")])
    keyboard.append([InlineKeyboardButton("🎟️ Mã giảm giá", callback_data="admin|discounts")])
    keyboard.append([InlineKeyboardButton("📢 Gửi thông báo hàng loạt", callback_data="admin|broadcast")])
    keyboard.append([InlineKeyboardButton("🏠 Trang chủ", callback_data="back_menu")])
    
    message = (
        "⚙️ **QUẢN LÝ SẢN PHẨM**\n"
        "━━━━━━━━━━━━━━━\n"
        f"📦 Tổng cộng: {len(PRODUCTS)} sản phẩm\n\n"
        "👇 Bấm vào sản phẩm để sửa/xóa, hoặc thêm mới:"
    )
    
    if query:
        await query.answer()
        await safe_edit_text(query, message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    else:
        await update.message.reply_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def admin_edit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    
    pid = query.data.split('|')[2]
    product = PRODUCTS.get(pid)
    if not product:
        await query.answer("❌ Sản phẩm không tồn tại!", show_alert=True)
        return
    
    keyboard = [
        [InlineKeyboardButton("📝 Sửa tên", callback_data=f"admin|editfield|{pid}|name")],
        [InlineKeyboardButton("💰 Sửa giá", callback_data=f"admin|editfield|{pid}|price")],
        [InlineKeyboardButton("📄 Sửa mô tả", callback_data=f"admin|editfield|{pid}|description")],
        [InlineKeyboardButton("📦 Sửa tồn kho", callback_data=f"admin|editfield|{pid}|stock")],
        [InlineKeyboardButton("🖼️ Sửa ảnh", callback_data=f"admin|editfield|{pid}|image")],
        [InlineKeyboardButton("🗑️ Xóa sản phẩm này", callback_data=f"admin|delete|{pid}")],
        [InlineKeyboardButton("🔙 Quay lại danh sách", callback_data="admin|panel")]
    ]
    message = (
        f"✏️ **{product['name']}**\n"
        "━━━━━━━━━━━━━━━\n"
        f"💰 Giá: {format_price(product['price'])}\n"
        f"📦 Tồn kho: {product['stock']}\n"
        f"📝 Mô tả: {product['description']}\n\n"
        "👇 Chọn thứ cần sửa:"
    )
    await safe_edit_text(query, message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def admin_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    
    pid = query.data.split('|')[2]
    product = PRODUCTS.get(pid)
    if not product:
        await query.answer("❌ Sản phẩm không tồn tại!", show_alert=True)
        return
    
    keyboard = [
        [InlineKeyboardButton("✅ Xác nhận xóa", callback_data=f"admin|delyes|{pid}")],
        [InlineKeyboardButton("❌ Không, giữ lại", callback_data=f"admin|edit|{pid}")]
    ]
    await safe_edit_text(
        query,
        f"⚠️ **XÁC NHẬN XÓA**\n━━━━━━━━━━━━━━━\nXóa sản phẩm **{product['name']}**?\nKhông thể hoàn tác sau khi xóa.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def admin_delete_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    
    pid = query.data.split('|')[2]
    product = PRODUCTS.pop(pid, None)
    save_data(data)
    
    if product:
        await query.answer(f"🗑️ Đã xóa: {product['name']}", show_alert=True)
    await admin_panel(update, context)

# --- THÊM SẢN PHẨM MỚI (hội thoại nhiều bước) ---
async def admin_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return ConversationHandler.END
    
    context.user_data['new_product'] = {}
    await safe_edit_text(
        query,
        "➕ **THÊM SẢN PHẨM MỚI**\n━━━━━━━━━━━━━━━\n📝 Nhập tên sản phẩm:\n\n(gõ /cancel để hủy bất cứ lúc nào)",
        parse_mode='Markdown'
    )
    return ADD_NAME

async def admin_add_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_product']['name'] = update.message.text.strip()
    await update.message.reply_text("💰 Nhập giá (chỉ số, VD: 150000):")
    return ADD_PRICE

async def admin_add_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cleaned = update.message.text.strip().replace('.', '').replace(',', '').replace('đ', '')
    if not cleaned.isdigit():
        await update.message.reply_text("⚠️ Giá không hợp lệ, chỉ nhập số. Nhập lại:")
        return ADD_PRICE
    context.user_data['new_product']['price'] = int(cleaned)
    await update.message.reply_text("📄 Nhập mô tả sản phẩm:")
    return ADD_DESC

async def admin_add_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_product']['description'] = update.message.text.strip()
    await update.message.reply_text("📦 Nhập số lượng tồn kho (chỉ số):")
    return ADD_STOCK

async def admin_add_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cleaned = update.message.text.strip()
    if not cleaned.isdigit():
        await update.message.reply_text("⚠️ Vui lòng nhập số nguyên. Nhập lại:")
        return ADD_STOCK
    context.user_data['new_product']['stock'] = int(cleaned)
    await update.message.reply_text("🖼️ Nhập link ảnh (URL), hoặc gõ `-` để dùng ảnh mặc định:")
    return ADD_IMAGE

async def admin_add_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    image = text if text != '-' else f"https://picsum.photos/400/400?random={random.randint(1, 9999)}"
    context.user_data['new_product']['image'] = image
    
    new_id = next_product_id()
    PRODUCTS[new_id] = context.user_data.pop('new_product')
    save_data(data)
    
    p = PRODUCTS[new_id]
    keyboard = [[InlineKeyboardButton("⚙️ Về quản lý sản phẩm", callback_data="admin|panel")]]
    await update.message.reply_text(
        f"✅ **Đã thêm sản phẩm mới!**\n━━━━━━━━━━━━━━━\n"
        f"🛍️ {p['name']}\n💰 {format_price(p['price'])}\n📦 Tồn kho: {p['stock']}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )
    return ConversationHandler.END

# --- SỬA 1 TRƯỜNG CỦA SẢN PHẨM (hội thoại 1 bước) ---
async def admin_editfield_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return ConversationHandler.END
    
    _, _, pid, field = query.data.split('|')
    product = PRODUCTS.get(pid)
    if not product:
        await query.answer("❌ Sản phẩm không tồn tại!", show_alert=True)
        return ConversationHandler.END
    
    context.user_data['edit_pid'] = pid
    context.user_data['edit_field'] = field
    label = FIELD_LABELS.get(field, field)
    current = product.get(field, '')
    if field == 'price':
        current = format_price(current)
    
    await safe_edit_text(
        query,
        f"✏️ Nhập {label} mới cho **{product['name']}**\n(hiện tại: {current})\n\n(gõ /cancel để hủy)",
        parse_mode='Markdown'
    )
    return EDIT_VALUE

async def admin_editfield_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pid = context.user_data.get('edit_pid')
    field = context.user_data.get('edit_field')
    product = PRODUCTS.get(pid)
    
    if not product:
        await update.message.reply_text("❌ Sản phẩm không còn tồn tại.")
        return ConversationHandler.END
    
    value = update.message.text.strip()
    if field in ('price', 'stock'):
        cleaned = value.replace('.', '').replace(',', '').replace('đ', '')
        if not cleaned.isdigit():
            await update.message.reply_text("⚠️ Vui lòng nhập số hợp lệ. Nhập lại:")
            return EDIT_VALUE
        value = int(cleaned)
    
    product[field] = value
    save_data(data)
    
    context.user_data.pop('edit_pid', None)
    context.user_data.pop('edit_field', None)
    
    keyboard = [[InlineKeyboardButton("⚙️ Về quản lý sản phẩm", callback_data="admin|panel")]]
    await update.message.reply_text(
        f"✅ Đã cập nhật {FIELD_LABELS.get(field, field)} thành công!",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ConversationHandler.END

async def admin_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop('new_product', None)
    context.user_data.pop('edit_pid', None)
    context.user_data.pop('edit_field', None)
    await update.message.reply_text("❌ Đã hủy thao tác.")
    return ConversationHandler.END

# --- QUẢN LÝ ĐƠN HÀNG (ADMIN) ---
STATUS_EMOJI = {
    'Chờ thanh toán': '⏳',
    'Đã thanh toán': '✅',
    'Hoàn thành': '📦',
    'Đã hủy': '❌'
}
STATUS_CODE = {
    'paid': 'Đã thanh toán',
    'completed': 'Hoàn thành',
    'cancelled': 'Đã hủy'
}

def find_order(order_id):
    for order in orders:
        if order['order_id'] == order_id:
            return order
    return None

async def admin_orders_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    
    recent = list(reversed(orders[-15:]))
    keyboard = []
    for order in recent:
        badge = STATUS_EMOJI.get(order['status'], '📦')
        keyboard.append([InlineKeyboardButton(
            f"{badge} {order['order_id']} — {format_price(order['total'])}",
            callback_data=f"admin|orderview|{order['order_id']}"
        )])
    keyboard.append([InlineKeyboardButton("📈 Thống kê doanh thu", callback_data="admin|stats")])
    keyboard.append([InlineKeyboardButton("📊 Xuất báo cáo (Excel/CSV)", callback_data="admin|export")])
    keyboard.append([InlineKeyboardButton("⚙️ Quản lý sản phẩm", callback_data="admin|panel")])
    keyboard.append([InlineKeyboardButton("🏠 Trang chủ", callback_data="back_menu")])
    
    pending_count = sum(1 for o in orders if o['status'] == 'Chờ thanh toán')
    message = (
        "📋 **QUẢN LÝ ĐƠN HÀNG**\n"
        "━━━━━━━━━━━━━━━\n"
        f"📦 Tổng số đơn: {len(orders)}\n"
        f"⏳ Đang chờ xử lý: {pending_count}\n\n"
        "👇 15 đơn gần nhất (bấm để xem & xử lý):" if recent else "Chưa có đơn hàng nào."
    )
    await safe_edit_text(query, message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def admin_order_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    
    order_id = query.data.split('|')[2]
    order = find_order(order_id)
    if not order:
        await query.answer("❌ Không tìm thấy đơn hàng!", show_alert=True)
        return
    
    items_text = "\n".join(
        f"   📦 {item['name']} x{item['quantity']} = {format_price(item['price'] * item['quantity'])}"
        for item in order['items'].values()
    )
    badge = STATUS_EMOJI.get(order['status'], '📦')
    message = (
        f"📋 **ĐƠN HÀNG {order['order_id']}**\n"
        "━━━━━━━━━━━━━━━\n"
        f"👤 Khách: {order['user_name']} (ID: `{order['user_id']}`)\n"
        f"📅 Ngày: {order['date']}\n"
        f"{badge} Trạng thái: **{order['status']}**\n\n"
        f"{items_text}\n\n"
        f"💰 **Tổng tiền:** {format_price(order['total'])}"
    )
    
    keyboard = []
    if order['status'] == 'Chờ thanh toán':
        keyboard.append([InlineKeyboardButton("✅ Xác nhận đã thanh toán", callback_data=f"admin|orderstatus|{order_id}|paid")])
        keyboard.append([InlineKeyboardButton("❌ Hủy đơn", callback_data=f"admin|orderstatus|{order_id}|cancelled")])
    elif order['status'] == 'Đã thanh toán':
        keyboard.append([InlineKeyboardButton("📦 Đánh dấu hoàn thành", callback_data=f"admin|orderstatus|{order_id}|completed")])
        keyboard.append([InlineKeyboardButton("❌ Hủy đơn", callback_data=f"admin|orderstatus|{order_id}|cancelled")])
    keyboard.append([InlineKeyboardButton("🔙 Quay lại danh sách", callback_data="admin|orders")])
    
    await safe_edit_text(query, message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def admin_order_setstatus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    
    _, _, order_id, code = query.data.split('|')
    order = find_order(order_id)
    new_status = STATUS_CODE.get(code)
    
    if not order or not new_status:
        await query.answer("❌ Không tìm thấy đơn hàng!", show_alert=True)
        return
    
    order['status'] = new_status
    
    # Trừ kho khi đơn được duyệt "Đã thanh toán" LẦN ĐẦU (tránh trừ trùng nếu đổi qua đổi lại)
    low_stock_alerts = []
    if new_status == 'Đã thanh toán' and not order.get('stock_deducted'):
        for pid, item in order['items'].items():
            product = PRODUCTS.get(pid)
            if product:
                product['stock'] = max(0, product['stock'] - item['quantity'])
                if product['stock'] <= 5:
                    low_stock_alerts.append(f"⚠️ **{product['name']}** chỉ còn {product['stock']} trong kho!")
        order['stock_deducted'] = True
    
    save_data(data)
    await query.answer(f"✅ Đã chuyển đơn {order_id} sang: {new_status}", show_alert=True)
    
    for alert in low_stock_alerts:
        await notify_admins(context, alert)
    
    # Báo cho khách biết trạng thái đơn đã thay đổi
    try:
        await context.bot.send_message(
            chat_id=order['user_id'],
            text=f"📢 Đơn hàng `{order_id}` của bạn đã được cập nhật: **{new_status}**",
            parse_mode='Markdown'
        )
    except Exception:
        pass
    
    await admin_order_view(update, context)

async def admin_export_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    
    if not orders:
        await query.answer("❌ Chưa có đơn hàng nào để xuất!", show_alert=True)
        return
    
    output = io.StringIO()
    # \ufeff (BOM) để Excel đọc đúng tiếng Việt có dấu
    output.write('\ufeff')
    writer = csv.writer(output)
    writer.writerow(['Mã đơn', 'Khách hàng', 'User ID', 'Sản phẩm', 'Tổng tiền', 'Mã giảm giá', 'Trạng thái', 'Ngày'])
    
    for o in orders:
        items_text = "; ".join(f"{it['name']} x{it['quantity']}" for it in o['items'].values())
        writer.writerow([
            o['order_id'], o['user_name'], o['user_id'], items_text,
            o['total'], o.get('discount_code') or '-', o['status'], o['date']
        ])
    
    file_bytes = io.BytesIO(output.getvalue().encode('utf-8'))
    file_bytes.name = f"baocao_donhang_{datetime.now().strftime('%d%m%Y')}.csv"
    
    await context.bot.send_document(
        chat_id=query.from_user.id,
        document=file_bytes,
        caption=f"📊 Báo cáo {len(orders)} đơn hàng — mở được bằng Excel/Google Sheets"
    )

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    
    paid_statuses = ('Đã thanh toán', 'Hoàn thành')
    revenue = sum(o['total'] for o in orders if o['status'] in paid_statuses)
    today_str = datetime.now().strftime('%d/%m/%Y')
    revenue_today = sum(o['total'] for o in orders if o['status'] in paid_statuses and o['date'].startswith(today_str))
    
    count_pending = sum(1 for o in orders if o['status'] == 'Chờ thanh toán')
    count_paid = sum(1 for o in orders if o['status'] == 'Đã thanh toán')
    count_completed = sum(1 for o in orders if o['status'] == 'Hoàn thành')
    count_cancelled = sum(1 for o in orders if o['status'] == 'Đã hủy')
    
    # Sản phẩm bán chạy nhất (theo số lượng, chỉ tính đơn đã thanh toán/hoàn thành)
    sold_count = {}
    for o in orders:
        if o['status'] in paid_statuses:
            for pid, item in o['items'].items():
                sold_count[item['name']] = sold_count.get(item['name'], 0) + item['quantity']
    top_products = sorted(sold_count.items(), key=lambda x: x[1], reverse=True)[:3]
    top_text = "\n".join(f"   🏆 {name}: {qty} sản phẩm" for name, qty in top_products) if top_products else "   (chưa có dữ liệu)"
    
    message = (
        "📈 **THỐNG KÊ DOANH THU**\n"
        "━━━━━━━━━━━━━━━\n"
        f"💰 Tổng doanh thu: {format_price(revenue)}\n"
        f"📅 Doanh thu hôm nay: {format_price(revenue_today)}\n"
        "━━━━━━━━━━━━━━━\n"
        f"📦 Tổng số đơn: {len(orders)}\n"
        f"⏳ Chờ xử lý: {count_pending}\n"
        f"✅ Đã thanh toán: {count_paid}\n"
        f"📦 Hoàn thành: {count_completed}\n"
        f"❌ Đã hủy: {count_cancelled}\n"
        "━━━━━━━━━━━━━━━\n"
        "🏆 **Bán chạy nhất:**\n"
        f"{top_text}"
    )
    keyboard = [[InlineKeyboardButton("🔙 Quay lại", callback_data="admin|orders")]]
    await safe_edit_text(query, message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

# --- GỬI THÔNG BÁO HÀNG LOẠT (ADMIN) ---
BROADCAST_MSG = 400

async def admin_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return ConversationHandler.END
    
    customer_count = len(set(o['user_id'] for o in orders))
    await safe_edit_text(
        query,
        f"📢 Gõ nội dung muốn gửi cho **{customer_count} khách hàng** đã từng đặt hàng:\n\n(gõ /cancel để hủy)",
        parse_mode='Markdown'
    )
    return BROADCAST_MSG

async def admin_broadcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_ids = set(o['user_id'] for o in orders)
    
    sent, failed = 0, 0
    for uid in user_ids:
        try:
            await context.bot.send_message(chat_id=uid, text=f"📢 **THÔNG BÁO**\n━━━━━━━━━━━━━━━\n{text}", parse_mode='Markdown')
            sent += 1
        except Exception:
            failed += 1
    
    keyboard = [[InlineKeyboardButton("⚙️ Về quản lý sản phẩm", callback_data="admin|panel")]]
    await update.message.reply_text(
        f"✅ Đã gửi xong!\n📤 Thành công: {sent}\n❌ Thất bại (đã chặn bot/xóa tài khoản): {failed}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ConversationHandler.END

# --- QUẢN LÝ MÃ GIẢM GIÁ (ADMIN) ---
ADD_CODE, ADD_TYPE, ADD_VALUE, ADD_LIMIT = range(10, 14)

async def admin_discounts_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    
    keyboard = []
    for code, d in DISCOUNTS.items():
        value_text = f"{d['value']}%" if d['type'] == 'percent' else format_price(d['value'])
        used = d.get('used', 0)
        limit_text = f"{used}/{d['limit']}" if d.get('limit') is not None else f"{used}/∞"
        keyboard.append([InlineKeyboardButton(f"🎟️ {code} (-{value_text}) — đã dùng {limit_text}", callback_data=f"admin|dcview|{code}")])
    keyboard.append([InlineKeyboardButton("➕ Tạo mã mới", callback_data="admin|dcadd")])
    keyboard.append([InlineKeyboardButton("⚙️ Về quản lý sản phẩm", callback_data="admin|panel")])
    
    message = (
        "🎟️ **QUẢN LÝ MÃ GIẢM GIÁ**\n"
        "━━━━━━━━━━━━━━━\n"
        f"Tổng cộng: {len(DISCOUNTS)} mã" if DISCOUNTS else "Chưa có mã giảm giá nào."
    )
    await safe_edit_text(query, message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def admin_discount_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    
    code = query.data.split('|')[2]
    d = DISCOUNTS.get(code)
    if not d:
        await query.answer("❌ Mã không tồn tại!", show_alert=True)
        return
    
    value_text = f"{d['value']}%" if d['type'] == 'percent' else format_price(d['value'])
    limit_text = str(d['limit']) if d.get('limit') is not None else "Không giới hạn"
    message = (
        f"🎟️ **MÃ: {code}**\n"
        "━━━━━━━━━━━━━━━\n"
        f"Loại giảm: {'Phần trăm' if d['type'] == 'percent' else 'Số tiền cố định'}\n"
        f"Giá trị: -{value_text}\n"
        f"Giới hạn lượt dùng: {limit_text}\n"
        f"Đã dùng: {d.get('used', 0)}"
    )
    keyboard = [
        [InlineKeyboardButton("🗑️ Xóa mã này", callback_data=f"admin|dcdel|{code}")],
        [InlineKeyboardButton("🔙 Quay lại", callback_data="admin|discounts")]
    ]
    await safe_edit_text(query, message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def admin_discount_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    
    code = query.data.split('|')[2]
    if code in DISCOUNTS:
        del DISCOUNTS[code]
        save_data(data)
        await query.answer(f"🗑️ Đã xóa mã {code}", show_alert=True)
    await admin_discounts_menu(update, context)

async def admin_discount_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return ConversationHandler.END
    
    context.user_data['new_discount'] = {}
    await safe_edit_text(query, "🎟️ Nhập **mã giảm giá** (VD: SALE10):\n\n(gõ /cancel để hủy)", parse_mode='Markdown')
    return ADD_CODE

async def admin_discount_add_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip().upper()
    if code in DISCOUNTS:
        await update.message.reply_text("⚠️ Mã này đã tồn tại rồi, nhập mã khác:")
        return ADD_CODE
    context.user_data['new_discount']['code'] = code
    
    keyboard = [
        [InlineKeyboardButton("% Phần trăm", callback_data="dctype|percent")],
        [InlineKeyboardButton("💰 Số tiền cố định", callback_data="dctype|fixed")]
    ]
    await update.message.reply_text("Chọn loại giảm giá:", reply_markup=InlineKeyboardMarkup(keyboard))
    return ADD_TYPE

async def admin_discount_add_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    discount_type = query.data.split('|')[1]
    context.user_data['new_discount']['type'] = discount_type
    
    hint = "phần trăm (VD: 10 nghĩa là giảm 10%)" if discount_type == 'percent' else "số tiền giảm (VD: 20000)"
    await query.message.reply_text(f"Nhập {hint}:")
    return ADD_VALUE

async def admin_discount_add_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("⚠️ Chỉ nhập số. Nhập lại:")
        return ADD_VALUE
    context.user_data['new_discount']['value'] = int(text)
    await update.message.reply_text("Giới hạn số lượt dùng tối đa là bao nhiêu? (gõ `0` = không giới hạn):")
    return ADD_LIMIT

async def admin_discount_add_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("⚠️ Chỉ nhập số. Nhập lại:")
        return ADD_LIMIT
    limit = int(text)
    
    nd = context.user_data.pop('new_discount')
    DISCOUNTS[nd['code']] = {
        'type': nd['type'],
        'value': nd['value'],
        'limit': limit if limit > 0 else None,
        'used': 0
    }
    save_data(data)
    
    value_text = f"{nd['value']}%" if nd['type'] == 'percent' else format_price(nd['value'])
    keyboard = [[InlineKeyboardButton("🎟️ Về quản lý mã giảm giá", callback_data="admin|discounts")]]
    await update.message.reply_text(
        f"✅ **Đã tạo mã {nd['code']}!**\nGiảm: {value_text}\nGiới hạn: {limit if limit > 0 else 'không giới hạn'}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )
    return ConversationHandler.END

# --- BẮT LỖI TOÀN CỤC (bot không bị crash chết luôn khi có lỗi bất ngờ) ---
async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Lỗi không mong muốn: %s", context.error, exc_info=context.error)
    try:
        tb_string = "".join(traceback.format_exception(None, context.error, context.error.__traceback__))[-3000:]
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"⚠️ **BOT GẶP LỖI**\n```\n{tb_string}\n```",
            parse_mode='Markdown'
        )
    except Exception:
        pass
    
    # Báo cho người dùng biết (nếu lỗi xảy ra trong lúc họ đang bấm nút/gõ gì đó)
    try:
        if isinstance(update, Update):
            if update.callback_query:
                await update.callback_query.answer("⚠️ Có lỗi xảy ra, đã báo cho admin. Thử lại sau nhé!", show_alert=True)
            elif update.message:
                await update.message.reply_text("⚠️ Có lỗi xảy ra, đã báo cho admin. Thử lại sau nhé!")
    except Exception:
        pass

APPLY_DISCOUNT = 200

async def apply_discount_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit_text(query, "🎟️ Gõ mã giảm giá của mày vào đây:\n\n(gõ /cancel để hủy)", parse_mode=None)
    return APPLY_DISCOUNT

async def apply_discount_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip().upper()
    d = DISCOUNTS.get(code)
    
    if not d:
        await update.message.reply_text("❌ Mã không tồn tại. Thử lại hoặc /cancel để hủy:")
        return APPLY_DISCOUNT
    if d.get('limit') is not None and d.get('used', 0) >= d['limit']:
        await update.message.reply_text("❌ Mã đã hết lượt sử dụng. Thử mã khác hoặc /cancel:")
        return APPLY_DISCOUNT
    
    context.user_data['discount_code'] = code
    value_text = f"{d['value']}%" if d['type'] == 'percent' else format_price(d['value'])
    keyboard = [[InlineKeyboardButton("🛒 Về giỏ hàng", callback_data="view_cart")]]
    await update.message.reply_text(
        f"✅ Áp dụng mã **{code}** thành công! (giảm {value_text})",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )
    return ConversationHandler.END

# --- START ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    keyboard = [
        [InlineKeyboardButton("🛍️ Xem sản phẩm", callback_data="view_products")],
        [InlineKeyboardButton("🛒 Giỏ hàng", callback_data="view_cart")],
        [InlineKeyboardButton("📋 Đơn hàng của tôi", callback_data="my_orders")],
        [InlineKeyboardButton("📞 Liên hệ", callback_data="contact")]
    ]
    if is_admin(user.id):
        keyboard.append([InlineKeyboardButton("⚙️ Quản lý sản phẩm (Admin)", callback_data="admin|panel")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"👋 **Chào mừng {user.first_name}!**\n"
        "━━━━━━━━━━━━━━━\n"
        "🏪 Cửa hàng công nghệ đồ chơi Free Fire\n\n"
        "✅ Thanh toán tự động qua chuyển khoản\n"
        "✅ Xác nhận đơn hàng tự động\n"
        "━━━━━━━━━━━━━━━\n"
        "👇 Chọn chức năng bên dưới:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

# --- VIEW CART ---
async def view_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    cart = carts.get(user_id, {})
    
    if not cart:
        keyboard = [[InlineKeyboardButton("🛍️ Mua sắm", callback_data="view_products")]]
        await safe_edit_text(query, "🛒 Giỏ hàng trống!", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=None)
        return
    
    total = 0
    total_items = sum(item['quantity'] for item in cart.values())
    message = f"🛒 **GIỎ HÀNG** ({total_items} món)\n━━━━━━━━━━━━━━━\n\n"
    keyboard = []
    
    for pid, item in cart.items():
        subtotal = item['price'] * item['quantity']
        total += subtotal
        message += f"📦 **{item['name']}**\n"
        message += f"   {format_price(item['price'])} x {item['quantity']} = {format_price(subtotal)}\n\n"
        keyboard.append([
            InlineKeyboardButton("➖", callback_data=f"cartdec_{pid}"),
            InlineKeyboardButton(f"{item['quantity']}", callback_data="noop"),
            InlineKeyboardButton("➕", callback_data=f"cartinc_{pid}"),
            InlineKeyboardButton("🗑️", callback_data=f"remove_{pid}")
        ])
    
    message += f"━━━━━━━━━━━━━━━\n💰 **Tạm tính:** {format_price(total)}\n"
    
    final_total = total
    code = context.user_data.get('discount_code')
    if code:
        discount_amount, final_total = calc_discount(total, code)
        if discount_amount > 0:
            message += f"🎟️ Mã **{code}**: -{format_price(discount_amount)}\n"
            message += f"💵 **Cần trả:** {format_price(final_total)}\n"
        else:
            context.user_data.pop('discount_code', None)
    
    keyboard.append([InlineKeyboardButton(
        "🎟️ Nhập mã giảm giá" if not code else f"🎟️ Đổi mã ({code})",
        callback_data="apply_discount"
    )])
    keyboard.append([InlineKeyboardButton("✅ Thanh toán ngay", callback_data="payment_bank")])
    keyboard.append([InlineKeyboardButton("🛍️ Tiếp tục mua sắm", callback_data="view_products")])
    keyboard.append([InlineKeyboardButton("🏠 Trang chủ", callback_data="back_menu")])
    
    await safe_edit_text(query, message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

# --- REMOVE FROM CART ---
async def remove_from_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    product_id = query.data.split('_')[1]
    cart = carts.get(user_id, {})
    
    if product_id in cart:
        if cart[product_id]['quantity'] > 1:
            cart[product_id]['quantity'] -= 1
        else:
            del cart[product_id]
        save_data(data)
    
    await view_cart(update, context)

# --- TĂNG/GIẢM SỐ LƯỢNG TRONG GIỎ ---
async def cart_qty_change(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    action, product_id = query.data.split('_')
    user_id = str(query.from_user.id)
    cart = carts.get(user_id, {})
    
    if product_id in cart:
        if action == 'cartinc':
            product = PRODUCTS.get(product_id)
            if product and cart[product_id]['quantity'] < product['stock']:
                cart[product_id]['quantity'] += 1
            else:
                await query.answer("⚠️ Đã đạt số lượng tồn kho tối đa!", show_alert=True)
        elif action == 'cartdec':
            if cart[product_id]['quantity'] > 1:
                cart[product_id]['quantity'] -= 1
            else:
                del cart[product_id]
        save_data(data)
    
    await query.answer()
    await view_cart(update, context)

# --- MY ORDERS ---
async def my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    user_orders = [o for o in orders if o['user_id'] == user_id]
    
    if not user_orders:
        keyboard = [[InlineKeyboardButton("🔙 Quay lại", callback_data="back_menu")]]
        await safe_edit_text(query, "📋 Bạn chưa có đơn hàng nào!", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=None)
        return
    
    message = "📋 **LỊCH SỬ ĐƠN HÀNG**\n\n"
    for order in user_orders[-5:]:
        message += f"📦 `{order['order_id']}`\n"
        message += f"💰 {format_price(order['total'])}\n"
        message += f"📌 {order['status']}\n"
        message += f"📅 {order['date']}\n\n"
    
    keyboard = [[InlineKeyboardButton("🔙 Quay lại", callback_data="back_menu")]]
    await safe_edit_text(query, message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

# --- CONTACT ---
async def contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    keyboard = [[InlineKeyboardButton("🔙 Quay lại", callback_data="back_menu")]]
    await safe_edit_text(
        query,
        "📞 **LIÊN HỆ**\n━━━━━━━━━━━━━━━\n"
        "📱 Telegram: @vipMTPanti\n"
        "📧 Email: dangnghia080510@gmail.com\n"
        "🕐 8:00 - 21:00 hàng ngày",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

# --- BACK MENU ---
async def back_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    cart = carts.get(str(query.from_user.id), {})
    cart_badge = f" ({sum(i['quantity'] for i in cart.values())})" if cart else ""
    
    keyboard = [
        [InlineKeyboardButton("🛍️ Xem sản phẩm", callback_data="view_products")],
        [InlineKeyboardButton(f"🛒 Giỏ hàng{cart_badge}", callback_data="view_cart")],
        [InlineKeyboardButton("📋 Đơn hàng", callback_data="my_orders")],
        [InlineKeyboardButton("📞 Liên hệ", callback_data="contact")]
    ]
    if is_admin(query.from_user.id):
        keyboard.append([InlineKeyboardButton("⚙️ Quản lý sản phẩm (Admin)", callback_data="admin|panel")])
    await safe_edit_text(
        query,
        "🏠 **MENU CHÍNH**\n━━━━━━━━━━━━━━━\n👇 Chọn chức năng:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

# --- MAIN ---
def main():
    app = Application.builder().token(TOKEN).build()
    
    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("addadmin", add_admin_cmd))
    app.add_handler(CommandHandler("deladmin", del_admin_cmd))
    app.add_handler(CommandHandler("admins", list_admin_cmd))
    
    # Callbacks
    app.add_handler(CallbackQueryHandler(view_products, pattern="^view_products$"))
    app.add_handler(CallbackQueryHandler(view_cart, pattern="^view_cart$"))
    app.add_handler(CallbackQueryHandler(my_orders, pattern="^my_orders$"))
    app.add_handler(CallbackQueryHandler(contact, pattern="^contact$"))
    app.add_handler(CallbackQueryHandler(back_menu, pattern="^back_menu$"))
    app.add_handler(CallbackQueryHandler(product_detail, pattern="^product_"))
    app.add_handler(CallbackQueryHandler(buy_now, pattern="^buy_"))
    app.add_handler(CallbackQueryHandler(remove_from_cart, pattern="^remove_"))
    app.add_handler(CallbackQueryHandler(add_to_cart, pattern="^add_"))
    app.add_handler(CallbackQueryHandler(qty_change, pattern="^qtyinc_|^qtydec_"))
    app.add_handler(CallbackQueryHandler(cart_qty_change, pattern="^cartinc_|^cartdec_"))
    app.add_handler(CallbackQueryHandler(noop, pattern="^noop$"))
    
    # Payment handlers
    app.add_handler(CallbackQueryHandler(process_payment, pattern="^payment_"))
    app.add_handler(CallbackQueryHandler(confirm_payment, pattern="^confirm_"))
    app.add_handler(CallbackQueryHandler(cancel_order, pattern="^cancel_"))
    
    # Admin - quản lý sản phẩm
    app.add_handler(CallbackQueryHandler(admin_panel, pattern="^admin\\|panel$"))
    app.add_handler(CallbackQueryHandler(admin_edit_menu, pattern="^admin\\|edit\\|"))
    app.add_handler(CallbackQueryHandler(admin_delete_confirm, pattern="^admin\\|delete\\|"))
    app.add_handler(CallbackQueryHandler(admin_delete_yes, pattern="^admin\\|delyes\\|"))
    app.add_handler(CallbackQueryHandler(admin_orders_menu, pattern="^admin\\|orders$"))
    app.add_handler(CallbackQueryHandler(admin_order_view, pattern="^admin\\|orderview\\|"))
    app.add_handler(CallbackQueryHandler(admin_order_setstatus, pattern="^admin\\|orderstatus\\|"))
    app.add_handler(CallbackQueryHandler(admin_stats, pattern="^admin\\|stats$"))
    app.add_handler(CallbackQueryHandler(admin_export_orders, pattern="^admin\\|export$"))
    
    # Admin - quản lý mã giảm giá
    app.add_handler(CallbackQueryHandler(admin_discounts_menu, pattern="^admin\\|discounts$"))
    app.add_handler(CallbackQueryHandler(admin_discount_view, pattern="^admin\\|dcview\\|"))
    app.add_handler(CallbackQueryHandler(admin_discount_delete, pattern="^admin\\|dcdel\\|"))
    
    # Tìm kiếm sản phẩm (khách)
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(search_product_start, pattern="^search_product$")],
        states={
            SEARCH_KEYWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, search_product_result)],
        },
        fallbacks=[CommandHandler("cancel", admin_cancel)],
    ))
    
    # Admin - gửi thông báo hàng loạt
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_broadcast_start, pattern="^admin\\|broadcast$")],
        states={
            BROADCAST_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_send)],
        },
        fallbacks=[CommandHandler("cancel", admin_cancel)],
    ))
    
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_start, pattern="^admin\\|add$")],
        states={
            ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_name)],
            ADD_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_price)],
            ADD_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_desc)],
            ADD_STOCK: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_stock)],
            ADD_IMAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_image)],
        },
        fallbacks=[CommandHandler("cancel", admin_cancel)],
    ))
    
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_editfield_start, pattern="^admin\\|editfield\\|")],
        states={
            EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_editfield_save)],
        },
        fallbacks=[CommandHandler("cancel", admin_cancel)],
    ))
    
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_discount_add_start, pattern="^admin\\|dcadd$")],
        states={
            ADD_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_discount_add_code)],
            ADD_TYPE: [CallbackQueryHandler(admin_discount_add_type, pattern="^dctype\\|")],
            ADD_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_discount_add_value)],
            ADD_LIMIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_discount_add_limit)],
        },
        fallbacks=[CommandHandler("cancel", admin_cancel)],
    ))
    
    # Khách hàng - nhập mã giảm giá
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(apply_discount_start, pattern="^apply_discount$")],
        states={
            APPLY_DISCOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, apply_discount_save)],
        },
        fallbacks=[CommandHandler("cancel", admin_cancel)],
    ))
    
    # Bắt lỗi toàn cục — quan trọng nhất, đặt cuối cùng
    app.add_error_handler(global_error_handler)
    
    # Job tự động hủy đơn "Chờ thanh toán" quá hạn — chạy mỗi 30 phút
    if app.job_queue is not None:
        app.job_queue.run_repeating(auto_cancel_pending_orders, interval=1800, first=60)
    else:
        print("⚠️ Không có job_queue (thiếu thư viện APScheduler) — tính năng tự động hủy đơn treo sẽ KHÔNG hoạt động.")
        print("   Cài bằng lệnh: pip install \"python-telegram-bot[job-queue]\"")
    
    print("🤖 Bot bán hàng TỰ ĐỘNG đang chạy...")
    try:
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        logger.critical("Bot dừng do lỗi nghiêm trọng: %s", e, exc_info=True)
        raise

if __name__ == "__main__":
    main()