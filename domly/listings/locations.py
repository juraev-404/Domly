CITIES = {
    "Душанбе": {"latitude": 38.5598, "longitude": 68.7870, "zoom": 12},
    "Худжанд": {"latitude": 40.2833, "longitude": 69.6222, "zoom": 12},
    "Бохтар": {"latitude": 37.8364, "longitude": 68.7803, "zoom": 13},
    "Куляб": {"latitude": 37.9146, "longitude": 69.7845, "zoom": 13},
    "Истаравшан": {"latitude": 39.9142, "longitude": 69.0033, "zoom": 13},
    "Турсунзаде": {"latitude": 38.5108, "longitude": 68.2303, "zoom": 13},
    "Пенджикент": {"latitude": 39.4952, "longitude": 67.6093, "zoom": 13},
    "Хорог": {"latitude": 37.4917, "longitude": 71.5558, "zoom": 13},
}

DEFAULT_CITY = "Душанбе"
CITY_SESSION_KEY = "selected_city"


def get_selected_city(request):
    city = request.session.get(CITY_SESSION_KEY, DEFAULT_CITY)
    return city if city in CITIES else DEFAULT_CITY
