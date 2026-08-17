from django import template

from listings.locations import localize_city_name


register = template.Library()


@register.filter
def city_label(value):
    """Translate a known city for display while preserving its stored value."""
    return localize_city_name(value)
