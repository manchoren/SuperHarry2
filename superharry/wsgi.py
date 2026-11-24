from django.conf import settings
from django.core.wsgi import get_wsgi_application
from whitenoise import WhiteNoise

application = WhiteNoise(
    get_wsgi_application(),
    root=settings.STATIC_ROOT,
    prefix=settings.STATIC_URL,
)
