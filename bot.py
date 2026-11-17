import os, asyncio, logging
from threading import Thread
from datetime import datetime
from bson import ObjectId
from telegram.error import TelegramError

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
import uvicorn
from pymongo import MongoClient, ASCENDING
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID = int(os.environ["OWNER_ID"])
MONGO_URI = os.environ["MONGO_URI"]
DB_NAME = os.getenv("DB_NAME", "hisaab_bot")

logging.basicConfig(level=logging.INFO)
mongo = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
db = mongo[DB_NAME]
users = db.users
accounts = db.accounts
transactions = db.transactions

users.create_index([("telegram_id", ASCENDING)], unique=True)
accounts.create_index([("owner_user_id", ASCENDING), ("name_key", ASCENDING)], unique=True)
transactions.create_index([("account_id", ASCENDING), ("created_at", ASCENDING)])

web = FastAPI()

@web.get("/health", response_class=PlainTextResponse)
async def health():
    try:
        mongo.admin.command("ping")
        return "OK"
    except Exception:
        return PlainTextResponse("Database unavailable", status_code=503)

@web.get("/", response_class=PlainTextResponse)
async def root():
    return "Hisaab Bot is running."

def get_user(tid):
    return users.find_one({"telegram_id": tid})

def language(tid):
    u = get_user(tid)
    return u.get("language", "hi") if u else "hi"

def owner(tid): return tid == OWNER_ID
def approved(tid):
    return owner(tid) or bool(users.find_one({"telegram_id": tid, "status": "approved"}))

def menu(lang, is_owner=False):
    if lang == "bn":
        rows = [[KeyboardButton("➕ হিসাব যোগ"), KeyboardButton("📋 তালিকা")],
                [KeyboardButton("🌐 ভাষা"), KeyboardButton("❓ সাহায্য")]]
        if is_owner: rows.extend([[KeyboardButton("👥 ব্যবহারকারীর হিসাব")], [KeyboardButton("🗑️ হিসাবের History ডিলিট")]])
    else:
        rows = [[KeyboardButton("➕ Hisaab Add"), KeyboardButton("📋 List")],
                [KeyboardButton("🌐 Language"), KeyboardButton("❓ Help")]]
        if is_owner: rows.extend([[KeyboardButton("👥 User Hisaab")], [KeyboardButton("🗑️ Delete History")]])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def lang_buttons():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🇮🇳 বাংলা", callback_data="lang:bn"),
                                   InlineKeyboardButton("🇮🇳 हिन्दी", callback_data="lang:hi")]])

WELCOME = """╭━━━ ✦ WELCOME ✦ ━━━╮
💰 *HISAAB BOT*
━━━━━━━━━━━━━━━━━━
🇮🇳 *हिन्दी + English*
Apna hisaab simple aur organized rakhiye.

🇮🇳 *বাংলা*
আপনার হিসাব সহজে এবং সুন্দরভাবে পরিচালনা করুন।
━━━━━━━━━━━━━━━━━━
✨ *This bot is created by GREEDY REX.*
╰━━━━━━━━━━━━━━━━━━╯"""

def ensure_user(u, status="pending"):
    old = get_user(u.id)
    if not old:
        users.insert_one({"telegram_id":u.id, "username":u.username, "first_name":u.first_name,
                          "last_name":u.last_name, "status":"owner" if u.id == OWNER_ID else status,
                          "language":"hi", "created_at":datetime.utcnow()})
    else:
        users.update_one({"telegram_id":u.id}, {"$set":{"username":u.username,"first_name":u.first_name,"last_name":u.last_name}})

async def send_permission_request(bot, user):
    """Send one notification per pending request; allow retry if Telegram delivery fails."""
    rec = get_user(user.id)
    if not rec:
        return False
    if rec.get("status") == "approved":
        return False
    if rec.get("request_notified"):
        return True
    users.update_one({"telegram_id": user.id}, {"$set": {"status": "pending"}})
    username = f"@{user.username}" if user.username else "Not set"
    text = ("🔔 New Permission Request\n\n"
            f"👤 Name: {user.full_name}\n🔗 Username: {username}\n🆔 User ID: {user.id}")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Approve", callback_data=f"approve:{user.id}"),
                                InlineKeyboardButton("❌ Reject", callback_data=f"reject:{user.id}")]])
    try:
        await bot.send_message(OWNER_ID, text, reply_markup=kb)
        users.update_one({"telegram_id": user.id}, {"$set": {"request_notified": True}})
        return True
    except TelegramError:
        logging.exception("Could not deliver permission request to OWNER_ID=%s", OWNER_ID)
        return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    ensure_user(u)
    if owner(u.id):
        await update.message.reply_text(WELCOME, parse_mode=ParseMode.MARKDOWN, reply_markup=lang_buttons())
        return
    rec = get_user(u.id)
    if rec.get("status") != "approved":
        sent = await send_permission_request(context.bot, u)
        if sent:
            msg = "🔒 Access request is pending. The owner has been notified."
        else:
            msg = ("🔒 Access is required. Please ask the owner to open the bot and send /start, then try /start again.")
        await update.message.reply_text(WELCOME + "\n\n" + msg, parse_mode=ParseMode.MARKDOWN)
        return
    await update.message.reply_text(WELCOME, parse_mode=ParseMode.MARKDOWN, reply_markup=lang_buttons())

