from django.conf import settings
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.cache import never_cache


@never_cache
def catalog(request):
    if not settings.UI_COMPONENT_CATALOG_ENABLED:
        raise Http404
    return render(request, "ui_component_catalog.html")
