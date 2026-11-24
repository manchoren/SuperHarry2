from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path
from django.views.decorators.http import require_http_methods

from .gateway import (
    GatewayFailure, available_routes, manage_gateway, probe_route, provider_connections,
)
from .models import ChatRoute


class ModelEntryForm(forms.Form):
    connection = forms.ChoiceField(label="Existing provider connection")
    model_id = forms.CharField(label="Model ID", max_length=255)
    model_name = forms.CharField(max_length=255, required=False)

    def __init__(self, connections, data=None):
        super().__init__(data)
        self.fields["connection"].choices = [
            (item["id"], f"{item['name']} ({item['provider']})") for item in connections
        ]


@admin.register(ChatRoute)
class ChatRouteAdmin(admin.ModelAdmin):
    def get_urls(self):
        view = require_http_methods(("GET", "POST"))(self.route_management)
        return [
            path("", self.admin_site.admin_view(view), name="superharry_chatroute_changelist"),
        ]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def route_management(self, request):
        if not self.has_view_permission(request):
            raise PermissionDenied
        if request.method == "POST":
            if not self.has_change_permission(request):
                raise PermissionDenied
            try:
                self.apply_action(request)
            except GatewayFailure as failure:
                self.message_user(request, str(failure), level=messages.ERROR)
            return redirect("admin:superharry_chatroute_changelist")
        request.current_app = self.admin_site.name
        routes = self.read_gateway(request, available_routes)
        connections = self.read_gateway(request, provider_connections)
        return TemplateResponse(request, "admin/chat_routes.html", {
            **self.admin_site.each_context(request),
            "title": "Chat routes",
            "routes": routes,
            "connections": connections,
            "model_form": ModelEntryForm(connections),
            "active_route": ChatRoute.active_id(),
            "can_change": self.has_change_permission(request),
        })

    def apply_action(self, request):
        action = request.POST.get("action")
        model_methods = {"add-model": "POST", "update-model": "PUT"}
        if action in model_methods:
            connections = provider_connections()
            form = ModelEntryForm(connections, request.POST)
            if not form.is_valid():
                raise GatewayFailure("Select an existing connection and enter a valid model ID and name.")
            data = form.cleaned_data
            providers = {item["id"]: item["provider"] for item in connections}
            payload = {"provider": providers[data["connection"]], "modelId": data["model_id"]}
            if data["model_name"]:
                payload["modelName"] = data["model_name"]
            manage_gateway("/api/provider-models", method=model_methods[action], payload=payload)
            self.message_user(request, "The model entry was saved.")
            return

        if action not in ("select", "test"):
            raise GatewayFailure("Unknown route operation.")
        route_id = request.POST.get("route_id", "")
        if len(route_id) > 255 or route_id not in available_routes():
            raise GatewayFailure("Select a route reported by the gateway.")
        if action == "select":
            ChatRoute.select(route_id)
            self.message_user(request, "The default route was updated.")
        else:
            probe_route(route_id)
            self.message_user(request, "Route test succeeded.")

    def read_gateway(self, request, operation):
        try:
            return operation()
        except GatewayFailure as failure:
            self.message_user(request, str(failure), level=messages.ERROR)
            return []
