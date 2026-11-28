from django.contrib import admin, messages
from django.contrib.admin.helpers import AdminForm
from django import forms
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.admin import AdminUserCreationForm, GroupAdmin, UserAdmin
from django.contrib.auth.forms import AdminPasswordChangeForm
from django.contrib.auth.models import Group, User
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.decorators import method_decorator
from django.utils.html import escape
from django.utils.translation import gettext
from django.views.decorators.debug import sensitive_post_parameters

from . import route_admin  # noqa: F401  # Registers the chat route admin view.
from .accounts import _audit, _sync_audit_target_privilege
from .models import ChatLimits, UserChatLimits, UserProfile


admin.site.unregister(User)
admin.site.unregister(Group)


@admin.register(ChatLimits)
class ChatLimitsAdmin(admin.ModelAdmin):
    list_display = ("daily_messages", "max_response_characters", "max_active_schedules",
                    "response_reservation_tokens", "auto_context_window_tokens",
                    "unknown_context_window_tokens", "preserve_first_messages", "preserve_recent_messages")

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
    class SuperuserPasswordChangeForm(AdminPasswordChangeForm):
        must_change_password = forms.BooleanField(
            label="Require a password change at next sign-in",
            required=False,
            initial=True,
        )

    class AddForm(AdminUserCreationForm):
        must_change_password = forms.BooleanField(
            label="Require a password change at first sign-in",
            required=False,
            initial=True,
        )

    add_form = AddForm
    change_password_form = SuperuserPasswordChangeForm
    change_user_password_template = "accounts/superuser_password_change.html"

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        if obj is not None and UserProfile.objects.filter(user=obj, deletion_pending=True).exists():
            return False
        return super().has_change_permission(request, obj)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not change:
            UserProfile.objects.update_or_create(
                user=obj,
                defaults={
                    "must_change_password": form.cleaned_data["must_change_password"]
                },
            )

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        _sync_audit_target_privilege(form.instance)
        _audit(request.user, form.instance, "edited" if change else "created")

    def get_form(self, request, obj=None, **kwargs):
        form_class = super().get_form(request, obj, **kwargs)
        if not request.user.is_superuser:
            return form_class

        class LastSuperuserProtectedForm(form_class):
            def clean(self):
                cleaned = super().clean()
                if obj is not None:
                    list(
                        User.objects.select_for_update()
                        .filter(is_superuser=True, is_active=True, is_staff=True)
                        .values_list("pk", flat=True)
                    )
                if (
                    obj is not None
                    and obj.is_superuser
                    and obj.is_active
                    and obj.is_staff
                    and (
                        cleaned.get("is_superuser", obj.is_superuser) is False
                        or cleaned.get("is_active", obj.is_active) is False
                        or cleaned.get("is_staff", obj.is_staff) is False
                    )
                    and not User.objects.filter(
                        is_superuser=True, is_active=True, is_staff=True
                    )
                    .exclude(pk=obj.pk)
                    .exists()
                ):
                    self.add_error(
                        "is_superuser",
                        "Keep at least one active superuser for account recovery.",
                    )
                return cleaned

        return LastSuperuserProtectedForm

    def get_urls(self):
        password_reset_url = path(
            "<id>/password/",
            self.admin_site.admin_view(self.redirect_password_reset),
            name="auth_user_password_change",
        )
        return [password_reset_url, *super().get_urls()]

    def redirect_password_reset(self, request, id):
        account = self.get_object(request, id)
        if account is None:
            raise Http404
        if not self.has_change_permission(request, account):
            raise PermissionDenied
        if account.is_superuser:
            if not request.user.is_superuser:
                raise Http404
            return self.user_change_password(request, id)
        if (
            not request.user.is_superuser
            and (
                account.is_staff
                or account.groups.exists()
                or account.user_permissions.exists()
            )
        ):
            raise Http404
        return redirect("account-password-reset", pk=account.pk)

    @method_decorator(sensitive_post_parameters())
    def user_change_password(self, request, id, form_url=""):
        user = self.get_object(request, id)
        if not self.has_change_permission(request, user):
            raise PermissionDenied
        if user is None or not user.is_superuser or not request.user.is_superuser:
            raise Http404

        if request.method == "POST":
            form = self.change_password_form(user, request.POST)
            if form.is_valid():
                valid_submission = (
                    form.cleaned_data["set_usable_password"]
                    or "unset-password" in request.POST
                )
                if not valid_submission:
                    messages.error(
                        request,
                        gettext("Conflicting form data submitted. Please try again."),
                    )
                    return HttpResponseRedirect(request.get_full_path())

                user = form.save()
                UserProfile.objects.update_or_create(
                    user=user,
                    defaults={
                        "must_change_password": form.cleaned_data["must_change_password"]
                    },
                )
                _audit(request.user, user, "password_reset")
                self.log_change(
                    request, user, self.construct_change_message(request, form, None)
                )
                message = (
                    gettext("Password changed successfully.")
                    if user.has_usable_password()
                    else gettext("Password-based authentication was disabled.")
                )
                messages.success(request, message)
                update_session_auth_hash(request, form.user)
                return HttpResponseRedirect(
                    reverse("admin:auth_user_change", args=(user.pk,))
                )
        else:
            form = self.change_password_form(user)

        admin_form = AdminForm(form, [(None, {"fields": list(form.base_fields)})], {})
        title = gettext("Change password: %s") if user.has_usable_password() else gettext("Set password: %s")
        context = {
            "title": title % escape(user.get_username()),
            "adminForm": admin_form,
            "form_url": form_url,
            "form": form,
            "is_popup": False,
            "is_popup_var": "_popup",
            "add": True,
            "change": False,
            "has_delete_permission": False,
            "has_change_permission": True,
            "has_absolute_url": False,
            "opts": self.opts,
            "original": user,
            "save_as": False,
            "show_save": True,
            **self.admin_site.each_context(request),
        }
        request.current_app = self.admin_site.name
        return TemplateResponse(
            request,
            self.change_user_password_template or "admin/auth/user/change_password.html",
            context,
        )

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
            fieldsets = super().get_fieldsets(request, obj)
            if obj is None:
                return tuple(
                    (name, {**options, "fields": (*options["fields"], "must_change_password")})
                    if name is None
                    else (name, options)
                    for name, options in fieldsets
                )
            if request.user.is_superuser and obj.is_superuser:
                return fieldsets
            return tuple(
                (name, {**options, "fields": tuple(field for field in options["fields"] if field != "is_active")})
                for name, options in fieldsets
            )
        return (
            (None, {"fields": ("username", "password")}),
            ("Personal info", {"fields": ("first_name", "last_name", "email")}),
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
