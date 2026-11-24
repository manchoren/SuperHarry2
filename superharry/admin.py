from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin, UserAdmin
from django.contrib.auth.models import Group, User

from . import route_admin  # Registers the chat route admin view.
from .models import ChatLimits, UserChatLimits


admin.site.unregister(User)
admin.site.unregister(Group)


@admin.register(ChatLimits)
class ChatLimitsAdmin(admin.ModelAdmin):
    list_display = ("daily_messages", "max_response_characters", "max_active_schedules")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(UserChatLimits)
class UserChatLimitsAdmin(admin.ModelAdmin):
    list_display = ("user", "daily_messages", "max_response_characters")
    list_select_related = ("user",)


@admin.register(User)
class AccountAdmin(UserAdmin):
    def get_queryset(self, request):
        accounts = super().get_queryset(request)
        if request.user.is_superuser:
            return accounts
        return accounts.filter(
            is_staff=False,
            is_superuser=False,
            groups__isnull=True,
            user_permissions__isnull=True,
        )

    def get_fieldsets(self, request, obj=None):
        if request.user.is_superuser or obj is None:
            return super().get_fieldsets(request, obj)
        return (
            (None, {"fields": ("username", "password")}),
            ("Personal info", {"fields": ("first_name", "last_name", "email")}),
            ("Account access", {"fields": ("is_active",)}),
        )

    def get_list_filter(self, request):
        if request.user.is_superuser:
            return super().get_list_filter(request)
        return ("is_active",)


@admin.register(Group)
class PrivilegeGroupAdmin(GroupAdmin):
    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    has_module_permission = has_view_permission
    has_add_permission = has_view_permission
    has_change_permission = has_view_permission
    has_delete_permission = has_view_permission
