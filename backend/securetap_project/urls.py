from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from accounts.views import DashboardAccountViewSet, LoginView, SystemSettingsView
from audit.views import AuditLogViewSet
from logs.views import (
    EntryLogViewSet,
    GateSummaryView,
    HealthView,
    IdentifyView,
    LiveLogsView,
    ManualOverrideView,
    VerifyView,
)
from reports.views import FarFrrView, SummaryView
from users.views import (
    BulkImportView,
    ConfusablePairViewSet,
    DeactivationRequestViewSet,
    PersonViewSet,
    PhotoQualityCheckView,
)

router = DefaultRouter()
router.register(r"users", PersonViewSet, basename="person")
router.register(r"logs", EntryLogViewSet, basename="entrylog")
router.register(r"accounts", DashboardAccountViewSet, basename="dashboard-account")
router.register(r"audit-log", AuditLogViewSet, basename="audit-log")
router.register(r"deactivation-requests", DeactivationRequestViewSet, basename="deactivation-request")
router.register(r"confusable-pairs", ConfusablePairViewSet, basename="confusable-pair")

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/auth/login", LoginView.as_view(), name="auth-login"),
    path("api/auth/refresh", TokenRefreshView.as_view(), name="auth-refresh"),
    path("api/verify", VerifyView.as_view(), name="verify"),
    path("api/identify", IdentifyView.as_view(), name="identify"),
    path("api/health", HealthView.as_view(), name="health"),
    path("api/gate-summary", GateSummaryView.as_view(), name="gate-summary"),
    path("api/logs/live", LiveLogsView.as_view(), name="logs-live"),
    path("api/logs/manual-override", ManualOverrideView.as_view(), name="logs-manual-override"),
    path("api/users/bulk-import", BulkImportView.as_view(), name="users-bulk-import"),
    path("api/users/check-photo-quality", PhotoQualityCheckView.as_view(), name="users-check-photo-quality"),
    path("api/reports/summary", SummaryView.as_view(), name="reports-summary"),
    path("api/reports/far-frr", FarFrrView.as_view(), name="reports-far-frr"),
    path("api/settings", SystemSettingsView.as_view(), name="system-settings"),
    path("api/", include(router.urls)),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
