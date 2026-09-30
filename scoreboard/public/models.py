from django.db import models
from django.utils.text import slugify
from django.contrib.auth import get_user_model
from ckeditor_uploader.fields import RichTextUploadingField

User = get_user_model()


class NewsPost(models.Model):
    title = models.CharField(max_length=300)
    slug = models.SlugField(max_length=300, unique=True, blank=True)
    body = RichTextUploadingField()
    excerpt = models.CharField(max_length=300, blank=True)
    cover_image = models.ImageField(upload_to='news/', blank=True, null=True)
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    published_at = models.DateTimeField()
    is_published = models.BooleanField(default=False)

    class Meta:
        ordering = ['-published_at']

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title


class Announcement(models.Model):
    title = models.CharField(max_length=300)
    event_date = models.DateField()
    location = models.CharField(max_length=200)
    short_description = models.CharField(max_length=200)
    full_description = RichTextUploadingField(blank=True, default='')
    registration_deadline = models.DateField(null=True, blank=True)
    registration_link = models.URLField(blank=True)
    is_published = models.BooleanField(default=False)

    class Meta:
        ordering = ['event_date']

    def __str__(self):
        return self.title


class StaticPage(models.Model):
    NAV_SECTION_CHOICES = [
        ('participate', 'Participate'),
        ('organize', 'Organize'),
        ('contest', 'Contest'),
        ('none', 'None'),
    ]
    title = models.CharField(max_length=300)
    slug = models.SlugField(max_length=300, unique=True)
    body = RichTextUploadingField()
    nav_section = models.CharField(max_length=20, choices=NAV_SECTION_CHOICES, default='none')
    nav_order = models.IntegerField(default=0)
    is_published = models.BooleanField(default=True)

    class Meta:
        ordering = ['nav_section', 'nav_order']

    def __str__(self):
        return self.title


class GalleryAlbum(models.Model):
    title = models.CharField(max_length=300)
    slug = models.SlugField(max_length=300, unique=True, blank=True)
    contest = models.ForeignKey(
        'contest.Contest', on_delete=models.SET_NULL, null=True, blank=True
    )
    event_date = models.DateField()
    description = models.TextField(blank=True)
    cover_photo = models.ImageField(upload_to='gallery/covers/', blank=True, null=True)
    is_published = models.BooleanField(default=False)

    class Meta:
        ordering = ['-event_date']

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title


class GalleryPhoto(models.Model):
    album = models.ForeignKey(GalleryAlbum, on_delete=models.CASCADE, related_name='photos')
    image = models.ImageField(upload_to='gallery/photos/')
    caption = models.CharField(max_length=300, blank=True)
    order = models.IntegerField(default=0)

    class Meta:
        ordering = ['album', 'order']

    def __str__(self):
        return f"{self.album.title} — {self.caption or self.pk}"
