from django.contrib import admin
admin.site.site_url = '/contest/'   # "View Site" in admin header → judge competitions list
admin.site.site_header  = 'RoboSTEAM Academy'
admin.site.site_title   = 'RoboSTEAM Admin'
admin.site.index_title  = 'Competition Management'
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
import ckeditor_uploader.urls

urlpatterns = [
    path("admin/", admin.site.urls),
    path('accounts/', include('django.contrib.auth.urls')),
    path('contest/', include("contest.urls")),
    path('chaining/', include('smart_selects.urls')),
    path('devices/', include('devices.urls')),
    path('ckeditor/', include(ckeditor_uploader.urls)),
    path("", include("public.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
