import logging
import json
import os
import re
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, CallbackQueryHandler, ChatMemberHandler
)

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
DATA_FILE = "/app/data/data.json"
os.makedirs("/app/data", exist_ok=True)

# ==================== DATA ====================
def load():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return default_data()

def default_data():
    return {
        "welcome": {
            "text": "أهلاً بك! 👋\nكيف أقدر أساعدك؟",
            "buttons": []
        },
        "auto_replies": {},
        "banned_users": [],
        "sub_admins": [],
        "users": {},
        "groups": {},
        "allow_groups": True,
        "stats": {"messages": 0, "broadcasts": 0},
        "channels": {}
    }

def default_channel_settings():
    return {
        "title": "",
        "username": "",
        "joined": datetime.now().isoformat(),
        "auto_button": True,          # زرار رابط القناة تلقائي
        "channel_link": "",           # رابط القناة
        "extra_buttons": [],          # أزرار إضافية [{text, url}]
        "repost_forwards": False,     # حذف الـ forwards وإعادة نشرها بأزرار القناة
    }

def default_group_settings():
    return {
        "title": "",
        "joined": datetime.now().isoformat(),
        # رسالة الترحيب
        "welcome_enabled": False,
        "welcome_text": "أهلاً بك {name} في {group}! 🎉",
        "welcome_once": True,  # True = أول مرة بس
        "welcome_buttons": [],
        # رسالة المغادرة
        "leave_enabled": False,
        "leave_text": "وداعاً {name}! 👋",
        "leave_once": True,
        # حماية
        "anti_links": False,
        "anti_links_action": "delete",  # delete / mute / ban
        "anti_links_threshold": 1,
        "anti_links_mute_duration": 60,  # دقايق
        "anti_username": False,
        "anti_username_action": "delete",
        "anti_username_threshold": 1,
        "anti_username_mute_duration": 60,
        "anti_forward": False,
        "anti_forward_action": "delete",
        "anti_forward_threshold": 1,
        "anti_forward_mute_duration": 60,
        # استثناءات
        "exceptions_users": [],  # IDs
        "exceptions_links": [],  # روابط مسموحة
        # تتبع المخالفات
        "violations": {},  # {user_id: {links: 0, username: 0, forward: 0}}
        # حظر الكلمات
        "anti_words": False,
        "anti_words_list": [],
        "anti_words_action": "delete",
        "anti_words_threshold": 1,
        "anti_words_mute_duration": 60,
        # تتبع الأعضاء (للترحيب مرة واحدة)
        "seen_members": [],
        "left_members": [],
    }

def save(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def is_admin(user_id, data=None):
    if int(user_id) == int(ADMIN_ID):
        return True
    if data is None:
        data = load()
    return str(user_id) in data.get("sub_admins", [])

def is_owner(user_id):
    return int(user_id) == int(ADMIN_ID)

def is_banned(user_id, data):
    return str(user_id) in data.get("banned_users", [])

def get_channel(data, chat_id):
    cid = str(chat_id)
    if cid not in data.get("channels", {}):
        if "channels" not in data:
            data["channels"] = {}
        data["channels"][cid] = default_channel_settings()
    else:
        defaults = default_channel_settings()
        for key, val in defaults.items():
            if key not in data["channels"][cid]:
                data["channels"][cid][key] = val
    return data["channels"][cid]

def get_group(data, chat_id):
    gid = str(chat_id)
    if gid not in data.get("groups", {}):
        data["groups"][gid] = default_group_settings()
    else:
        # نضيف الإعدادات الجديدة للمجموعات القديمة اللي مالهاش
        defaults = default_group_settings()
        for key, val in defaults.items():
            if key not in data["groups"][gid]:
                data["groups"][gid][key] = val
    return data["groups"][gid]

def register_user(user, data):
    uid = str(user.id)
    if uid not in data["users"]:
        data["users"][uid] = {
            "name": user.full_name,
            "username": user.username or "",
            "joined": datetime.now().isoformat()
        }

def build_kb(buttons):
    keyboard = []
    for btn in buttons:
        if btn.get("url"):
            keyboard.append([InlineKeyboardButton(btn["text"], url=btn["url"])])
    return InlineKeyboardMarkup(keyboard) if keyboard else None

async def safe_edit(query, text, reply_markup=None, parse_mode="Markdown"):
    """تعديل الرسالة مع fallback لرسالة جديدة"""
    # حاول تعدل الرسالة الأول
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        return
    except Exception as e:
        logger.warning(f"edit_message_text failed: {type(e).__name__}: {e}")
    
    # لو فشل، ابعت رسالة جديدة وامسح القديمة
    try:
        await query.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        try:
            await query.message.delete()
        except:
            pass
    except Exception as e2:
        logger.error(f"reply_text also failed: {type(e2).__name__}: {e2}")
        # آخر محاولة: show alert
        try:
            await query.answer("حدث خطأ، جرب مرة أخرى", show_alert=True)
        except:
            pass

def action_label(action):
    return {"delete": "🗑️ حذف", "mute": "🔇 كتم", "ban": "🚫 حظر"}.get(action, action)

# ==================== ADMIN PANEL HOME ====================
def admin_home_kb(data):
    allow = "✅" if data.get("allow_groups", True) else "❌"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 الإحصائيات", callback_data="admin_stats")],
        [InlineKeyboardButton("💬 رسالة الترحيب في الخاص", callback_data="admin_welcome")],
        [InlineKeyboardButton("🤖 الردود التلقائية", callback_data="admin_replies")],
        [InlineKeyboardButton("👥 إدارة المجموعات", callback_data="admin_groups")],
        [InlineKeyboardButton("📢 إدارة القنوات", callback_data="admin_channels")],
        [InlineKeyboardButton("📢 برودكاست", callback_data="admin_broadcast_menu")],
        [InlineKeyboardButton("🚫 المحظورون", callback_data="admin_banned")],
        [InlineKeyboardButton("👮 الأدمنز", callback_data="admin_admins")],
        [InlineKeyboardButton(f"🔓 انضمام للمجموعات: {allow}", callback_data="admin_toggle_groups")],
    ])

async def show_admin_home(update, context, data=None):
    if data is None:
        data = load()
    text = "🎛️ *لوحة التحكم الرئيسية*"
    kb = admin_home_kb(data)
    if update.callback_query:
        await safe_edit(update.callback_query, text, reply_markup=kb, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")

# ==================== START ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = load()
    if is_admin(user.id, data):
        await show_admin_home(update, context, data)
        return
    if is_banned(user.id, data):
        await update.message.reply_text("⛔ أنت محظور من استخدام هذا البوت.")
        return
    register_user(user, data)
    data["stats"]["messages"] = data["stats"].get("messages", 0) + 1
    save(data)
    welcome = data["welcome"]
    keyboard = []
    for btn in welcome["buttons"]:
        if btn.get("url"):
            keyboard.append([InlineKeyboardButton(btn["text"], url=btn["url"])])
    keyboard.append([InlineKeyboardButton("📝 إرسال اقتراح أو شكوى", callback_data="send_suggestion")])
    kb = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(welcome["text"], reply_markup=kb, parse_mode="Markdown")

# ==================== GROUPS MANAGEMENT ====================
async def show_channels_list(update, context, data):
    channels = data.get("channels", {})
    if not channels:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="admin_home")]])
        await safe_edit(update.callback_query, "📢 *إدارة القنوات*\n\nمفيش قنوات حالياً.\n\nأضف البوت كـ Admin في قناة وهتظهر هنا.", reply_markup=kb, parse_mode="Markdown")
        return
    rows = []
    for cid, c in channels.items():
        title = c.get("title", cid)
        rows.append([InlineKeyboardButton(f"📢 {title}", callback_data=f"channel_{cid}")])
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_home")])
    await safe_edit(update.callback_query,
        f"📢 *إدارة القنوات* ({len(channels)})",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="Markdown"
    )

async def show_channel_settings(update, context, cid, data):
    c = get_channel(data, cid)
    title = c.get("title", cid)
    auto = "✅ مفعّل" if c["auto_button"] else "❌ معطّل"
    link = c.get("channel_link", "") or "غير محدد"
    btns_text = "\n".join([f"• {b['text']} → {b.get('url','')}" for b in c.get("extra_buttons", [])]) or "لا يوجد"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🔗 زرار القناة التلقائي: {auto}", callback_data=f"ctoggle_auto_{cid}")],
        [InlineKeyboardButton(f"♻️ إعادة نشر الـ Forwards: {'✅' if c.get('repost_forwards') else '❌'}", callback_data=f"ctoggle_repost_{cid}")],
        [InlineKeyboardButton("✏️ تعديل رابط القناة", callback_data=f"cedit_link_{cid}")],
        [InlineKeyboardButton("➕ إضافة زر إضافي", callback_data=f"cedit_add_btn_{cid}")],
        [InlineKeyboardButton("🗑️ حذف زر إضافي", callback_data=f"cedit_del_btn_{cid}")],
        [InlineKeyboardButton("👁️ معاينة الأزرار", callback_data=f"cpreview_{cid}")],
        [InlineKeyboardButton("📋 نسخ الإعدادات من قناة", callback_data=f"ccopy_settings_{cid}")],
        [InlineKeyboardButton("🚪 إخراج البوت من القناة", callback_data=f"cleave_{cid}")],
        [InlineKeyboardButton("🔙 رجوع للقنوات", callback_data="admin_channels")],
    ])
    await safe_edit(update.callback_query,
        f"📢 *إعدادات قناة: {title}*\n\n"
        f"🔗 زرار تلقائي: {auto}\n"
        f"📎 رابط القناة: `{link}`\n"
        f"🔘 أزرار إضافية:\n{btns_text}",
        reply_markup=kb, parse_mode="Markdown"
    )

