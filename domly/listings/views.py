from django.shortcuts import render
from django.contrib.auth.decorators import login_required


@login_required
def create_listing(request):
    return render(request, "listings/create.html")

def listing_list(request):
    return render(request, "base.html")
