from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from . import chat, csv_views, memories

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(next_page="login"), name="logout"),
    path("memories/", memories.MemoryList.as_view(), name="memories"),
    path("memories/new/", memories.MemoryCreate.as_view(), name="memory-create"),
    path("memories/<int:pk>/edit/", memories.MemoryEdit.as_view(), name="memory-edit"),
    path("memories/<int:pk>/delete/", memories.MemoryDelete.as_view(), name="memory-delete"),
    path("csv/", csv_views.upload, name="csv-upload"),
    path("csv/<int:pk>/", csv_views.detail, name="csv-detail"),
    path("csv/<int:pk>/approve/", csv_views.approve, name="csv-approve"),
    path("csv/<int:pk>/download/", csv_views.download, name="csv-download"),
    path("", chat.page, name="chat"),
    path("chat/<int:conversation_id>/", chat.page, name="chat-history"),
    path("chat/send/", chat.send, name="chat-send"),
    path("chat/<int:conversation_id>/send/", chat.send, name="chat-continue"),
    path("chat/<int:conversation_id>/memory/", chat.memory_setting, name="chat-memory"),
    path("chat/<int:conversation_id>/retry/<int:reply_id>/", chat.retry, name="chat-retry"),
]
