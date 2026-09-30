from django.urls import path
from . import views

app_name = 'public'

urlpatterns = [
    path('', views.homepage, name='homepage'),
    path('news/', views.news_list, name='news_list'),
    path('news/<slug:slug>/', views.news_detail, name='news_detail'),
    path('contests/upcoming/', views.announcement_list, name='announcement_list'),
    path('contests/upcoming/<int:pk>/', views.announcement_detail, name='announcement_detail'),
    path('gallery/', views.gallery_list, name='gallery_list'),
    path('gallery/<slug:slug>/', views.gallery_detail, name='gallery_detail'),
    path('pages/<slug:slug>/', views.static_page, name='static_page'),
]
