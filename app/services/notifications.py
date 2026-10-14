import smtplib
from email.message import EmailMessage

import requests

from app.config import settings
from app.models import NotificationChannel


def send_email_notification(channel: NotificationChannel, message: str) -> bool:
    if not settings.smtp_host or not settings.smtp_username or not settings.smtp_password:
        return False

    email = EmailMessage()
    email["Subject"] = "TCG Tracker promo alert"
    email["From"] = settings.smtp_from or settings.smtp_username
    email["To"] = channel.target
    email.set_content(message)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
        server.starttls()
        server.login(settings.smtp_username, settings.smtp_password)
        server.send_message(email)
    return True


def send_discord_notification(channel: NotificationChannel, message: str) -> bool:
    if not settings.discord_webhook_url:
        return False
    response = requests.post(settings.discord_webhook_url, json={"content": message}, timeout=15)
    return response.ok


def send_telegram_notification(channel: NotificationChannel, message: str) -> bool:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return False
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    payload = {"chat_id": settings.telegram_chat_id, "text": message}
    response = requests.post(url, data=payload, timeout=15)
    return response.ok


def send_notification(channel: NotificationChannel, message: str) -> bool:
    if channel.channel_type == "email":
        return send_email_notification(channel, message)
    if channel.channel_type == "discord":
        return send_discord_notification(channel, message)
    if channel.channel_type == "telegram":
        return send_telegram_notification(channel, message)
    return False
