from django import forms
from .models import Sick, Form, Protocol

class SickForm(forms.ModelForm):
    """疾患データフォーム"""
    class Meta:
        model = Sick
        fields = [
            'diesease', 'diesease_text', 'keyword',
            'protocol', 'protocol_text',
            'processing', 'processing_text',
            'contrast', 'contrast_text',
            'diesease_img', 'protocol_img',
            'processing_img', 'contrast_img'
        ]
        widgets = {
            'diesease': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '疾患名'}),
            'diesease_text': forms.Textarea(attrs={'class': 'form-control', 'rows': 4, 'placeholder': '疾患詳細'}),
            'keyword': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '症状・キーワード'}),
            'protocol': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '撮影プロトコル'}),
            'protocol_text': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': '撮影詳細'}),
            'processing': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '画像処理'}),
            'processing_text': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': '処理方法'}),
            'contrast': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '造影プロトコル'}),
            'contrast_text': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': '造影詳細'}),
        }

class FormForm(forms.ModelForm):
    """お知らせフォーム"""
    class Meta:
        model = Form
        fields = ['title', 'main', 'post_img']
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'タイトル'}),
            'main': forms.Textarea(attrs={'class': 'form-control', 'rows': 5, 'placeholder': 'お知らせ内容'}),
        }

class ProtocolForm(forms.ModelForm):
    """CTプロトコルフォーム"""
    class Meta:
        model = Protocol
        fields = ['category', 'title', 'content', 'protocol_img']
        widgets = {
            'category': forms.Select(attrs={'class': 'form-control'}),
            'title': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'プロトコルタイトル'}),
            'content': forms.Textarea(attrs={'class': 'form-control', 'rows': 5, 'placeholder': 'プロトコル内容'}),
        }
