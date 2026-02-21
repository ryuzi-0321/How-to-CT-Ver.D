from django.shortcuts import render, get_object_or_404, redirect
from django.views import View
from django.db.models import Q
from django.http import JsonResponse
from .models import Sick, Form, Protocol
from .forms import SickForm, FormForm, ProtocolForm

class IndexView(View):
    """ホームページ"""
    def get(self, request):
        forms = Form.objects.all()[:5]
        context = {
            'page_title': 'ホーム',
            'forms': forms,
        }
        return render(request, 'ct_app/index.html', context)

class SickListView(View):
    """疾患一覧"""
    def get(self, request):
        sicks = Sick.objects.all()
        context = {
            'page_title': '疾患検索',
            'sicks': sicks,
        }
        return render(request, 'ct_app/sick_list.html', context)

class SickSearchView(View):
    """疾患検索"""
    def get(self, request):
        query = request.GET.get('q', '')
        sicks = []
        
        if query:
            sicks = Sick.objects.filter(
                Q(diesease__icontains=query) |
                Q(diesease_text__icontains=query) |
                Q(keyword__icontains=query) |
                Q(protocol__icontains=query)
            )
        
        context = {
            'page_title': '疾患検索',
            'sicks': sicks,
            'query': query,
        }
        return render(request, 'ct_app/sick_list.html', context)

class SickDetailView(View):
    """疾患詳細"""
    def get(self, request, pk):
        sick = get_object_or_404(Sick, pk=pk)
        context = {
            'page_title': sick.diesease,
            'sick': sick,
        }
        return render(request, 'ct_app/sick_detail.html', context)

class SickCreateView(View):
    """疾患作成"""
    def get(self, request):
        form = SickForm()
        context = {
            'page_title': '新規疾患作成',
            'form': form,
        }
        return render(request, 'ct_app/sick_form.html', context)
    
    def post(self, request):
        form = SickForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            return redirect('sick_detail', pk=form.instance.pk)
        context = {
            'page_title': '新規疾患作成',
            'form': form,
        }
        return render(request, 'ct_app/sick_form.html', context)

class SickUpdateView(View):
    """疾患編集"""
    def get(self, request, pk):
        sick = get_object_or_404(Sick, pk=pk)
        form = SickForm(instance=sick)
        context = {
            'page_title': '疾患編集',
            'form': form,
            'sick': sick,
        }
        return render(request, 'ct_app/sick_form.html', context)
    
    def post(self, request, pk):
        sick = get_object_or_404(Sick, pk=pk)
        form = SickForm(request.POST, request.FILES, instance=sick)
        if form.is_valid():
            form.save()
            return redirect('sick_detail', pk=sick.pk)
        context = {
            'page_title': '疾患編集',
            'form': form,
            'sick': sick,
        }
        return render(request, 'ct_app/sick_form.html', context)

class SickDeleteView(View):
    """疾患削除"""
    def post(self, request, pk):
        sick = get_object_or_404(Sick, pk=pk)
        sick.delete()
        return redirect('sick_list')

class FormListView(View):
    """お知らせ一覧"""
    def get(self, request):
        forms = Form.objects.all()
        context = {
            'page_title': 'お知らせ',
            'forms': forms,
        }
        return render(request, 'ct_app/form_list.html', context)

class FormDetailView(View):
    """お知らせ詳細"""
    def get(self, request, pk):
        form = get_object_or_404(Form, pk=pk)
        context = {
            'page_title': form.title,
            'form': form,
        }
        return render(request, 'ct_app/form_detail.html', context)

class FormCreateView(View):
    """お知らせ作成"""
    def get(self, request):
        form = FormForm()
        context = {
            'page_title': '新規お知らせ作成',
            'form': form,
        }
        return render(request, 'ct_app/form_form.html', context)
    
    def post(self, request):
        form = FormForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            return redirect('form_detail', pk=form.instance.pk)
        context = {
            'page_title': '新規お知らせ作成',
            'form': form,
        }
        return render(request, 'ct_app/form_form.html', context)

class FormUpdateView(View):
    """お知らせ編集"""
    def get(self, request, pk):
        form_obj = get_object_or_404(Form, pk=pk)
        form = FormForm(instance=form_obj)
        context = {
            'page_title': 'お知らせ編集',
            'form': form,
            'form_obj': form_obj,
        }
        return render(request, 'ct_app/form_form.html', context)
    
    def post(self, request, pk):
        form_obj = get_object_or_404(Form, pk=pk)
        form = FormForm(request.POST, request.FILES, instance=form_obj)
        if form.is_valid():
            form.save()
            return redirect('form_detail', pk=form_obj.pk)
        context = {
            'page_title': 'お知らせ編集',
            'form': form,
            'form_obj': form_obj,
        }
        return render(request, 'ct_app/form_form.html', context)

class FormDeleteView(View):
    """お知らせ削除"""
    def post(self, request, pk):
        form = get_object_or_404(Form, pk=pk)
        form.delete()
        return redirect('form_list')

class ProtocolListView(View):
    """CTプロトコル一覧"""
    def get(self, request):
        category = request.GET.get('category', '')
        if category:
            protocols = Protocol.objects.filter(category=category)
        else:
            protocols = Protocol.objects.all()
        
        categories = Protocol.CATEGORY_CHOICES
        context = {
            'page_title': 'CTプロトコル',
            'protocols': protocols,
            'categories': categories,
            'selected_category': category,
        }
        return render(request, 'ct_app/protocol_list.html', context)

class ProtocolDetailView(View):
    """CTプロトコル詳細"""
    def get(self, request, pk):
        protocol = get_object_or_404(Protocol, pk=pk)
        context = {
            'page_title': protocol.title,
            'protocol': protocol,
        }
        return render(request, 'ct_app/protocol_detail.html', context)

class ProtocolCreateView(View):
    """CTプロトコル作成"""
    def get(self, request):
        form = ProtocolForm()
        context = {
            'page_title': '新規プロトコル作成',
            'form': form,
        }
        return render(request, 'ct_app/protocol_form.html', context)
    
    def post(self, request):
        form = ProtocolForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            return redirect('protocol_detail', pk=form.instance.pk)
        context = {
            'page_title': '新規プロトコル作成',
            'form': form,
        }
        return render(request, 'ct_app/protocol_form.html', context)

class ProtocolUpdateView(View):
    """CTプロトコル編集"""
    def get(self, request, pk):
        protocol = get_object_or_404(Protocol, pk=pk)
        form = ProtocolForm(instance=protocol)
        context = {
            'page_title': 'プロトコル編集',
            'form': form,
            'protocol': protocol,
        }
        return render(request, 'ct_app/protocol_form.html', context)
    
    def post(self, request, pk):
        protocol = get_object_or_404(Protocol, pk=pk)
        form = ProtocolForm(request.POST, request.FILES, instance=protocol)
        if form.is_valid():
            form.save()
            return redirect('protocol_detail', pk=protocol.pk)
        context = {
            'page_title': 'プロトコル編集',
            'form': form,
            'protocol': protocol,
        }
        return render(request, 'ct_app/protocol_form.html', context)

class ProtocolDeleteView(View):
    """CTプロトコル削除"""
    def post(self, request, pk):
        protocol = get_object_or_404(Protocol, pk=pk)
        protocol.delete()
        return redirect('protocol_list')
