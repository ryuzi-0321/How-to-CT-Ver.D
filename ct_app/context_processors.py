# context_processors.py
from django.conf import settings

def maintenance_status(request):
    return {
        'is_maintenance': getattr(settings, 'IS_MAINTENANCE', False)
    }