from django.contrib import admin
from .models import NewsPost, Announcement, StaticPage, GalleryAlbum, GalleryPhoto


@admin.register(NewsPost)
class NewsPostAdmin(admin.ModelAdmin):
    list_display = ('title', 'is_published', 'published_at', 'author')
    list_filter = ('is_published',)
    search_fields = ('title',)
    prepopulated_fields = {'slug': ('title',)}


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ('title', 'event_date', 'location', 'is_published')
    list_filter = ('is_published',)
    ordering = ('event_date',)


@admin.register(StaticPage)
class StaticPageAdmin(admin.ModelAdmin):
    list_display = ('title', 'slug', 'nav_section', 'nav_order', 'is_published')
    list_editable = ('nav_order', 'is_published')
    prepopulated_fields = {'slug': ('title',)}


class GalleryPhotoInline(admin.TabularInline):
    model = GalleryPhoto
    extra = 1


@admin.register(GalleryAlbum)
class GalleryAlbumAdmin(admin.ModelAdmin):
    list_display = ('title', 'event_date', 'is_published')
    prepopulated_fields = {'slug': ('title',)}
    inlines = [GalleryPhotoInline]
