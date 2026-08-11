from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

TELEGRAM_MESSAGE_LIMIT = 4096
TELEGRAM_SAFE_MESSAGE_LIMIT = 3900


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.effective_chat is None or update.message is None:
        return

    graph = context.application.bot_data["chat_graph"]
    result = await graph.ainvoke(
        {
            "telegram_user_id": update.effective_user.id,
            "chat_id": update.effective_chat.id,
            "message_text": update.message.text or "",
        }
    )
    response = result.get("final_response") or result.get(
        "response",
        "I could not generate a response.",
    )
    for chunk in split_telegram_message(response):
        await update.message.reply_text(chunk)


def register_handlers(application: Application) -> None:
    commands = [
        "start",
        "help",
        "sources",
        "addsource",
        "sourceconfig",
        "sourcefields",
        "sourcetest",
        "sourcepack",
        "removesource",
        "refresh",
        "memory",
        "forget",
        "resetmemory",
        "resources",
        "runtime",
        "job",
        "refreshreport",
        "trace",
        "step",
        "alerts",
        "research",
        "candidates",
        "signals",
        "researchstatus",
        "sourcehealth",
        "skills",
    ]
    for command in commands:
        application.add_handler(CommandHandler(command, handle_message))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))


def split_telegram_message(text: str) -> list[str]:
    if len(text) <= TELEGRAM_SAFE_MESSAGE_LIMIT:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= TELEGRAM_SAFE_MESSAGE_LIMIT:
            chunks.append(remaining)
            break

        split_at = remaining.rfind("\n\n", 0, TELEGRAM_SAFE_MESSAGE_LIMIT)
        if split_at < TELEGRAM_SAFE_MESSAGE_LIMIT // 2:
            split_at = remaining.rfind("\n", 0, TELEGRAM_SAFE_MESSAGE_LIMIT)
        if split_at < TELEGRAM_SAFE_MESSAGE_LIMIT // 2:
            split_at = TELEGRAM_SAFE_MESSAGE_LIMIT

        chunk = remaining[:split_at].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split_at:].strip()

    return chunks or [""]