async def permission_request(update, context):
    q = update.callback_query
    await q.answer()
    u = q.from_user
    ensure_user(u)
    sent = await send_permission_request(context.bot, u)
    await q.edit_message_text("✅ Permission request sent to the bot owner." if sent else
                              "⚠️ Could not notify the owner. Please ask the owner to open the bot and send /start, then try again.")

async def permission_decision(update, context):
    q=update.callback_query
    if not owner(q.from_user.id):
        await q.answer("Owner only.",show_alert=True); return
    action, sid=q.data.split(":"); tid=int(sid)
    status="approved" if action=="approve" else "rejected"
    users.update_one({"telegram_id":tid},{"$set":{"status":status,"request_notified":False}},upsert=True)
    await q.edit_message_text("✅ User approved." if status=="approved" else "❌ User rejected.")
    if status=="approved":
        await context.bot.send_message(tid,"✅ *Access Approved*\n\nAb aap bot ka hisaab use kar sakte ho.",
                                        parse_mode=ParseMode.MARKDOWN,reply_markup=menu(language(tid)))

async def language_cmd(update, context):
    await update.message.reply_text("🌐 Language / ভাষা নির্বাচন করুন:",reply_markup=lang_buttons())

async def language_cb(update, context):
    q=update.callback_query; await q.answer()
    lang=q.data.split(":")[1]
    users.update_one({"telegram_id":q.from_user.id},{"$set":{"language":lang}},upsert=True)
    msg="✅ ভাষা বাংলা করা হয়েছে।" if lang=="bn" else "✅ Language set to हिन्दी (Hinglish)."
    await q.edit_message_text(msg)
    await context.bot.send_message(q.from_user.id,"মূল menu:" if lang=="bn" else "Main menu:",
                                   reply_markup=menu(lang,owner(q.from_user.id)))

async def help_cmd(update, context):
    if not approved(update.effective_user.id): return
    if language(update.effective_user.id)=="bn":
        text=("❓ *সাহায্য*\n\n`Rahul, 500` → ₹500 যোগ হবে\n"
              "`Rahul, -200` → ₹200 বিয়োগ হবে\n\n"
              "/start — শুরু\n/add — হিসাব যোগ\n/list — হিসাবের তালিকা\n"
              "/users — Owner-এর user list\n/language — ভাষা\n/example — উদাহরণ\n/help — সাহায্য")
    else:
        text=("❓ *Help*\n\n`Rahul, 500` → ₹500 add hoga\n"
              "`Rahul, -200` → ₹200 minus hoga\n\n"
              "/start — Start\n/add — Hisaab add\n/list — Hisaab list\n"
              "/users — Owner user list\n/language — Language\n/example — Example\n/help — Help")
    await update.message.reply_text(text,parse_mode=ParseMode.MARKDOWN)

async def example_cmd(update, context):
    if not approved(update.effective_user.id): return
    if language(update.effective_user.id)=="bn":
        text="🧮 *উদাহরণ*\n\n`Rahul, 500`\n→ ₹500 যোগ\n\n`Rahul, 250`\n→ ₹250 যোগ\n\n`Rahul, -100`\n→ ₹100 বিয়োগ\n\n💰 *Rahul-এর Total: ₹650*"
    else:
        text="🧮 *Example*\n\n`Rahul, 500`\n→ ₹500 add\n\n`Rahul, 250`\n→ ₹250 add\n\n`Rahul, -100`\n→ ₹100 minus\n\n💰 *Rahul ka Total: ₹650*"
    await update.message.reply_text(text,parse_mode=ParseMode.MARKDOWN)

