import logging
import json
import os
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, CallbackQueryHandler
)

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
DATA_FILE = "data.json"

# ==================== DATA ====================
def load():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "welcome": {
            "text": "أهلاً بك! 👋\nكيف أقدر أساعدك؟",
            "buttons": []
        },
        "auto_replies": {},
        "banned_users": [],
        "users": {},
        "groups": {},
        "allow_groups": True,
        "stats": {"messages": 0, "broadcasts": 0}
    }

def save(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def is_admin(user_id):
    return user_id == ADMIN_ID

def is_banned(user_id, data):
    return str(user_id) in data.get("banned_users", [])

def build_kb(buttons):
    keyboard = []
    for btn in buttons:
        if btn.get("url"):
            keyboard.append([InlineKeyboardButton(btn["text"], url=btn["url"])])
        elif btn.get("callback"):
            keyboard.append([InlineKeyboardButton(btn["text"], callback_data=btn["callback"])])
    return InlineKeyboardMarkup(keyboard) if keyboard else None

def register_user(user, data):
    uid = str(user.id)
    if uid not in data["users"]:
        data["users"][uid] = {
            "name": user.full_name,
            "username": user.username or "",
            "joined": datetime.now().isoformat()
        }
        save(data)

def register_group(chat, data):
    gid = str(chat.id)
    if gid not in data["groups"]:
        data["groups"][gid] = {
            "title": chat.title,
            "joined": datetime.now().isoformat()
        }
        save(data)

# ==================== ADMIN PANEL ====================
def admin_home_kb(data):
    allow = "✅ مفعّل" if data.get("allow_groups", True) else "❌ معطّل"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 الإحصائيات", callback_data="admin_stats")],
        [InlineKeyboardButton("💬 رسالة الترحيب", callback_data="admin_welcome")],
        [InlineKeyboardButton("🤖 الردود التلقائية", callback_data="admin_replies")],
        [InlineKeyboardButton("📢 برودكاست", callback_data="admin_broadcast_menu")],
        [InlineKeyboardButton("🚫 المحظورون", callback_data="admin_banned")],
        [InlineKeyboardButton(f"🔓 انضمام للمجموعات: {allow}", callback_data="admin_toggle_groups")],
    ])

async def show_admin_home(update, context, data=None):
    if data is None:
        data = load()
    text = "🎛️ *لوحة التحكم الرئيسية*"
    kb = admin_home_kb(data)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")

# ==================== START ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = load()

    if is_admin(user.id):
        await show_admin_home(update, context, data)
        return

    if is_banned(user.id, data):
        await update.message.reply_text("⛔ أنت محظور من استخدام هذا البوت.")
        return

    register_user(user, data)
    data["stats"]["messages"] = data["stats"].get("messages", 0) + 1
    save(data)

    welcome = data["welcome"]
    kb = build_kb(welcome["buttons"])
    await update.message.reply_text(welcome["text"], reply_markup=kb, parse_mode="Markdown")

