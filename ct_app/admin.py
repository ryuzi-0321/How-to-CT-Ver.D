from django.contrib import admin
from .models import Sick, Form, Protocol, NightShift, NightShiftImage, Question, Answer

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

@admin.register(NightShift)
class NightShiftAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_at', 'updated_at')
    search_fields = ('name',)
    list_filter = ('created_at',)

@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ('title', 'author', 'created_at', 'updated_at', 'related_protocols')
    search_fields = ('title', 'content')
    list_filter = ('created_at', 'related_protocols')

@admin.register(Answer)
class AnswerAdmin(admin.ModelAdmin):
    list_display = ('question', 'author', 'created_at', 'updated_at')
    search_fields = ('content',)
    list_filter = ('created_at', 'question')