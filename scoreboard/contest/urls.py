from django.urls import path

from . import views, registration_views, organise_views, judge_mobile_views, participate_views

app_name = 'contest'

urlpatterns = [
    path('', views.index, name='index'),
    path('contest/<int:contest_id>', views.contest_competitions, name='contest_competitions'),
    path('contest/<int:contest_id>/ranking/', views.contest_ranking, name='contest_ranking'),
    path('team/<int:team_id>', views.contest_team, name='contest_team'),
    path('competition_board/<int:competition_id>', views.competition_board, name='competition_board'),
    path('competition/<int:competition_id>/run/create', views.run_create, name='run_create'),
    path('competition/<int:competition_id>/fragment/', views.competition_board_fragment, name='competition_board_fragment'),
    path('run/<int:run_id>/preflight', views.run_preflight, name='run_preflight'),
    path('run/<int:run_id>/start', views.run_start, name='run_start'),
    path('run/<int:run_id>/stop', views.run_stop, name='run_stop'),
    path('run/<int:run_id>/discard', views.run_discard, name='run_discard'),
    path('run/<int:run_id>/score', views.run_score, name='run_score'),
    path('run/<int:run_id>/penalty', views.run_penalty, name='run_penalty'),
    path('run/<int:run_id>/manual_entry', views.manual_entry, name='run_manual_entry'),
    # Registration
    path('register/',              registration_views.register,                  name='register'),
    path('register/pending/',      registration_views.register_pending,          name='register_pending'),
    path('register/done/',         registration_views.register_participant_done, name='register_participant_done'),
    # Account management
    path('account/cancel/',    views.account_cancel,    name='account_cancel'),
    path('account/cancelled/', views.account_cancelled, name='account_cancelled'),
    path('post-login/',        views.post_login_redirect, name='post_login_redirect'),
    # Participant self-registration
    path('participate/',                        participate_views.participate_dashboard, name='participate_dashboard'),
    path('participate/join/<int:contest_id>/',  participate_views.participate_join,      name='participate_join'),
    path('participate/leave/<int:contest_id>/', participate_views.participate_leave,     name='participate_leave'),
    # My Robots (Phase 5.21) — all owner-scoped, all self-service
    path('participate/robots/',                          participate_views.robots_list,   name='robots_list'),
    path('participate/robots/claim/',                    participate_views.robot_claim,   name='robot_claim'),
    path('participate/robots/<int:robot_id>/rename/',    participate_views.robot_rename,  name='robot_rename'),
    path('participate/robots/<int:robot_id>/release/',   participate_views.robot_release, name='robot_release'),
    path('participate/robots/bind/<int:contest_id>/',    participate_views.robot_bind,    name='robot_bind'),
    path('participate/robots/<int:robot_id>/test/',      participate_views.robot_test,    name='robot_test'),
    path('participate/robots/<int:robot_id>/status/',    participate_views.robot_status,  name='robot_status'),
    # Organiser interface
    path('organise/',                                          organise_views.organise_dashboard,     name='organise_dashboard'),
    path('organise/competition/new/',                          organise_views.organise_contest_new,   name='organise_contest_new'),
    path('organise/competition/<int:contest_id>/',             organise_views.organise_contest,       name='organise_contest'),
    path('organise/competition/<int:contest_id>/edit/',        organise_views.organise_contest_edit,  name='organise_contest_edit'),
    path('organise/competition/<int:contest_id>/team/new/',    organise_views.organise_team_new,      name='organise_team_new'),
    path('organise/team/<int:team_id>/edit/',                  organise_views.organise_team_edit,     name='organise_team_edit'),
    path('organise/team/<int:team_id>/delete/',                organise_views.organise_team_delete,   name='organise_team_delete'),
    path('organise/competition/<int:contest_id>/category/new/', organise_views.organise_category_new, name='organise_category_new'),
    path('organise/category/<int:category_id>/edit/',          organise_views.organise_category_edit, name='organise_category_edit'),
    # Device console (Phase 5.21)
    path('organise/devices/',                                  organise_views.organise_devices,          name='organise_devices'),
    path('organise/device/<int:device_id>/approval/',          organise_views.organise_device_approval,  name='organise_device_approval'),
    path('organise/team/<int:team_id>/robot/',                 organise_views.organise_team_robot,       name='organise_team_robot'),
    path('organise/competition/<int:contest_id>/readiness/',   organise_views.organise_readiness,        name='organise_readiness'),
    # Mobile judge interface
    path('judge/',                                              judge_mobile_views.judge_select,      name='judge_select'),
    path('judge/<int:competition_id>/',                         judge_mobile_views.judge_panel,       name='judge_panel'),
    path('judge/<int:competition_id>/state/',                   judge_mobile_views.judge_panel_state, name='judge_panel_state'),
    path('judge/<int:competition_id>/run/<int:run_id>/score/',  judge_mobile_views.judge_score,       name='judge_score'),
]
