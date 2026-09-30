import datetime
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.utils import timezone

IMG_PH = (
    '<div class="img-placeholder" style="background:#e8f4f7;border:2px dashed #2d8fa5;'
    'border-radius:8px;padding:40px;text-align:center;color:#2d8fa5;margin:1rem 0;">'
    '&#128247; Image coming soon &mdash; upload via Admin &rarr; Edit &rarr; Image button'
    '</div>'
)

VID_PH = (
    '<div class="video-placeholder" style="background:#0f1728;border:2px dashed #f5a623;'
    'border-radius:8px;padding:60px;text-align:center;color:#f5a623;margin:1rem 0;">'
    '&#127916; Video coming soon &mdash; paste YouTube/Vimeo embed code here via Admin &rarr; Edit'
    '</div>'
)


class Command(BaseCommand):
    help = 'Seed initial public website content (idempotent — safe to run twice)'

    def handle(self, *args, **options):
        User = get_user_model()
        admin_user = User.objects.filter(is_superuser=True).first()
        self._seed_static_pages()
        self._seed_news(admin_user)
        self._seed_announcements()
        self._seed_gallery()
        self.stdout.write(self.style.SUCCESS('Public website content seeded successfully.'))

    def _seed_static_pages(self):
        from public.models import StaticPage

        # Pages that keep placeholder content (get_or_create — don't overwrite if admin edited)
        placeholder_pages = [
            {
                'slug': 'participate',
                'title': 'How to Participate',
                'nav_section': 'participate',
                'nav_order': 1,
                'body': (
                    '<h2>How to Participate</h2>'
                    '<p>RoboSTEAM competitions are open to student teams from partner schools across Bulgaria, Slovakia, and Romania.</p>'
                    '<h3>Step 1: Find your local organiser</h3>'
                    '<p>Contact the RoboSTEAM coordinator at your school. They will register your school and provide access to the competition platform.</p>'
                    '<h3>Step 2: Form a team</h3>'
                    '<p>Gather 2&ndash;4 students interested in robotics or programming.</p>'
                    '<h3>Step 3: Build your robot</h3>'
                    '<p>Follow the <a href="/pages/how-to-build-robot/">How to Build a Robot</a> guide.</p>'
                    + IMG_PH +
                    '<h3>Step 4: Register and compete!</h3>'
                    '<p>Your organiser will provide a registration link when a competition is announced.</p>'
                ),
            },
            {
                'slug': 'register',
                'title': 'Register',
                'nav_section': 'participate',
                'nav_order': 5,
                'body': (
                    '<h2>Register Your Team</h2>'
                    '<p>Team registration is managed by your local organiser. Steps:</p>'
                    '<ol><li>Contact your local organiser (teacher or school coordinator).</li>'
                    '<li>Your organiser registers your school and receives login credentials.</li>'
                    '<li>Once registered, your team appears in the competition system.</li></ol>'
                    '<p><strong>No local organiser yet?</strong> Contact the RoboSTEAM project team through Erasmus+ project channels.</p>'
                ),
            },
            {
                'slug': 'video-scoreboard',
                'title': 'Video – Scoreboard Application',
                'nav_section': 'organize',
                'nav_order': 6,
                'body': (
                    '<h2>Video: Scoreboard Application</h2>'
                    '<p>Watch this tutorial to learn how to use the RoboSTEAM Competition Scoreboard.</p>'
                    + VID_PH +
                    '<p><em>Instructional video coming soon. Contact your local organiser for a live demonstration.</em></p>'
                ),
            },
            {
                'slug': 'privacy-policy',
                'title': 'Privacy Policy',
                'nav_section': 'none',
                'nav_order': 0,
                'body': (
                    '<h2>Privacy Policy</h2>'
                    '<p><em>Placeholder text &mdash; final wording to be supplied by the RoboSTEAM project team.</em></p>'
                    '<p>When you register for an account on the RoboSTEAM Academy competition platform, '
                    'we collect the personal data you provide (name, email address, organisation, country, '
                    'and &mdash; for participants &mdash; team name). This data is used solely to operate '
                    'competitions: authenticating your account, displaying results, and communicating with you '
                    'about events.</p>'
                    '<p>Your data is processed under the Erasmus+ project KA220-VET-7CF4F308 and is not shared '
                    'with third parties outside the project partnership (Bulgaria, Slovakia, Romania). '
                    'You may request deletion of your account and associated personal data at any time via '
                    'the &ldquo;Close your account&rdquo; option or by contacting your local organiser.</p>'
                ),
            },
        ]

        for page_data in placeholder_pages:
            obj, created = StaticPage.objects.get_or_create(
                slug=page_data['slug'],
                defaults={
                    'title': page_data['title'],
                    'nav_section': page_data['nav_section'],
                    'nav_order': page_data['nav_order'],
                    'body': page_data['body'],
                    'is_published': True,
                }
            )
            action = 'Created' if created else 'Skipped (exists)'
            self.stdout.write(f'  {action}: {page_data["slug"]}')

        # Pages updated with real WP content (update_or_create — always apply latest content)
        real_pages = [
            {
                'slug': 'general-rules',
                'title': 'General Rules',
                'nav_section': 'contest',
                'nav_order': 1,
                'body': (
                    '<h2>General information</h2>'
                    '<p>RoboSTEAM competitions are educational events where student teams test their autonomous robots against structured challenges. The rules below apply to all categories unless a category-specific rule overrides them.</p>'

                    '<h2>Team definitions</h2>'
                    '<ul>'
                    '<li>Each team consists of 2&ndash;4 students supervised by a teacher or mentor.</li>'
                    '<li>Each school may nominate a maximum of three 3-member teams per sub-category.</li>'
                    '<li>Teams must be registered by their local organiser before the competition.</li>'
                    '<li>The competitive team must be nominated based on the results of a school-level round.</li>'
                    '</ul>'

                    '<h2>Responsibility</h2>'
                    '<ul>'
                    '<li>Teams are responsible for the safe operation of their robot at all times.</li>'
                    '<li>Teams must comply with all organiser instructions regarding safety and health protection.</li>'
                    '<li>Robots that damage the arena or pose a safety risk will be disqualified immediately.</li>'
                    '<li>Unsportsmanlike conduct results in immediate disqualification.</li>'
                    '</ul>'

                    '<h2>Competition documents</h2>'
                    '<p>Before competing, teams must submit:</p>'
                    '<ul>'
                    '<li>Completed registration form (submitted by the organiser).</li>'
                    '<li>Technical data sheet for their robot (dimensions, weight, microcontroller type).</li>'
                    '</ul>'

                    '<h2>Robot rules</h2>'
                    '<ul>'
                    '<li>Maximum dimensions: 30 cm × 30 cm × 30 cm</li>'
                    '<li>Maximum weight: 2 kg</li>'
                    '<li>Must use a Raspberry Pi Pico W or compatible microcontroller.</li>'
                    '<li>No external communication during a run (except competition server MQTT commands).</li>'
                    '<li>Robots are subject to technical inspection before the competition begins.</li>'
                    '</ul>'

                    '<h2>Surprise rule</h2>'
                    '<p>Organisers may introduce a surprise element (e.g., a modified track section or an additional obstacle) announced on the day of competition. Teams are given equal time to adapt.</p>'

                    '<h2>Tournament format</h2>'
                    '<ul>'
                    '<li><strong>Timed categories</strong> (e.g., Line Following, Autonomous Task): lowest time wins. Penalties add seconds for infractions.</li>'
                    '<li><strong>Judged categories</strong> (e.g., Object Manipulation, Master Control): judges award a score from 1–100.</li>'
                    '<li>Each team receives a fixed number of runs per category (configurable by the organiser, typically 3).</li>'
                    '<li>The <strong>best run</strong> counts as the team’s result for the category ranking.</li>'
                    '</ul>'

                    '<h2>Robot ride (diploma criteria)</h2>'
                    '<p>Diplomas are awarded based on the percentage of the maximum possible points achieved in the best robot run:</p>'
                    '<table class="table table-bordered">'
                    '<thead><tr><th>% of total points (best robot ride)</th><th>Certificate</th></tr></thead>'
                    '<tbody>'
                    '<tr><td>&lt; 25%</td><td>Participation</td></tr>'
                    '<tr><td>25–50%</td><td>Bronze diploma</td></tr>'
                    '<tr><td>50–75%</td><td>Silver diploma</td></tr>'
                    '<tr><td>&gt; 75%</td><td>Golden diploma</td></tr>'
                    '</tbody>'
                    '</table>'

                    '<h2>Rights and obligations</h2>'
                    '<ul>'
                    '<li>Teams have the right to appeal against a jury decision (in writing, within 24 hours).</li>'
                    '<li>Teams must familiarise themselves with the Competition Rules and comply with them.</li>'
                    '<li>Participation in the competition itself is free; teams pay for their own travel, food, and accommodation.</li>'
                    '</ul>'
                ),
            },
            {
                'slug': 'competition-and-organization',
                'title': 'Competition & Organization',
                'nav_section': 'organize',
                'nav_order': 2,
                'body': (
                    '<ul>'
                    '<li><em>Can I submit one robot to more than one category?</em><br>Yes you can.</li>'
                    '<li><em>Can I compete with the same robot for several years?</em><br>Yes you can.</li>'
                    '<li><em>Is it possible to agree on a testing date? Will we be able to try out the track?</em><br>'
                    'Yes, if there is sufficient interest, we plan 2–3 test days when the tracks are open to the public. '
                    'The exact date is published on the website. In addition, the track will be available for testing '
                    'the day before the competition.</li>'
                    '<li><em>I probably won’t be able to finish the job, so I’d rather not come.</em><br>'
                    'It often happens that a participant arrives who does not even start after all. The important thing '
                    'is to come and try. You don’t have to win — at least you will gain experience and win next year.</li>'
                    '<li><em>What is covered and what do I have to pay for myself?</em><br>'
                    'Participation in the competition itself is free and competitors are provided with a small snack. '
                    'However, you pay for travel, food and accommodation yourself.</li>'
                    '</ul>'
                ),
            },
            {
                'slug': 'judge-guide',
                'title': 'Judge Guide',
                'nav_section': 'organize',
                'nav_order': 3,
                'body': (
                    '<p><strong>Before the competition</strong><br>'
                    'During registration you already picked the category where you want to be a judge. The next step is '
                    'to read and understand the general and age group rules. Here we tried to gather everything else, '
                    'focusing on how to be a great and confident judge.</p>'

                    '<p><strong>Judge meetings</strong><br>'
                    'We will let you know about the exact date of the online judge meeting later in the season. '
                    'In-person judge meetings will take place on the competition day, in the morning. If there are any '
                    'teams in the competition with whom you have any connection (you coached, mentored them; they are '
                    'relatives or close friends) let the head judges know and they will make sure not to assign you to '
                    'score those teams.</p>'

                    '<p><strong>Judge attitude</strong><br>'
                    'As a judge you are not merely responsible for providing a fair and just competition environment, '
                    'but you are also responsible to make the competition a positive experience, provide comfort when '
                    'needed.</p>'
                    '<ul>'
                    '<li>Try to enjoy the event and be as enthusiastic as possible.</li>'
                    '<li>Take all questions seriously. Make sure all questions get answered.</li>'
                    '<li>Speak with the children. Listen when they have something to say.</li>'
                    '<li>Comfort and support team members when something doesn’t go the way they wanted.</li>'
                    '<li>In a 50-50% disputable situation, rule in favour of the team.</li>'
                    '</ul>'

                    '<p><strong>Judge tasks at the competition</strong><br>'
                    'There will be at least one head judge present at the national final. There will be at least two '
                    'judges at each competition table to ensure fairness. The pairings will be announced before the '
                    'competition day. The exact scoring procedure will be discussed at the judge meetings.</p>'
                ),
            },
            {
                'slug': 'call-for-participation',
                'title': 'Call for Participation',
                'nav_section': 'participate',
                'nav_order': 4,
                'body': (
                    '<h2>Would you like to volunteer at RoboSteam?</h2>'
                    '<p>We are looking for volunteers who are:</p>'
                    '<ul>'
                    '<li>Over 15 years old</li>'
                    '<li>Enthusiastic about digital innovations</li>'
                    '<li>At least a basic English-speaker</li>'
                    '<li>Interested in how young people (8–19 years) find innovative solutions</li>'
                    '<li>Keen on taking part in an innovative event combining sports excitement with robotics</li>'
                    '<li>Happy to be part of a great community</li>'
                    '</ul>'
                    '<p><strong>Then apply now to be a member of our RoboSteam team!</strong></p>'
                ),
            },
            {
                'slug': 'what-is-a-competition',
                'title': 'What is a Competition',
                'nav_section': 'participate',
                'nav_order': 3,
                'body': (
                    '<p><strong>Mission of the competition:</strong></p>'
                    '<ul>'
                    '<li>Motivate students to study technical fields and STEM subjects.</li>'
                    '<li>Present young creators with their own ideas and solutions in front of potential employers.</li>'
                    '<li>Teach students to discuss with technical experts and develop critical thinking and creativity.</li>'
                    '<li>Gain experience from working in a creative, competitive but friendly environment.</li>'
                    '</ul>'
                    '<hr>'
                    '<p><strong>Competition structure</strong></p>'
                    '<ul>'
                    '<li>The RoboSTEAM competition is a two-day event showing results of students’ work in robotics and automation.</li>'
                    '<li>Day 1: participants’ workshops, lectures by technical experts, panel discussions, and robot homologation (verifying compliance with Competition Rules).</li>'
                    '<li>Day 2: the competition itself.</li>'
                    '</ul>'
                    '<hr>'
                    '<p><strong>Conditions of participation:</strong></p>'
                    '<ul>'
                    '<li>Each school can nominate a maximum of three 3-member teams per subcategory.</li>'
                    '<li>Each competitive team must be registered via the online registration form.</li>'
                    '<li>The competitive team must be nominated based on the results of the school round.</li>'
                    '</ul>'
                    '<hr>'
                    '<p><strong>Rights and obligations of competitive teams:</strong></p>'
                    '<ul>'
                    '<li>Teams have the right to appeal against a jury decision (in writing, within 24 hours).</li>'
                    '<li>Teams must comply with all organizer instructions regarding safety and health protection.</li>'
                    '<li>Teams must familiarise themselves with the Competition Rules and comply with them.</li>'
                    '</ul>'
                ),
            },
            {
                'slug': 'how-to-build-robot',
                'title': 'How to Build a Robot',
                'nav_section': 'participate',
                'nav_order': 2,
                'body': (
                    '<h2>How to Build Your Competition Robot</h2>'
                    '<p>Our competition platform is designed for the <strong>PicoBot</strong> — a robot built around '
                    'the <strong>Raspberry Pi Pico W</strong> microcontroller.</p>'

                    '<h3>Step 1: Hardware</h3>'
                    '<p>Follow the PicoBot assembly guide: '
                    '<a href="https://github.com/robosteamdev/picobot-setup" target="_blank">picobot-setup on GitHub</a>'
                    '</p>'

                    '<h3>Step 2: Software</h3>'
                    '<p>Install the web-control interface: '
                    '<a href="https://github.com/robosteamdev/picobot-web-control" target="_blank">picobot-web-control on GitHub</a>'
                    '</p>'

                    '<h3>Step 3: Competition Library</h3>'
                    '<p>Use the RoboSTEAM competition library (MicroPython) to connect your robot to the competition '
                    'server via MQTT. The library handles START/STOP commands automatically.</p>'
                    '<p>Source code and documentation: '
                    '<a href="https://github.com/robosteamdev/picobot-competition-library" target="_blank">picobot-competition-library on GitHub</a>'
                    '</p>'
                ),
            },
            {
                'slug': 'organize',
                'title': 'How to Organize',
                'nav_section': 'organize',
                'nav_order': 5,
                'body': (
                    '<h2>How to Organize a RoboSTEAM Competition</h2>'
                    '<p>This guide is for teachers, trainers, and school coordinators who want to host a competition event.</p>'
                    '<h3>Requirements</h3>'
                    '<ul><li>A venue with space for competition tracks and team tables</li>'
                    '<li>WiFi with access to the competition server</li>'
                    '<li>At least one lap timer device (NodeMCU + E18 IR sensor) for timed categories</li>'
                    '<li>A laptop or tablet for the judge interface</li></ul>'
                    + IMG_PH +
                    '<h3>Setting up the competition</h3>'
                    '<ol><li>Log into the Competition Platform as an organiser.</li>'
                    '<li>Create a new Contest and add Categories.</li>'
                    '<li>Register participating teams.</li>'
                    '<li>Assign a lap timer device to timed categories.</li>'
                    '<li>Set the competition to RUNNING when ready.</li></ol>'
                    '<p>See the <a href="/pages/judge-guide/">Judge Guide</a> for step-by-step operating instructions.</p>'
                ),
            },
        ]

        for page_data in real_pages:
            obj, created = StaticPage.objects.update_or_create(
                slug=page_data['slug'],
                defaults={
                    'title': page_data['title'],
                    'nav_section': page_data['nav_section'],
                    'nav_order': page_data['nav_order'],
                    'body': page_data['body'],
                    'is_published': True,
                }
            )
            action = 'Created' if created else 'Updated'
            self.stdout.write(f'  {action}: {page_data["slug"]}')

        # New pages (get_or_create — create once, then editable via admin)
        new_pages = [
            {
                'slug': 'rules-guide',
                'title': 'Rules Guide',
                'nav_section': 'organize',
                'nav_order': 1,
                'body': (
                    '<ul>'
                    '<li><em>Will there be direct sunlight on the track?</em><br>'
                    'Since competitions are held indoors, usually not much sunlight penetrates the room.</li>'
                    '<li><em>Do obstacles on the track reflect ultrasounds?</em><br>Yes.</li>'
                    '<li><em>Can the robot leave the track momentarily and continue?</em><br>'
                    'It depends on what the jury says.</li>'
                    '<li><em>Can I use time between attempts for manual programming?</em><br>No way!</li>'
                    '<li><em>Can there be several robots on the track at the same time?</em><br>No.</li>'
                    '<li><em>What shape will the start of the race track take?</em><br>'
                    'The start is always the same. The circle is painted with a broken line so the robot doesn’t '
                    'catch the “track” and drive around it.</li>'
                    '<li><em>Does the robot in the free ride category also have a maximum size of 25×25×25 cm? '
                    'Can it be remotely controlled?</em><br>No size limit. Yes, remote control is allowed.</li>'
                    '</ul>'
                ),
            },
            {
                'slug': 'actual-contest-line-follower',
                'title': 'Actual Contest – Line Follower',
                'nav_section': 'contest',
                'nav_order': 1,
                'body': (
                    '<h2>LINEFOLLOWER</h2>'
                    '<h3>The competition task</h3>'
                    '<p>Build an autonomous mobile robot which will travel the whole defined path in the shortest time. '
                    'The direction and course is defined by a dark line.</p>'

                    '<h3>The path</h3>'
                    '<p>The background will be white with a dark (black) navigation line (width 15 ± 1 mm). Total '
                    'length of the path will not exceed 10 metres. The smallest radius of curves will be 30 cm. '
                    'Total rising of the path will not exceed 2 cm; maximum rising or declining is 5%.</p>'

                    '<h3>Timing</h3>'
                    '<p>The robot starts within the starting area, 30 cm before the start line (infrared beam of the '
                    'timing device). When the beam is interrupted by any part of the robot, time starts. Time stops '
                    'when the finishing beam is interrupted.</p>'

                    '<h3>Track designs</h3>'
                    '<img src="/media/uploads/lf-track-1.png" alt="Line follower track design 1" style="max-width:100%;border-radius:8px;margin-bottom:1rem;">'
                    '<img src="/media/uploads/lf-track-2.png" alt="Line follower track design 2" style="max-width:100%;border-radius:8px;margin-bottom:1rem;">'

                    '<h3>Robot operation</h3>'
                    '<p>There must not be any interference between the robot and its team after the robot is set in '
                    'the starting circle and turned on. The robot may not leave the line after crossing the start line '
                    '(avoiding obstacles is the only exception).</p>'
                    '<img src="/media/uploads/lf-robots.jpg" alt="Line follower robots at competition" style="max-width:100%;border-radius:8px;margin-bottom:1rem;">'

                    '<h3>Competition format</h3>'
                    '<p>Every robot has a total of 3 runs. The time between runs can be used for fixing and adjusting '
                    'the robot. The robot has 8 minutes to complete the path per run.</p>'

                    '<h3>Evaluation</h3>'
                    '<p>The winner is the robot with the shortest time from any of the three runs. Unfinished runs are '
                    'not evaluated.</p>'

                    '<div class="ratio ratio-16x9" style="max-width:640px;margin-top:1rem;">'
                    '<iframe src="https://www.youtube.com/embed/nX6ZTd6w9bA" title="Line Follower competition" allowfullscreen></iframe>'
                    '</div>'
                ),
            },
            {
                'slug': 'previous-contest-maze-solver',
                'title': 'Previous Contest – Maze Solver',
                'nav_section': 'contest',
                'nav_order': 2,
                'body': (
                    '<h2>Maze Solver category</h2>'
                    '<h3>Introduction</h3>'
                    '<p>A Maze Solver is a small robot controlled vehicle that can navigate its way through an '
                    'unknown and unconnected maze.</p>'

                    '<h3>The competition task</h3>'
                    '<p>The objective is to impart to the robot an adaptive intelligence to explore different maze '
                    'configurations and to work out the optimum route for the shortest travel time from start to '
                    'finish.</p>'

                    '<h3>Maze specifications</h3>'
                    '<p>The maze comprises 16 × 16 multiples of an 18 cm × 18 cm unit square. Walls are 5 cm high '
                    'and 1.2 cm thick. Passageways between walls are 16.8 cm wide.</p>'
                    '<img src="/media/uploads/maze-design-1.png" alt="Maze design (a)" style="max-width:100%;border-radius:8px;margin-bottom:0.5rem;">'
                    '<img src="/media/uploads/maze-design-2.png" alt="Maze design (b)" style="max-width:100%;border-radius:8px;margin-bottom:1rem;">'

                    '<h3>Scoring</h3>'
                    '<p>Handicapped Time Score = Run Time + Touch Penalty (3 seconds per touch). Fastest time counts.</p>'

                    '<h3>Time limits</h3>'
                    '<p>The robot has 5 minutes total. Maximum 10 runs.</p>'

                    '<h3>Evaluation</h3>'
                    '<p>The winner is the robot with the shortest Handicapped Time Score.</p>'

                    '<div class="ratio ratio-16x9" style="max-width:640px;margin-top:1rem;">'
                    '<iframe src="https://www.youtube.com/embed/c7SDOrYH3Oc" title="Maze Solver competition" allowfullscreen></iframe>'
                    '</div>'
                ),
            },
        ]

        for page_data in new_pages:
            obj, created = StaticPage.objects.get_or_create(
                slug=page_data['slug'],
                defaults={
                    'title': page_data['title'],
                    'nav_section': page_data['nav_section'],
                    'nav_order': page_data['nav_order'],
                    'body': page_data['body'],
                    'is_published': True,
                }
            )
            action = 'Created' if created else 'Skipped (exists)'
            self.stdout.write(f'  {action}: {page_data["slug"]}')

    def _seed_news(self, admin_user):
        from public.models import NewsPost
        posts = [
            {
                'title': 'What is a Competition and How Does it Look?',
                'body': (
                    '<h2>What is a Competition and How Does it Look?</h2>'
                    '<p>Robot competitions are educational events where student teams test their robots against structured challenges. Here is what to expect at a RoboSTEAM competition event.</p>'
                    + IMG_PH +
                    '<h3>The experience</h3>'
                    '<p>Teams arrive, check in their robots for technical inspection, then get practice time on the competition tracks. The action happens during qualifying rounds.</p>'
                    '<p>The live scoreboard updates in real time as each run is completed.</p>'
                    + IMG_PH +
                    '<h3>What makes it educational?</h3>'
                    '<p>Students gain experience in engineering design, debugging under pressure, teamwork, and presenting their work.</p>'
                ),
                'published_at': datetime.datetime(2023, 1, 4, 12, 0, tzinfo=datetime.timezone.utc),
            },
            {
                'title': 'RoboSTEAM Competitions Platform Launched',
                'body': (
                    '<h2>RoboSTEAM Competitions Platform Launched</h2>'
                    '<p>We are pleased to announce the launch of the RoboSTEAM Competition Platform, part of the ROBO STEAM ACADEMY Erasmus+ project.</p>'
                    '<h3>Key features</h3>'
                    '<ul><li>Automated lap timing via hardware IR sensor integration</li>'
                    '<li>Real-time scoreboard with WebSocket push updates</li>'
                    '<li>Support for both timed and judged competition categories</li>'
                    '<li>Open-source PicoBot library for student robots (Raspberry Pi Pico W)</li>'
                    '<li>MQTT-based robot control and lap timer communication</li></ul>'
                    + IMG_PH +
                    '<p>The platform is developed as part of WP3 (Advanced Robo STEM Games and Jury) and will be deployed at partner schools in Bulgaria, Slovakia, and Romania.</p>'
                ),
                'published_at': datetime.datetime(2026, 6, 28, 10, 0, tzinfo=datetime.timezone.utc),
            },
            {
                'title': 'Video: How to Use the Competition Scoreboard',
                'body': (
                    '<h2>Video: How to Use the Competition Scoreboard</h2>'
                    '<p>This tutorial walks judges and organisers through the RoboSTEAM Competition Scoreboard application.</p>'
                    + VID_PH +
                    '<p>Topics covered: logging in, creating runs, starting timed runs, entering scores, and reading the live scoreboard.</p>'
                    '<p><em>Full instructional video coming soon.</em></p>'
                ),
                'published_at': datetime.datetime(2026, 6, 1, 10, 0, tzinfo=datetime.timezone.utc),
            },
        ]

        for post_data in posts:
            obj, created = NewsPost.objects.get_or_create(
                title=post_data['title'],
                defaults={
                    'body': post_data['body'],
                    'published_at': post_data['published_at'],
                    'is_published': True,
                    'author': admin_user,
                }
            )
            action = 'Created' if created else 'Skipped (exists)'
            self.stdout.write(f'  {action}: {post_data["title"][:50]}')

    def _seed_announcements(self):
        from public.models import Announcement
        obj, created = Announcement.objects.get_or_create(
            title='RoboSTEAM 2027 Spring Competition',
            defaults={
                'event_date': datetime.date(2027, 3, 15),
                'location': 'Sofia, Bulgaria (TBC)',
                'short_description': 'The next RoboSTEAM international competition. Registration opens January 2027.',
                'registration_link': '',
                'is_published': True,
            }
        )
        action = 'Created' if created else 'Skipped (exists)'
        self.stdout.write(f'  {action}: RoboSTEAM 2027 Spring Competition')

    def _seed_gallery(self):
        from public.models import GalleryAlbum
        obj, created = GalleryAlbum.objects.get_or_create(
            title='Sample Gallery — Photos Coming Soon',
            defaults={
                'event_date': datetime.date(2026, 1, 1),
                'description': 'Photos from RoboSTEAM competitions will appear here.',
                'is_published': False,
            }
        )
        action = 'Created' if created else 'Skipped (exists)'
        self.stdout.write(f'  {action}: Sample Gallery')
