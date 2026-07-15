from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from accounts.views import LoginView
from logs.views import EntryLogViewSet, IdentifyView, LiveLogsView, VerifyView
from reports.views import SummaryView
from users.views import BulkImportView, PersonViewSet

router = DefaultRouter()
router.register(r"users", PersonViewSet, basename="person")
router.register(r"logs", EntryLogViewSet, basename="entrylog")

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/auth/login", LoginView.as_view(), name="auth-login"),
    path("api/auth/refresh", TokenRefreshView.as_view(), name="auth-refresh"),
    path("api/verify", VerifyView.as_view(), name="verify"),
    path("api/identify", IdentifyView.as_view(), name="identify"),
    path("api/logs/live", LiveLogsView.as_view(), name="logs-live"),
    path("api/users/bulk-import", BulkImportView.as_view(), name="users-bulk-import"),
    path("api/reports/summary", SummaryView.as_view(), name="reports-summary"),
    path("api/", include(router.urls)),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
