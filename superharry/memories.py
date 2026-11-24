from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpResponseRedirect
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from .models import SavedMemory


@method_decorator(never_cache, name="dispatch")
class OwnedMemoryView(LoginRequiredMixin):
    model = SavedMemory
    success_url = reverse_lazy("memories")

    def get_queryset(self):
        return super().get_queryset().filter(owner=self.request.user)


class MemoryList(OwnedMemoryView, ListView):
    template_name = "memories/list.html"
    context_object_name = "memories"


class MemoryCreate(OwnedMemoryView, CreateView):
    fields = ("content",)
    template_name = "memories/form.html"

    def form_valid(self, form):
        form.instance.owner = self.request.user
        return super().form_valid(form)


class MemoryEdit(OwnedMemoryView, UpdateView):
    fields = ("content",)
    template_name = "memories/form.html"

    def form_valid(self, form):
        updated = self.get_queryset().filter(pk=self.object.pk).update(
            content=form.cleaned_data["content"]
        )
        if not updated:
            raise Http404
        return HttpResponseRedirect(self.get_success_url())


class MemoryDelete(OwnedMemoryView, DeleteView):
    http_method_names = ("get", "head", "post", "options")
    template_name = "memories/delete.html"
