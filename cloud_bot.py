# -*- coding: utf-8 -*-
import os, asyncio, random, re, time, aiohttp, psutil, platform
from telegram import (
    Update, LinkPreviewOptions,
    InlineKeyboardButton, InlineKeyboardMarkup,
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler,
    ContextTypes, filters,
)
from playwright.async_api import async_playwright

# ═══════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN is missing! Please provide it in your setup/environment.")

AUTH_PIN         = os.environ.get("AUTH_PIN", "2602")
LOCKOUT_DURATION = 1800
MAX_PIN_ATTEMPTS = 3

LOCALES = [
    {"locale": "de-DE", "timezone": "Europe/Berlin",
     "geo": {"latitude": 52.5200, "longitude": 13.4050}, "label": "Berlin"},
    {"locale": "de-DE", "timezone": "Europe/Berlin",
     "geo": {"latitude": 48.1351, "longitude": 11.5820}, "label": "Munich"},
    {"locale": "de-DE", "timezone": "Europe/Berlin",
     "geo": {"latitude": 53.5511, "longitude": 9.9937},  "label": "Hamburg"},
    {"locale": "de-DE", "timezone": "Europe/Berlin",
     "geo": {"latitude": 50.9333, "longitude": 6.9500},  "label": "Cologne"},
    {"locale": "de-DE", "timezone": "Europe/Berlin",
     "geo": {"latitude": 50.1109, "longitude": 8.6821},  "label": "Frankfurt"},
    {"locale": "de-AT", "timezone": "Europe/Vienna",
     "geo": {"latitude": 48.2082, "longitude": 16.3738}, "label": "Vienna"},
    {"locale": "de-CH", "timezone": "Europe/Zurich",
     "geo": {"latitude": 47.3769, "longitude": 8.5417},  "label": "Zurich"},
    {"locale": "de-DE", "timezone": "Europe/Berlin",
     "geo": {"latitude": 51.3397, "longitude": 12.3731}, "label": "Leipzig"},
    {"locale": "de-DE", "timezone": "Europe/Berlin",
     "geo": {"latitude": 51.4556, "longitude": 7.0116},  "label": "Dortmund"},
    {"locale": "de-DE", "timezone": "Europe/Berlin",
     "geo": {"latitude": 49.4521, "longitude": 11.0767}, "label": "Nuremberg"},
]

RANDOM_NAME_POOL = [
    "Crown","Scepter","Throne","Guard","Knight","Baron","Lord","Prince",
    "Count","Titan","Pharaoh","Volcano","Emperor","Reeve","Strategist","Paladin",
    "Centurion","General","Monarch","Overlord","Gladiator","Clem","ClemX",
    "ClemZ","ClemK","ClemR","ClemV","ClemT","ClemA","ClemS","ClemG",
    "Joh","JohX","JohZ","JohK","JohR","JohV","JohT","JohA",
    "Santi","SantiX","SantiZ","SantiK","Henri","HenriX","HenriZ",
    "Benji","BenjiX","BenjiZ","KahX","KahZ","KahK","KahR","KahV",
    "Rex","Ares","Thor","Odin","Zeus","Ace","Neo","Max",
    "Wolf","Blade","Storm","Blitz","Nova","Viper","Cobra","Lynx",
    "Ghost","Shade","Rage","Fury","Apex","Zeta","Echo","Flux",
]

(
    AWAIT_AUTH_PIN,
    CONFIG_DASHBOARD,
    EDIT_PIN,
    EDIT_AMOUNT,
    EDIT_UUID,
    EDIT_PREFIX,
    EDIT_CUSTOM_NAMES,
    EDIT_DELAY_MIN,
    EDIT_DELAY_MAX,
    EDIT_BATCH,
    EDIT_PODIUM,
    EDIT_CARPET,
) = range(12)

ACTIVE_ENGINE_TASKS: dict = {}
ACTIVE_CONTEXTS:     dict = {}
ANSWERS_PER_CHAT:    dict = {}
LAST_ROUND_PER_CHAT: dict = {}
AUTH_LOCKOUTS:       dict = {}
ROUND_STATS:         dict = {}

_HTTP_SESSION = None
_CTX_LOCK     = asyncio.Lock()

DEFAULT_CONFIG = {
    "pin":            "",
    "amount":         10,
    "uuid":           None,
    "random_names":   True,
    "prefix":         "Bot",
    "custom_names":   [],
    "antikick":       True,
    "matrix":         True,
    "follower":       False,
    "delay_min":      1.0,
    "delay_max":      2.8,
    "batch_size":     5,
    "podium_enabled": False,
    "podium_slots":   2,
    "chameleon":      False,
    "carpet_enabled": False,
    "carpet_text":    "",
    "geo_spoof":      True,
    "live_ticker":    True,
}

# ═══════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════
async def get_session():
    global _HTTP_SESSION
    if _HTTP_SESSION is None or _HTTP_SESSION.closed:
        connector = aiohttp.TCPConnector(limit=50, ttl_dns_cache=300)
        _HTTP_SESSION = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=12),
        )
    return _HTTP_SESSION

def check_lockout(chat_id):
    now  = time.time()
    data = AUTH_LOCKOUTS.get(chat_id)
    if data and data.get("attempts", 0) >= MAX_PIN_ATTEMPTS:
        remaining = data.get("lockout_until", 0) - now
        if remaining > 0:
            return remaining
        AUTH_LOCKOUTS[chat_id] = {"attempts": 0, "lockout_until": 0}
    return 0.0

