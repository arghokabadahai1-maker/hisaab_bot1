# Hisaab Bot — GREEDY REX

## Features
- Telegram Python bot
- MongoDB storage
- Owner-only administration
- Permission request with Approve/Reject
- User accounts are private from other users and from users to Owner accounts
- Owner can inspect approved users
- `Name, amount` adds or subtracts money
- Every transaction returns the current account total
- Languages: 🇮🇳 বাংলা and 🇮🇳 हिन्दी (actual language is Hinglish)
- Render Web Service health endpoint: `/health`
- Creator credit: **GREEDY REX**

## Render
Build Command:
`pip install -r requirements.txt`

Start Command:
`python bot.py`

Health Check Path:
`/health`

## Environment Variables
`BOT_TOKEN` = Telegram BotFather token
`OWNER_ID` = owner's numeric Telegram user ID
`MONGO_URI` = MongoDB connection string
`DB_NAME` = `hisaab_bot`

Do not upload real secrets or `.env` files to GitHub.
