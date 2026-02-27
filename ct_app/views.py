from django.shortcuts import render, get_object_or_404, redirect
from django.views import View
from django.db.models import Q
from django.http import JsonResponse, HttpResponse
from django.core.management import call_command
from django.conf import settings
from .models import Sick, Form, Protocol, BackupHistory
from .forms import SickForm, FormForm, ProtocolForm

import os
import shutil
import zipfile
import tempfile
import json
from io import StringIO, BytesIO
from datetime import datetime

def get_protocol_data(request):
    title = request.GET.get('title', None)
    data = Protocol.objects.filter(title=title).first()
    
    if data:
        return JsonResponse({
            'exists': True,
            'protocol_text': data.protocol_detail, # Protocolモデルのフィールド名に合わせる
            'processing': '', # 必要に応じて
            'processing_text': data.processing_detail,
            'contrast': '', # 必要に応じて
            'contrast_text': data.contrast_detail,
        })
        
    data = Sick.objects.filter(protocol=title).order_by('-created_at').first()
    
    if data:
        return JsonResponse({
            'exists': True,
            'protocol_text': data.protocol_text,
            'processing': data.processing,
            'processing_text': data.processing_text,
            'contrast': data.contrast,
            'contrast_text': data.contrast_text,
        })
    return JsonResponse({'exists': False})

def protocol_autocomplete(request):
    term = request.GET.get('term', '')
    protocol_titles = list(Protocol.objects.filter(title__icontains=term).values_list('title', flat=True))
    sick_titles = list(Sick.objects.filter(protocol__icontains=term).values_list('protocol', flat=True))
    
    titles = list(set(protocol_titles + sick_titles))
    return JsonResponse(titles, safe=False)

