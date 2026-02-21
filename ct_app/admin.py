from django.contrib import admin
from .models import Sick, Form, Protocol

@admin.register(Sick)
class SickAdmin(admin.ModelAdmin):
    list_display = ('diesease', 'protocol', 'created_at', 'updated_at')
    search_fields = ('diesease', 'keyword')
    list_filter = ('created_at', 'protocol')

@admin.register(Form)
class FormAdmin(admin.ModelAdmin):
    list_display = ('title', 'created_at', 'updated_at')
    search_fields = ('title',)
    list_filter = ('created_at',)

@admin.register(Protocol)
class ProtocolAdmin(admin.ModelAdmin):
    list_display = ('category', 'title', 'created_at', 'updated_at')
    search_fields = ('title', 'category')
    list_filter = ('category', 'created_at')
