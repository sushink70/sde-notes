from django.urls import path
from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("level1/stock-check/", views.level1_stock_check, name="level1"),
    path("level2/webhook-test/", views.level2_webhook_test, name="level2"),
    path("level3/import-avatar/", views.level3_import_avatar, name="level3"),
    path("level4/export-report/", views.level4_export_report, name="level4"),
    path("level4/check-log/", views.level4_check_log, name="level4log"),
]