async def add_cmd(update, context):
    if not approved(update.effective_user.id): return
    lang=language(update.effective_user.id)
    text="Naam aur amount bhejiye: `Rahul, 500` ya `Rahul, -200`" if lang!="bn" else "নাম এবং amount পাঠান: `Rahul, 500` অথবা `Rahul, -200`"
    await update.message.reply_text(text,parse_mode=ParseMode.MARKDOWN)

def parse(text):
    if "," not in text: return None
    name, raw=text.split(",",1)
    name=name.strip(); raw=raw.strip().replace("₹","").replace(",","")
    if not name or len(name)>80: return None
    try: amount=float(raw)
    except: return None
    if amount==0: return None
    return name,amount

async def transaction(update, context):
    tid=update.effective_user.id
    if not approved(tid): return
    parsed=parse(update.message.text.strip())
    if not parsed: return
    name,amount=parsed; key=name.casefold()
    acc=accounts.find_one({"owner_user_id":tid,"name_key":key})
    if not acc:
        res=accounts.insert_one({"owner_user_id":tid,"name":name,"name_key":key,"created_at":datetime.utcnow()})
        aid=res.inserted_id
    else: aid=acc["_id"]
    transactions.insert_one({"account_id":aid,"amount":amount,"created_at":datetime.utcnow()})
    total_doc=list(transactions.aggregate([{"$match":{"account_id":aid}},{"$group":{"_id":None,"total":{"$sum":"$amount"}}}]))
    total=total_doc[0]["total"]
    if language(tid)=="bn":
        text=f"✅ *{name}*-এর হিসাবে ₹{abs(amount):,.2f} {'যোগ' if amount>0 else 'বিয়োগ'} হয়েছে.\n\n💰 *{name}-এর Total: ₹{total:,.2f}*"
    else:
        text=f"✅ *{name}* ke account mein ₹{abs(amount):,.2f} {'add' if amount>0 else 'minus'} hua.\n\n💰 *{name} ka Total: ₹{total:,.2f}*"
    await update.message.reply_text(text,parse_mode=ParseMode.MARKDOWN)

def total_for(aid):
    d=list(transactions.aggregate([{"$match":{"account_id":aid}},{"$group":{"_id":None,"total":{"$sum":"$amount"}}}]))
    return d[0]["total"] if d else 0

async def list_cmd(update, context):
    tid=update.effective_user.id
    if not approved(tid): return
    rows=list(accounts.find({"owner_user_id":tid}).sort("name",ASCENDING))
    if not rows:
        await update.message.reply_text("📋 কোনো হিসাব নেই।" if language(tid)=="bn" else "📋 Abhi koi hisaab nahi hai."); return
    lines=["📋 *হিসাবের তালিকা*\n" if language(tid)=="bn" else "📋 *Hisaab List*\n"]; grand=0
    for i,a in enumerate(rows,1):
        total=total_for(a["_id"]); grand+=total; lines.append(f"{i}. *{a['name']}* — ₹{total:,.2f}")
    lines += ["\n━━━━━━━━━━━━",f"💰 *{'সর্বমোট' if language(tid)=='bn' else 'Overall Total'}: ₹{grand:,.2f}*"]
    await update.message.reply_text("\n".join(lines),parse_mode=ParseMode.MARKDOWN)

async def users_cmd(update, context):
    if not owner(update.effective_user.id):
        await update.message.reply_text("🔒 Owner only."); return
    rows=list(users.find({"status":"approved","telegram_id":{"$ne":OWNER_ID}}).sort("first_name",ASCENDING))
    if not rows:
        await update.message.reply_text("👥 No approved users yet."); return
    kb=[]
    for u in rows:
        label=u.get("first_name") or u.get("username") or str(u["telegram_id"])
        kb.append([InlineKeyboardButton(f"👤 {label}",callback_data=f"vu:{u['telegram_id']}")])
    await update.message.reply_text("👥 Select a user to view their accounts:",reply_markup=InlineKeyboardMarkup(kb))

async def view_user(update, context):
    q=update.callback_query
    if not owner(q.from_user.id): await q.answer("Owner only.",show_alert=True); return
    tid=int(q.data.split(":")[1]); u=get_user(tid)
    rows=list(accounts.find({"owner_user_id":tid}).sort("name",ASCENDING))
    if not rows: await q.edit_message_text("No accounts yet."); return
    kb=[[InlineKeyboardButton(f"{a['name']} — ₹{total_for(a['_id']):,.2f}",callback_data=f"va:{tid}:{a['_id']}")] for a in rows]
    await q.edit_message_text(f"👤 *{u.get('first_name') or u.get('username') or tid}'s Accounts*",parse_mode=ParseMode.MARKDOWN,reply_markup=InlineKeyboardMarkup(kb))

