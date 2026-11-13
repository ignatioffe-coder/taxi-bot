"""Small server-side client for YooKassa payments and recurring charges."""

import logging
import uuid

import requests

import config
import subscriptions

logger = logging.getLogger(__name__)
API_URL = "https://api.yookassa.ru/v3"


class YooKassaError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def is_configured() -> bool:
    return bool(config.YOOKASSA_SHOP_ID and config.YOOKASSA_SECRET_KEY)


def _request(method: str, path: str, *, payload: dict | None = None,
             idempotence_key: str | None = None) -> dict:
    if not is_configured():
        raise YooKassaError("YooKassa credentials are not configured")
    headers = {"Content-Type": "application/json"}
    if idempotence_key:
        headers["Idempotence-Key"] = idempotence_key
    try:
        response = requests.request(
            method,
            f"{API_URL}{path}",
            auth=(config.YOOKASSA_SHOP_ID, config.YOOKASSA_SECRET_KEY),
            headers=headers,
            json=payload,
            timeout=(5, 20),
        )
    except requests.RequestException as exc:
        raise YooKassaError("Could not reach YooKassa") from exc
    if not response.ok:
        logger.error("YooKassa API returned HTTP %s", response.status_code)
        raise YooKassaError(
            "YooKassa rejected the request",
            status_code=response.status_code,
        )
    try:
        result = response.json()
    except ValueError as exc:
        raise YooKassaError("YooKassa returned an invalid response") from exc
    if not isinstance(result, dict):
        raise YooKassaError("YooKassa returned an invalid response")
    return result


def create_subscription_payment(
    payment_ref: str,
    user_id: int,
    return_url: str,
    idempotence_key: str,
) -> dict:
    return _request(
        "POST",
        "/payments",
        idempotence_key=idempotence_key,
        payload={
            "amount": {
                "value": f"{subscriptions.MONTHLY_PRICE_RUB:.2f}",
                "currency": "RUB",
            },
            "capture": True,
            "confirmation": {"type": "redirect", "return_url": return_url},
            "description": "Подписка MOVENTA на 30 дней",
            "save_payment_method": True,
            "metadata": {
                "payment_ref": payment_ref,
                "user_id": str(user_id),
                "purpose": "subscription",
            },
        },
    )


def create_recurring_payment(
    payment_ref: str,
    user_id: int,
    payment_method_id: str,
    idempotence_key: str,
) -> dict:
    return _request(
        "POST",
        "/payments",
        idempotence_key=idempotence_key,
        payload={
            "amount": {
                "value": f"{subscriptions.MONTHLY_PRICE_RUB:.2f}",
                "currency": "RUB",
            },
            "capture": True,
            "description": "Автопродление подписки MOVENTA на 30 дней",
            "payment_method_id": payment_method_id,
            "metadata": {
                "payment_ref": payment_ref,
                "user_id": str(user_id),
                "purpose": "subscription_renewal",
            },
        },
    )


def get_payment(payment_id: str) -> dict:
    return _request("GET", f"/payments/{payment_id}")


def new_idempotence_key() -> str:
    return uuid.uuid4().hex
