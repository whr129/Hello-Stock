from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters


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
    await update.message.reply_text(
        result.get("final_response") or result.get("response", "I could not generate a response.")
    )


def register_handlers(application: Application) -> None:
    commands = [
        "start",
        "help",
        "stocks",
        "sources",
        "addsource",
        "sourceconfig",
        "sourcefields",
        "sourcetest",
        "removesource",
        "refresh",
        "memory",
        "forget",
        "resetmemory",
        "runtime",
        "job",
        "trace",
        "step",
        "alerts",
        "research",
        "candidates",
        "signals",
        "researchstatus",
        "skills",
    ]
    for command in commands:
        application.add_handler(CommandHandler(command, handle_message))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
