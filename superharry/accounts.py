from functools import wraps

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .models import AccountAudit, UserProfile


ADMIN_GROUP = "Administrators"
PRIVILEGE_FIELDS = {"administrators_group", "groups", "is_staff", "is_superuser", "user_permissions"}


def is_account_manager(user):
    return user.is_authenticated and (
        user.is_superuser
        or (
            user.is_staff
            and user.groups.filter(name=ADMIN_GROUP).exists()
        )
    )


def account_manager_required(view):
    @wraps(view)
    @login_required
    def checked_view(request, *args, **kwargs):
        if not is_account_manager(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return checked_view


def _reject_delegated_privilege_changes(request):
    if not request.user.is_superuser and PRIVILEGE_FIELDS.intersection(request.POST):
        raise PermissionDenied


def regular_accounts():
    user_model = get_user_model()
    return user_model._default_manager.filter(
        is_staff=False,
        is_superuser=False,
        groups__isnull=True,
        user_permissions__isnull=True,
    )


def manageable_account_targets(actor):
    """Return regular targets for delegates and non-superuser targets for owners."""
    user_model = get_user_model()
    if actor.is_superuser:
        return user_model._default_manager.exclude(is_superuser=True).distinct()
    return regular_accounts()


def manageable_accounts(user):
    return manageable_account_targets(user)


class RegularAccountCreateForm(forms.ModelForm):
    password = forms.CharField(
        label="Initial password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    must_change_password = forms.BooleanField(
        label="Require a password change at first sign-in",
        required=False,
        initial=True,
    )
    administrators_group = forms.BooleanField(
        label="Make this account an administrator",
        required=False,
        initial=False,
    )

    def __init__(self, *args, allow_admin_role=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.allow_admin_role = allow_admin_role
        if not allow_admin_role:
            self.fields.pop("administrators_group")

    class Meta:
        model = get_user_model()
        fields = ("username", "first_name", "last_name", "email")

    def clean_password(self):
        password = self.cleaned_data["password"]
        validate_password(password, self.instance)
        return password

    def save(self, commit=True):
        account = super().save(commit=False)
        account.set_password(self.cleaned_data["password"])
        account.is_active = True
        account.is_staff = bool(
            self.allow_admin_role and self.cleaned_data.get("administrators_group")
        )
        account.is_superuser = False
        if commit:
            account.save()
        return account


class RegularAccountEditForm(forms.ModelForm):
    administrators_group = forms.BooleanField(
        label="Member of Administrators group",
        required=False,
    )

    def __init__(self, *args, allow_admin_role=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.allow_admin_role = allow_admin_role
        if allow_admin_role:
            self.fields["administrators_group"].initial = self.instance.groups.filter(
                name=ADMIN_GROUP
            ).exists()
        else:
            self.fields.pop("administrators_group")

    class Meta:
        model = get_user_model()
        fields = ("username", "first_name", "last_name", "email")

    def save(self, commit=True):
        account = super().save(commit=False)
        if self.allow_admin_role:
            if self.cleaned_data.get("administrators_group"):
                account.is_staff = True
            elif not account.user_permissions.exists():
                account.is_staff = False
        if commit:
            account.save()
            if self.allow_admin_role:
                _set_administrators_group(
                    account, self.cleaned_data.get("administrators_group", False)
                )
        return account


def _set_administrators_group(account, enabled):
    group, _ = Group.objects.get_or_create(name=ADMIN_GROUP)
    if enabled:
        account.groups.add(group)
    else:
        account.groups.remove(group)


class AccountPasswordResetForm(SetPasswordForm):
    must_change_password = forms.BooleanField(
        label="Require a password change at next sign-in",
        required=False,
        initial=True,
    )


def _audit(actor, target, action):
    AccountAudit.objects.create(
        actor=actor,
        target=target,
        actor_id_snapshot=actor.pk,
        actor_username=actor.get_username(),
        actor_is_privileged=(
            actor.is_staff
            or actor.is_superuser
            or actor.groups.exists()
            or actor.user_permissions.exists()
        ),
        target_id_snapshot=target.pk,
        target_username=target.get_username(),
        target_is_privileged=(
            target.is_staff
            or target.is_superuser
            or target.groups.exists()
            or target.user_permissions.exists()
        ),
        action=action,
    )


def _is_privileged_account(account):
    return bool(
        account.is_staff
        or account.is_superuser
        or account.groups.exists()
        or account.user_permissions.exists()
    )


def _sync_audit_target_privilege(account):
    AccountAudit.objects.filter(target=account).update(
        target_is_privileged=_is_privileged_account(account)
    )


def _account_role(account):
    if account.is_staff or account.is_superuser or account.groups.exists() or account.user_permissions.exists():
        return "Administrator"
    return "Regular"


@never_cache
@account_manager_required
@require_http_methods(["GET"])
def account_list(request):
    accounts = manageable_accounts(request.user).prefetch_related("groups")
    search = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    role = request.GET.get("role", "")
    if search:
        accounts = accounts.filter(Q(username__icontains=search) | Q(email__icontains=search))
    if status == "active":
        accounts = accounts.filter(is_active=True)
    elif status == "inactive":
        accounts = accounts.filter(is_active=False)
    if role == "regular":
        accounts = accounts.filter(
            is_staff=False,
            is_superuser=False,
            groups__isnull=True,
            user_permissions__isnull=True,
        )
    elif role == "administrator":
        if not request.user.is_superuser:
            accounts = accounts.none()
        else:
            accounts = accounts.filter(
                Q(is_staff=True) | Q(groups__isnull=False) | Q(user_permissions__isnull=False)
            ).exclude(is_superuser=True)
    accounts = accounts.distinct().order_by("username", "pk")
    return render(
        request,
        "accounts/list.html",
        {
            "accounts": accounts,
            "search": search,
            "status_filter": status,
            "role_filter": role,
            "can_filter_admins": request.user.is_superuser,
        },
    )


@never_cache
@account_manager_required
@require_http_methods(["GET"])
def account_audit_log(request):
    events = AccountAudit.objects.order_by("-occurred_at", "-pk")
    if not request.user.is_superuser:
        events = events.filter(target_is_privileged=False)

    paginator = Paginator(events, 25)
    page = paginator.get_page(request.GET.get("page"))
    action_labels = {
        "created": "Created",
        "edited": "Edited",
        "password_reset": "Password reset",
        "deactivated": "Deactivated",
        "reactivated": "Reactivated",
        "deleted": "Deleted",
    }
    for event in page:
        event.action_label = action_labels.get(
            event.action, event.action.replace("_", " ").title()
        )

    return render(
        request,
        "accounts/audit.html",
        {
            "page": page,
            "events": page.object_list,
            "can_see_privileged_events": request.user.is_superuser,
        },
    )


@never_cache
@account_manager_required
@require_http_methods(["GET"])
def account_detail(request, pk):
    account = get_object_or_404(manageable_accounts(request.user), pk=pk)
    from .account_lifecycle import pending_cancellations

    deletion_pending = UserProfile.objects.filter(user=account, deletion_pending=True).exists()
    deletion_stop_confirmed = UserProfile.objects.filter(
        user=account, deletion_stop_confirmed=True
    ).exists()
    pending = (
        {"chat": 0, "skills": 0, "total": 0}
        if deletion_stop_confirmed
        else pending_cancellations(account.pk, include_shared_skills=deletion_pending)
    )
    return render(
        request,
        "accounts/detail.html",
        {
            "account": account,
            "account_role": _account_role(account),
            "can_edit": request.user.is_superuser or not (
                account.is_staff
                or account.is_superuser
                or account.groups.exists()
                or account.user_permissions.exists()
            ),
            "must_change_password": UserProfile.objects.filter(
                user=account, must_change_password=True
            ).exists(),
            "deletion_pending": deletion_pending,
            "pending_cancellations": pending,
            "can_manage_admins": request.user.is_superuser,
        },
    )


@never_cache
@account_manager_required
@require_http_methods(["GET", "POST"])
def account_create(request):
    if request.method == "POST":
        _reject_delegated_privilege_changes(request)
    form = RegularAccountCreateForm(
        request.POST or None,
        allow_admin_role=request.user.is_superuser,
    )
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            account = form.save()
            if request.user.is_superuser:
                _set_administrators_group(
                    account, form.cleaned_data.get("administrators_group", False)
                )
                _sync_audit_target_privilege(account)
            UserProfile.objects.update_or_create(
                user=account,
                defaults={"must_change_password": form.cleaned_data["must_change_password"]},
            )
            _audit(request.user, account, "created")
        return redirect("account-detail", pk=account.pk)
    return render(request, "accounts/form.html", {"form": form, "creating": True})


@never_cache
@account_manager_required
@require_http_methods(["GET", "POST"])
def account_edit(request, pk):
    account = get_object_or_404(manageable_account_targets(request.user), pk=pk)
    if request.method == "POST":
        _reject_delegated_privilege_changes(request)
    if UserProfile.objects.filter(user=account, deletion_pending=True).exists():
        raise Http404
    form = RegularAccountEditForm(
        request.POST or None,
        instance=account,
        allow_admin_role=request.user.is_superuser,
    )
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            account = form.save()
            if request.user.is_superuser:
                _sync_audit_target_privilege(account)
            _audit(request.user, account, "edited")
        return redirect("account-detail", pk=account.pk)
    return render(request, "accounts/form.html", {"form": form, "creating": False, "account": account})


@never_cache
@account_manager_required
@require_http_methods(["GET", "POST"])
def account_password_reset(request, pk):
    account = get_object_or_404(manageable_account_targets(request.user), pk=pk)
    if UserProfile.objects.filter(user=account, deletion_pending=True).exists():
        raise Http404
    form = AccountPasswordResetForm(account, request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            form.save()
            UserProfile.objects.update_or_create(
                user=account,
                defaults={"must_change_password": form.cleaned_data["must_change_password"]},
            )
            _audit(request.user, account, "password_reset")
        return redirect("account-detail", pk=account.pk)
    return render(
        request,
        "accounts/password_reset.html",
        {"form": form, "account": account},
    )


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def password_change(request):
    from django.contrib.auth import update_session_auth_hash
    from django.contrib.auth.forms import PasswordChangeForm

    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            user = form.save()
            UserProfile.objects.update_or_create(
                user=user,
                defaults={"must_change_password": False},
            )
        update_session_auth_hash(request, user)
        return redirect("chat")
    return render(request, "registration/required_password_change.html", {"form": form})
