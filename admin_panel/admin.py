from django.contrib import admin
from .models import AuditLog, QuickResponse, Snippet, BusinessHours, Filter, HandoffConfig

admin.site.register(AuditLog)
admin.site.register(QuickResponse)
admin.site.register(Snippet)
admin.site.register(BusinessHours)
admin.site.register(Filter)
admin.site.register(HandoffConfig)
