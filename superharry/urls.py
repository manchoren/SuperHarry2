from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from . import chat

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(next_page="login"), name="logout"),
    path("", chat.page, name="chat"),
    path("chat/<int:conversation_id>/", chat.page, name="chat-history"),
    path("chat/send/", chat.send, name="chat-send"),
    path("chat/<int:conversation_id>/send/", chat.send, name="chat-continue"),
    path("chat/<int:conversation_id>/retry/<int:reply_id>/", chat.retry, name="chat-retry"),
]
