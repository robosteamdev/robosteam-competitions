"""Phase H — per-role walkthrough over real HTTP against the running server.

Not the Django test client: this goes through Daphne, the middleware stack, real
session cookies and real CSRF, so it also proves the things a unit test cannot —
that XFrameOptionsMiddleware is live, that logout is POST-only, that an anonymous
visitor gets boards without controls, and that a Participant is refused the judge
endpoints by the deployed app rather than by a mocked request.

    python walk.py <contest_id> <timed_id> <judged_id> <team_id>
"""
import http.cookiejar
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = 'http://127.0.0.1:8000'
PASSWORD = 'walkthrough-pw-2026'

CONTEST, TIMED, JUDGED, TEAM = (int(a) for a in sys.argv[1:5])

failures = []
checks = 0


def check(label, condition, detail=''):
    global checks
    checks += 1
    if condition:
        print(f'  ok   {label}')
    else:
        print(f'  FAIL {label} {detail}')
        failures.append(label)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Surface 302s instead of following them.

    Following redirects would turn every "anonymous POST bounced to the login
    page" into a 200 and hide exactly what this script is meant to check.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Session:
    """A browser-ish session: cookie jar, CSRF token, redirects left visible."""

    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar), _NoRedirect,
        )

    def _csrf(self):
        for cookie in self.jar:
            if cookie.name == 'csrftoken':
                return cookie.value
        return ''

    def _open(self, request):
        try:
            with self.opener.open(request) as response:
                return (response.status,
                        response.read().decode('utf-8', 'replace'),
                        dict(response.headers))
        except urllib.error.HTTPError as exc:
            return (exc.code,
                    exc.read().decode('utf-8', 'replace'),
                    dict(exc.headers))

    def get(self, path, follow=True):
        """GET, following redirects by default; returns the FINAL status/body."""
        for _ in range(5):
            status, body, headers = self._open(urllib.request.Request(BASE + path))
            if follow and status in (301, 302, 303, 307) and 'Location' in headers:
                path = urllib.parse.urlparse(headers['Location']).path or '/'
                continue
            return status, body, headers
        return status, body, headers

    def post(self, path, data=None, json_body=False):
        """POST without following; returns (status, body, Location or '')."""
        if not self._csrf():
            self.get(path)
        token = self._csrf()
        payload = dict(data or {})
        headers = {'X-CSRFToken': token, 'Referer': BASE + path}
        if json_body:
            body = json.dumps(payload).encode()
            headers['Content-Type'] = 'application/json'
        else:
            payload['csrfmiddlewaretoken'] = token
            body = urllib.parse.urlencode(payload, doseq=True).encode()
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        request = urllib.request.Request(BASE + path, data=body, headers=headers)
        status, text, response_headers = self._open(request)
        return status, text, response_headers.get('Location', '')

    def login(self, username):
        self.get('/accounts/login/')
        return self.post('/accounts/login/', {'username': username, 'password': PASSWORD})


def logged_in(session):
    """The navbar renders a POST logout form only for an authenticated user.

    Don't test for the absence of 'Sign in' — the footer Quick Links carry a
    permanent Sign in link for everyone.
    """
    _, body, _ = session.get('/')
    return "action=\"/accounts/logout/\"" in body or '>Logout<' in body


# ── Anonymous ────────────────────────────────────────────────────────────────
print('\n=== anonymous ===')
anon = Session()

status, body, headers = anon.get('/')
check('homepage 200', status == 200, status)
check('X-Frame-Options header present (new middleware live)',
      headers.get('X-Frame-Options') == 'DENY', headers.get('X-Frame-Options'))

status, body, _ = anon.get('/contest/')
check('competition index public', status == 200, status)

status, board, _ = anon.get(f'/contest/competition_board/{TIMED}')
check('TIMED board readable anonymously', status == 200, status)
check('board renders no judge JS controls', 'startRun(' not in board)
check('board offers no "Queue run for" control', 'Queue run for' not in board)

status, _, _ = anon.get(f'/contest/contest/{CONTEST}/ranking/')
check('overall ranking public', status == 200, status)

status, _, location = anon.post(f'/contest/competition/{TIMED}/run/create', {'team_id': TEAM})
check('anonymous run_create bounced to login',
      status == 302 and '/accounts/login' in location, f'{status} {location}')

