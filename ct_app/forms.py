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
        
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            if field_name != 'diesease':
                field.required = False

class FormForm(forms.ModelForm):
    """お知らせフォーム"""
    class Meta:
        model = Form
        fields = ['title', 'main', 'post_img']
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'タイトル'}),
            'main': forms.Textarea(attrs={'class': 'form-control', 'rows': 5, 'placeholder': 'お知らせ内容'}),
        }
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            if field_name != 'title':
                field.required = False    

class ProtocolForm(forms.ModelForm):
    """CTプロトコルフォーム"""
    class Meta:
        model = Protocol
        fields = ['category', 'title', 'content', 'protocol_detail', 'contrast_detail', 'processing_detail','protocol_img']
        widgets = {
            'category': forms.Select(attrs={'class': 'form-control'}),
            'title': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'プロトコルタイトル'}),
            'content': forms.Textarea(attrs={'class': 'form-control', 'rows': 5, 'placeholder': 'プロトコル概要'}),
            'protocol_detail': forms.Textarea(attrs={'class': 'form-control', 'rows': 5, 'placeholder': '撮影手順・条件など'}),
            'contrast_detail': forms.Textarea(attrs={'class': 'form-control', 'rows': 5, 'placeholder': '造影剤量・タイミングなど'}),
            'processing_detail': forms.Textarea(attrs={'class': 'form-control', 'rows': 5, 'placeholder': '再構成・3D処理など'}),
        }
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            if field_name != 'title' and field_name != 'category':
                field.required = False    