def lockout_msg(remaining):
    mins = int(remaining // 60)
    secs = int(remaining % 60)
    return (
        "🔒 <b>Access Locked</b>\n"
        f"PIN incorrect {MAX_PIN_ATTEMPTS}x.\n"
        f"Unlocks in: <code>{mins}m {secs}s</code>"
    )

def escape_html(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def get_cfg(ud):
    cfg = dict(DEFAULT_CONFIG)
    cfg.update({k: v for k, v in ud.items() if k in DEFAULT_CONFIG})
    return cfg

def carpet_names(text, amount, fallback_pool):
    words = text.split()
    if not words:
        return [random.choice(fallback_pool) + "_" + str(random.randint(10, 99)) for _ in range(amount)]
    # Reversed order: Bot 1 gets the last word, last Bot gets the first word.
    # This displays correctly on the lobby screen (late joiners appear higher up).
    reversed_words = list(reversed(words))
    return [reversed_words[i % len(reversed_words)][:15] for i in range(amount)]

def parse_custom_names(raw: str) -> list:
    if "," in raw:
        parts = [n.strip() for n in raw.split(",") if n.strip()]
    else:
        parts = [n.strip() for n in raw.split() if n.strip()]
    return [p[:15] for p in parts]

# ═══════════════════════════════════════════════════════════════════
# DASHBOARD v11 — Mobile-safe: max 2 Buttons per row
# ═══════════════════════════════════════════════════════════════════
def build_dashboard(ud):
    c  = get_cfg(ud)
    ON = "✅"
    OF = "❌"

    pin_val  = c["pin"] if c["pin"] else "—"
    uuid_val = (c["uuid"][:18] + "…") if c["uuid"] else "Auto"

    if c["carpet_enabled"] and c["carpet_text"]:
        names_val = "Carpet: " + c["carpet_text"][:10] + ("…" if len(c["carpet_text"]) > 10 else "")
    elif c["random_names"]:
        names_val = "Random"
    elif c["custom_names"]:
        names_val = " ".join(c["custom_names"][:3]) + ("…" if len(c["custom_names"]) > 3 else "")
    else:
        names_val = "Prefix: " + c["prefix"]

    podium_val = ("Top-" + str(c["podium_slots"])) if c["podium_enabled"] else "Off"

    text = (
        "👑 <b>IMPERIAL CORE  v11</b>\n"
        "─────────────────────\n"
        "🎯 PIN      <code>" + escape_html(pin_val)    + "</code>\n"
        "🤖 Bots     <code>" + str(c["amount"])        + "</code>\n"
        "⏱ Delay    <code>" + str(c["delay_min"]) + " – " + str(c["delay_max"]) + " s</code>\n"
        "📦 Batch    <code>" + str(c["batch_size"])    + "</code>\n"
        "🔍 UUID     <code>" + escape_html(uuid_val)   + "</code>\n"
        "🎭 Names    <code>" + escape_html(names_val)  + "</code>\n"
        "─────────────────────\n"
        "🛡 Anti-Kick   " + (ON if c["antikick"]    else OF) + "\n"
        "⚡ Matrix      " + (ON if c["matrix"]       else OF) + "\n"
        "🐢 Follower    " + (ON if c["follower"]     else OF) + "\n"
        "🎭 Chameleon   " + (ON if c["chameleon"]    else OF) + "\n"
        "🗺 Geo         " + (ON if c["geo_spoof"]    else OF) + "\n"
        "📢 Ticker      " + (ON if c["live_ticker"]  else OF) + "\n"
        "👑 Podium      " + (ON + " " + podium_val if c["podium_enabled"] else OF) + "\n"
        "─────────────────────\n"
        "<i>Setup → 🚀 Launch</i>"
    )

    kb = [
        [InlineKeyboardButton("⚙️  BASE", callback_data="noop")],
        [
            InlineKeyboardButton("🎯 Set PIN",   callback_data="edit_pin"),
            InlineKeyboardButton("🤖 Bots: " + str(c["amount"]), callback_data="edit_amount"),
        ],
        [
            InlineKeyboardButton("⏱ Delay",         callback_data="edit_delay"),
            InlineKeyboardButton("📦 Batch: " + str(c["batch_size"]), callback_data="edit_batch"),
        ],
        [
            InlineKeyboardButton("🔍 UUID",          callback_data="edit_uuid"),
            InlineKeyboardButton("🗑 Clear UUID",  callback_data="clear_uuid"),
        ],

        [InlineKeyboardButton("🎭  NAMES", callback_data="noop")],
        [
            InlineKeyboardButton("🎲 Random " + (ON if c["random_names"] else OF), callback_data="toggle_random_names"),
            InlineKeyboardButton("🏷 Prefix: " + c["prefix"][:6], callback_data="edit_prefix"),
        ],
        [
            InlineKeyboardButton("👥 Custom Names",  callback_data="edit_custom_names"),
            InlineKeyboardButton("🗑 Clear Names", callback_data="clear_custom_names"),
        ],
        [
            InlineKeyboardButton("🧹 Carpet " + (ON if c["carpet_enabled"] else OF), callback_data="toggle_carpet"),
            InlineKeyboardButton("✏️ Carpet Text",  callback_data="edit_carpet"),
        ],

        [InlineKeyboardButton("🔧  FEATURES", callback_data="noop")],
        [
            InlineKeyboardButton("🛡 Anti-Kick " + (ON if c["antikick"]  else OF), callback_data="toggle_antikick"),
            InlineKeyboardButton("⚡ Matrix "    + (ON if c["matrix"]    else OF), callback_data="toggle_matrix"),
        ],
        [
            InlineKeyboardButton("🐢 Follower " + (ON if c["follower"]  else OF), callback_data="toggle_follower"),
            InlineKeyboardButton("🎭 Chameleon " + (ON if c["chameleon"] else OF), callback_data="toggle_chameleon"),
        ],
        [
            InlineKeyboardButton("🗺 Geo "       + (ON if c["geo_spoof"]   else OF), callback_data="toggle_geo"),
            InlineKeyboardButton("📢 Ticker "    + (ON if c["live_ticker"] else OF), callback_data="toggle_ticker"),
        ],

        [InlineKeyboardButton("👑  PODIUM", callback_data="noop")],
        [
            InlineKeyboardButton("👑 Podium " + (ON if c["podium_enabled"] else OF), callback_data="toggle_podium"),
            InlineKeyboardButton("🔢 Slots: " + str(c["podium_slots"]),              callback_data="edit_podium"),
        ],

        [InlineKeyboardButton("📋  PRESETS", callback_data="noop")],
        [
            InlineKeyboardButton("⚡ Fast",      callback_data="preset_fast"),
            InlineKeyboardButton("🕵️ Stealth",  callback_data="preset_stealth"),
        ],
        [
            InlineKeyboardButton("👑 Podium",    callback_data="preset_podium"),
            InlineKeyboardButton("🔄 Reset",     callback_data="reset_config"),
        ],

        [InlineKeyboardButton("─────────────────────", callback_data="noop")],
        [
            InlineKeyboardButton("🚀  LAUNCH", callback_data="launch"),
            InlineKeyboardButton("❌  Cancel", callback_data="cancel"),
        ],
    ]
    return text, InlineKeyboardMarkup(kb)


async def send_dashboard(update, context):
    text, markup = build_dashboard(context.user_data)
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=markup)
    return CONFIG_DASHBOARD

async def refresh_dashboard(query, context):
    text, markup = build_dashboard(context.user_data)
    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════════════
# AUTH
# ═══════════════════════════════════════════════════════════════════
async def start_wizard(update, context):
    chat_id   = update.effective_chat.id
    remaining = check_lockout(chat_id)
    if remaining > 0:
        await update.message.reply_text(lockout_msg(remaining), parse_mode="HTML")
        return ConversationHandler.END

    await execute_nuke(update, context, silent=True)
    context.user_data.clear()
    for k, v in DEFAULT_CONFIG.items():
        context.user_data[k] = v

    await update.message.reply_text(
        "👑 <b>IMPERIAL CORE  v11</b>\n"
        "─────────────────────\n"
        "🔐 Security system active\n"
        "─────────────────────\n"
        "🔑 <b>Send Access PIN:</b>",
        parse_mode="HTML",
    )
    return AWAIT_AUTH_PIN

async def process_auth_pin(update, context):
    chat_id   = update.effective_chat.id
    remaining = check_lockout(chat_id)
    if remaining > 0:
        await update.message.reply_text(lockout_msg(remaining), parse_mode="HTML")
        return ConversationHandler.END

    pin = update.message.text.strip()
    AUTH_LOCKOUTS.setdefault(chat_id, {"attempts": 0, "lockout_until": 0})

    if pin == AUTH_PIN:
        AUTH_LOCKOUTS[chat_id]["attempts"] = 0
        await update.message.reply_text("✅ <b>Access granted</b>", parse_mode="HTML")
        return await send_dashboard(update, context)
    else:
        AUTH_LOCKOUTS[chat_id]["attempts"] += 1
        attempts = AUTH_LOCKOUTS[chat_id]["attempts"]
        if attempts >= MAX_PIN_ATTEMPTS:
            AUTH_LOCKOUTS[chat_id]["lockout_until"] = time.time() + LOCKOUT_DURATION
            await update.message.reply_text(lockout_msg(LOCKOUT_DURATION), parse_mode="HTML")
            return ConversationHandler.END
        left = MAX_PIN_ATTEMPTS - attempts
        await update.message.reply_text(
            "❌ Incorrect PIN  (" + str(attempts) + "/" + str(MAX_PIN_ATTEMPTS) + ")\n"
            "<b>" + str(left) + "</b> attempt(s) left. Try again:",
            parse_mode="HTML",
        )
        return AWAIT_AUTH_PIN

# ═══════════════════════════════════════════════════════════════════
# DASHBOARD CALLBACKS
# ═══════════════════════════════════════════════════════════════════
async def dashboard_callback(update, context):
    query = update.callback_query
    await query.answer()
    data  = query.data
    ud    = context.user_data

    if data == "noop":
        return CONFIG_DASHBOARD

    TOGGLES = {
        "toggle_antikick":     ("antikick",      True),
        "toggle_matrix":       ("matrix",         True),
        "toggle_follower":     ("follower",       False),
        "toggle_random_names": ("random_names",   True),
        "toggle_chameleon":    ("chameleon",      False),
        "toggle_geo":          ("geo_spoof",      True),
        "toggle_ticker":       ("live_ticker",    True),
        "toggle_podium":       ("podium_enabled", False),
        "toggle_carpet":       ("carpet_enabled", False),
    }
    if data in TOGGLES:
        key, default = TOGGLES[data]
        ud[key] = not ud.get(key, default)
        await refresh_dashboard(query, context)
        return CONFIG_DASHBOARD

    if data == "clear_uuid":
        ud["uuid"] = None
        await query.answer("UUID cleared ✓")
        await refresh_dashboard(query, context)
        return CONFIG_DASHBOARD

    if data == "clear_custom_names":
        ud["custom_names"] = []
        await query.answer("Names cleared ✓")
        await refresh_dashboard(query, context)
        return CONFIG_DASHBOARD

    PRESET_MAP = {
        "preset_fast": {
            "random_names": True, "antikick": True, "matrix": True, "follower": False,
            "delay_min": 1.0, "delay_max": 2.8, "batch_size": 5,
            "podium_enabled": False, "chameleon": False, "geo_spoof": True,
            "live_ticker": True, "carpet_enabled": False,
        },
        "preset_stealth": {
            "random_names": True, "antikick": True, "matrix": True, "follower": True,
            "delay_min": 3.5, "delay_max": 6.0, "batch_size": 2,
            "podium_enabled": False, "chameleon": False, "geo_spoof": True,
            "live_ticker": True, "carpet_enabled": False,
        },
        "preset_podium": {
            "random_names": True, "antikick": True, "matrix": True, "follower": False,
            "delay_min": 0.5, "delay_max": 1.5, "batch_size": 5,
            "podium_enabled": True, "podium_slots": 2, "chameleon": False,
            "geo_spoof": True, "live_ticker": True, "carpet_enabled": False,
        },
    }
    if data in PRESET_MAP:
        ud.update(PRESET_MAP[data])
        labels = {"preset_fast": "⚡ Fast", "preset_stealth": "🕵️ Stealth", "preset_podium": "👑 Podium"}
        await query.answer(labels[data] + " loaded!")
        await refresh_dashboard(query, context)
        return CONFIG_DASHBOARD

    if data == "reset_config":
        for k, v in DEFAULT_CONFIG.items():
            ud[k] = v
        await query.answer("🔄 Reset!")
        await refresh_dashboard(query, context)
        return CONFIG_DASHBOARD

    if data == "cancel":
        await query.edit_message_text(
            "❌ <b>Cancelled.</b>\n/start for new setup.",
            parse_mode="HTML"
        )
        return ConversationHandler.END

    if data == "launch":
        c = get_cfg(ud)
        if not c["pin"]:
            await query.answer("⚠️ Set PIN first!", show_alert=True)
            return CONFIG_DASHBOARD
        return await do_launch(query, context, c)

    # Input Dialogs
    c = get_cfg(ud)
    PROMPTS = {
        "edit_pin": (EDIT_PIN,
            "🎯 <b>Game PIN</b>\n"
            "─────────────────────\n"
            "Current: <code>" + (escape_html(c["pin"]) if c["pin"] else "—") + "</code>\n\n"
            "New PIN (4–10 digits):"),
        "edit_amount": (EDIT_AMOUNT,
            "🤖 <b>Bot Amount</b>\n"
            "─────────────────────\n"
            "Current: <code>" + str(c["amount"]) + "</code>\n\n"
            "New amount (1–200):"),
        "edit_uuid": (EDIT_UUID,
            "🔍 <b>Quiz UUID</b>\n"
            "─────────────────────\n"
            "Current: <code>" + (escape_html(c["uuid"]) if c["uuid"] else "Auto") + "</code>\n\n"
            "Enter UUID\nor <code>no</code> for Auto:"),
        "edit_prefix": (EDIT_PREFIX,
            "🏷 <b>Bot Prefix</b>\n"
            "─────────────────────\n"
            "Current: <code>" + escape_html(c["prefix"]) + "</code>\n"
            "Example: <code>" + escape_html(c["prefix"]) + "_1</code>, <code>" + escape_html(c["prefix"]) + "_2</code>\n\n"
            "New prefix (max 10 chars):"),
        "edit_custom_names": (EDIT_CUSTOM_NAMES,
            "👥 <b>Custom Names</b>\n"
            "─────────────────────\n"
            "Current: <code>" + (escape_html(" ".join(c["custom_names"][:4])) if c["custom_names"] else "—") + "</code>\n\n"
            "With space:\n<code>Henri Clemens Santi</code>\n"
            "With comma:\n<code>Henri,Clemens,Santi</code>\n\n"
            "<code>no</code> = keep current"),
        "edit_delay": (EDIT_DELAY_MIN,
            "⏱ <b>Delay</b>\n"
            "─────────────────────\n"
            "Current: <code>" + str(c["delay_min"]) + "s – " + str(c["delay_max"]) + "s</code>\n\n"
            "Enter <b>Min-Delay</b> (e.g. <code>1.0</code>):"),
        "edit_batch": (EDIT_BATCH,
            "📦 <b>Batch Size</b>\n"
            "─────────────────────\n"
            "Current: <code>" + str(c["batch_size"]) + "</code>\n"
            "Bots starting simultaneously.\n\n"
            "New size (1–20):"),
        "edit_podium": (EDIT_PODIUM,
            "👑 <b>Podium Slots</b>\n"
            "─────────────────────\n"
            "Current: <code>Top-" + str(c["podium_slots"]) + "</code>\n\n"
            "How many bots on podium? (1–10):"),
        "edit_carpet": (EDIT_CARPET,
            "🧹 <b>Name Carpet</b>\n"
            "─────────────────────\n"
            "Current: <code>" + (escape_html(c["carpet_text"][:25]) if c["carpet_text"] else "—") + "</code>\n\n"
            "Words with space:\n"
            "<code>CLEMENS RULES HERE</code>\n"
            "Bot1=CLEMENS Bot2=RULES …\n\n"
            "<code>no</code> = keep current"),
    }
    if data in PROMPTS:
        next_state, prompt = PROMPTS[data]
        await query.edit_message_text(
            prompt + "\n\n<i>/cancel = back</i>",
            parse_mode="HTML",
        )
        return next_state

    return CONFIG_DASHBOARD

# ═══════════════════════════════════════════════════════════════════
# INPUT HANDLERS
# ═══════════════════════════════════════════════════════════════════
async def _back(update, context):
    text, markup = build_dashboard(context.user_data)
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=markup)
    return CONFIG_DASHBOARD