# ── Participant ──────────────────────────────────────────────────────────────
print('\n=== participant ===')
part = Session()
part.get('/contest/register/')
status, body, location = part.post('/contest/register/', {
    'username': 'walk_participant', 'email': 'walk_participant@example.invalid',
    'first_name': 'Walk', 'last_name': 'Participant',
    'password': PASSWORD, 'password2': PASSWORD,
    'requested_role': 'participant', 'team_name': 'Walk Participant Team',
    'country': 'BG', 'organisation': 'Walk School', 'consent': 'on',
})
check('participant self-registration accepted (302, no form errors)',
      status == 302, f'{status} {"errorlist" in body and "form errors" or ""}')
check('participant sent to the "you can log in now" page',
      'done' in location or 'pending' not in location, location)

status, _, _ = part.login('walk_participant')
check('participant login accepted (302)', status == 302, status)
check('participant is authenticated (auto-activated, no admin approval)',
      logged_in(part))

status, body, _ = part.get('/contest/participate/')
check('participant dashboard 200', status == 200, status)

status, _, _ = part.post(f'/contest/participate/join/{CONTEST}/')
check('participant can join an OPEN competition', status in (200, 302), status)
_, dash, _ = part.get('/contest/participate/')
check('joined competition shows on dashboard', 'Walk Cup' in dash)

status, _, _ = part.post(f'/contest/competition/{TIMED}/run/create', {'team_id': TEAM})
check('participant refused judge endpoint (403)', status == 403, status)

status, _, _ = part.post(f'/contest/participate/leave/{CONTEST}/')
check('participant can leave', status in (200, 302), status)
_, dash, _ = part.get('/contest/participate/')
check('left competition no longer joined', 'Withdraw' not in dash)

# ── Judge ────────────────────────────────────────────────────────────────────
print('\n=== judge ===')
judge = Session()
status, _, _ = judge.login('walk_judge')
check('judge login accepted (302)', status == 302, status)
check('judge is authenticated', logged_in(judge))

status, board, _ = judge.get(f'/contest/competition_board/{TIMED}')
check('judge sees the board', status == 200, status)
check('judge board renders controls',
      'Queue run for' in board and 'startRun(' in board)

status, body, _ = judge.post(f'/contest/competition/{TIMED}/run/create', {'team_id': TEAM})
check('judge creates a run', status in (200, 302), status)

_, board, _ = judge.get(f'/contest/competition_board/{TIMED}')
run_ids = [int(n) for n in re.findall(r'startRun\((\d+)\)', board)]
run_id = max(run_ids) if run_ids else None
check('created run visible on the board', run_id is not None)

if run_id:
    status, body, _ = judge.post(f'/contest/run/{run_id}/start', json_body=True)
    check('judge starts the run', status == 200 and 'started' in body, f'{status} {body[:80]}')

    status, body, _ = judge.post(f'/contest/run/{run_id}/penalty', {'penalty_seconds': '2.5'})
    check('judge records a penalty on the ACTIVE run', status == 200, f'{status} {body[:80]}')

    status, body, _ = judge.post(f'/contest/run/{run_id}/stop', json_body=True)
    check('judge voids the run', status == 200 and 'voided' in body, f'{status} {body[:80]}')

    status, body, _ = judge.post(f'/contest/run/{run_id}/manual_entry', {
        'manual_time_ms': '12345', 'comment': 'walkthrough manual entry',
    })
    check('judge records a manual result on the voided run',
          status in (200, 302), f'{status} {body[:100]}')
    _, board, _ = judge.get(f'/contest/competition_board/{TIMED}')
    check('manual result appears on the board', '12.345' in board or '12345' in board)

# discard: a fresh PENDING run
judge.post(f'/contest/competition/{TIMED}/run/create', {'team_id': TEAM})
_, board, _ = judge.get(f'/contest/competition_board/{TIMED}')
pending_ids = [int(n) for n in re.findall(r'startRun\((\d+)\)', board)]
if pending_ids:
    discard_id = max(pending_ids)
    status, body, _ = judge.post(f'/contest/run/{discard_id}/discard', json_body=True)
    check('judge discards a PENDING run', status == 200 and 'discarded' in body,
          f'{status} {body[:80]}')

# judged scoring + penalty
judge.post(f'/contest/competition/{JUDGED}/run/create', {'team_id': TEAM})
_, jboard, _ = judge.get(f'/contest/competition_board/{JUDGED}')
jids = [int(n) for n in re.findall(r'startRun\((\d+)\)', jboard)]
if jids:
    jid = max(jids)
    judge.post(f'/contest/run/{jid}/start', json_body=True)
    status, body, _ = judge.post(f'/contest/run/{jid}/score', {'score': '88', 'comment': 'walk'})
    check('judge scores a JUDGED run', status in (200, 302), status)
    status, body, _ = judge.post(f'/contest/run/{jid}/penalty', {'penalty_points': '8'})
    check('judge penalises a JUDGED run', status == 200, f'{status} {body[:80]}')

