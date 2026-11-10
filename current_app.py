import requests

def get_current_rate(default: str = "USD", currencies: list[str] = ["EUR", "GBP", "JPY"]):
    url = "https://api.exchangerate.host/live"
    params = {
        "access_key": "9c58220595f766b9e1adde8c481bea98",
        "source": default,
        "currencies": ",".join(currencies)
        # ",".join(currencies) означает объединение в строку с разделителем-запятой
    }

    response = requests.get(url, params=params)
    data = response.json()
    return data

def convert_currency(amount, from_currency, to_currency):
    url = "https://api.currencylayer.com/convert"
    params = {
        "access_key": "9c58220595f766b9e1adde8c481bea98",
        "from": from_currency,
        "to": to_currency,
        "amount": amount
    }

    response = requests.get(url, params=params)
    data = response.json()
    return data

def get_all_supported_currencies():
    url = "https://api.exchangerate.host/list"
    params = {
        "access_key": "9c58220595f766b9e1adde8c481bea98"
    }
    response = requests.get(url, params=params)
    data = response.json()
    return data

if __name__ == "__main__":
    print(convert_currency(100, "USD", "EUR"))
