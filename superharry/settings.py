import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

secret_file = os.environ.get("DJANGO_SECRET_KEY_FILE")
SECRET_KEY = (
    Path(secret_file).read_text(encoding="utf-8").strip()
    if secret_file
    else os.environ.get("DJANGO_SECRET_KEY", "")
)
if not SECRET_KEY:
    raise ImproperlyConfigured("Set a nonempty Django signing key.")
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]
ROOT_URLCONF = "superharry.urls"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
TIME_ZONE = os.environ.get("DJANGO_TIME_ZONE", "America/Guayaquil")
USE_TZ = True

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "superharry",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("DJANGO_DATABASE_PATH", "db.sqlite3"),
    },
}
STATIC_URL = "/static/"
STATIC_ROOT = Path(__file__).resolve().parent.parent / "staticfiles"
