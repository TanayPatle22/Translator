from django.urls import path
from . import views 

urlpatterns = [
    path('', views.translate_view, name = "translate"),
    path("translate_pdf/", views.translate_pdf_view, name="translate_pdf"),
    path('download-pdf/', views.download_pdf, name='download_pdf'),
]