class IndexView(View):
    """ホームページ"""
    def get(self, request):
        forms = Form.objects.all().order_by('-created_at')
        sicks = Sick.objects.all()
        protocols = Protocol.objects.all()
        context = {
            'page_title': 'ホーム',
            'forms': forms,
            'sicks': sicks,
            'protocols': protocols,
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


# ===== バックアップ・復元機能 =====

class BackupPageView(View):
    """バックアップ管理ページ"""
    def get(self, request):
        histories = BackupHistory.objects.all()[:10]
        context = {
            'page_title': 'バックアップ管理',
            'histories': histories,
        }
        return render(request, 'ct_app/backup.html', context)


class ExportBackupView(View):
    """バックアップ作成・ダウンロード"""
    def get(self, request):
        zip_filename = None
        try:
            # 一時ディレクトリを作成
            with tempfile.TemporaryDirectory() as temp_dir:
                # DBデータをJSONでダンプ
                db_file = os.path.join(temp_dir, 'db.json')
                with open(db_file, 'w', encoding='utf-8') as f:
                    call_command('dumpdata', stdout=f)
                
                # メディアファイルをコピー
                media_backup_dir = os.path.join(temp_dir, 'media')
                if os.path.exists(settings.MEDIA_ROOT):
                    shutil.copytree(settings.MEDIA_ROOT, media_backup_dir)
                else:
                    os.makedirs(media_backup_dir)
                
                # ZIPファイルを作成
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                zip_filename = f'backup_{timestamp}.zip'
                zip_path = os.path.join(temp_dir, zip_filename)
                
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    # db.jsonを追加
                    zipf.write(db_file, arcname='db.json')
                    
                    # mediaディレクトリを再帰的に追加
                    if os.path.exists(media_backup_dir):
                        for root, dirs, files in os.walk(media_backup_dir):
                            for file in files:
                                file_path = os.path.join(root, file)
                                arcname = os.path.relpath(file_path, temp_dir)
                                zipf.write(file_path, arcname=arcname)
                
                # ZIPファイルを読み込んでレスポンス
                with open(zip_path, 'rb') as f:
                    zip_content = f.read()
                
                # バックアップ履歴を記録（成功）
                BackupHistory.objects.create(
                    backup_type='export',
                    filename=zip_filename,
                    status='success'
                )
                
                response = HttpResponse(zip_content, content_type='application/zip')
                response['Content-Disposition'] = f'attachment; filename="{zip_filename}"'
                return response
        
        except Exception as e:
            # エラーが発生した場合
            BackupHistory.objects.create(
                backup_type='export',
                filename=zip_filename,
                status='failed',
                error_message=str(e)
            )
            
            context = {
                'page_title': 'バックアップ管理',
                'error': f'バックアップ作成に失敗しました: {str(e)}',
                'histories': BackupHistory.objects.all()[:10],
            }
            return render(request, 'ct_app/backup.html', context)


class ImportBackupView(View):
    """バックアップ復元"""
    def post(self, request):
        try:
            if 'backup_file' not in request.FILES:
                context = {
                    'page_title': 'バックアップ管理',
                    'error': 'ファイルが選択されていません',
                    'histories': BackupHistory.objects.all()[:10],
                }
                return render(request, 'ct_app/backup.html', context)
            
            backup_file = request.FILES['backup_file']
            
            # アップロードされたファイルが ZIP かどうかチェック
            if not backup_file.name.endswith('.zip'):
                context = {
                    'page_title': 'バックアップ管理',
                    'error': 'ZIPファイルのみ対応しています',
                    'histories': BackupHistory.objects.all()[:10],
                }
                return render(request, 'ct_app/backup.html', context)
            
            # 一時ディレクトリで展開
            with tempfile.TemporaryDirectory() as temp_dir:
                zip_path = os.path.join(temp_dir, 'backup.zip')
                
                # ZIPファイルを保存
                with open(zip_path, 'wb') as f:
                    for chunk in backup_file.chunks():
                        f.write(chunk)
                
                # ZIPを展開
                with zipfile.ZipFile(zip_path, 'r') as zipf:
                    zipf.extractall(temp_dir)
                
                # db.jsonを確認
                db_file = os.path.join(temp_dir, 'db.json')
                if not os.path.exists(db_file):
                    BackupHistory.objects.create(
                        backup_type='import',
                        filename=backup_file.name,
                        status='failed',
                        error_message='db.json が見つかりません'
                    )
                    
                    context = {
                        'page_title': 'バックアップ管理',
                        'error': 'バックアップファイル内に db.json が見つかりません',
                        'histories': BackupHistory.objects.all()[:10],
                    }
                    return render(request, 'ct_app/backup.html', context)
                
                # DBを復元
                with open(db_file, 'r', encoding='utf-8') as f:
                    call_command('loaddata', f.name, verbosity=0)
                
                # メディアファイルを復元
                media_backup_dir = os.path.join(temp_dir, 'media')
                if os.path.exists(media_backup_dir):
                    # 既存のメディアファイルをバックアップ
                    if os.path.exists(settings.MEDIA_ROOT):
                        backup_media = settings.MEDIA_ROOT + '_backup'
                        if os.path.exists(backup_media):
                            shutil.rmtree(backup_media)
                        shutil.move(settings.MEDIA_ROOT, backup_media)
                    
                    # 復元したメディアをコピー
                    shutil.copytree(media_backup_dir, settings.MEDIA_ROOT)
                
                # バックアップ履歴を記録（成功）
                BackupHistory.objects.create(
                    backup_type='import',
                    filename=backup_file.name,
                    status='success'
                )
                
                context = {
                    'page_title': 'バックアップ管理',
                    'success': 'バックアップを復元しました',
                    'histories': BackupHistory.objects.all()[:10],
                }
                return render(request, 'ct_app/backup.html', context)
        
        except Exception as e:
            BackupHistory.objects.create(
                backup_type='import',
                filename=backup_file.name if 'backup_file' in request.FILES else 'unknown',
                status='failed',
                error_message=str(e)
            )
            
            context = {
                'page_title': 'バックアップ管理',
                'error': f'復元に失敗しました: {str(e)}',
                'histories': BackupHistory.objects.all()[:10],
            }
            return render(request, 'ct_app/backup.html', context)