# ── Organiser ────────────────────────────────────────────────────────────────
print('\n=== organiser ===')
org = Session()
status, _, _ = org.login('walk_org')
check('organiser login accepted (302)', status == 302, status)
check('organiser is authenticated', logged_in(org))

status, body, _ = org.get('/contest/organise/')
check('organiser dashboard 200', status == 200, status)

status, body, _ = org.post('/contest/organise/competition/new/', {
    'name': 'Walk Organised Cup', 'description': 'created by the walkthrough',
    'status': 'OPEN', 'points_table': '{"1": 10, "2": 8, "default": 1}',
})
check('organiser creates a competition', status in (200, 302), status)

_, dash, _ = org.get('/contest/organise/')
new_ids = [int(n) for n in re.findall(r'/contest/organise/competition/(\d+)/', dash)]
new_id = max(new_ids) if new_ids else None
check('new competition listed on the dashboard', 'Walk Organised Cup' in dash)

if new_id:
    for team_name in ('Walk Team One', 'Walk Team Two'):
        status, _, _ = org.post(
            f'/contest/organise/competition/{new_id}/team/new/', {'name': team_name},
        )
        check(f'organiser adds team {team_name!r}', status in (200, 302), status)
    _, detail, _ = org.get(f'/contest/organise/competition/{new_id}/')
    check('both teams listed', 'Walk Team One' in detail and 'Walk Team Two' in detail)

    status, body, _ = org.post(f'/contest/organise/competition/{new_id}/category/new/', {
        'name': 'Walk Category', 'competition_type': 'JUDGED', 'status': 'OPEN',
        'num_laps': '1', 'num_runs': '3', 'timeout_seconds': '60',
    })
    check('organiser adds a category', status in (200, 302), status)

    # The G3 validator must reject a bad points table through the real form.
    status, body, _ = org.post(f'/contest/organise/competition/{new_id}/edit/', {
        'name': 'Walk Organised Cup', 'description': '', 'status': 'OPEN',
        'points_table': '{"first": 10}',
    })
    _, after, _ = org.get(f'/contest/organise/competition/{new_id}/')
    check('bad points table rejected by the form', 'first' not in after)

# ── Admin ────────────────────────────────────────────────────────────────────
print('\n=== admin ===')
admin = Session()
status, _, _ = admin.login('walk_admin')
check('admin login accepted (302)', status == 302, status)
check('admin is authenticated', logged_in(admin))

status, body, _ = admin.get('/admin/')
check('django admin reachable', status == 200, status)

status, body, _ = admin.get(f'/admin/auth/user/?q=walk_pending')
check('admin user list 200', status == 200, status)
check('pending user awaiting approval is listed', 'walk_pending' in body)
match = re.search(r'name="_selected_action" value="(\d+)"', body)
if match:
    status, body, _ = admin.post('/admin/auth/user/', {
        'action': 'approve_users', '_selected_action': match.group(1),
        'index': '0', 'select_across': '0',
    })
    check('admin runs the approve action', status in (200, 302), status)
    _, after, _ = admin.get('/admin/auth/user/?q=walk_pending')
    check('approved user is now active',
          'walk_pending' in after and 'icon-yes' in after)
else:
    check('found the pending user checkbox', False, 'no _selected_action in changelist')

status, body, _ = admin.get('/admin/devices/laptimerdevice/')
check('admin device list 200', status == 200, status)
check('pending walkthrough device visible', 'Walk Timer' in body)
dmatch = re.findall(r'name="_selected_action" value="(\d+)"', body)
if dmatch:
    status, body, _ = admin.post('/admin/devices/laptimerdevice/', {
        'action': 'approve_devices', '_selected_action': dmatch,
        'index': '0', 'select_across': '0',
    })
    check('admin runs the device approve action', status in (200, 302), status)

# ── logout is POST-only ──────────────────────────────────────────────────────
print('\n=== session handling ===')
status, _, _ = admin.get('/accounts/logout/')
check('GET logout refused (POST-only)', status in (403, 405), status)
status, _, _ = admin.post('/accounts/logout/')
check('POST logout accepted', status in (200, 302), status)
check('admin is logged out', not logged_in(admin))

print(f'\n{checks - len(failures)}/{checks} checks passed')
if failures:
    print('FAILED:')
    for name in failures:
        print(f'  - {name}')
    sys.exit(1)
print('WALKTHROUGH PASSED')
