"""Phase C — login / role UX tests.

Covers: neutral sign-in page (no 'Judge Login' text), role-aware post-login
landing, POST-only logout (Django >=5), and a rendering password-reset page.
"""
from django.contrib.auth.models import Group, User
from django.test import TestCase


def _group(name):
    g, _ = Group.objects.get_or_create(name=name)
    return g


def _user(username, group=None, superuser=False):
    if superuser:
        return User.objects.create_superuser(username, password='pass')
    u = User.objects.create_user(username, password='pass', is_active=True)
    if group:
        u.groups.add(_group(group))
    return u


class LoginPageTest(TestCase):
    def test_login_page_is_role_neutral(self):
        r = self.client.get('/accounts/login/')
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertNotIn('Judge Login', body)
        self.assertIn('Sign in', body)

    def test_login_page_links_request_account_and_reset(self):
        r = self.client.get('/accounts/login/')
        body = r.content.decode()
        self.assertIn('/contest/register/', body)
        self.assertIn('/accounts/password_reset/', body)


class PostLoginRedirectTest(TestCase):
    def test_participant_lands_on_dashboard(self):
        self.client.force_login(_user('p1', group='Participant'))
        r = self.client.get('/contest/post-login/')
        self.assertEqual(r.status_code, 302)
        self.assertIn('/contest/participate/', r['Location'])

    def test_organiser_lands_on_organise_dashboard(self):
        self.client.force_login(_user('o1', group='Organiser'))
        r = self.client.get('/contest/post-login/')
        self.assertEqual(r.status_code, 302)
        self.assertIn('/contest/organise/', r['Location'])

    def test_judge_lands_on_contest_index(self):
        self.client.force_login(_user('j1', group='Judge'))
        r = self.client.get('/contest/post-login/')
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r['Location'].rstrip('/').endswith('/contest'))

    def test_superuser_lands_on_admin(self):
        self.client.force_login(_user('a1', superuser=True))
        r = self.client.get('/contest/post-login/')
        self.assertEqual(r.status_code, 302)
        self.assertIn('/admin/', r['Location'])

    def test_admin_precedence_over_other_groups(self):
        """A superuser who is also an Organiser still lands on /admin/."""
        u = _user('a2', group='Organiser', superuser=False)
        u.is_superuser = True
        u.save()
        self.client.force_login(u)
        r = self.client.get('/contest/post-login/')
        self.assertIn('/admin/', r['Location'])


class LogoutMethodTest(TestCase):
    def setUp(self):
        self.user = _user('logoutuser')

    def test_get_logout_not_allowed(self):
        self.client.force_login(self.user)
        r = self.client.get('/accounts/logout/')
        self.assertEqual(r.status_code, 405)

    def test_post_logout_logs_out_and_redirects(self):
        self.client.force_login(self.user)
        r = self.client.post('/accounts/logout/')
        self.assertEqual(r.status_code, 302)
        # subsequent request is anonymous
        r2 = self.client.get('/contest/post-login/')
        self.assertEqual(r2.status_code, 302)
        self.assertIn('/accounts/login/', r2['Location'])


class PasswordResetPageTest(TestCase):
    def test_password_reset_form_renders(self):
        r = self.client.get('/accounts/password_reset/')
        self.assertEqual(r.status_code, 200)
        self.assertIn('Reset your password', r.content.decode())
