import datetime
from django.test import TestCase
from django.contrib.admin.sites import site as admin_site
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.core.management import call_command

from .models import NewsPost, Announcement, StaticPage, GalleryAlbum, GalleryPhoto

User = get_user_model()


class NewsPostModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('npuser', password='pass')

    def test_slug_auto_generated(self):
        post = NewsPost.objects.create(
            title='My Test Post', body='<p>x</p>',
            published_at=timezone.now(), is_published=True, author=self.user,
        )
        self.assertEqual(post.slug, 'my-test-post')

    def test_str(self):
        self.assertEqual(str(NewsPost(title='Hello World')), 'Hello World')

    def test_ordering_newest_first(self):
        p1 = NewsPost.objects.create(
            title='Old', body='x', slug='old-post',
            published_at=datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc),
            is_published=True, author=self.user,
        )
        p2 = NewsPost.objects.create(
            title='New', body='x', slug='new-post',
            published_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
            is_published=True, author=self.user,
        )
        posts = list(NewsPost.objects.all())
        self.assertEqual(posts[0], p2)
        self.assertEqual(posts[1], p1)


class AnnouncementModelTest(TestCase):
    def test_str(self):
        self.assertEqual(str(Announcement(title='Spring Cup')), 'Spring Cup')

    def test_ordering_by_event_date(self):
        a1 = Announcement.objects.create(
            title='Later', event_date=datetime.date(2027, 6, 1),
            location='Bratislava', short_description='test'
        )
        a2 = Announcement.objects.create(
            title='Earlier', event_date=datetime.date(2027, 3, 1),
            location='Sofia', short_description='test'
        )
        items = list(Announcement.objects.all())
        self.assertEqual(items[0], a2)
        self.assertEqual(items[1], a1)


class StaticPageModelTest(TestCase):
    def test_str(self):
        self.assertEqual(str(StaticPage(title='Rules')), 'Rules')

    def test_ordering_by_section_and_order(self):
        p1 = StaticPage.objects.create(
            title='B', slug='b-page', body='x', nav_section='organize', nav_order=2
        )
        p2 = StaticPage.objects.create(
            title='A', slug='a-page', body='x', nav_section='organize', nav_order=1
        )
        pages = list(StaticPage.objects.all())
        self.assertEqual(pages[0], p2)
        self.assertEqual(pages[1], p1)


class GalleryModelTest(TestCase):
    def test_album_slug_auto_generated(self):
        album = GalleryAlbum.objects.create(
            title='Spring Cup 2026', event_date=datetime.date(2026, 3, 15)
        )
        self.assertEqual(album.slug, 'spring-cup-2026')

    def test_album_str(self):
        self.assertEqual(str(GalleryAlbum(title='My Album')), 'My Album')

    def test_photo_str(self):
        album = GalleryAlbum.objects.create(
            title='Test Album', event_date=datetime.date(2026, 1, 1)
        )
        photo = GalleryPhoto.objects.create(
            album=album, image='test.jpg', caption='Nice photo'
        )
        self.assertIn('Nice photo', str(photo))


class PublicViewsTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('viewtestuser', password='pass')
        NewsPost.objects.create(
            title='Published Post', slug='published-post', body='<p>x</p>',
            published_at=timezone.now(), is_published=True, author=self.user,
        )
        NewsPost.objects.create(
            title='Draft Post', slug='draft-post', body='<p>x</p>',
            published_at=timezone.now(), is_published=False, author=self.user,
        )
        self.ann = Announcement.objects.create(
            title='Spring Cup', event_date=datetime.date(2027, 3, 15),
            location='Sofia', short_description='test', is_published=True
        )
        StaticPage.objects.create(
            title='Rules', slug='general-rules', body='<p>rules</p>',
            nav_section='contest', is_published=True
        )
        StaticPage.objects.create(
            title='Draft', slug='draft-page', body='<p>x</p>',
            nav_section='none', is_published=False
        )
        GalleryAlbum.objects.create(
            title='Published Album', slug='published-album',
            event_date=datetime.date(2026, 3, 15), is_published=True
        )

    def test_homepage_200(self):
        self.assertEqual(self.client.get('/').status_code, 200)

    def test_news_list_200(self):
        self.assertEqual(self.client.get('/news/').status_code, 200)

    def test_news_detail_200(self):
        self.assertEqual(self.client.get('/news/published-post/').status_code, 200)

    def test_news_detail_404_unpublished(self):
        self.assertEqual(self.client.get('/news/draft-post/').status_code, 404)

    def test_news_detail_404_missing(self):
        self.assertEqual(self.client.get('/news/nonexistent/').status_code, 404)

    def test_announcement_list_200(self):
        self.assertEqual(self.client.get('/contests/upcoming/').status_code, 200)

    def test_announcement_detail_200(self):
        self.assertEqual(
            self.client.get(f'/contests/upcoming/{self.ann.pk}/').status_code, 200
        )

    def test_gallery_list_200(self):
        self.assertEqual(self.client.get('/gallery/').status_code, 200)

    def test_gallery_detail_200(self):
        self.assertEqual(self.client.get('/gallery/published-album/').status_code, 200)

    def test_static_page_200(self):
        self.assertEqual(self.client.get('/pages/general-rules/').status_code, 200)

    def test_static_page_404_unpublished(self):
        self.assertEqual(self.client.get('/pages/draft-page/').status_code, 404)

    def test_static_page_404_missing(self):
        self.assertEqual(self.client.get('/pages/does-not-exist/').status_code, 404)


class PublicAdminTest(TestCase):
    def test_models_registered(self):
        self.assertTrue(admin_site.is_registered(NewsPost))
        self.assertTrue(admin_site.is_registered(Announcement))
        self.assertTrue(admin_site.is_registered(StaticPage))
        self.assertTrue(admin_site.is_registered(GalleryAlbum))


class SeedContentIdempotencyTest(TestCase):
    def test_seed_twice_no_duplicates(self):
        call_command('seed_content', verbosity=0)
        count_pages = StaticPage.objects.count()
        count_news = NewsPost.objects.count()
        count_ann = Announcement.objects.count()
        count_albums = GalleryAlbum.objects.count()

        call_command('seed_content', verbosity=0)
        self.assertEqual(StaticPage.objects.count(), count_pages)
        self.assertEqual(NewsPost.objects.count(), count_news)
        self.assertEqual(Announcement.objects.count(), count_ann)
        self.assertEqual(GalleryAlbum.objects.count(), count_albums)