async def recv_pin(update, context):
    val = update.message.text.strip()
    if not re.fullmatch(r"\d{4,10}", val):
        await update.message.reply_text("❌ Digits only, 4–10 characters:")
        return EDIT_PIN
    context.user_data["pin"] = val
    await update.message.reply_text(
        "✅ PIN: <code>" + escape_html(val) + "</code>", parse_mode="HTML")
    return await _back(update, context)

async def recv_amount(update, context):
    try:
        val = int(update.message.text.strip())
        if not (1 <= val <= 200): raise ValueError
        context.user_data["amount"] = val
    except ValueError:
        await update.message.reply_text("❌ Number between 1–200:")
        return EDIT_AMOUNT
    await update.message.reply_text(
        "✅ Bots: <code>" + str(val) + "</code>", parse_mode="HTML")
    return await _back(update, context)

async def recv_uuid(update, context):
    val = update.message.text.strip()
    if val.lower() == "no":
        context.user_data["uuid"] = None
        await update.message.reply_text("✅ UUID: <i>Auto Mode</i>", parse_mode="HTML")
    else:
        context.user_data["uuid"] = val
        await update.message.reply_text(
            "✅ UUID: <code>" + escape_html(val[:36]) + "</code>", parse_mode="HTML")
    return await _back(update, context)

