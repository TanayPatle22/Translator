from django.urls import path
from . import views 

urlpatterns = [
    path('', views.translate_view, name = "translate"),
    path('translate/', views.translate_view, name="translate"), 
    path('download-pdf/', views.download_pdf, name='download_pdf'),
]