# ==================== CALLBACKS ====================
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cb = query.data
    user_id = query.from_user.id

    if not is_admin(user_id):
        return

    data = load()

    # ===== الرئيسية =====
    if cb == "admin_home":
        await show_admin_home(update, context, data)

    # ===== الإحصائيات =====
    elif cb == "admin_stats":
        users = len(data.get("users", {}))
        groups = len(data.get("groups", {}))
        banned = len(data.get("banned_users", []))
        msgs = data["stats"].get("messages", 0)
        bcast = data["stats"].get("broadcasts", 0)
        text = (
            "📊 *الإحصائيات*\n\n"
            f"👤 المستخدمون: `{users}`\n"
            f"👥 المجموعات: `{groups}`\n"
            f"🚫 المحظورون: `{banned}`\n"
            f"💬 الرسائل المستقبلة: `{msgs}`\n"
            f"📢 البرودكاستات: `{bcast}`"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="admin_home")]])
        await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")

    # ===== رسالة الترحيب =====
    elif cb == "admin_welcome":
        welcome = data["welcome"]
        btns_text = "\n".join([f"• {b['text']} → {b.get('url','')}" for b in welcome["buttons"]]) or "لا يوجد أزرار"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ تعديل النص", callback_data="welcome_edit_text")],
            [InlineKeyboardButton("➕ إضافة زر", callback_data="welcome_add_btn")],
            [InlineKeyboardButton("🗑️ حذف زر", callback_data="welcome_del_btn")],
            [InlineKeyboardButton("👁️ معاينة", callback_data="welcome_preview")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="admin_home")],
        ])
        await query.edit_message_text(
            f"💬 *إعدادات رسالة الترحيب*\n\n📝 النص الحالي:\n`{welcome['text']}`\n\n🔘 الأزرار:\n{btns_text}",
            reply_markup=kb, parse_mode="Markdown"
        )

    elif cb == "welcome_preview":
        welcome = data["welcome"]
        kb_prev = build_kb(welcome["buttons"])
        await query.message.reply_text(welcome["text"], reply_markup=kb_prev, parse_mode="Markdown")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="admin_welcome")]])
        await query.edit_message_reply_markup(reply_markup=kb)

    elif cb == "welcome_edit_text":
        context.user_data["waiting"] = "welcome_text"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="admin_welcome")]])
        await query.edit_message_text("✏️ ابعت النص الجديد لرسالة الترحيب:", reply_markup=kb, parse_mode="Markdown")

    elif cb == "welcome_add_btn":
        context.user_data["waiting"] = "add_btn"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="admin_welcome")]])
        await query.edit_message_text(
            "➕ *إضافة زر*\n\nابعت بالشكل ده:\n`اسم الزر | الرابط`\n\nمثال:\n`📞 تواصل | https://t.me/youssefasc`",
            reply_markup=kb, parse_mode="Markdown"
        )

    elif cb == "welcome_del_btn":
        buttons = data["welcome"]["buttons"]
        if not buttons:
            await query.answer("مفيش أزرار!", show_alert=True)
            return
        rows = [[InlineKeyboardButton(f"🗑️ {b['text']}", callback_data=f"delbtn_{i}")] for i, b in enumerate(buttons)]
        rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_welcome")])
        await query.edit_message_text("🗑️ اختار الزر اللي عايز تحذفه:", reply_markup=InlineKeyboardMarkup(rows))

    elif cb.startswith("delbtn_"):
        idx = int(cb.split("_")[1])
        removed = data["welcome"]["buttons"].pop(idx)
        save(data)
        await query.answer(f"✅ تم حذف: {removed['text']}", show_alert=True)
        await show_admin_home(update, context, data)

    # ===== الردود التلقائية =====
    elif cb == "admin_replies":
        replies = data.get("auto_replies", {})
        text = "🤖 *الردود التلقائية*\n\n"
        if replies:
            text += "\n".join([f"• `{k}` ← {v}" for k, v in replies.items()])
        else:
            text += "لا يوجد ردود تلقائية بعد"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ إضافة رد", callback_data="reply_add")],
            [InlineKeyboardButton("🗑️ حذف رد", callback_data="reply_del")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="admin_home")],
        ])
        await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")

    elif cb == "reply_add":
        context.user_data["waiting"] = "add_reply"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="admin_replies")]])
        await query.edit_message_text(
            "➕ *إضافة رد تلقائي*\n\nابعت بالشكل ده:\n`الكلمة | الرد`\n\nمثال:\n`مرحبا | وعليكم السلام! 😊`",
            reply_markup=kb, parse_mode="Markdown"
        )

    elif cb == "reply_del":
        replies = data.get("auto_replies", {})
        if not replies:
            await query.answer("مفيش ردود!", show_alert=True)
            return
        rows = [[InlineKeyboardButton(f"🗑️ {k}", callback_data=f"delreply_{k}")] for k in replies]
        rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_replies")])
        await query.edit_message_text("🗑️ اختار الرد اللي عايز تحذفه:", reply_markup=InlineKeyboardMarkup(rows))

    elif cb.startswith("delreply_"):
        key = cb[9:]
        data["auto_replies"].pop(key, None)
        save(data)
        await query.answer(f"✅ تم حذف: {key}", show_alert=True)
        await show_admin_home(update, context, data)

    # ===== برودكاست =====
    elif cb == "admin_broadcast_menu":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("👤 للأشخاص بس", callback_data="bcast_users")],
            [InlineKeyboardButton("👥 للمجموعات بس", callback_data="bcast_groups")],
            [InlineKeyboardButton("📢 للكل", callback_data="bcast_all")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="admin_home")],
        ])
        await query.edit_message_text("📢 *برودكاست*\n\nاختار مين هتبعت له:", reply_markup=kb, parse_mode="Markdown")

    elif cb in ["bcast_users", "bcast_groups", "bcast_all"]:
        context.user_data["waiting"] = f"broadcast_{cb.split('_')[1]}"
        target = {"bcast_users": "الأشخاص", "bcast_groups": "المجموعات", "bcast_all": "الكل"}[cb]
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="admin_broadcast_menu")]])
        await query.edit_message_text(
            f"📢 *برودكاست لـ {target}*\n\nابعت الرسالة اللي عايز ترسلها:",
            reply_markup=kb, parse_mode="Markdown"
        )

    # ===== المحظورون =====
    elif cb == "admin_banned":
        banned = data.get("banned_users", [])
        text = "🚫 *المحظورون*\n\n"
        text += "\n".join([f"• `{uid}`" for uid in banned]) if banned else "لا يوجد محظورون"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚫 حظر مستخدم", callback_data="ban_user")],
            [InlineKeyboardButton("✅ رفع حظر", callback_data="unban_user")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="admin_home")],
        ])
        await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")

    elif cb == "ban_user":
        context.user_data["waiting"] = "ban_user"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="admin_banned")]])
        await query.edit_message_text("🚫 ابعت الـ ID بتاع المستخدم اللي عايز تحظره:", reply_markup=kb)

    elif cb == "unban_user":
        context.user_data["waiting"] = "unban_user"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="admin_banned")]])
        await query.edit_message_text("✅ ابعت الـ ID بتاع المستخدم اللي عايز ترفع حظره:", reply_markup=kb)

    # ===== تفعيل/تعطيل الانضمام للمجموعات =====
    elif cb == "admin_toggle_groups":
        data["allow_groups"] = not data.get("allow_groups", True)
        save(data)
        status = "مفعّل ✅" if data["allow_groups"] else "معطّل ❌"
        await query.answer(f"الانضمام للمجموعات: {status}", show_alert=True)
        await show_admin_home(update, context, data)

