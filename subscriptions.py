"""Telegram Stars subscription settings and user-facing terms."""

import secrets

TRIAL_DAYS = 7
TRIAL_SECONDS = TRIAL_DAYS * 24 * 60 * 60
SUBSCRIPTION_PERIOD_SECONDS = 30 * 24 * 60 * 60
MONTHLY_PRICE_STARS = 300
PREMIUM_BOT_URL = "https://t.me/PremiumBot"
INVOICE_PAYLOAD_PREFIX = "taxi_forecast:monthly"

TERMS_TEXT_RU = (
    "Условия MOVENTA\n\n"
    "• Пробный доступ действует 7 дней с момента заполнения профиля и "
    "предоставляется один раз на аккаунт Telegram.\n"
    f"• Подписка стоит {MONTHLY_PRICE_STARS} ⭐ за 30 дней и автоматически "
    "продлевается каждые 30 дней после первой оплаты.\n"
    "• Во время пробного периода списаний нет. Подписку можно оформить сразу "
    "и начать оплаченный период немедленно либо оформить её после окончания "
    "пробного периода.\n"
    "• Отменить автопродление можно в Telegram: Настройки → Мои звёзды → "
    "Подписки. Доступ сохранится до оплаченной даты.\n"
    "• Для вопросов по оплате отправьте команду /paysupport.\n\n"
    "Нажимая кнопку оплаты, пользователь подтверждает согласие с этими условиями."
)


def create_invoice_payload(user_id: int) -> str:
    return f"{INVOICE_PAYLOAD_PREFIX}:{user_id}:{secrets.token_urlsafe(12)}"


def invoice_payload_belongs_to_user(payload: str, user_id: int) -> bool:
    return payload.startswith(f"{INVOICE_PAYLOAD_PREFIX}:{user_id}:")
