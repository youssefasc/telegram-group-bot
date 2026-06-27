import logging
import json
import os
from datetime import datetime
from telegram import Update, ChatPermissions
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, ChatMemberHandler
)

# ==================== CONFIG ====================
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== DATA STORE ====================
DATA_FILE = "bot_data.json"

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"groups": {}, "warnings": {}}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_group(data, chat_id):
    key = str(chat_id)
    if key not in data["groups"]:
        data["groups"][key] = {
            "welcome_msg": "اهلا وسهلا {name} في المجموعة!",
            "anti_links": False,
            "anti_spam": False,
            "custom_commands": {},
            "members_joined": 0,
            "messages_count": 0,
        }
    return data["groups"][key]

async def is_admin(update, context):
    user = update.effective_user
    chat = update.effective_chat
    member = await context.bot.get_chat_member(chat.id, user.id)
    return member.status in ["administrator", "creator"]

async def get_target_user(update, context):
    if update.message.reply_to_message:
        return update.message.reply_to_message.from_user
    if context.args:
        try:
            user_id = int(context.args[0])
            return await context.bot.get_chat_member(update.effective_chat.id, user_id)
        except:
            pass
    return None

async def welcome_new_member(update, context):
    data = load_data()
    chat_id = update.effective_chat.id
    group = get_group(data, chat_id)
    for member in update.message.new_chat_members:
        if member.is_bot:
            continue
        group["members_joined"] += 1
        msg = group["welcome_msg"].replace("{name}", f"[{member.first_name}](tg://user?id={member.id})")
        await update.message.reply_text(msg, parse_mode="Markdown")
    save_data(data)

async def set_welcome(update, context):
    if not await is_admin(update, context):
        await update.message.reply_text("الامر ده للادمن بس!")
        return
    if not context.args:
        await update.message.reply_text("استخدم: /setwelcome رسالتك", parse_mode="Markdown")
        return
    data = load_data()
    group = get_group(data, update.effective_chat.id)
    group["welcome_msg"] = " ".join(context.args)
    save_data(data)
    await update.message.reply_text("تم تعديل رسالة الترحيب!")

async def ban_user(update, context):
    if not await is_admin(update, context):
        await update.message.reply_text("الامر ده للادمن بس!")
        return
    target = await get_target_user(update, context)
    if not target:
        await update.message.reply_text("رد على رسالة شخص او اكتب ID")
        return
    reason = " ".join(context.args[1:]) if len(context.args) > 1 else "لا يوجد سبب"
    await context.bot.ban_chat_member(update.effective_chat.id, target.id)
    await update.message.reply_text(f"تم حظر [{target.first_name}](tg://user?id={target.id})", parse_mode="Markdown")

async def kick_user(update, context):
    if not await is_admin(update, context):
        await update.message.reply_text("الامر ده للادمن بس!")
        return
    target = await get_target_user(update, context)
    if not target:
        await update.message.reply_text("رد على رسالة شخص او اكتب ID")
        return
    await context.bot.ban_chat_member(update.effective_chat.id, target.id)
    await context.bot.unban_chat_member(update.effective_chat.id, target.id)
    await update.message.reply_text(f"تم طرد [{target.first_name}](tg://user?id={target.id})", parse_mode="Markdown")

async def warn_user(update, context):
    if not await is_admin(update, context):
        await update.message.reply_text("الامر ده للادمن بس!")
        return
    target = await get_target_user(update, context)
    if not target:
        await update.message.reply_text("رد على رسالة شخص او اكتب ID")
        return
    data = load_data()
    chat_id = str(update.effective_chat.id)
    user_id = str(target.id)
    if chat_id not in data["warnings"]:
        data["warnings"][chat_id] = {}
    if user_id not in data["warnings"][chat_id]:
        data["warnings"][chat_id][user_id] = 0
    data["warnings"][chat_id][user_id] += 1
    warns = data["warnings"][chat_id][user_id]
    save_data(data)
    if warns >= 3:
        await context.bot.ban_chat_member(update.effective_chat.id, target.id)
        await update.message.reply_text(f"[{target.first_name}](tg://user?id={target.id}) وصل 3 تحذيرات وتم حظره!", parse_mode="Markdown")
        data["warnings"][chat_id][user_id] = 0
        save_data(data)
    else:
        await update.message.reply_text(f"تحذير لـ [{target.first_name}](tg://user?id={target.id}) - عدد التحذيرات: {warns}/3", parse_mode="Markdown")

async def unwarn_user(update, context):
    if not await is_admin(update, context):
        await update.message.reply_text("الامر ده للادمن بس!")
        return
    target = await get_target_user(update, context)
    if not target:
        await update.message.reply_text("رد على رسالة شخص")
        return
    data = load_data()
    chat_id = str(update.effective_chat.id)
    user_id = str(target.id)
    if chat_id in data["warnings"] and user_id in data["warnings"][chat_id]:
        data["warnings"][chat_id][user_id] = max(0, data["warnings"][chat_id][user_id] - 1)
        save_data(data)
    await update.message.reply_text(f"تم ازالة تحذير من [{target.first_name}](tg://user?id={target.id})", parse_mode="Markdown")