# ==================== MESSAGES ====================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text or ""
    data = load()
    waiting = context.user_data.get("waiting")

    # ===== أوامر الأدمن =====
    if is_admin(user.id) and waiting:
        context.user_data.pop("waiting")

        # تعديل نص الترحيب
        if waiting == "welcome_text":
            data["welcome"]["text"] = text
            save(data)
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع للوحة", callback_data="admin_welcome")]])
            await update.message.reply_text("✅ تم تعديل نص الترحيب!", reply_markup=kb)

        # إضافة زر
        elif waiting == "add_btn":
            if "|" in text:
                parts = text.split("|", 1)
                data["welcome"]["buttons"].append({"text": parts[0].strip(), "url": parts[1].strip()})
                save(data)
                kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="admin_welcome")]])
                await update.message.reply_text(f"✅ تم إضافة الزر: *{parts[0].strip()}*", reply_markup=kb, parse_mode="Markdown")
            else:
                await update.message.reply_text("⚠️ الشكل غلط! استخدم:\n`اسم الزر | الرابط`", parse_mode="Markdown")
                context.user_data["waiting"] = waiting

        # إضافة رد تلقائي
        elif waiting == "add_reply":
            if "|" in text:
                parts = text.split("|", 1)
                data["auto_replies"][parts[0].strip()] = parts[1].strip()
                save(data)
                kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="admin_replies")]])
                await update.message.reply_text(f"✅ تم إضافة الرد على: *{parts[0].strip()}*", reply_markup=kb, parse_mode="Markdown")
            else:
                await update.message.reply_text("⚠️ الشكل غلط! استخدم:\n`الكلمة | الرد`", parse_mode="Markdown")
                context.user_data["waiting"] = waiting

        # برودكاست
        elif waiting in ["broadcast_users", "broadcast_groups", "broadcast_all"]:
            target = waiting.split("_")[1]
            sent = 0
            failed = 0

            if target in ["users", "all"]:
                for uid in data.get("users", {}):
                    try:
                        await context.bot.send_message(int(uid), text)
                        sent += 1
                    except:
                        failed += 1

            if target in ["groups", "all"]:
                for gid in data.get("groups", {}):
                    try:
                        await context.bot.send_message(int(gid), text)
                        sent += 1
                    except:
                        failed += 1

            data["stats"]["broadcasts"] = data["stats"].get("broadcasts", 0) + 1
            save(data)
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع للوحة", callback_data="admin_home")]])
            await update.message.reply_text(
                f"📢 *تم البرودكاست!*\n\n✅ أُرسل لـ: `{sent}`\n❌ فشل: `{failed}`",
                reply_markup=kb, parse_mode="Markdown"
            )

        # حظر مستخدم
        elif waiting == "ban_user":
            uid = text.strip()
            if uid not in data["banned_users"]:
                data["banned_users"].append(uid)
                save(data)
                await update.message.reply_text(f"✅ تم حظر المستخدم: `{uid}`", parse_mode="Markdown")
            else:
                await update.message.reply_text("⚠️ المستخدم ده محظور أصلاً!")

        # رفع حظر
        elif waiting == "unban_user":
            uid = text.strip()
            if uid in data["banned_users"]:
                data["banned_users"].remove(uid)
                save(data)
                await update.message.reply_text(f"✅ تم رفع حظر: `{uid}`", parse_mode="Markdown")
            else:
                await update.message.reply_text("⚠️ المستخدم ده مش محظور!")
        return

    # ===== مستخدم عادي =====
    if is_banned(user.id, data):
        return

    register_user(user, data)
    data["stats"]["messages"] = data["stats"].get("messages", 0) + 1

    # ردود تلقائية
    for keyword, reply in data.get("auto_replies", {}).items():
        if keyword.lower() in text.lower():
            save(data)
            await update.message.reply_text(reply)
            return

    save(data)

    # رسالة الترحيب لأي رسالة
    welcome = data["welcome"]
    kb = build_kb(welcome["buttons"])
    await update.message.reply_text(welcome["text"], reply_markup=kb, parse_mode="Markdown")

# ==================== GROUP EVENTS ====================
async def group_added(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load()
    if not data.get("allow_groups", True):
        await context.bot.leave_chat(update.effective_chat.id)
        return
    register_group(update.effective_chat, data)

# ==================== MAIN ====================
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_message))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, group_added))
    print("🤖 البوت شغال...")
    app.run_polling()

if __name__ == "__main__":
    main()