async def recv_prefix(update, context):
    val = update.message.text.strip()[:10]
    context.user_data["prefix"]       = val
    context.user_data["random_names"] = False
    await update.message.reply_text(
        "✅ Prefix: <code>" + escape_html(val) + "</code>\n"
        "→ <code>" + escape_html(val) + "_1</code>, <code>" + escape_html(val) + "_2</code> …",
        parse_mode="HTML")
    return await _back(update, context)

async def recv_custom_names(update, context):
    val = update.message.text.strip()
    if val.lower() != "no":
        names = parse_custom_names(val)
        if names:
            context.user_data["custom_names"]  = names
            context.user_data["random_names"]  = False
            preview = " / ".join(names[:5]) + ("…" if len(names) > 5 else "")
            await update.message.reply_text(
                "✅ " + str(len(names)) + " Names:\n<code>" + escape_html(preview) + "</code>",
                parse_mode="HTML")
        else:
            await update.message.reply_text("❌ No valid names detected.")
    return await _back(update, context)

async def recv_delay_min(update, context):
    try:
        val = float(update.message.text.strip().replace(",", "."))
        if not (0.1 <= val <= 30.0): raise ValueError
        context.user_data["delay_min"] = round(val, 1)
    except ValueError:
        await update.message.reply_text("❌ Number 0.1–30.0:")
        return EDIT_DELAY_MIN
    dmin = context.user_data["delay_min"]
    await update.message.reply_text(
        "✅ Min: <code>" + str(dmin) + "s</code>\n"
        "Now enter <b>Max-Delay</b> (≥ " + str(dmin) + "):",
        parse_mode="HTML")
    return EDIT_DELAY_MAX

async def recv_delay_max(update, context):
    dmin = context.user_data.get("delay_min", 0.1)
    try:
        val = float(update.message.text.strip().replace(",", "."))
        if val < dmin: raise ValueError
        context.user_data["delay_max"] = round(val, 1)
    except ValueError:
        await update.message.reply_text("❌ Must be ≥ " + str(dmin) + "s:")
        return EDIT_DELAY_MAX
    await update.message.reply_text(
        "✅ Delay: <code>" + str(dmin) + "s – " + str(context.user_data["delay_max"]) + "s</code>",
        parse_mode="HTML")
    return await _back(update, context)

async def recv_batch(update, context):
    try:
        val = int(update.message.text.strip())
        if not (1 <= val <= 20): raise ValueError
        context.user_data["batch_size"] = val
    except ValueError:
        await update.message.reply_text("❌ Number 1–20:")
        return EDIT_BATCH
    await update.message.reply_text(
        "✅ Batch: <code>" + str(val) + "</code>", parse_mode="HTML")
    return await _back(update, context)

async def recv_podium(update, context):
    try:
        val = int(update.message.text.strip())
        if not (1 <= val <= 10): raise ValueError
        context.user_data["podium_slots"]   = val
        context.user_data["podium_enabled"] = True
    except ValueError:
        await update.message.reply_text("❌ Number 1–10:")
        return EDIT_PODIUM
    await update.message.reply_text(
        "✅ Podium: <code>Top-" + str(val) + "</code>  (enabled)",
        parse_mode="HTML")
    return await _back(update, context)

async def recv_carpet(update, context):
    val = update.message.text.strip()
    if val.lower() != "no" and val:
        context.user_data["carpet_text"]    = val
        context.user_data["carpet_enabled"] = True
        context.user_data["random_names"]   = False
        words   = val.split()
        preview = " | ".join("Bot" + str(i + 1) + "=" + w for i, w in enumerate(words[:4]))
        if len(words) > 4:
            preview += " …"
        await update.message.reply_text(
            "✅ Carpet:\n<code>" + escape_html(preview) + "</code>",
            parse_mode="HTML")
    return await _back(update, context)

async def recv_cancel(update, context):
    return await _back(update, context)

# ═══════════════════════════════════════════════════════════════════
# LAUNCH
# ═══════════════════════════════════════════════════════════════════
async def do_launch(query, context, c):
    chat_id = query.message.chat_id
    config  = {
        "amount":            c["amount"],
        "prefix":            c["prefix"],
        "random_names":      c["random_names"],
        "custom_names_list": c["custom_names"],
        "antikick_enabled":  c["antikick"],
        "matrix_enabled":    c["matrix"],
        "follower_enabled":  c["follower"],
        "delay_min":         c["delay_min"],
        "delay_max":         c["delay_max"],
        "batch_size":        c["batch_size"],
        "podium_enabled":    c["podium_enabled"],
        "podium_slots":      c["podium_slots"],
        "chameleon":         c["chameleon"],
        "geo_spoof":         c["geo_spoof"],
        "live_ticker":       c["live_ticker"],
        "carpet_enabled":    c["carpet_enabled"],
        "carpet_text":       c["carpet_text"],
    }
    engine = TelegramKahootEngine(
        pin=c["pin"], quiz_id=c["uuid"],
        config=config, bot=context.bot, chat_id=chat_id,
    )
    ACTIVE_ENGINE_TASKS[chat_id] = engine
    asyncio.create_task(engine.start_flooding())

    yn  = lambda v: "ON" if v else "OFF"
    txt = (
        "🚀 <b>LAUNCHED!</b>\n"
        "─────────────────────\n"
        "🎯 PIN      <code>" + escape_html(c["pin"])  + "</code>\n"
        "🤖 Bots     <code>" + str(c["amount"])       + "</code>\n"
        "⏱ Delay    <code>" + str(c["delay_min"]) + " – " + str(c["delay_max"]) + "s</code>\n"
        "📦 Batch    <code>" + str(c["batch_size"])   + "</code>\n"
        "─────────────────────\n"
        "🛡 Anti-Kick  <code>" + yn(c["antikick"])  + "</code>\n"
        "⚡ Matrix     <code>" + yn(c["matrix"])    + "</code>\n"
        "👑 Podium     <code>" + (yn(True) + " Top-" + str(c["podium_slots"]) if c["podium_enabled"] else yn(False)) + "</code>\n"
        "🎭 Chameleon  <code>" + yn(c["chameleon"]) + "</code>\n"
        "─────────────────────\n"
        "📡 Live updates to follow.\n"
        "🛑 /stop to terminate"
    )
    try:
        await query.edit_message_text(txt, parse_mode="HTML")
    except Exception:
        await context.bot.send_message(chat_id=chat_id, text=txt, parse_mode="HTML")
    return ConversationHandler.END

