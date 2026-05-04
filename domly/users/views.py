from django.shortcuts import render

def login_view(request):
    return render(request, "base.html")

def register_view(request):
    return render(request, "base.html")

def logout_view(request):
    return render(request, "base.html") 
