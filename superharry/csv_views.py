import json
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from .csv_protocol import MAX_INPUT_BYTES, enqueue, read_result
from .models import CsvFile, CsvSummary


def spool():
    return Path(settings.CSV_SPOOL) if settings.CSV_SPOOL else None


@never_cache
@login_required
@require_http_methods(("GET", "POST"))
def upload(request):
    files = CsvFile.objects.defer("payload").filter(owner=request.user).order_by("-pk")
    if request.method == "POST":
        incoming = request.FILES.get("file")
        if not incoming or not incoming.name.lower().endswith(".csv") or incoming.size > MAX_INPUT_BYTES:
            return render(request, "csv/upload.html", {
                "error": "Choose a CSV file up to 256 KiB.",
                "files": files,
            }, status=400)
        record = CsvFile.objects.create(
            owner=request.user, name=incoming.name, payload=incoming.read(MAX_INPUT_BYTES + 1)
        )
        return redirect("csv-detail", pk=record.pk)
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
@require_POST
def approve(request, pk):
    owned_file = get_object_or_404(CsvFile, pk=pk, owner=request.user)
    if request.POST.get("confirm") != "yes":
        return HttpResponse("Confirm this file before submitting a job.", status=400)
    directory = spool()
    if directory is None:
        return HttpResponse("CSV processing is not configured.", status=503)
    job, _ = CsvSummary.objects.get_or_create(file=owned_file)
    try:
        enqueue(directory, job.job_id, bytes(owned_file.payload))
    except OSError:
        return HttpResponse("The CSV runner could not accept the job. Try again.", status=503)
    return redirect("csv-detail", pk=pk)


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
