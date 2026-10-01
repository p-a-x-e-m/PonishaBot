from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.index, name="index"),
    path("settings/", views.settings_page, name="settings"),
    path("api/status", views.api_status, name="api_status"),
    path("api/events", views.api_events, name="api_events"),
    path("api/logs", views.api_logs, name="api_logs"),
    path("api/start", views.api_start, name="api_start"),
    path("api/stop", views.api_stop, name="api_stop"),
    path("api/login", views.api_login, name="api_login"),
    path("api/login_done", views.api_login_done, name="api_login_done"),
    path("api/auth/request-otp", views.api_request_otp, name="api_request_otp"),
    path("api/auth/verify-otp", views.api_verify_otp, name="api_verify_otp"),
    path("api/auth/otp-status", views.api_otp_status, name="api_otp_status"),
    path("api/bid", views.api_bid, name="api_bid"),
    path("api/settings", views.api_settings, name="api_settings"),
    path("api/test-ai", views.api_test_ai, name="api_test_ai"),
    path("api/test-telegram", views.api_test_tg, name="api_test_tg"),
    path("api/demo", views.api_demo, name="api_demo"),
    path("api/skills", views.api_skills, name="api_skills"),
    path("api/dry_run", views.api_dry_run, name="api_dry_run"),
    path("api/open_project", views.api_open_project, name="api_open_project"),
]
