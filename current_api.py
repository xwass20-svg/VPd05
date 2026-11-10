"""Работа с CurrencyLayer / exchangerate.host через endpoint convert."""

from __future__ import annotations

import requests

from config import ACCESS_KEY

# convert у api.exchangerate.host отдаёт HTML-документацию,
# рабочий endpoint для того же ключа — currencylayer.com
CONVERT_URL = "https://api.currencylayer.com/convert"
LIST_URL = "https://api.currencylayer.com/list"

TIMEOUT = 20


class CurrencyApiError(Exception):
    """Ошибка запроса или ответа валютного API."""


def _check_response(data: dict) -> dict:
    if not isinstance(data, dict):
        raise CurrencyApiError("API вернуло неожиданный ответ.")
    if data.get("success") is False:
        error = data.get("error") or {}
        info = error.get("info") or error.get("type") or "Неизвестная ошибка API."
        raise CurrencyApiError(str(info))
    return data


def convert_currency(amount: float, from_currency: str, to_currency: str) -> dict:
    """Конвертация суммы через /convert с access_key."""
    params = {
        "access_key": ACCESS_KEY,
        "from": from_currency.upper(),
        "to": to_currency.upper(),
        "amount": amount,
    }
    try:
        response = requests.get(CONVERT_URL, params=params, timeout=TIMEOUT)
        if response.status_code == 429:
            raise CurrencyApiError(
                "Превышен лимит запросов API (429). Подождите немного и попробуйте снова."
            )
        response.raise_for_status()
        data = response.json()
    except CurrencyApiError:
        raise
    except requests.RequestException as exc:
        raise CurrencyApiError(f"Не удалось связаться с API: {exc}") from exc
    except ValueError as exc:
        raise CurrencyApiError("API вернуло не-JSON ответ.") from exc

    return _check_response(data)


def get_all_supported_currencies() -> dict:
    """Список поддерживаемых валют."""
    params = {"access_key": ACCESS_KEY}
    try:
        response = requests.get(LIST_URL, params=params, timeout=TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        raise CurrencyApiError(f"Не удалось получить список валют: {exc}") from exc
    except ValueError as exc:
        raise CurrencyApiError("API вернуло не-JSON ответ.") from exc

    return _check_response(data)


def get_rate(from_currency: str, to_currency: str) -> float:
    """Курс: сколько единиц to_currency за 1 from_currency."""
    data = convert_currency(1, from_currency, to_currency)
    info = data.get("info") or {}
    quote = info.get("quote")
    if quote is None:
        result = data.get("result")
        if result is None:
            raise CurrencyApiError("В ответе API нет курса.")
        return float(result)
    return float(quote)


def convert_amount(amount: float, from_currency: str, to_currency: str) -> float:
    """Сконвертировать сумму и вернуть числовой результат."""
    data = convert_currency(amount, from_currency, to_currency)
    result = data.get("result")
    if result is None:
        raise CurrencyApiError("В ответе API нет результата конвертации.")
    return float(result)