async def view_account(update, context):
    q=update.callback_query
    if not owner(q.from_user.id): await q.answer("Owner only.",show_alert=True); return
    from bson import ObjectId
    _,tid,aid=q.data.split(":")
    a=accounts.find_one({"_id":ObjectId(aid),"owner_user_id":int(tid)})
    if not a: await q.answer("Account not found.",show_alert=True); return
    tx=list(transactions.find({"account_id":a["_id"]}).sort("created_at",ASCENDING))
    lines=[f"👤 *{a['name']}*\n"]
    for i,t in enumerate(tx,1): lines.append(f"{i}️⃣ {'+' if t['amount']>0 else ''}₹{t['amount']:,.2f}")
    lines+=["\n━━━━━━━━━━━━",f"💰 *Total: ₹{sum(t['amount'] for t in tx):,.2f}*"]
    await q.edit_message_text("\n".join(lines),parse_mode=ParseMode.MARKDOWN)

async def delete_history_cmd(update, context):
    if not owner(update.effective_user.id):
        await update.message.reply_text("🔒 Owner only.")
        return
    context.user_data["awaiting_delete_history_name"] = True
    await update.message.reply_text("যে হিসাবের নাম ডিলিট করতে চাও, সেটি হুবহু লিখো। বড়/ছোট হাতের অক্ষর আলাদা ধরা হবে না।\n\nCancel করতে /cancel পাঠাও।")

async def find_delete_history_name(update, context, name):
    key = name.strip().casefold()
    matches = list(accounts.find({"name_key": key}))
    if not matches:
        # Compatibility for older account documents without name_key.
        matches = [a for a in accounts.find({"name": {"$exists": True}})
                   if a.get("name", "").strip().casefold() == key]
    if not matches:
        await update.message.reply_text("❌ এই বানানে কোনো হিসাব পাওয়া যায়নি। নামের বানান ঠিক আছে কি না দেখো।")
        return
    context.user_data["delete_history_matches"] = [str(a["_id"]) for a in matches]
    context.user_data.pop("awaiting_delete_history_name", None)
    if len(matches) == 1:
        a = matches[0]
        u = get_user(a["owner_user_id"])
        owner_name = (u or {}).get("first_name") or (u or {}).get("username") or str(a["owner_user_id"])
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🗑️ হ্যাঁ, ডিলিট করো", callback_data=f"dhconfirm:{a['_id']}"),
                                    InlineKeyboardButton("❌ Cancel", callback_data="dhcancel")]])
        await update.message.reply_text(f"⚠️ নিশ্চিত করো: {a['name']} — User: {owner_name}\nএই হিসাবের সব Transaction History স্থায়ীভাবে ডিলিট হবে।", reply_markup=kb)
        return
    kb=[]
    for a in matches[:50]:
        u=get_user(a["owner_user_id"])
        owner_name=(u or {}).get("first_name") or (u or {}).get("username") or str(a["owner_user_id"])
        kb.append([InlineKeyboardButton(f"{a['name']} — {owner_name}", callback_data=f"dhselect:{a['_id']}")])
    kb.append([InlineKeyboardButton("❌ Cancel", callback_data="dhcancel")])
    await update.message.reply_text("একই নামে একাধিক হিসাব পাওয়া গেছে। সঠিক User-এর হিসাবটি বেছে নাও:", reply_markup=InlineKeyboardMarkup(kb))

async def delete_history_select(update, context):
    q=update.callback_query
    if not owner(q.from_user.id):
        await q.answer("Owner only.", show_alert=True); return
    aid=q.data.split(":",1)[1]
    try: a=accounts.find_one({"_id":ObjectId(aid)})
    except Exception: a=None
    if not a:
        await q.answer("Account not found.", show_alert=True); return
    u=get_user(a["owner_user_id"])
    owner_name=(u or {}).get("first_name") or (u or {}).get("username") or str(a["owner_user_id"])
    kb=InlineKeyboardMarkup([[InlineKeyboardButton("🗑️ হ্যাঁ, ডিলিট করো", callback_data=f"dhconfirm:{aid}"),
                              InlineKeyboardButton("❌ Cancel", callback_data="dhcancel")]])
    await q.edit_message_text(f"⚠️ নিশ্চিত করো: {a['name']} — User: {owner_name}\nএই হিসাবের সব Transaction History স্থায়ীভাবে ডিলিট হবে।", reply_markup=kb)

