from .models import CsvFile


def navigation(request):
    if not request.user.is_authenticated:
        return {}
    return {
        "can_manage_accounts": request.user.is_superuser
        or (
            request.user.is_staff
            and request.user.groups.filter(name="Administrators").exists()
        ),
        "has_previous_csv_results": CsvFile.objects.filter(
            owner=request.user, summary__isnull=False
        ).exists()
    }