# ═══════════════════════════════════════════════════════════════════
# ENGINE
# ═══════════════════════════════════════════════════════════════════
class TelegramKahootEngine:
    def __init__(self, pin, quiz_id, config, bot, chat_id):
        self.pin            = pin
        self.quiz_id        = quiz_id
        self.config         = config
        self.bot            = bot
        self.chat_id        = chat_id
        self.uuid_extracted = bool(quiz_id)
        self.stop_event     = asyncio.Event()

        ANSWERS_PER_CHAT.setdefault(chat_id, {})
        self.answers = ANSWERS_PER_CHAT[chat_id]
        self.answers.clear()
        LAST_ROUND_PER_CHAT[chat_id] = -1

        ROUND_STATS[chat_id] = {"active_bots": 0, "kicked_total": 0, "kicked_round": 0}
        self.stats = ROUND_STATS[chat_id]

    @property
    def last_round(self):
        return LAST_ROUND_PER_CHAT.get(self.chat_id, -1)

    @last_round.setter
    def last_round(self, v):
        LAST_ROUND_PER_CHAT[self.chat_id] = v

    async def send(self, text):
        try:
            await self.bot.send_message(
                chat_id=self.chat_id, text=text,
                parse_mode="HTML",
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
        except Exception as e:
            print("[send]", e)

    async def log_mission_start(self):
        c  = self.config
        yn = lambda v: "ON" if v else "OFF"
        await self.send(
            "👑 <b>MISSION START</b>\n"
            "─────────────────────\n"
            "🎯 PIN      <code>" + escape_html(self.pin)   + "</code>\n"
            "🤖 Bots     <code>" + str(c["amount"])        + "</code>\n"
            "⏱ Delay    <code>" + str(c["delay_min"]) + " – " + str(c["delay_max"]) + "s</code>\n"
            "📦 Batch    <code>" + str(c["batch_size"])    + "</code>\n"
            "─────────────────────\n"
            "⚡ Matrix    <code>" + yn(c["matrix_enabled"])    + "</code>\n"
            "🛡 Anti-Kick <code>" + yn(c["antikick_enabled"])  + "</code>\n"
            "👑 Podium    <code>" + (yn(True) + " Top-" + str(c["podium_slots"]) if c["podium_enabled"] else yn(False)) + "</code>\n"
            "🎭 Chameleon <code>" + yn(c["chameleon"])         + "</code>\n"
            "🗺 Geo       <code>" + yn(c["geo_spoof"])         + "</code>\n"
            "─────────────────────\n"
            "🚀 Booting up instances…"
        )

    async def log_joined(self, bot_id, name):
        self.stats["active_bots"] += 1
        await self.send(
            "✅ Bot <b>#" + str(bot_id) + "</b> joined\n"
            "👤 <code>" + escape_html(name) + "</code>"
        )

    async def log_antikick(self, old, new):
        self.stats["kicked_total"] += 1
        self.stats["kicked_round"] += 1
        await self.send(
            "🚨 <b>Anti-Kick!</b>\n"
            "Old:  <code>" + escape_html(old) + "</code>\n"
            "New:  <code>" + escape_html(new) + "</code>"
        )

    async def log_mission_end(self):
        await self.send(
            "🛑 <b>Mission Terminated</b>\n"
            "─────────────────────\n"
            "All instances closed.\n"
            "👉 /start for new setup"
        )

    async def log_quiz_loaded(self, title, count, source):
        await self.send(
            "🔓 <b>Quiz Loaded</b>\n"
            "─────────────────────\n"
            "📋 Title:  <code>" + escape_html(title) + "</code>\n"
            "❓ Questions: <code>" + str(count)          + "</code>\n"
            "📡 Source: <i>"   + escape_html(source)  + "</i>\n"
            "─────────────────────\n"
            "✅ Answers ready."
        )

    async def log_round(self, current, total, item):
        filled = round((current / total) * 10) if total > 0 else 0
        bar    = "▓" * filled + "░" * (10 - filled)
        pct    = round((current / total) * 100) if total > 0 else 0

        q_type = item.get("type", "quiz") if item else "quiz"
        TYPE_LABEL = {
            "quiz":       "Multi-Choice",
            "true_false": "True/False",
            "open_ended": "Free Text",
            "word_cloud": "Free Text",
        }
        type_label = TYPE_LABEL.get(q_type, "Multi-Choice")

        msg  = "📊 <b>Round " + str(current) + " / " + str(total) + "</b>\n"
        msg += "<code>" + bar + " " + str(pct) + "%</code>  <i>" + type_label + "</i>\n"
        msg += "─────────────────────\n"

        if item:
            q_text = item.get("text", "").strip()
            if len(q_text) > 100:
                q_text = q_text[:97] + "…"
            if q_text:
                msg += "❓ <i>" + escape_html(q_text) + "</i>\n"
                msg += "─────────────────────\n"

            if q_type in ("open_ended", "word_cloud"):
                texts = item.get("correct_texts", [])
                msg += "✅ <b>Answer</b>  (Free Text)\n"
                for a in texts[:3]:
                    msg += "  · <code>" + escape_html(a) + "</code>\n"
            elif q_type == "true_false":
                idx   = item.get("correct_index", 0)
                ans   = item.get("correct_text", "")
                dot   = "🔵" if idx == 0 else "🔴"
                badge = "True · Blue" if idx == 0 else "False · Red"
                msg += "✅ <b>Answer</b>\n"
                msg += dot + "  <code>" + escape_html(ans) + "</code>\n"
                msg += "<i>" + badge + "</i>\n"
            else:
                idx    = item.get("correct_index", -1)
                ans    = item.get("correct_text", "")
                DOTS   = {0: "🔴", 1: "🔵", 2: "🟡", 3: "🟢"}
                BADGES = {0: "Red · Triangle", 1: "Blue · Diamond",
                          2: "Yellow · Circle",  3: "Green · Square"}
                msg += "✅ <b>Answer</b>\n"
                msg += DOTS.get(idx, "⬜") + "  <code>" + escape_html(ans) + "</code>\n"
                msg += "<i>" + BADGES.get(idx, "?") + "</i>\n"
        else:
            msg += "⚠️ <i>No UUID — unknown</i>\n"

        if self.config.get("live_ticker", True):
            chat_str   = str(self.chat_id)
            active     = len(ACTIVE_CONTEXTS.get(chat_str, []))
            kicked_r   = self.stats.get("kicked_round", 0)
            kicked_tot = self.stats.get("kicked_total", 0)
            msg += (
                "─────────────────────\n"
                "🤖 Bots:         <code>" + str(active)     + "</code>\n"
                "🚨 Kicks Round:  <code>" + str(kicked_r)   + "</code>\n"
                "🚨 Kicks Total:  <code>" + str(kicked_tot) + "</code>"
            )
            self.stats["kicked_round"] = 0

        await self.send(msg.rstrip())

    async def fetch_answers(self, uuid, source="Manual Entry"):
        self.answers.clear()
        re_tag = re.compile(r"<[^>]+>")
        try:
            session = await get_session()
            async with session.get("https://create.kahoot.it/rest/kahoots/" + uuid) as res:
                if res.status != 200:
                    await self.send("❌ API " + str(res.status) + " — UUID invalid.")
                    return False

                data      = await res.json()
                questions = data.get("questions", [])
                title     = data.get("title", "Unknown")

                for idx, q in enumerate(questions):
                    q_text  = re_tag.sub("", q.get("question", "")).strip()
                    q_type  = q.get("type", "quiz")
                    choices = q.get("choices", [])
                    c_idx, c_text, c_texts = -1, "", []

                    for i, ch in enumerate(choices):
                        ans        = re_tag.sub("", ch.get("answer", "")).strip()
                        is_correct = ch.get("correct", False)
                        if is_correct:
                            c_idx, c_text = i, ans
                            c_texts.append(ans)
                        elif q_type in ("open_ended", "word_cloud") and ch.get("correct", True):
                            c_texts.append(ans)

                    self.answers[idx] = {
                        "text":          q_text,
                        "type":          q_type,
                        "correct_index": c_idx,
                        "correct_text":  c_text,
                        "correct_texts": c_texts,
                    }

                await self.log_quiz_loaded(title, len(questions), source)

                SHAPES = {0: "🔴", 1: "🔵", 2: "🟡", 3: "🟢"}
                chunk, chunk_len = ["📖 <b>Answer Overview</b>"], 30

                for f_idx, it in self.answers.items():
                    raw_q  = it["text"][:75] + ("…" if len(it["text"]) > 75 else "")
                    line_q = "\n<b>" + str(f_idx + 1) + ".</b> <i>" + escape_html(raw_q) + "</i>"
                    if it["type"] in ("open_ended", "word_cloud"):
                        line_a = "  🟣 " + ", ".join(escape_html(x) for x in it["correct_texts"])
                    elif it["type"] == "true_false":
                        sym    = "🔵 True" if it["correct_index"] == 0 else "🔴 False"
                        line_a = "  ✅ " + sym + "  <code>" + escape_html(it["correct_text"]) + "</code>"
                    else:
                        sym    = SHAPES.get(it["correct_index"], "⬜")
                        line_a = "  ✅ " + sym + "  <code>" + escape_html(it["correct_text"]) + "</code>"
                    entry = line_q + "\n" + line_a
                    if chunk_len + len(entry) > 3800:
                        await self.send("\n".join(chunk))
                        chunk, chunk_len = [], 0
                    chunk.append(entry)
                    chunk_len += len(entry)

                if chunk:
                    await self.send("\n".join(chunk))

                self.uuid_extracted = True
                return True

        except Exception as e:
            await self.send("❌ Error: <code>" + escape_html(str(e)) + "</code>")
        return False

    async def extract_uuid_from_dom(self, page):
        if self.uuid_extracted:
            return
        try:
            uuid = await page.evaluate(
                "() => window.Kahoot?.quizId ?? window.gameState?.quizId ?? null"
            )
            if uuid and len(uuid) > 10:
                await self.fetch_answers(uuid, source="Auto-Espionage")
        except Exception:
            pass

    async def read_player_names_from_dom(self, page):
        try:
            names = await page.evaluate("""
                () => {
                    const sel = [
                        '[data-functional-selector*="player"]',
                        '[class*="player-name"]',
                        '[class*="PlayerName"]',
                        'li[class*="player"] span',
                    ];
                    for (const s of sel) {
                        const nodes = document.querySelectorAll(s);
                        if (nodes.length > 0)
                            return Array.from(nodes).map(n => n.textContent.trim()).filter(t => t.length > 0);
                    }
                    return [];
                }
            """)
            return names or []
        except Exception:
            return []

    # ── v11: Fast Kick Detection via DOM ──
    async def _is_kicked(self, page) -> bool:
        try:
            return bool(await page.evaluate("""
                () => {
                    const body = document.body ? document.body.innerText.toLowerCase() : '';
                    const kickWords = [
                        'kicked', 'removed', 'banned', 'you were removed',
                        'you have been kicked', 'you have been removed'
                    ];
                    return kickWords.some(t => body.includes(t));
                }
            """))
        except Exception:
            return False

    async def scan_and_click(self, page, bot_name, is_elite=False):
        BTN = [
            ["button[data-functional-selector='answer-0']", "div[data-functional-selector='answer-0']"],
            ["button[data-functional-selector='answer-1']", "div[data-functional-selector='answer-1']"],
            ["button[data-functional-selector='answer-2']", "div[data-functional-selector='answer-2']"],
            ["button[data-functional-selector='answer-3']", "div[data-functional-selector='answer-3']"],
        ]
        d_min = self.config.get("delay_min", 1.0)
        d_max = self.config.get("delay_max", 2.8)

        while not self.stop_event.is_set():
            try:
                if not self.uuid_extracted:
                    await self.extract_uuid_from_dom(page)

                screen = await page.evaluate("""
                    () => {
                        const rx = /(\\d+)\\s*(von|\\/|of)\\s*(\\d+)/i;
                        const w  = document.createTreeWalker(
                            document.body, NodeFilter.SHOW_TEXT,
                            { acceptNode: n => n.textContent.length < 30
                                ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP }
                        );
                        let node;
                        while ((node = w.nextNode())) {
                            const m = node.textContent.trim().match(rx);
                            if (m) return {
                                idx:   parseInt(m[1], 10) - 1,
                                cur:   parseInt(m[1], 10),
                                total: parseInt(m[3], 10)
                            };
                        }
                        return { idx: -1, cur: -1, total: -1 };
                    }
                """)

                q_idx = screen.get("idx",   -1)
                cur   = screen.get("cur",   -1)
                total = screen.get("total", -1)

                if q_idx != -1 and q_idx > self.last_round:
                    self.last_round = q_idx
                    await self.log_round(cur, total, self.answers.get(q_idx))

                if self.config["matrix_enabled"] or self.config["follower_enabled"]:
                    visible, locators = [], {}
                    for c_idx, selectors in enumerate(BTN):
                        for sel in selectors:
                            loc = page.locator(sel)
                            if await loc.is_visible() and await loc.is_enabled():
                                visible.append(c_idx)
                                locators[c_idx] = loc
                                break

                    if visible:
                        item        = self.answers.get(q_idx) if q_idx >= 0 else None
                        correct_idx = item.get("correct_index", -1) if item else -1

                        if self.config.get("podium_enabled", False):
                            if is_elite:
                                delay  = random.uniform(0.5, 1.2)
                                target = correct_idx if correct_idx in visible else random.choice(visible)
                            else:
                                delay  = random.uniform(d_max * 0.8, d_max)
                                wrongs = [i for i in visible if i != correct_idx]
                                target = random.choice(wrongs) if wrongs else random.choice(visible)
                        elif self.config["follower_enabled"]:
                            delay  = random.uniform(3.0, 5.5)
                            target = random.choice(visible)
                        else:
                            delay  = random.uniform(d_min, d_max)
                            target = random.choice(visible)

                        await asyncio.sleep(delay)
                        if self.stop_event.is_set():
                            break
                        try:
                            await locators[target].click(timeout=1500)
                            sub = page.locator(
                                "button:has-text('Senden'), "
                                "button:has-text('Submit'), "
                                "button[data-functional-selector='submit-button']"
                            )
                            if await sub.is_visible():
                                await sub.click(timeout=1000)
                        except Exception:
                            pass

                        while visible and not self.stop_event.is_set():
                            await asyncio.sleep(1)
                            still = any([await locators[i].is_visible() for i in visible if i in locators])
                            if not still:
                                break

                    tf = page.locator(
                        "input[placeholder*='Antwort'], input[placeholder*='antwort'], "
                        "input[placeholder*='answer'], input[placeholder*='Answer'], "
                        "input[data-functional-selector='text-input-field']"
                    )
                    if await tf.is_visible() and await tf.is_editable():
                        await asyncio.sleep(random.uniform(2.0, 4.0))
                        if self.stop_event.is_set():
                            break
                        await tf.click()
                        ans_text = "Clemens"
                        if q_idx in self.answers and self.answers[q_idx]["correct_texts"]:
                            ans_text = random.choice(self.answers[q_idx]["correct_texts"])
                        await tf.type(ans_text, delay=random.randint(55, 120))
                        for sel in [
                            "button[type='submit']",
                            "button[data-functional-selector='submit-button']",
                            "button:has-text('Senden')",
                            "button:has-text('Submit')",
                        ]:
                            try:
                                btn = page.locator(sel)
                                if await btn.is_visible():
                                    await btn.click(timeout=1000)
                                    break
                            except Exception:
                                pass
                        while await tf.is_visible() and not self.stop_event.is_set():
                            await asyncio.sleep(1)

                await asyncio.sleep(0.4)

            except Exception:
                await asyncio.sleep(1)

    # ── Launch Bot v11 ──
    async def launch_bot(self, browser, bot_id, start_name, is_elite=False):
        base_name    = start_name
        current_name = start_name
        chat_str     = str(self.chat_id)

        async def _close_ctx(ctx):
            async with _CTX_LOCK:
                lst = ACTIVE_CONTEXTS.get(chat_str, [])
                if ctx in lst:
                    lst.remove(ctx)
            try:
                await ctx.close()
            except Exception:
                pass

        while not self.stop_event.is_set():
            ctx    = None
            rejoin = False
            try:
                locale_cfg = random.choice(LOCALES) if self.config.get("geo_spoof", True) else LOCALES[0]

                ctx = await browser.new_context(
                    viewport={"width": 1024, "height": 768},
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                    locale=locale_cfg["locale"],
                    timezone_id=locale_cfg["timezone"],
                    geolocation=locale_cfg["geo"],
                    permissions=["geolocation"],
                )

                async with _CTX_LOCK:
                    ACTIVE_CONTEXTS.setdefault(chat_str, []).append(ctx)

                page = await ctx.new_page()
                await page.add_init_script(
                    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
                )
                await page.goto("https://kahoot.it/", timeout=60000, wait_until="domcontentloaded")
                if self.stop_event.is_set():
                    return

                pin_input = (
                    page.get_by_placeholder("Spiel-PIN")
                    .or_(page.get_by_placeholder("Game PIN"))
                    .or_(page.locator("input[name='gameId']"))
                )
                await pin_input.wait_for(state="visible", timeout=20000)
                await asyncio.sleep(random.uniform(0.3, 0.9))
                await pin_input.click()
                await pin_input.type(self.pin, delay=random.randint(80, 160))
                await page.keyboard.press("Enter")
                try:
                    await pin_input.wait_for(state="hidden", timeout=6000)
                except Exception:
                    try:
                        await page.locator("button[type='submit']").click(timeout=2000)
                    except Exception:
                        pass

                if self.stop_event.is_set():
                    return

                if self.config.get("chameleon", False):
                    real_names = await self.read_player_names_from_dom(page)
                    if real_names:
                        stolen       = random.choice(real_names)
                        current_name = (stolen + "\u200b")[:15]

                nick_input = (
                    page.get_by_placeholder("Spitzname")
                    .or_(page.get_by_placeholder("Nickname"))
                    .or_(page.locator("input[name='nickname']"))
                )
                await nick_input.wait_for(state="visible", timeout=25000)
                await asyncio.sleep(random.uniform(0.2, 0.7))
                await nick_input.click()
                await nick_input.type(current_name, delay=random.randint(70, 140))
                await page.keyboard.press("Enter")
                try:
                    join_btn = page.locator("button[type='submit']").or_(
                        page.get_by_role("button", name="Los geht's!")
                    ).or_(
                        page.get_by_role("button", name="Enter")
                    )
                    await join_btn.click(timeout=2000)
                except Exception:
                    pass

                await self.log_joined(bot_id, current_name)

                action_task = asyncio.create_task(
                    self.scan_and_click(page, current_name, is_elite=is_elite)
                )

                # ── Anti-Kick Loop ────────────────────────────────
                await asyncio.sleep(4.0)

                while not self.stop_event.is_set():
                    await asyncio.sleep(1.5)
                    if self.config["antikick_enabled"]:
                        try:
                            kicked = await asyncio.wait_for(self._is_kicked(page), timeout=1.5)
                        except (asyncio.TimeoutError, Exception):
                            kicked = False

                        if kicked:
                            old_name     = current_name
                            suffix       = "_" + str(random.randint(10, 99))
                            current_name = base_name[:15 - len(suffix)] + suffix
                            action_task.cancel()
                            await self.log_antikick(old_name, current_name)
                            rejoin = True
                            break

            except Exception:
                pass
            finally:
                if ctx is not None:
                    await _close_ctx(ctx)

            if not rejoin:
                break

    async def start_flooding(self):
        self.last_round = -1
        await self.log_mission_start()

        if self.quiz_id:
            await self.fetch_answers(self.quiz_id, source="Pre-fetch")

        amount = self.config["amount"]
        c      = self.config

        if c.get("carpet_enabled") and c.get("carpet_text"):
            names = carpet_names(c["carpet_text"], amount, RANDOM_NAME_POOL)
        elif c["random_names"]:
            names = [random.choice(RANDOM_NAME_POOL) + "_" + str(random.randint(10, 99)) for _ in range(amount)]
        elif c["custom_names_list"]:
            names = list(c["custom_names_list"][:amount])
            while len(names) < amount:
                names.append(c["prefix"] + "_" + str(len(names) + 1))
        else:
            names = [c["prefix"] + "_" + str(i) for i in range(1, amount + 1)]

        podium_slots = c.get("podium_slots", 2) if c.get("podium_enabled") else 0
        carpet_mode  = c.get("carpet_enabled") and c.get("carpet_text")

        async with async_playwright() as pw:
            browser    = await pw.chromium.launch(headless=True)
            batch_size = c.get("batch_size", 5)
            tasks      = []

            for i, name in enumerate(names, start=1):
                if self.stop_event.is_set():
                    break
                is_elite = (i <= podium_slots)
                tasks.append(asyncio.create_task(
                    self.launch_bot(browser, i, name, is_elite=is_elite)
                ))
                if carpet_mode:
                    await asyncio.sleep(random.uniform(0.8, 1.5))
                elif i % batch_size == 0:
                    await asyncio.sleep(random.uniform(1.5, 2.5))

            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                await browser.close()
            except Exception:
                pass

        await self.log_mission_end()

# ═══════════════════════════════════════════════════════════════════
# EMERGENCY STOP
# ═══════════════════════════════════════════════════════════════════
async def execute_nuke(update, context, silent=False):
    chat_id  = update.effective_chat.id
    chat_str = str(chat_id)

    engine = ACTIVE_ENGINE_TASKS.pop(chat_id, None)
    if engine:
        engine.stop_event.set()

    for ctx in list(ACTIVE_CONTEXTS.get(chat_str, [])):
        try:
            await ctx.close()
        except Exception:
            pass
    ACTIVE_CONTEXTS.pop(chat_str, None)
    context.user_data.clear()

    if not silent:
        await update.message.reply_text(
            "🛑 <b>Emergency Stop</b>\n"
            "─────────────────────\n"
            "✅ Bots stopped.\n"
            "🔄 Config reset.\n"
            "─────────────────────\n"
            "👉 /start for new setup",
            parse_mode="HTML",
        )
    return ConversationHandler.END

# ═══════════════════════════════════════════════════════════════════
# /status
# ═══════════════════════════════════════════════════════════════════
async def cmd_status(update, context):
    chat_id  = update.effective_chat.id
    chat_str = str(chat_id)

    cpu  = psutil.cpu_percent(interval=1)
    ram  = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    try:
        temps  = psutil.sensors_temperatures()
        temp_v = None
        for key in ("coretemp", "cpu_thermal", "acpitz", "k10temp"):
            if key in temps:
                temp_v = temps[key][0].current
                break
        temp_str = str(round(temp_v, 1)) + " °C" if temp_v else "N/A"
    except Exception:
        temp_str = "N/A"

    active_bots = len(ACTIVE_CONTEXTS.get(chat_str, []))
    engine      = ACTIVE_ENGINE_TASKS.get(chat_id)
    mission     = "🟢 Active" if engine and not engine.stop_event.is_set() else "🔴 Inactive"
    kicks_total = ROUND_STATS.get(chat_id, {}).get("kicked_total", 0)

    await update.message.reply_text(
        "🖥 <b>System Monitor</b>\n"
        "─────────────────────\n"
        "🔥 CPU:     <code>" + str(cpu) + "%</code>\n"
        "🌡 Temp:    <code>" + temp_str + "</code>\n"
        "💾 RAM:     <code>" + str(round(ram.percent, 1)) + "%</code>"
        "  (<code>" + str(round(ram.used / 1024**3, 1)) + "/" + str(round(ram.total / 1024**3, 1)) + " GB</code>)\n"
        "💿 Disk:    <code>" + str(round(disk.percent, 1)) + "%</code>"
        "  (<code>" + str(round(disk.free / 1024**3, 1)) + " GB free</code>)\n"
        "─────────────────────\n"
        "🤖 Bots:    <code>" + str(active_bots) + "</code>\n"
        "📡 Status:  " + mission + "\n"
        "🚨 Kicks:   <code>" + str(kicks_total) + "</code>\n"
        "─────────────────────\n"
        "🐍 Python:  <code>" + platform.python_version() + "</code>\n"
        "💻 OS:      <code>" + platform.system() + " " + platform.release() + "</code>",
        parse_mode="HTML",
    )

# ═══════════════════════════════════════════════════════════════════
# /answers
# ═══════════════════════════════════════════════════════════════════
async def cmd_answers(update, context):
    chat_id   = update.effective_chat.id
    remaining = check_lockout(chat_id)
    if remaining > 0:
        await update.message.reply_text(lockout_msg(remaining), parse_mode="HTML")
        return

    if not context.args:
        await update.message.reply_text(
            "❌ <b>No UUID provided</b>\n"
            "Example:\n"
            "<code>/answers 1234abcd-1234-abcd-1234-123456abcdef</code>",
            parse_mode="HTML",
        )
        return

    uuid   = context.args[0].strip()
    re_tag = re.compile(r"<[^>]+>")
    SHAPES = {0: "🔴 Red", 1: "🔵 Blue", 2: "🟡 Yellow", 3: "🟢 Green"}

    await update.message.reply_text(
        "📡 Loading Quiz…\n<code>" + escape_html(uuid) + "</code>",
        parse_mode="HTML",
    )

    try:
        session = await get_session()
        async with session.get("https://create.kahoot.it/rest/kahoots/" + uuid) as res:
            if res.status != 200:
                await update.message.reply_text("❌ Status " + str(res.status), parse_mode="HTML")
                return

            data      = await res.json()
            questions = data.get("questions", [])
            title     = escape_html(data.get("title", "Unknown"))

            lines = [
                "👑 <b>Quiz Extracted</b>",
                "─────────────────────",
                "📋 <b>Title:</b> <code>" + title + "</code>",
                "❓ <b>Questions:</b> " + str(len(questions)),
                "─────────────────────",
            ]

            for idx, q in enumerate(questions):
                q_text  = escape_html(re_tag.sub("", q.get("question", "")).strip())
                q_type  = q.get("type", "quiz")
                choices = q.get("choices", [])

                lines.append("\n<b>" + str(idx + 1) + ".</b> <i>" + q_text + "</i>")
                correct = []
                for c_idx, ch in enumerate(choices):
                    ans = escape_html(re_tag.sub("", ch.get("answer", "")).strip())
                    if ch.get("correct", False):
                        label = ("🔵 True" if c_idx == 0 else "🔴 False") if q_type == "true_false" else SHAPES.get(c_idx, "❓")
                        correct.append("<b>" + ans + "</b>  " + label)
                    elif q_type in ("open_ended", "word_cloud") and ch.get("correct", True):
                        correct.append("<code>" + ans + "</code>")

                lines.append("💡 " + ("  ·  ".join(correct) if correct else "<i>none</i>"))

            full = "\n".join(lines)
            for i in range(0, len(full), 4000):
                await update.message.reply_text(full[i:i + 4000], parse_mode="HTML")

    except Exception as e:
        await update.message.reply_text("❌ <code>" + escape_html(str(e)) + "</code>", parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════════
# SHUTDOWN & MAIN
# ═══════════════════════════════════════════════════════════════════
async def shutdown_hook(app):
    global _HTTP_SESSION
    if _HTTP_SESSION and not _HTTP_SESSION.closed:
        await _HTTP_SESSION.close()

def main():
    print("Starting Imperial Cloud Core v11.0…")
    app = Application.builder().token(TELEGRAM_TOKEN).post_shutdown(shutdown_hook).build()

    wizard = ConversationHandler(
        entry_points=[CommandHandler("start", start_wizard)],
        states={
            AWAIT_AUTH_PIN:    [MessageHandler(filters.TEXT & ~filters.COMMAND, process_auth_pin)],
            CONFIG_DASHBOARD:  [CallbackQueryHandler(dashboard_callback)],
            EDIT_PIN:          [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_pin),
                                CommandHandler("cancel", recv_cancel)],
            EDIT_AMOUNT:       [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_amount),
                                CommandHandler("cancel", recv_cancel)],
            EDIT_UUID:         [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_uuid),
                                CommandHandler("cancel", recv_cancel)],
            EDIT_PREFIX:       [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_prefix),
                                CommandHandler("cancel", recv_cancel)],
            EDIT_CUSTOM_NAMES: [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_custom_names),
                                CommandHandler("cancel", recv_cancel)],
            EDIT_DELAY_MIN:    [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_delay_min),
                                CommandHandler("cancel", recv_cancel)],
            EDIT_DELAY_MAX:    [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_delay_max),
                                CommandHandler("cancel", recv_cancel)],
            EDIT_BATCH:        [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_batch),
                                CommandHandler("cancel", recv_cancel)],
            EDIT_PODIUM:       [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_podium),
                                CommandHandler("cancel", recv_cancel)],
            EDIT_CARPET:       [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_carpet),
                                CommandHandler("cancel", recv_cancel)],
        },
        fallbacks=[
            CommandHandler("stop",   execute_nuke),
            CommandHandler("cancel", recv_cancel),
        ],
        allow_reentry=True,
        per_chat=True,
        per_user=True,
    )

    app.add_handler(wizard)
    app.add_handler(CommandHandler("stop",      execute_nuke))
    app.add_handler(CommandHandler("answers",   cmd_answers))
    app.add_handler(CommandHandler("status",    cmd_status))

    print("Bot running — v11.0 — all features active.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
