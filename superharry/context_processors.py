from .models import CsvFile


def navigation(request):
    if not request.user.is_authenticated:
        return {}
    return {
        "has_previous_csv_results": CsvFile.objects.filter(
            owner=request.user, summary__isnull=False
        ).exists()
    }
