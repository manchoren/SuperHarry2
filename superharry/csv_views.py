import json
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .csv_protocol import read_result
from .models import CsvFile, CsvSummary


def spool():
    return Path(settings.CSV_SPOOL) if settings.CSV_SPOOL else None


@never_cache
@login_required
@require_http_methods(("GET",))
def upload(request):
    files = CsvFile.objects.defer("payload").filter(owner=request.user, summary__isnull=False).order_by("-pk")
    return render(request, "csv/upload.html", {"files": files})


@never_cache
@login_required
@require_http_methods(("GET", "HEAD"))
def detail(request, pk):
    record = get_object_or_404(
        CsvFile.objects.defer("payload").select_related("summary"), pk=pk, owner=request.user
    )
    job = getattr(record, "summary", None)
    result = read_result(spool(), job.job_id) if job else None
    return render(request, "csv/detail.html", {
        "record": record, "job": job, "result": result, "runner_configured": spool() is not None,
    })


@never_cache
@login_required
@require_http_methods(("GET", "HEAD"))
def download(request, pk):
    job = get_object_or_404(CsvSummary, file_id=pk, file__owner=request.user)
    result = read_result(spool(), job.job_id)
    if not result or result.get("status") != "complete":
        raise Http404
    return HttpResponse(
        json.dumps(result["summary"], ensure_ascii=False, indent=2),
        content_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="csv-summary.json"'},
    )
