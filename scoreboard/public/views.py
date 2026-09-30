from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from .models import NewsPost, Announcement, StaticPage, GalleryAlbum


def homepage(request):
    today = timezone.now().date()
    announcements = Announcement.objects.filter(
        is_published=True, event_date__gte=today
    ).order_by('event_date')[:3]
    news_posts = NewsPost.objects.filter(is_published=True).order_by('-published_at')[:3]
    return render(request, 'public/homepage.html', {
        'announcements': announcements,
        'news_posts': news_posts,
    })


def news_list(request):
    posts = NewsPost.objects.filter(is_published=True).order_by('-published_at')
    return render(request, 'public/news_list.html', {'posts': posts})


def news_detail(request, slug):
    post = get_object_or_404(NewsPost, slug=slug, is_published=True)
    return render(request, 'public/news_detail.html', {'post': post})


def announcement_list(request):
    announcements = Announcement.objects.filter(is_published=True)
    return render(request, 'public/announcement_list.html', {'announcements': announcements})


def announcement_detail(request, pk):
    announcement = get_object_or_404(Announcement, pk=pk, is_published=True)
    return render(request, 'public/announcement_detail.html', {'announcement': announcement})


def gallery_list(request):
    albums = GalleryAlbum.objects.filter(is_published=True)
    return render(request, 'public/gallery_list.html', {'albums': albums})


def gallery_detail(request, slug):
    album = get_object_or_404(GalleryAlbum, slug=slug, is_published=True)
    return render(request, 'public/gallery_detail.html', {'album': album})


def static_page(request, slug):
    page = get_object_or_404(StaticPage, slug=slug, is_published=True)
    return render(request, 'public/static_page.html', {'page': page})