async def delete_history_confirm(update, context):
    q=update.callback_query
    if not owner(q.from_user.id):
        await q.answer("Owner only.", show_alert=True); return
    aid=q.data.split(":",1)[1]
    try: oid=ObjectId(aid)
    except Exception:
        await q.answer("Invalid account.", show_alert=True); return
    account=accounts.find_one({"_id":oid})
    if not account:
        await q.edit_message_text("এই হিসাবটি আর পাওয়া যাচ্ছে না।")
        return
    result=transactions.delete_many({"account_id":oid})
    accounts.delete_one({"_id":oid})
    await q.edit_message_text(f"✅ {account['name']} হিসাবটি এবং এর {result.deleted_count}টি Transaction History ডিলিট করা হয়েছে।")

async def delete_history_cancel(update, context):
    q=update.callback_query
    if not owner(q.from_user.id):
        await q.answer("Owner only.", show_alert=True); return
    context.user_data.pop("delete_history_matches", None)
    context.user_data.pop("awaiting_delete_history_name", None)
    await q.edit_message_text("❌ Delete History বাতিল করা হয়েছে।")

async def cancel_cmd(update, context):
    context.user_data.pop("awaiting_delete_history_name", None)
    context.user_data.pop("delete_history_matches", None)
    await update.message.reply_text("Cancelled.")

async def callbacks(update, context):
    q=update.callback_query
    if q.data.startswith("request:"): await permission_request(update,context)
    elif q.data.startswith("approve:") or q.data.startswith("reject:"): await permission_decision(update,context)
    elif q.data.startswith("lang:"): await language_cb(update,context)
    elif q.data.startswith("vu:"): await view_user(update,context)
    elif q.data.startswith("va:"): await view_account(update,context)
    elif q.data.startswith("dhselect:"): await delete_history_select(update,context)
    elif q.data.startswith("dhconfirm:"): await delete_history_confirm(update,context)
    elif q.data == "dhcancel": await delete_history_cancel(update,context)

async def text_router(update, context):
    t=update.message.text.strip()
    if owner(update.effective_user.id) and context.user_data.get("awaiting_delete_history_name"):
        await find_delete_history_name(update, context, t)
        return
    if t in ("➕ হিসাব যোগ","➕ Hisaab Add"): await add_cmd(update,context)
    elif t in ("📋 তালিকা","📋 List"): await list_cmd(update,context)
    elif t in ("🌐 ভাষা","🌐 Language"): await language_cmd(update,context)
    elif t in ("❓ সাহায্য","❓ Help"): await help_cmd(update,context)
    elif t in ("👥 ব্যবহারকারীর হিসাব","👥 User Hisaab"): await users_cmd(update,context)
    elif t in ("🗑️ হিসাবের History ডিলিট","🗑️ Delete History"): await delete_history_cmd(update,context)
    elif parse(t): await transaction(update,context)

async def post_init(app):
    await app.bot.set_my_commands([
        ("start","Start the bot"),("add","Add a new hisaab"),("list","View your hisaab"),
        ("users","Owner: view user hisaab"),("language","Change language"),
        ("example","See an example"),("help","Help"),("deletehistory","Owner: delete an account and its history"),("cancel","Cancel current action")
    ])

def start_web():
    uvicorn.run(web,host="0.0.0.0",port=int(os.getenv("PORT","10000")))

async def main():
    application=Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start",start))
    application.add_handler(CommandHandler("add",add_cmd))
    application.add_handler(CommandHandler("list",list_cmd))
    application.add_handler(CommandHandler("users",users_cmd))
    application.add_handler(CommandHandler("language",language_cmd))
    application.add_handler(CommandHandler("example",example_cmd))
    application.add_handler(CommandHandler("help",help_cmd))
    application.add_handler(CommandHandler("deletehistory",delete_history_cmd))
    application.add_handler(CommandHandler("cancel",cancel_cmd))
    application.add_handler(CallbackQueryHandler(callbacks))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,text_router))

    # run_polling() is synchronous in python-telegram-bot; do not await it.
    # Start and stop the async application lifecycle explicitly instead.
    await application.initialize()
    await post_init(application)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    import signal
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):
            pass

    try:
        await application.updater.start_polling()
        await application.start()
        logging.info("Telegram bot polling started successfully.")
        await stop_event.wait()
    finally:
        if application.updater and application.updater.running:
            await application.updater.stop()
        if application.running:
            await application.stop()
        await application.shutdown()

if __name__=="__main__":
    Thread(target=start_web,daemon=True).start()
    asyncio.run(main())