async def show_groups_list(update, context, data):
    groups = data.get("groups", {})
    if not groups:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="admin_home")]])
        await safe_edit(update.callback_query, "👥 *إدارة المجموعات*\n\nمفيش مجموعات حالياً.", reply_markup=kb, parse_mode="Markdown")
        return
    rows = []
    seen_titles = {}  # لمنع التكرار
    for gid, g in groups.items():
        title = g.get("title", gid)
        # لو في مجموعتين بنفس العنوان، اجمعهم في واحدة (الأحدث)
        if title in seen_titles:
            old_gid = seen_titles[title]
            # احذف المجموعة القديمة من الـ rows
            rows = [r for r in rows if r[0].callback_data != f"group_{old_gid}"]
        seen_titles[title] = gid
        rows.append([InlineKeyboardButton(f"👥 {title}", callback_data=f"group_{gid}")])
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_home")])
    count = len(rows) - 1  # مش حاسب زرار الرجوع
    await safe_edit(update.callback_query, 
        f"👥 *إدارة المجموعات* ({count})",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="Markdown"
    )

async def show_group_settings(update, context, gid, data):
    g = get_group(data, gid)
    title = g.get("title", gid)

    def s(val): return "✅" if val else "❌"
    def once(val): return "أول مرة" if val else "كل مرة"

    text = (
        f"⚙️ *إعدادات: {title}*\n\n"
        f"👋 ترحيب: {s(g['welcome_enabled'])} ({once(g['welcome_once'])})\n"
        f"👋 مغادرة: {s(g['leave_enabled'])} ({once(g['leave_once'])})\n\n"
        f"🔗 حظر روابط: {s(g['anti_links'])} | عقوبة: {action_label(g['anti_links_action'])} | حد: {g['anti_links_threshold']}\n"
        f"👤 حظر يوزر: {s(g['anti_username'])} | عقوبة: {action_label(g['anti_username_action'])} | حد: {g['anti_username_threshold']}\n"
        f"↩️ حظر فورورد: {s(g['anti_forward'])} | عقوبة: {action_label(g['anti_forward_action'])} | حد: {g['anti_forward_threshold']}\n"
        f"🤬 حظر كلمات: {s(g['anti_words'])} | عقوبة: {action_label(g['anti_words_action'])} | حد: {g['anti_words_threshold']} | {len(g['anti_words_list'])} كلمة\n\n"
        f"👥 استثناءات: {len(g['exceptions_users'])} مستخدم | {len(g['exceptions_links'])} رابط"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("👋 إعدادات الترحيب", callback_data=f"gs_welcome_{gid}"),
         InlineKeyboardButton("🚪 إعدادات المغادرة", callback_data=f"gs_leave_{gid}")],
        [InlineKeyboardButton("🔗 حظر الروابط", callback_data=f"gs_links_{gid}"),
         InlineKeyboardButton("👤 حظر اليوزر", callback_data=f"gs_username_{gid}")],
        [InlineKeyboardButton("↩️ حظر الفورورد", callback_data=f"gs_forward_{gid}")],
        [InlineKeyboardButton("🤬 حظر الكلمات", callback_data=f"gs_words_{gid}")],
        [InlineKeyboardButton("👥 الاستثناءات", callback_data=f"gs_exceptions_{gid}")],
        [InlineKeyboardButton("📋 نسخ الإعدادات من مجموعة", callback_data=f"gcopy_settings_{gid}")],
        [InlineKeyboardButton("🚪 إخراج البوت من المجموعة", callback_data=f"gleave_group_{gid}")],
        [InlineKeyboardButton("🔙 رجوع للمجموعات", callback_data="admin_groups")],
    ])
    try:
        await safe_edit(update.callback_query, text, reply_markup=kb, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"edit_message_text failed: {e}")
        try:
            await update.callback_query.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")
        except Exception as e2:
            logger.error(f"reply_text also failed: {e2}")

async def show_welcome_settings(update, context, gid, data):
    g = get_group(data, gid)
    s = lambda v: "✅ مفعّل" if v else "❌ معطّل"
    once = lambda v: "أول مرة فقط" if v else "كل مرة"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"الترحيب: {s(g['welcome_enabled'])}", callback_data=f"gtoggle_welcome_{gid}")],
        [InlineKeyboardButton(f"الإرسال: {once(g['welcome_once'])}", callback_data=f"gtoggle_welcome_once_{gid}")],
        [InlineKeyboardButton("✏️ تعديل النص", callback_data=f"gedit_welcome_text_{gid}")],
        [InlineKeyboardButton("➕ إضافة زر", callback_data=f"gedit_welcome_btn_{gid}")],
        [InlineKeyboardButton("👁️ معاينة", callback_data=f"gpreview_welcome_{gid}")],
        [InlineKeyboardButton("🔙 رجوع", callback_data=f"group_{gid}")],
    ])
    await safe_edit(update.callback_query, 
        f"👋 *إعدادات الترحيب*\n\nالنص الحالي:\n`{g['welcome_text']}`\n\n"
        f"المتغيرات: {{name}} = اسم العضو، {{group}} = اسم المجموعة",
        reply_markup=kb, parse_mode="Markdown"
    )

async def show_leave_settings(update, context, gid, data):
    g = get_group(data, gid)
    s = lambda v: "✅ مفعّل" if v else "❌ معطّل"
    once = lambda v: "أول مرة فقط" if v else "كل مرة"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"المغادرة: {s(g['leave_enabled'])}", callback_data=f"gtoggle_leave_{gid}")],
        [InlineKeyboardButton(f"الإرسال: {once(g['leave_once'])}", callback_data=f"gtoggle_leave_once_{gid}")],
        [InlineKeyboardButton("✏️ تعديل النص", callback_data=f"gedit_leave_text_{gid}")],
        [InlineKeyboardButton("🔙 رجوع", callback_data=f"group_{gid}")],
    ])
    await safe_edit(update.callback_query, 
        f"🚪 *إعدادات المغادرة*\n\nالنص الحالي:\n`{g['leave_text']}`",
        reply_markup=kb, parse_mode="Markdown"
    )

async def show_protection_settings(update, context, gid, data, ptype):
    g = get_group(data, gid)
    labels = {"links": ("🔗", "الروابط", "anti_links"), "username": ("👤", "اليوزر نيم", "anti_username"), "forward": ("↩️", "الفورورد", "anti_forward")}
    emoji, name, key = labels[ptype]
    enabled = g[key]
    action = g[f"{key}_action"]
    threshold = g[f"{key}_threshold"]
    mute_dur = g.get(f"{key}_mute_duration", 60)
    s = lambda v: "✅ مفعّل" if v else "❌ معطّل"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{emoji} {name}: {s(enabled)}", callback_data=f"gtoggle_{ptype}_{gid}")],
        [InlineKeyboardButton(f"⚖️ العقوبة: {action_label(action)}", callback_data=f"gaction_{ptype}_{gid}")],
        [InlineKeyboardButton(f"🔢 عدد المخالفات: {threshold}", callback_data=f"gthreshold_{ptype}_{gid}")],
        [InlineKeyboardButton(f"⏱️ مدة الكتم: {mute_dur} دقيقة", callback_data=f"gmute_dur_{ptype}_{gid}")],
        [InlineKeyboardButton("🔙 رجوع", callback_data=f"group_{gid}")],
    ])
    await safe_edit(update.callback_query, 
        f"{emoji} *إعدادات حظر {name}*",
        reply_markup=kb, parse_mode="Markdown"
    )

async def show_words_settings(update, context, gid, data):
    g = get_group(data, gid)
    s = lambda v: "✅ مفعّل" if v else "❌ معطّل"
    words = g.get("anti_words_list", [])
    words_text = "، ".join(words) if words else "لا يوجد كلمات محظورة"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🤬 حظر الكلمات: {s(g['anti_words'])}", callback_data=f"gtoggle_words_{gid}")],
        [InlineKeyboardButton(f"⚖️ العقوبة: {action_label(g['anti_words_action'])}", callback_data=f"gaction_words_{gid}")],
        [InlineKeyboardButton(f"🔢 عدد المخالفات: {g['anti_words_threshold']}", callback_data=f"gthreshold_words_{gid}")],
        [InlineKeyboardButton(f"⏱️ مدة الكتم: {g.get('anti_words_mute_duration', 60)} دقيقة", callback_data=f"gmute_dur_words_{gid}")],
        [InlineKeyboardButton("➕ إضافة كلمة", callback_data=f"gwords_add_{gid}"),
         InlineKeyboardButton("🗑️ حذف كلمة", callback_data=f"gwords_del_{gid}")],
        [InlineKeyboardButton("🔙 رجوع", callback_data=f"group_{gid}")],
    ])
    await safe_edit(update.callback_query, 
        f"🤬 *حظر الكلمات*\n\nالكلمات المحظورة:\n`{words_text}`",
        reply_markup=kb, parse_mode="Markdown"
    )

async def show_exceptions(update, context, gid, data):
    g = get_group(data, gid)
    users_text = "\n".join([f"• `{u}`" for u in g["exceptions_users"]]) or "لا يوجد"
    links_text = "\n".join([f"• `{l}`" for l in g["exceptions_links"]]) or "لا يوجد"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ إضافة مستخدم", callback_data=f"gexc_add_user_{gid}"),
         InlineKeyboardButton("🗑️ حذف مستخدم", callback_data=f"gexc_del_user_{gid}")],
        [InlineKeyboardButton("➕ إضافة رابط مسموح", callback_data=f"gexc_add_link_{gid}"),
         InlineKeyboardButton("🗑️ حذف رابط", callback_data=f"gexc_del_link_{gid}")],
        [InlineKeyboardButton("🔙 رجوع", callback_data=f"group_{gid}")],
    ])
    await safe_edit(update.callback_query, 
        f"👥 *الاستثناءات*\n\n👤 المستخدمون المستثنون:\n{users_text}\n\n🔗 الروابط المسموحة:\n{links_text}",
        reply_markup=kb, parse_mode="Markdown"
    )