async def filter_messages(update, context):
    if not update.message or not update.message.text:
        return
    data = load_data()
    chat_id = update.effective_chat.id
    group = get_group(data, chat_id)
    group["messages_count"] += 1
    save_data(data)
    if await is_admin(update, context):
        return
    text = update.message.text
    if group["anti_links"]:
        import re
        if re.search(r'(https?://|t\.me/|@\w+|www\.)', text, re.IGNORECASE):
            await update.message.delete()
            await context.bot.send_message(chat_id, f"[{update.effective_user.first_name}](tg://user?id={update.effective_user.id}) الروابط ممنوعة!", parse_mode="Markdown")
            return
    if group["anti_spam"]:
        last_msg = context.user_data.get("last_msg", "")
        msg_count = context.user_data.get("spam_count", 0)
        if text == last_msg:
            msg_count += 1
            context.user_data["spam_count"] = msg_count
            if msg_count >= 3:
                await update.message.delete()
                await context.bot.send_message(chat_id, f"[{update.effective_user.first_name}](tg://user?id={update.effective_user.id}) لا تكرر الرسائل!", parse_mode="Markdown")
                return
        else:
            context.user_data["last_msg"] = text
            context.user_data["spam_count"] = 0
    for cmd, response in group["custom_commands"].items():
        if text.lower() == cmd.lower():
            await update.message.reply_text(response)
            return

async def toggle_antilinks(update, context):
    if not await is_admin(update, context):
        await update.message.reply_text("الامر ده للادمن بس!")
        return
    data = load_data()
    group = get_group(data, update.effective_chat.id)
    group["anti_links"] = not group["anti_links"]
    save_data(data)
    status = "مفعل" if group["anti_links"] else "معطل"
    await update.message.reply_text(f"فلترة الروابط: {status}")

async def toggle_antispam(update, context):
    if not await is_admin(update, context):
        await update.message.reply_text("الامر ده للادمن بس!")
        return
    data = load_data()
    group = get_group(data, update.effective_chat.id)
    group["anti_spam"] = not group["anti_spam"]
    save_data(data)
    status = "مفعل" if group["anti_spam"] else "معطل"
    await update.message.reply_text(f"حماية السبام: {status}")

async def add_command(update, context):
    if not await is_admin(update, context):
        await update.message.reply_text("الامر ده للادمن بس!")
        return
    if len(context.args) < 2:
        await update.message.reply_text("استخدم: /addcmd !امر الرد")
        return
    cmd = context.args[0]
    response = " ".join(context.args[1:])
    data = load_data()
    group = get_group(data, update.effective_chat.id)
    group["custom_commands"][cmd] = response
    save_data(data)
    await update.message.reply_text(f"تم اضافة الامر: {cmd}")

async def del_command(update, context):
    if not await is_admin(update, context):
        await update.message.reply_text("الامر ده للادمن بس!")
        return
    if not context.args:
        await update.message.reply_text("استخدم: /delcmd !امر")
        return
    cmd = context.args[0]
    data = load_data()
    group = get_group(data, update.effective_chat.id)
    if cmd in group["custom_commands"]:
        del group["custom_commands"][cmd]
        save_data(data)
        await update.message.reply_text(f"تم حذف الامر: {cmd}")
    else:
        await update.message.reply_text("الامر ده مش موجود")

async def stats(update, context):
    data = load_data()
    chat_id = update.effective_chat.id
    group = get_group(data, chat_id)
    chat = update.effective_chat
    try:
        member_count = await context.bot.get_chat_member_count(chat_id)
    except:
        member_count = "?"
    await update.message.reply_text(
        f"احصائيات {chat.title}\n"
        f"عدد الاعضاء: {member_count}\n"
        f"الرسائل: {group['messages_count']}\n"
        f"فلترة روابط: {'مفعل' if group['anti_links'] else 'معطل'}\n"
        f"حماية سبام: {'مفعل' if group['anti_spam'] else 'معطل'}\n"
        f"اوامر مخصصة: {len(group['custom_commands'])}"
    )

async def help_cmd(update, context):
    await update.message.reply_text(
        "اوامر البوت:\n"
        "/setwelcome - تعديل رسالة الترحيب\n"
        "/ban - حظر عضو\n"
        "/kick - طرد عضو\n"
        "/warn - تحذير (3 = حظر)\n"
        "/unwarn - ازالة تحذير\n"
        "/antilinks - فلترة الروابط\n"
        "/antispam - حماية السبام\n"
        "/addcmd - اضافة امر\n"
        "/delcmd - حذف امر\n"
        "/stats - احصائيات"
    )

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member))
    app.add_handler(CommandHandler("setwelcome", set_welcome))
    app.add_handler(CommandHandler("ban", ban_user))
    app.add_handler(CommandHandler("kick", kick_user))
    app.add_handler(CommandHandler("warn", warn_user))
    app.add_handler(CommandHandler("unwarn", unwarn_user))
    app.add_handler(CommandHandler("antilinks", toggle_antilinks))
    app.add_handler(CommandHandler("antispam", toggle_antispam))
    app.add_handler(CommandHandler("addcmd", add_command))
    app.add_handler(CommandHandler("delcmd", del_command))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, filter_messages))
    print("البوت شغال...")
    app.run_polling()

if __name__ == "__main__":
    main()
