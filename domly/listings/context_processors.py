from .locations import CITIES, get_selected_city
from .models import ListingReport


def location(request):
    can_moderate = request.user.is_authenticated and (
        request.user.is_moderator or request.user.is_superuser
    )
    return {
        "selected_city": get_selected_city(request),
        "available_cities": CITIES.keys(),
        "pending_report_count": (
            ListingReport.objects.filter(status=ListingReport.Status.PENDING).count()
            if can_moderate
            else 0
        ),
    }
