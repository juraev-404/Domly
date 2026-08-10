from .locations import CITIES, get_selected_city


def location(request):
    return {
        "selected_city": get_selected_city(request),
        "available_cities": CITIES.keys(),
    }