# ==================== CALLBACKS ====================
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cb = query.data
    user_id = query.from_user.id
    data = load()

    logger.info(f"=== CALLBACK: '{cb}' from user {user_id} ===")

    # ===== callbacks عامة للمستخدمين =====
    if cb == "send_suggestion":
        context.user_data["waiting"] = "user_suggestion"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_suggestion")]])
        await query.edit_message_text("📝 *إرسال اقتراح أو شكوى*\n\nاكتب رسالتك:", reply_markup=kb, parse_mode="Markdown")
        return

    elif cb == "cancel_suggestion":
        context.user_data.pop("waiting", None)
        welcome = data["welcome"]
        keyboard = []
        for btn in welcome["buttons"]:
            if btn.get("url"):
                keyboard.append([InlineKeyboardButton(btn["text"], url=btn["url"])])
        keyboard.append([InlineKeyboardButton("📝 إرسال اقتراح أو شكوى", callback_data="send_suggestion")])
        await query.edit_message_text(welcome["text"], reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return

    # ===== تحقق من الأدمن =====
    if not is_admin(user_id, data):
        await query.answer("❌ مش مسموح!", show_alert=True)
        return

    # ===== الرئيسية =====
    if cb == "admin_home":
        await show_admin_home(update, context, data)

    # ===== الإحصائيات =====
    elif cb == "admin_stats":
        users = len(data.get("users", {}))
        groups = len(data.get("groups", {}))
        channels = len(data.get("channels", {}))
        banned = len(data.get("banned_users", []))
        sub_admins = len(data.get("sub_admins", []))
        msgs = data["stats"].get("messages", 0)
        bcast = data["stats"].get("broadcasts", 0)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="admin_home")]])
        await safe_edit(update.callback_query,
            f"📊 *الإحصائيات*\n\n👤 المستخدمون: `{users}`\n👥 المجموعات: `{groups}`\n📢 القنوات: `{channels}`\n🚫 المحظورون: `{banned}`\n👮 الأدمنز: `{sub_admins}`\n💬 الرسائل: `{msgs}`\n📣 البرودكاستات: `{bcast}`",
            reply_markup=kb, parse_mode="Markdown"
        )

    # ===== رسالة الترحيب في الخاص =====
    elif cb == "admin_welcome":
        welcome = data["welcome"]
        btns_text = "\n".join([f"• {b['text']} → {b.get('url','')}" for b in welcome["buttons"]]) or "لا يوجد"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ تعديل النص", callback_data="welcome_edit_text")],
            [InlineKeyboardButton("➕ إضافة زر", callback_data="welcome_add_btn")],
            [InlineKeyboardButton("🗑️ حذف زر", callback_data="welcome_del_btn")],
            [InlineKeyboardButton("👁️ معاينة", callback_data="welcome_preview")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="admin_home")],
        ])
        await query.edit_message_text(
            f"💬 *رسالة الترحيب في الخاص*\n\n📝 النص:\n`{welcome['text']}`\n\n🔘 الأزرار:\n{btns_text}",
            reply_markup=kb, parse_mode="Markdown"
        )

    elif cb == "welcome_preview":
        welcome = data["welcome"]
        kb_prev = build_kb(welcome["buttons"])
        await query.message.reply_text(welcome["text"], reply_markup=kb_prev, parse_mode="Markdown")
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="admin_welcome")]]))

    elif cb == "welcome_edit_text":
        context.user_data["waiting"] = "welcome_text"
        await query.edit_message_text("✏️ ابعت النص الجديد:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="admin_welcome")]]))

    elif cb == "welcome_add_btn":
        context.user_data["waiting"] = "add_btn"
        await query.edit_message_text("➕ ابعت:\n`اسم الزر | الرابط`", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="admin_welcome")]]), parse_mode="Markdown")

    elif cb == "welcome_del_btn":
        buttons = data["welcome"]["buttons"]
        if not buttons:
            await query.answer("مفيش أزرار!", show_alert=True)
            return
        rows = [[InlineKeyboardButton(f"🗑️ {b['text']}", callback_data=f"delbtn_{i}")] for i, b in enumerate(buttons)]
        rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_welcome")])
        await query.edit_message_text("🗑️ اختار الزر:", reply_markup=InlineKeyboardMarkup(rows))

    elif cb.startswith("delbtn_"):
        idx = int(cb.split("_")[1])
        data["welcome"]["buttons"].pop(idx)
        save(data)
        await query.answer("✅ تم الحذف", show_alert=True)
        await show_admin_home(update, context, data)

    # ===== الردود التلقائية =====
    elif cb == "admin_replies":
        replies = data.get("auto_replies", {})
        text = "🤖 *الردود التلقائية*\n\n"
        text += "\n".join([f"• `{k}` ← {v}" for k, v in replies.items()]) if replies else "لا يوجد ردود"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ إضافة رد", callback_data="reply_add")],
            [InlineKeyboardButton("🗑️ حذف رد", callback_data="reply_del")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="admin_home")],
        ])
        await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")

    elif cb == "reply_add":
        context.user_data["waiting"] = "add_reply"
        await query.edit_message_text("➕ ابعت:\n`الكلمة | الرد`", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="admin_replies")]]), parse_mode="Markdown")

    elif cb == "reply_del":
        replies = data.get("auto_replies", {})
        if not replies:
            await query.answer("مفيش ردود!", show_alert=True)
            return
        rows = [[InlineKeyboardButton(f"🗑️ {k}", callback_data=f"delreply_{k}")] for k in replies]
        rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_replies")])
        await query.edit_message_text("🗑️ اختار الرد:", reply_markup=InlineKeyboardMarkup(rows))

    elif cb.startswith("delreply_"):
        key = cb[9:]
        data["auto_replies"].pop(key, None)
        save(data)
        await query.answer(f"✅ تم الحذف", show_alert=True)
        await show_admin_home(update, context, data)

    # ===== إدارة القنوات =====
    elif cb == "admin_channels":
        await show_channels_list(update, context, data)

    elif cb.startswith("channel_") and not any(cb.startswith(x) for x in ["ctoggle_", "cedit_", "cpreview_", "cleave_", "cconfirm_"]):
        cid = cb[8:]
        await show_channel_settings(update, context, cid, data)

    elif cb.startswith("ctoggle_auto_"):
        cid = cb[13:]
        c = get_channel(data, cid)
        c["auto_button"] = not c["auto_button"]
        save(data)
        await query.answer(f"✅ الزرار التلقائي: {'مفعّل' if c['auto_button'] else 'معطّل'}", show_alert=True)
        await show_channel_settings(update, context, cid, data)

    elif cb.startswith("ctoggle_repost_"):
        cid = cb[15:]
        c = get_channel(data, cid)
        c["repost_forwards"] = not c.get("repost_forwards", False)
        save(data)
        status = "مفعّل ✅" if c["repost_forwards"] else "معطّل ❌"
        await query.answer(f"♻️ إعادة نشر الـ Forwards: {status}", show_alert=True)
        await show_channel_settings(update, context, cid, data)

    elif cb.startswith("cedit_link_"):
        cid = cb[11:]
        context.user_data["waiting"] = f"channel_link_{cid}"
        await safe_edit(update.callback_query,
            "✏️ ابعت رابط القناة:\nمثال: `https://t.me/yourchannel`",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"channel_{cid}")]]),
            parse_mode="Markdown"
        )

    elif cb.startswith("cedit_add_btn_"):
        cid = cb[14:]
        context.user_data["waiting"] = f"channel_add_btn_{cid}"
        await safe_edit(update.callback_query,
            "➕ ابعت الزر بالشكل ده:\n`اسم الزر | الرابط`\nمثال: `🌐 موقعنا | https://example.com`",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"channel_{cid}")]]),
            parse_mode="Markdown"
        )

    elif cb.startswith("cedit_del_btn_"):
        cid = cb[14:]
        c = get_channel(data, cid)
        btns = c.get("extra_buttons", [])
        if not btns:
            await query.answer("مفيش أزرار!", show_alert=True)
            return
        rows = [[InlineKeyboardButton(f"🗑️ {b['text']}", callback_data=f"cdelbtn_{i}_{cid}")] for i, b in enumerate(btns)]
        rows.append([InlineKeyboardButton("🔙 رجوع", callback_data=f"channel_{cid}")])
        await safe_edit(update.callback_query, "🗑️ اختار الزر:", reply_markup=InlineKeyboardMarkup(rows))

    elif cb.startswith("cdelbtn_"):
        rest = cb[8:]
        idx_str, cid = rest.rsplit("_", 1)
        idx = int(idx_str)
        c = get_channel(data, cid)
        if idx < len(c.get("extra_buttons", [])):
            c["extra_buttons"].pop(idx)
            save(data)
        await query.answer("✅ تم الحذف", show_alert=True)
        await show_channel_settings(update, context, cid, data)

    elif cb.startswith("cpreview_"):
        cid = cb[9:]
        c = get_channel(data, cid)
        kb_btns = []
        if c.get("auto_button") and c.get("channel_link"):
            kb_btns.append([InlineKeyboardButton(f"📢 {c.get('title', 'القناة')}", url=c["channel_link"])])
        for btn in c.get("extra_buttons", []):
            kb_btns.append([InlineKeyboardButton(btn["text"], url=btn["url"])])
        if kb_btns:
            await query.message.reply_text("👁️ معاينة الأزرار:", reply_markup=InlineKeyboardMarkup(kb_btns))
        else:
            await query.answer("مفيش أزرار للمعاينة!", show_alert=True)
        await safe_edit(update.callback_query, f"📢 *إعدادات قناة: {c.get('title', cid)}*",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data=f"channel_{cid}")]]),
            parse_mode="Markdown")

    elif cb.startswith("ccopy_settings_"):
        # نسخ الإعدادات لقناة من قناة أخرى
        target_cid = cb[15:]
        channels = data.get("channels", {})
        other_channels = {cid: c for cid, c in channels.items() if cid != target_cid}
        if not other_channels:
            await query.answer("مفيش قنوات تانية عشان تنسخ منها!", show_alert=True)
            return
        rows = []
        for cid, c in other_channels.items():
            title = c.get("title", cid)
            rows.append([InlineKeyboardButton(
                f"📋 {title}",
                callback_data=f"ccopy_from_{cid}_to_{target_cid}"
            )])
        rows.append([InlineKeyboardButton("🔙 رجوع", callback_data=f"channel_{target_cid}")])
        await safe_edit(update.callback_query,
            "📋 *نسخ الإعدادات*\n\nاختار القناة اللي عايز تنسخ منها:",
            reply_markup=InlineKeyboardMarkup(rows), parse_mode="Markdown")

    elif cb.startswith("ccopy_from_"):
        # تنفيذ النسخ للقنوات
        rest = cb[11:]
        parts = rest.split("_to_")
        if len(parts) != 2:
            await query.answer("خطأ!", show_alert=True)
            return
        from_cid, to_cid = parts[0], parts[1]
        src = data.get("channels", {}).get(from_cid)
        dst = data.get("channels", {}).get(to_cid)
        if not src or not dst:
            await query.answer("قناة مش موجودة!", show_alert=True)
            return
        keys_to_copy = [
            "auto_button", "extra_buttons", "repost_forwards"
        ]
        # channel_link مش بنسخها عشان كل قناة ليها رابطها الخاص
        for key in keys_to_copy:
            if key in src:
                dst[key] = src[key]
        save(data)
        src_title = src.get("title", from_cid)
        await query.answer(f"✅ تم نسخ الإعدادات من {src_title}", show_alert=True)
        await show_channel_settings(update, context, to_cid, data)

    elif cb.startswith("cleave_"):
        cid = cb[7:]
        c = get_channel(data, cid)
        title = c.get("title", cid)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ نعم، أخرج", callback_data=f"cconfirm_leave_{cid}")],
            [InlineKeyboardButton("❌ لا", callback_data=f"channel_{cid}")],
        ])
        await safe_edit(update.callback_query,
            f"⚠️ هل تريد إخراج البوت من قناة *{title}*؟",
            reply_markup=kb, parse_mode="Markdown")

    elif cb.startswith("cconfirm_leave_"):
        cid = cb[15:]
        c = get_channel(data, cid)
        title = c.get("title", cid)
        try:
            await context.bot.leave_chat(int(cid))
            if cid in data.get("channels", {}):
                del data["channels"][cid]
                save(data)
            await safe_edit(update.callback_query, f"✅ تم الخروج من قناة *{title}*",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="admin_channels")]]),
                parse_mode="Markdown")
        except Exception as e:
            await safe_edit(update.callback_query, f"❌ فشل: {e}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data=f"channel_{cid}")]]),
                parse_mode="Markdown")

    # ===== إدارة المجموعات =====
    elif cb == "admin_groups":
        await show_groups_list(update, context, data)

    elif cb.startswith("group_") and not any(cb.startswith(x) for x in ["gs_", "gtoggle_", "gaction_", "gthreshold_", "gmute_dur_", "gedit_", "gpreview_", "gexc_", "gwords_"]):
        gid = cb[6:]
        await show_group_settings(update, context, gid, data)

    elif cb.startswith("gs_welcome_"):
        gid = cb[11:]
        await show_welcome_settings(update, context, gid, data)

    elif cb.startswith("gs_leave_"):
        gid = cb[9:]
        await show_leave_settings(update, context, gid, data)

    elif cb.startswith("gs_links_"):
        gid = cb[9:]
        await show_protection_settings(update, context, gid, data, "links")

    elif cb.startswith("gs_username_"):
        gid = cb[12:]
        await show_protection_settings(update, context, gid, data, "username")

    elif cb.startswith("gs_forward_"):
        gid = cb[11:]
        await show_protection_settings(update, context, gid, data, "forward")

    elif cb.startswith("gs_words_"):
        gid = cb[9:]
        await show_words_settings(update, context, gid, data)

    elif cb.startswith("gcopy_settings_"):
        # نسخ الإعدادات لمجموعة من مجموعة أخرى
        target_gid = cb[15:]
        groups = data.get("groups", {})
        other_groups = {gid: g for gid, g in groups.items() if gid != target_gid}
        if not other_groups:
            await query.answer("مفيش مجموعات تانية عشان تنسخ منها!", show_alert=True)
            return
        rows = []
        for gid, g in other_groups.items():
            title = g.get("title", gid)
            rows.append([InlineKeyboardButton(
                f"📋 {title}",
                callback_data=f"gcopy_from_{gid}_to_{target_gid}"
            )])
        rows.append([InlineKeyboardButton("🔙 رجوع", callback_data=f"group_{target_gid}")])
        await safe_edit(update.callback_query,
            "📋 *نسخ الإعدادات*\n\nاختار المجموعة اللي عايز تنسخ منها:",
            reply_markup=InlineKeyboardMarkup(rows), parse_mode="Markdown")

    elif cb.startswith("gcopy_from_"):
        # تنفيذ النسخ للمجموعات
        rest = cb[11:]  # gid_to_target_gid
        parts = rest.split("_to_")
        if len(parts) != 2:
            await query.answer("خطأ!", show_alert=True)
            return
        from_gid, to_gid = parts[0], parts[1]
        src = data.get("groups", {}).get(from_gid)
        dst = data.get("groups", {}).get(to_gid)
        if not src or not dst:
            await query.answer("مجموعة مش موجودة!", show_alert=True)
            return
        # الإعدادات اللي هتتنسخ (بدون violations وseen_members وleft_members)
        keys_to_copy = [
            "welcome_enabled", "welcome_text", "welcome_once", "welcome_buttons",
            "leave_enabled", "leave_text", "leave_once",
            "anti_links", "anti_links_action", "anti_links_threshold", "anti_links_mute_duration",
            "anti_username", "anti_username_action", "anti_username_threshold", "anti_username_mute_duration",
            "anti_forward", "anti_forward_action", "anti_forward_threshold", "anti_forward_mute_duration",
            "anti_words", "anti_words_list", "anti_words_action", "anti_words_threshold", "anti_words_mute_duration",
            "exceptions_users", "exceptions_links",
        ]
        for key in keys_to_copy:
            if key in src:
                dst[key] = src[key]
        save(data)
        src_title = src.get("title", from_gid)
        dst_title = dst.get("title", to_gid)
        await query.answer(f"✅ تم نسخ الإعدادات من {src_title}", show_alert=True)
        await show_group_settings(update, context, to_gid, data)

    elif cb.startswith("gleave_group_"):
        gid = cb[13:]
        g = get_group(data, gid)
        title = g.get("title", gid)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ نعم، أخرج البوت", callback_data=f"gconfirm_leave_{gid}")],
            [InlineKeyboardButton("❌ لا، ارجع", callback_data=f"group_{gid}")],
        ])
        await safe_edit(update.callback_query,
            f"⚠️ *تأكيد الخروج*\n\nهل تريد إخراج البوت من مجموعة *{title}*؟",
            reply_markup=kb, parse_mode="Markdown")

    elif cb.startswith("gconfirm_leave_"):
        gid = cb[15:]
        g = get_group(data, gid)
        title = g.get("title", gid)
        try:
            await context.bot.leave_chat(int(gid))
            # حذف المجموعة من الداتا
            if gid in data.get("groups", {}):
                del data["groups"][gid]
                save(data)
            await safe_edit(update.callback_query,
                f"✅ تم إخراج البوت من مجموعة *{title}*",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="admin_groups")]]),
                parse_mode="Markdown")
        except Exception as e:
            await safe_edit(update.callback_query,
                f"❌ فشل الخروج: {str(e)}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data=f"group_{gid}")]]),
                parse_mode="Markdown")

    elif cb.startswith("gs_exceptions_"):
        gid = cb[14:]
        await show_exceptions(update, context, gid, data)

    # ===== تبديل إعدادات المجموعة =====
    elif cb.startswith("gtoggle_"):
        parts = cb[8:].rsplit("_", 1)
        setting, gid = parts[0], parts[1]
        g = get_group(data, gid)
        key_map = {"welcome": "welcome_enabled", "leave": "leave_enabled", "links": "anti_links", "username": "anti_username", "forward": "anti_forward"}
        if setting in key_map:
            g[key_map[setting]] = not g[key_map[setting]]
            save(data)
            await query.answer("✅ تم التعديل", show_alert=True)
            # رجوع للإعداد الصح
            if setting in ["welcome"]:
                await show_welcome_settings(update, context, gid, data)
            elif setting in ["leave"]:
                await show_leave_settings(update, context, gid, data)
            else:
                ptype = {"links": "links", "username": "username", "forward": "forward"}[setting]
                await show_protection_settings(update, context, gid, data, ptype)

        elif setting == "welcome_once":
            g["welcome_once"] = not g["welcome_once"]
            save(data)
            await query.answer("✅ تم التعديل", show_alert=True)
            await show_welcome_settings(update, context, gid, data)

        elif setting == "leave_once":
            g["leave_once"] = not g["leave_once"]
            save(data)
            await query.answer("✅ تم التعديل", show_alert=True)
            await show_leave_settings(update, context, gid, data)

        elif setting == "words":
            g["anti_words"] = not g["anti_words"]
            save(data)
            await query.answer("✅ تم التعديل", show_alert=True)
            await show_words_settings(update, context, gid, data)

    # ===== تعديل العقوبة =====
    elif cb.startswith("gaction_"):
        rest = cb[8:]
        ptype, gid = rest.rsplit("_", 1)
        g = get_group(data, gid)
        key = {"links": "anti_links", "username": "anti_username", "forward": "anti_forward", "words": "anti_words"}[ptype]
        actions = ["delete", "mute", "ban"]
        current = g[f"{key}_action"]
        next_action = actions[(actions.index(current) + 1) % len(actions)]
        g[f"{key}_action"] = next_action
        save(data)
        await query.answer(f"✅ العقوبة: {action_label(next_action)}", show_alert=True)
        if ptype == "words":
            await show_words_settings(update, context, gid, data)
        else:
            await show_protection_settings(update, context, gid, data, ptype)

    # ===== تعديل عدد المخالفات =====
    elif cb.startswith("gthreshold_"):
        rest = cb[11:]
        ptype, gid = rest.rsplit("_", 1)
        g = get_group(data, gid)
        key = {"links": "anti_links", "username": "anti_username", "forward": "anti_forward", "words": "anti_words"}[ptype]
        current = g[f"{key}_threshold"]
        next_val = (current % 5) + 1
        g[f"{key}_threshold"] = next_val
        save(data)
        await query.answer(f"✅ الحد: {next_val} مخالفة", show_alert=True)
        if ptype == "words":
            await show_words_settings(update, context, gid, data)
        else:
            await show_protection_settings(update, context, gid, data, ptype)

    # ===== تعديل مدة الكتم =====
    elif cb.startswith("gmute_dur_"):
        rest = cb[10:]
        ptype, gid = rest.rsplit("_", 1)
        g = get_group(data, gid)
        key = f"{'anti_links' if ptype=='links' else 'anti_username' if ptype=='username' else 'anti_forward'}_mute_duration"
        options = [5, 10, 30, 60, 120, 1440]
        current = g.get(key, 60)
        next_val = options[(options.index(current) + 1) % len(options)] if current in options else 60
        g[key] = next_val
        save(data)
        await query.answer(f"✅ مدة الكتم: {next_val} دقيقة", show_alert=True)
        if ptype == "words":
            await show_words_settings(update, context, gid, data)
        else:
            await show_protection_settings(update, context, gid, data, ptype)

    # ===== تعديل نص الترحيب/المغادرة =====
    elif cb.startswith("gedit_welcome_text_"):
        gid = cb[19:]
        context.user_data["waiting"] = f"group_welcome_text_{gid}"
        await query.edit_message_text("✏️ ابعت نص الترحيب الجديد:\n\n`{name}` = اسم العضو\n`{group}` = اسم المجموعة",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"gs_welcome_{gid}")]]), parse_mode="Markdown")

    elif cb.startswith("gedit_leave_text_"):
        gid = cb[17:]
        context.user_data["waiting"] = f"group_leave_text_{gid}"
        await query.edit_message_text("✏️ ابعت نص المغادرة الجديد:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"gs_leave_{gid}")]]))

    elif cb.startswith("gedit_welcome_btn_"):
        gid = cb[18:]
        context.user_data["waiting"] = f"group_welcome_btn_{gid}"
        await query.edit_message_text("➕ ابعت:\n`اسم الزر | الرابط`",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"gs_welcome_{gid}")]]), parse_mode="Markdown")

    elif cb.startswith("gwords_add_"):
        gid = cb[11:]
        context.user_data["waiting"] = f"words_add_{gid}"
        await query.edit_message_text(
            "➕ *إضافة كلمة محظورة*\n\nابعت الكلمة أو الكلمات (كل كلمة في سطر):",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"gs_words_{gid}")]]),
            parse_mode="Markdown"
        )

    elif cb.startswith("gwords_del_"):
        gid = cb[11:]
        g = get_group(data, gid)
        words = g.get("anti_words_list", [])
        if not words:
            await query.answer("مفيش كلمات محظورة!", show_alert=True)
            return
        rows = [[InlineKeyboardButton(f"🗑️ {w}", callback_data=f"gwords_delw_{i}_{gid}")] for i, w in enumerate(words)]
        rows.append([InlineKeyboardButton("🔙 رجوع", callback_data=f"gs_words_{gid}")])
        await query.edit_message_text("🗑️ اختار الكلمة اللي عايز تحذفها:", reply_markup=InlineKeyboardMarkup(rows))

    elif cb.startswith("gwords_delw_"):
        rest = cb[12:]
        idx_str, gid = rest.rsplit("_", 1)
        idx = int(idx_str)
        g = get_group(data, gid)
        if idx < len(g["anti_words_list"]):
            removed = g["anti_words_list"].pop(idx)
            save(data)
            await query.answer(f"✅ تم حذف: {removed}", show_alert=True)
        await show_words_settings(update, context, gid, data)

    elif cb.startswith("gpreview_welcome_"):
        gid = cb[17:]
        g = get_group(data, gid)
        kb_prev = build_kb(g.get("welcome_buttons", []))
        await query.message.reply_text(g["welcome_text"], reply_markup=kb_prev)
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data=f"gs_welcome_{gid}")]]))

    # ===== الاستثناءات =====
    elif cb.startswith("gexc_add_user_"):
        gid = cb[14:]
        context.user_data["waiting"] = f"exc_add_user_{gid}"
        await query.edit_message_text("➕ ابعت الـ ID بتاع المستخدم المستثنى:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"gs_exceptions_{gid}")]]))

    elif cb.startswith("gexc_del_user_"):
        gid = cb[14:]
        g = get_group(data, gid)
        users = g["exceptions_users"]
        if not users:
            await query.answer("مفيش مستخدمين!", show_alert=True)
            return
        rows = [[InlineKeyboardButton(f"🗑️ {u}", callback_data=f"gexc_deluid_{u}_{gid}")] for u in users]
        rows.append([InlineKeyboardButton("🔙 رجوع", callback_data=f"gs_exceptions_{gid}")])
        await query.edit_message_text("🗑️ اختار المستخدم:", reply_markup=InlineKeyboardMarkup(rows))

    elif cb.startswith("gexc_deluid_"):
        rest = cb[12:]
        uid, gid = rest.rsplit("_", 1)
        g = get_group(data, gid)
        if uid in g["exceptions_users"]:
            g["exceptions_users"].remove(uid)
            save(data)
        await query.answer("✅ تم الحذف", show_alert=True)
        await show_exceptions(update, context, gid, data)

    elif cb.startswith("gexc_add_link_"):
        gid = cb[14:]
        context.user_data["waiting"] = f"exc_add_link_{gid}"
        await query.edit_message_text("➕ ابعت الرابط المسموح (مثال: t.me/channel):",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"gs_exceptions_{gid}")]]))

    elif cb.startswith("gexc_del_link_"):
        gid = cb[14:]
        g = get_group(data, gid)
        links = g["exceptions_links"]
        if not links:
            await query.answer("مفيش روابط!", show_alert=True)
            return
        rows = [[InlineKeyboardButton(f"🗑️ {l}", callback_data=f"gexc_dellink_{i}_{gid}")] for i, l in enumerate(links)]
        rows.append([InlineKeyboardButton("🔙 رجوع", callback_data=f"gs_exceptions_{gid}")])
        await query.edit_message_text("🗑️ اختار الرابط:", reply_markup=InlineKeyboardMarkup(rows))

    elif cb.startswith("gexc_dellink_"):
        rest = cb[13:]
        idx_str, gid = rest.rsplit("_", 1)
        idx = int(idx_str)
        g = get_group(data, gid)
        if idx < len(g["exceptions_links"]):
            g["exceptions_links"].pop(idx)
            save(data)
        await query.answer("✅ تم الحذف", show_alert=True)
        await show_exceptions(update, context, gid, data)

    # ===== برودكاست =====
    elif cb == "admin_broadcast_menu":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("👤 للأشخاص بس", callback_data="bcast_users")],
            [InlineKeyboardButton("👥 للمجموعات بس", callback_data="bcast_groups")],
            [InlineKeyboardButton("📢 للكل", callback_data="bcast_all")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="admin_home")],
        ])
        await query.edit_message_text("📢 *برودكاست*\n\nاختار:", reply_markup=kb, parse_mode="Markdown")

    elif cb in ["bcast_users", "bcast_groups", "bcast_all"]:
        context.user_data["waiting"] = f"broadcast_{cb.split('_')[1]}"
        target = {"bcast_users": "الأشخاص", "bcast_groups": "المجموعات", "bcast_all": "الكل"}[cb]
        await query.edit_message_text(f"📢 ابعت الرسالة لـ {target}:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="admin_broadcast_menu")]]))

    # ===== المحظورون =====
    elif cb == "admin_banned":
        banned = data.get("banned_users", [])
        text = "🚫 *المحظورون*\n\n" + ("\n".join([f"• `{u}`" for u in banned]) if banned else "لا يوجد")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚫 حظر مستخدم", callback_data="ban_user")],
            [InlineKeyboardButton("✅ رفع حظر", callback_data="unban_user")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="admin_home")],
        ])
        await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")

    elif cb == "ban_user":
        context.user_data["waiting"] = "ban_user"
        await query.edit_message_text("🚫 ابعت الـ ID:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="admin_banned")]]))

    elif cb == "unban_user":
        context.user_data["waiting"] = "unban_user"
        await query.edit_message_text("✅ ابعت الـ ID:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="admin_banned")]]))

    # ===== الأدمنز =====
    elif cb == "admin_admins":
        if not is_owner(user_id):
            await query.answer("❌ للأدمن الرئيسي فقط!", show_alert=True)
            return
        sub_admins = data.get("sub_admins", [])
        text = "👮 *الأدمنز*\n\n" + ("\n".join([f"• `{u}`" for u in sub_admins]) if sub_admins else "لا يوجد")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ إضافة أدمن", callback_data="add_admin")],
            [InlineKeyboardButton("🗑️ حذف أدمن", callback_data="del_admin")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="admin_home")],
        ])
        await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")

    elif cb == "add_admin":
        if not is_owner(user_id):
            await query.answer("❌ للأدمن الرئيسي فقط!", show_alert=True)
            return
        context.user_data["waiting"] = "add_admin"
        await query.edit_message_text("➕ ابعت الـ ID:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="admin_admins")]]))

    elif cb == "del_admin":
        if not is_owner(user_id):
            await query.answer("❌ للأدمن الرئيسي فقط!", show_alert=True)
            return
        sub_admins = data.get("sub_admins", [])
        if not sub_admins:
            await query.answer("مفيش أدمنز!", show_alert=True)
            return
        rows = [[InlineKeyboardButton(f"🗑️ {u}", callback_data=f"deladmin_{u}")] for u in sub_admins]
        rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_admins")])
        await query.edit_message_text("🗑️ اختار:", reply_markup=InlineKeyboardMarkup(rows))

    elif cb.startswith("deladmin_"):
        if not is_owner(user_id):
            await query.answer("❌ للأدمن الرئيسي فقط!", show_alert=True)
            return
        uid = cb[9:]
        if uid in data.get("sub_admins", []):
            data["sub_admins"].remove(uid)
            save(data)
        await query.answer("✅ تم الحذف", show_alert=True)
        await show_admin_home(update, context, data)

    # ===== تبديل انضمام المجموعات =====
    elif cb == "admin_toggle_groups":
        data["allow_groups"] = not data.get("allow_groups", True)
        save(data)
        status = "مفعّل ✅" if data["allow_groups"] else "معطّل ❌"
        await query.answer(f"الانضمام: {status}", show_alert=True)
        await show_admin_home(update, context, data)

# ==================== MESSAGES (PRIVATE) ====================
async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.message
    if not msg or not user:
        return
    text = msg.text or ""
    data = load()
    waiting = context.user_data.get("waiting", "")

    # ===== برودكاست موجّه (forward) =====
    if is_admin(user.id, data) and waiting.startswith("broadcast_fwd_"):
        context.user_data.pop("waiting")
        target = waiting[len("broadcast_fwd_"):]
        targets_list = []
        if target in ["users", "all"]:
            targets_list += list(data.get("users", {}).keys())
        if target in ["groups", "all"]:
            targets_list += list(data.get("groups", {}).keys())
        if target in ["channels", "all"]:
            targets_list += list(data.get("channels", {}).keys())
        if not targets_list:
            await msg.reply_text("⚠️ مفيش جهات للإرسال!",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="admin_home")]]))
            return
        await msg.reply_text(f"⏳ جاري الإرسال لـ {len(targets_list)} جهة...")
        sent = failed = 0

        # بناء header للمصدر الأصلي
        origin = getattr(msg, "forward_origin", None)
        fwd_header = ""
        if origin:
            orig_channel = getattr(origin, "chat", None)
            orig_user = getattr(origin, "sender_user", None)
            orig_name = getattr(origin, "sender_user_name", None)
            if orig_channel:
                ch_title = getattr(orig_channel, "title", "")
                ch_username = getattr(orig_channel, "username", "")
                if ch_username:
                    fwd_header = "📢 Forwarded from [" + ch_title + "](https://t.me/" + ch_username + ")\n\n"
                else:
                    fwd_header = "📢 Forwarded from " + ch_title + "\n\n"
            elif orig_user:
                name = orig_user.full_name
                uid = orig_user.id
                fwd_header = "👤 Forwarded from [" + name + "](tg://user?id=" + str(uid) + ")\n\n"
            elif orig_name:
                fwd_header = "👤 Forwarded from " + orig_name + "\n\n"

        for chat_id in targets_list:
            try:
                if fwd_header and msg.text:
                    await context.bot.send_message(
                        chat_id=int(chat_id),
                        text=fwd_header + msg.text,
                        parse_mode="Markdown"
                    )
                elif fwd_header and (msg.photo or msg.video or msg.document):
                    await context.bot.copy_message(
                        chat_id=int(chat_id),
                        from_chat_id=msg.chat_id,
                        message_id=msg.message_id,
                        caption=fwd_header + (msg.caption or ""),
                        parse_mode="Markdown"
                    )
                else:
                    await context.bot.forward_message(
                        chat_id=int(chat_id),
                        from_chat_id=msg.chat_id,
                        message_id=msg.message_id
                    )
                sent += 1
            except Exception as e:
                logger.warning(f"Fwd broadcast failed to {chat_id}: {e}")
                failed += 1

        data["stats"]["broadcasts"] = data["stats"].get("broadcasts", 0) + 1
        save(data)
        await msg.reply_text(
            f"↩️ *تم البرودكاست الموجّه!*\n\n✅ أُرسل: `{sent}`\n❌ فشل: `{failed}`",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="admin_home")]]),
            parse_mode="Markdown"
        )
        return

    if is_admin(user.id, data) and waiting:
        context.user_data.pop("waiting")

        if waiting == "welcome_text":
            data["welcome"]["text"] = text
            save(data)
            await update.message.reply_text("✅ تم!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="admin_welcome")]]))

        elif waiting == "add_btn":
            if "|" in text:
                parts = text.split("|", 1)
                data["welcome"]["buttons"].append({"text": parts[0].strip(), "url": parts[1].strip()})
                save(data)
                await update.message.reply_text("✅ تم إضافة الزر!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="admin_welcome")]]))
            else:
                context.user_data["waiting"] = waiting
                await update.message.reply_text("⚠️ الشكل غلط! `اسم الزر | الرابط`", parse_mode="Markdown")

        elif waiting == "add_reply":
            if "|" in text:
                parts = text.split("|", 1)
                data["auto_replies"][parts[0].strip()] = parts[1].strip()
                save(data)
                await update.message.reply_text("✅ تم إضافة الرد!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="admin_replies")]]))
            else:
                context.user_data["waiting"] = waiting
                await update.message.reply_text("⚠️ الشكل غلط! `الكلمة | الرد`", parse_mode="Markdown")

        elif waiting.startswith("broadcast_"):
            target = waiting[len("broadcast_"):]  # users/groups/channels/all
            msg = update.message
            sent = failed = 0
            targets_list = []
            if target in ["users", "all"]:
                targets_list += list(data.get("users", {}).keys())
            if target in ["groups", "all"]:
                targets_list += list(data.get("groups", {}).keys())
            if target in ["channels", "all"]:
                targets_list += list(data.get("channels", {}).keys())

            if not targets_list:
                await update.message.reply_text("⚠️ مفيش جهات للإرسال!",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="admin_home")]]))
                return

            await update.message.reply_text(f"⏳ جاري الإرسال لـ {len(targets_list)} جهة...")

            for chat_id in targets_list:
                try:
                    # copy_message بيبعت الرسالة كما هي (نص/صورة/فيديو/إلخ) بدون "Forwarded from"
                    await context.bot.copy_message(
                        chat_id=int(chat_id),
                        from_chat_id=msg.chat_id,
                        message_id=msg.message_id
                    )
                    sent += 1
                except Exception as e:
                    logger.warning(f"Broadcast failed to {chat_id}: {e}")
                    failed += 1

            data["stats"]["broadcasts"] = data["stats"].get("broadcasts", 0) + 1
            save(data)
            await update.message.reply_text(f"📢 تم!\n✅ أُرسل: `{sent}`\n❌ فشل: `{failed}`",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="admin_home")]]), parse_mode="Markdown")

        elif waiting == "ban_user":
            uid = text.strip()
            if uid not in data["banned_users"]:
                data["banned_users"].append(uid)
                save(data)
            await update.message.reply_text(f"✅ تم حظر: `{uid}`", parse_mode="Markdown")

        elif waiting == "unban_user":
            uid = text.strip()
            if uid in data["banned_users"]:
                data["banned_users"].remove(uid)
                save(data)
            await update.message.reply_text(f"✅ تم رفع الحظر: `{uid}`", parse_mode="Markdown")

        elif waiting == "add_admin":
            if not is_owner(user.id):
                return
            uid = text.strip()
            if "sub_admins" not in data:
                data["sub_admins"] = []
            if uid not in data["sub_admins"]:
                data["sub_admins"].append(uid)
                save(data)
                try:
                    await context.bot.send_message(int(uid), "🎉 تم تعيينك أدمن! اضغط /start")
                except:
                    pass
            await update.message.reply_text(f"✅ تم إضافة أدمن: `{uid}`",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="admin_admins")]]), parse_mode="Markdown")

        # إعدادات المجموعات
        elif waiting.startswith("group_welcome_text_"):
            gid = waiting[19:]
            g = get_group(data, gid)
            g["welcome_text"] = text
            save(data)
            await update.message.reply_text("✅ تم تعديل نص الترحيب!",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data=f"gs_welcome_{gid}")]]))

        elif waiting.startswith("group_leave_text_"):
            gid = waiting[17:]
            g = get_group(data, gid)
            g["leave_text"] = text
            save(data)
            await update.message.reply_text("✅ تم تعديل نص المغادرة!",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data=f"gs_leave_{gid}")]]))

        elif waiting.startswith("group_welcome_btn_"):
            gid = waiting[18:]
            if "|" in text:
                parts = text.split("|", 1)
                g = get_group(data, gid)
                g["welcome_buttons"].append({"text": parts[0].strip(), "url": parts[1].strip()})
                save(data)
                await update.message.reply_text("✅ تم إضافة الزر!",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data=f"gs_welcome_{gid}")]]))
            else:
                context.user_data["waiting"] = waiting
                await update.message.reply_text("⚠️ الشكل غلط! `اسم الزر | الرابط`", parse_mode="Markdown")

        elif waiting.startswith("exc_add_user_"):
            gid = waiting[13:]
            uid = text.strip()
            g = get_group(data, gid)
            if uid not in g["exceptions_users"]:
                g["exceptions_users"].append(uid)
                save(data)
            await update.message.reply_text(f"✅ تم إضافة الاستثناء: `{uid}`",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data=f"gs_exceptions_{gid}")]]), parse_mode="Markdown")

        elif waiting.startswith("exc_add_link_"):
            gid = waiting[13:]
            g = get_group(data, gid)
            if text not in g["exceptions_links"]:
                g["exceptions_links"].append(text.strip())
                save(data)
            await update.message.reply_text(f"✅ تم إضافة الرابط المسموح!",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data=f"gs_exceptions_{gid}")]]))

        elif waiting.startswith("words_add_"):
            gid = waiting[10:]
            g = get_group(data, gid)
            new_words = [w.strip().lower() for w in text.split("\n") if w.strip()]
            added = []
            for w in new_words:
                if w not in g["anti_words_list"]:
                    g["anti_words_list"].append(w)
                    added.append(w)
            save(data)
            await update.message.reply_text(
                f"✅ تم إضافة {len(added)} كلمة محظورة:\n" + "، ".join(added),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data=f"gs_words_{gid}")]]),
                parse_mode="Markdown"
            )

        # ===== القنوات =====
        elif waiting.startswith("channel_link_"):
            cid = waiting[13:]
            c = get_channel(data, cid)
            c["channel_link"] = text.strip()
            save(data)
            await update.message.reply_text("✅ تم تحديث رابط القناة!",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data=f"channel_{cid}")]]))

        elif waiting.startswith("channel_add_btn_"):
            cid = waiting[16:]
            if "|" in text:
                parts = text.split("|", 1)
                c = get_channel(data, cid)
                c.setdefault("extra_buttons", []).append({"text": parts[0].strip(), "url": parts[1].strip()})
                save(data)
                await update.message.reply_text(f"✅ تم إضافة الزر: *{parts[0].strip()}*",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data=f"channel_{cid}")]]),
                    parse_mode="Markdown")
            else:
                context.user_data["waiting"] = waiting
                await update.message.reply_text("⚠️ الشكل غلط! استخدم:\n`اسم الزر | الرابط`", parse_mode="Markdown")
        return

    # اقتراح/شكوى
    if waiting == "user_suggestion":
        context.user_data.pop("waiting", None)
        name = user.full_name
        username = f"@{user.username}" if user.username else "بدون يوزر"
        admin_msg = f"📩 *اقتراح/شكوى جديدة*\n\n👤 [{name}](tg://user?id={user.id})\n🆔 `{user.id}`\n📛 {username}\n\n💬 *الرسالة:*\n{text}"
        try:
            await context.bot.send_message(ADMIN_ID, admin_msg, parse_mode="Markdown")
            for sub_id in data.get("sub_admins", []):
                try:
                    await context.bot.send_message(int(sub_id), admin_msg, parse_mode="Markdown")
                except:
                    pass
        except:
            pass
        await update.message.reply_text("✅ تم إرسال رسالتك للإدارة، شكراً!")
        return

    if is_banned(user.id, data):
        return

    register_user(user, data)
    data["stats"]["messages"] = data["stats"].get("messages", 0) + 1

    for keyword, reply in data.get("auto_replies", {}).items():
        if keyword.lower() in text.lower():
            save(data)
            await update.message.reply_text(reply)
            return

    save(data)
    welcome = data["welcome"]
    keyboard = []
    for btn in welcome["buttons"]:
        if btn.get("url"):
            keyboard.append([InlineKeyboardButton(btn["text"], url=btn["url"])])
    keyboard.append([InlineKeyboardButton("📝 إرسال اقتراح أو شكوى", callback_data="send_suggestion")])
    await update.message.reply_text(welcome["text"], reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

# ==================== GROUP PROTECTION ====================
async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return
    chat = update.effective_chat
    user = update.effective_user
    if not user:
        return

    logger.info(f"Group msg from {user.id} in {chat.id}: {msg.text[:30] if msg.text else 'no text'}")

    data = load()
    gid = str(chat.id)
    g = get_group(data, gid)
    # تسجيل عنوان المجموعة دايماً
    if g.get("title") != chat.title:
        g["title"] = chat.title or gid
        save(data)

    # الأدمنز مستثنون
    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
        logger.info(f"Member status for {user.id}: {member.status}")
        if member.status in ["administrator", "creator"]:
            logger.info(f"Skipping admin {user.id}")
            return
    except Exception as e:
        logger.error(f"getChatMember error: {e}")

    # المستخدمون المستثنون
    if str(user.id) in g.get("exceptions_users", []):
        return

    uid = str(user.id)
    if uid not in g.get("violations", {}):
        g["violations"][uid] = {"links": 0, "username": 0, "forward": 0}

    deleted = False

    # فحص الروابط
    logger.info(f"anti_links={g['anti_links']}, text={bool(msg.text)}")
    if g["anti_links"] and msg.text:
        exc_links = g.get("exceptions_links", [])
        link_pattern = r'(https?://|t\.me/|www\.)[^\s]+'
        links_found = re.findall(link_pattern, msg.text, re.IGNORECASE)
        logger.info(f"Links found: {links_found}")
        has_forbidden_link = False
        for lf in re.finditer(link_pattern, msg.text, re.IGNORECASE):
            url = lf.group()
            if not any(exc in url for exc in exc_links):
                has_forbidden_link = True
                break
        logger.info(f"has_forbidden_link={has_forbidden_link}")
        if has_forbidden_link:
            g["violations"][uid]["links"] += 1
            if g["violations"][uid]["links"] >= g["anti_links_threshold"]:
                g["violations"][uid]["links"] = 0
                await apply_action(context, chat, user, msg, g["anti_links_action"], g.get("anti_links_mute_duration", 60))
            else:
                try:
                    await msg.delete()
                except:
                    pass
            deleted = True

    # فحص اليوزر نيم
    if not deleted and g["anti_username"] and msg.text:
        if re.search(r'@\w+', msg.text):
            g["violations"][uid]["username"] += 1
            if g["violations"][uid]["username"] >= g["anti_username_threshold"]:
                g["violations"][uid]["username"] = 0
                await apply_action(context, chat, user, msg, g["anti_username_action"], g.get("anti_username_mute_duration", 60))
            else:
                try:
                    await msg.delete()
                except:
                    pass
            deleted = True

    # فحص الكلمات المحظورة
    if not deleted and g.get("anti_words") and msg.text:
        words_list = g.get("anti_words_list", [])
        msg_lower = msg.text.lower()
        found_word = any(word in msg_lower for word in words_list)
        if found_word:
            g["violations"][uid]["words"] = g["violations"][uid].get("words", 0) + 1
            if g["violations"][uid]["words"] >= g.get("anti_words_threshold", 1):
                g["violations"][uid]["words"] = 0
                await apply_action(context, chat, user, msg, g.get("anti_words_action", "delete"), g.get("anti_words_mute_duration", 60))
            else:
                try:
                    await msg.delete()
                except:
                    pass
            deleted = True

    # فحص الفورورد
    if not deleted and g["anti_forward"] and msg.forward_origin:
        g["violations"][uid]["forward"] += 1
        if g["violations"][uid]["forward"] >= g["anti_forward_threshold"]:
            g["violations"][uid]["forward"] = 0
            await apply_action(context, chat, user, msg, g["anti_forward_action"], g.get("anti_forward_mute_duration", 60))
        else:
            try:
                await msg.delete()
            except:
                pass

    save(data)

async def apply_action(context, chat, user, msg, action, mute_duration=60):
    try:
        await msg.delete()
    except:
        pass
    if action == "delete":
        pass
    elif action == "mute":
        until = datetime.now() + timedelta(minutes=mute_duration)
        try:
            await context.bot.restrict_chat_member(
                chat.id, user.id,
                ChatPermissions(can_send_messages=False),
                until_date=until
            )
            await context.bot.send_message(chat.id, f"🔇 [{user.first_name}](tg://user?id={user.id}) تم كتمه لمدة {mute_duration} دقيقة.", parse_mode="Markdown")
        except:
            pass
    elif action == "ban":
        try:
            await context.bot.ban_chat_member(chat.id, user.id)
            await context.bot.send_message(chat.id, f"🚫 [{user.first_name}](tg://user?id={user.id}) تم حظره.", parse_mode="Markdown")
        except:
            pass

# ==================== WELCOME / LEAVE ====================
async def handle_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.chat_member
    if not result:
        return
    chat = result.chat
    data = load()
    gid = str(chat.id)
    g = get_group(data, gid)
    g["title"] = chat.title or gid

    old_status = result.old_chat_member.status
    new_status = result.new_chat_member.status
    user = result.new_chat_member.user

    # عضو جديد انضم
    if old_status in ["left", "kicked"] and new_status == "member":
        if not data.get("allow_groups", True):
            return
        if g["welcome_enabled"]:
            uid = str(user.id)
            seen = g.get("seen_members", [])
            if g["welcome_once"] and uid in seen:
                save(data)
                return
            if uid not in seen:
                g.setdefault("seen_members", []).append(uid)
            welcome_text = g["welcome_text"].replace("{name}", f"[{user.first_name}](tg://user?id={user.id})").replace("{group}", chat.title or "")
            kb = build_kb(g.get("welcome_buttons", []))
            try:
                await context.bot.send_message(chat.id, welcome_text, reply_markup=kb, parse_mode="Markdown")
            except:
                pass

    # عضو غادر
    elif old_status == "member" and new_status in ["left", "kicked"]:
        if g["leave_enabled"]:
            uid = str(user.id)
            left = g.get("left_members", [])
            if g["leave_once"] and uid in left:
                save(data)
                return
            if uid not in left:
                g.setdefault("left_members", []).append(uid)
            leave_text = g["leave_text"].replace("{name}", f"[{user.first_name}](tg://user?id={user.id})").replace("{group}", chat.title or "")
            try:
                await context.bot.send_message(chat.id, leave_text, parse_mode="Markdown")
            except:
                pass

    save(data)

# ==================== BOT ADDED TO GROUP ====================
async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يضيف أزرار تلقائياً على كل رسالة في القناة"""
    msg = update.channel_post or update.message
    if not msg:
        return
    chat = update.effective_chat
    if not chat:
        return

    data = load()
    cid = str(chat.id)
    c = get_channel(data, cid)

    # تسجيل القناة
    if c.get("title") != chat.title:
        c["title"] = chat.title or cid
        c["username"] = f"@{chat.username}" if chat.username else ""
        if not c.get("channel_link") and chat.username:
            c["channel_link"] = f"https://t.me/{chat.username}"
        save(data)

    # بناء الأزرار
    kb_btns = []
    if c.get("auto_button") and c.get("channel_link"):
        kb_btns.append([InlineKeyboardButton(f"📢 {c.get('title', 'القناة')}", url=c["channel_link"])])
    for btn in c.get("extra_buttons", []):
        if btn.get("url"):
            kb_btns.append([InlineKeyboardButton(btn["text"], url=btn["url"])])

    # هل الرسالة محولة (forward)؟
    is_forward = getattr(msg, "forward_origin", None) or getattr(msg, "forward_from", None) or getattr(msg, "forward_from_chat", None)

    # لو إعداد إعادة نشر الـ Forwards مفعّل والرسالة محولة
    if c.get("repost_forwards") and is_forward:
        try:
            # احذف الرسالة الـ forwarded
            await msg.delete()
        except Exception as e:
            logger.warning(f"Could not delete forward: {e}")
            return

        # أعد نشرها كـ copy نظيفة مع الأزرار
        try:
            kb = InlineKeyboardMarkup(kb_btns) if kb_btns else None
            if msg.text:
                await context.bot.send_message(
                    chat_id=chat.id,
                    text=msg.text,
                    reply_markup=kb,
                    entities=msg.entities
                )
            elif msg.photo:
                await context.bot.send_photo(
                    chat_id=chat.id,
                    photo=msg.photo[-1].file_id,
                    caption=msg.caption,
                    caption_entities=msg.caption_entities,
                    reply_markup=kb
                )
            elif msg.video:
                await context.bot.send_video(
                    chat_id=chat.id,
                    video=msg.video.file_id,
                    caption=msg.caption,
                    caption_entities=msg.caption_entities,
                    reply_markup=kb
                )
            elif msg.document:
                await context.bot.send_document(
                    chat_id=chat.id,
                    document=msg.document.file_id,
                    caption=msg.caption,
                    caption_entities=msg.caption_entities,
                    reply_markup=kb
                )
            elif msg.audio:
                await context.bot.send_audio(
                    chat_id=chat.id,
                    audio=msg.audio.file_id,
                    caption=msg.caption,
                    caption_entities=msg.caption_entities,
                    reply_markup=kb
                )
            elif msg.voice:
                await context.bot.send_voice(
                    chat_id=chat.id,
                    voice=msg.voice.file_id,
                    caption=msg.caption,
                    reply_markup=kb
                )
            elif msg.sticker:
                await context.bot.send_sticker(
                    chat_id=chat.id,
                    sticker=msg.sticker.file_id,
                    reply_markup=kb
                )
            elif msg.animation:
                await context.bot.send_animation(
                    chat_id=chat.id,
                    animation=msg.animation.file_id,
                    caption=msg.caption,
                    caption_entities=msg.caption_entities,
                    reply_markup=kb
                )
            elif msg.video_note:
                await context.bot.send_video_note(
                    chat_id=chat.id,
                    video_note=msg.video_note.file_id,
                    reply_markup=kb
                )
            elif msg.poll:
                # البولز مش ممكن نعمل copy ليها بس نقدر نبعت رسالة نص
                pass
            else:
                # أي نوع تاني - copy_message
                await context.bot.copy_message(
                    chat_id=chat.id,
                    from_chat_id=msg.chat_id,
                    message_id=msg.message_id,
                    reply_markup=kb
                )
        except Exception as e:
            logger.error(f"Could not repost channel forward: {e}")
        return

    # رسالة عادية - أضف الأزرار
    if not kb_btns:
        return

    try:
        await msg.edit_reply_markup(reply_markup=InlineKeyboardMarkup(kb_btns))
    except Exception as e:
        logger.warning(f"Could not edit channel post markup: {e}")

async def my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.my_chat_member
    if not result:
        return
    chat = result.chat
    new_status = result.new_chat_member.status
    data = load()

    if new_status in ["member", "administrator"]:
        if not data.get("allow_groups", True):
            try:
                await context.bot.leave_chat(chat.id)
            except:
                pass
            return
        gid = str(chat.id)
        # تنظيف أي مجموعات تانية بنفس العنوان (duplicates)
        title = chat.title or gid
        for existing_gid in list(data.get("groups", {}).keys()):
            if existing_gid != gid and data["groups"][existing_gid].get("title") == title:
                logger.info(f"Removing duplicate group: {existing_gid} (same title as {gid})")
                del data["groups"][existing_gid]
        # قناة أو مجموعة؟
        if chat.type == "channel":
            c = get_channel(data, gid)
            c["title"] = title
            c["username"] = f"@{chat.username}" if chat.username else ""
            if not c.get("channel_link") and chat.username:
                c["channel_link"] = f"https://t.me/{chat.username}"
        else:
            g = get_group(data, gid)
            g["title"] = title
        save(data)
    elif new_status in ["left", "kicked"]:
        gid = str(chat.id)
        if gid in data.get("groups", {}):
            del data["groups"][gid]
            save(data)
        if gid in data.get("channels", {}):
            del data["channels"][gid]
            save(data)

# ==================== WELCOME/LEAVE via Message ====================
async def handle_new_member_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يشتغل على NEW_CHAT_MEMBERS - أكثر موثوقية من ChatMemberHandler"""
    msg = update.message
    if not msg or not msg.new_chat_members:
        return
    chat = update.effective_chat
    data = load()
    gid = str(chat.id)
    g = get_group(data, gid)
    g["title"] = chat.title or gid

    for user in msg.new_chat_members:
        if user.is_bot:
            continue
        if not g.get("welcome_enabled"):
            continue
        uid = str(user.id)
        seen = g.setdefault("seen_members", [])
        if g.get("welcome_once") and uid in seen:
            continue
        if uid not in seen:
            seen.append(uid)
        welcome_text = g.get("welcome_text", "أهلاً {name}!").replace(
            "{name}", f"[{user.first_name}](tg://user?id={user.id})"
        ).replace("{group}", chat.title or "")
        kb = build_kb(g.get("welcome_buttons", []))
        try:
            await context.bot.send_message(chat.id, welcome_text, reply_markup=kb, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Welcome msg failed: {e}")

    save(data)

async def handle_left_member_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يشتغل على LEFT_CHAT_MEMBER - أكثر موثوقية من ChatMemberHandler"""
    msg = update.message
    if not msg or not msg.left_chat_member:
        return
    chat = update.effective_chat
    user = msg.left_chat_member
    if user.is_bot:
        return
    data = load()
    gid = str(chat.id)
    g = get_group(data, gid)

    if not g.get("leave_enabled"):
        return

    uid = str(user.id)
    left = g.setdefault("left_members", [])
    if g.get("leave_once") and uid in left:
        save(data)
        return
    if uid not in left:
        left.append(uid)

    leave_text = g.get("leave_text", "وداعاً {name}!").replace(
        "{name}", f"[{user.first_name}](tg://user?id={user.id})"
    ).replace("{group}", chat.title or "")
    try:
        await context.bot.send_message(chat.id, leave_text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Leave msg failed: {e}")
    save(data)

# ==================== MAIN ====================
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start, filters=filters.ChatType.PRIVATE))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, handle_private_message))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & ~filters.StatusUpdate.ALL, handle_group_message))
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL, handle_channel_post))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_member_message))
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, handle_left_member_message))
    app.add_handler(ChatMemberHandler(handle_member_update, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(ChatMemberHandler(my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    print("🤖 البوت شغال...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
