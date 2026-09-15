from django.shortcuts import render, get_object_or_404, redirect
from django.views import View
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.contrib.auth import get_user_model
from django.urls import reverse_lazy
from django.db.models import Q
from django.db import connections
from django.http import JsonResponse, HttpResponse
from django.core.management import call_command
from django.conf import settings
from .models import Sick, Form, Protocol, NightShift, NightShiftImage, BackupHistory, NoticeImage, SickImage,ProtocolImage, Question, Answer
from .forms import SickForm, FormForm, ProtocolForm, NightShiftForm, QuestionForm, AnswerForm

import os
import shutil
import zipfile
import tempfile
import json
from io import StringIO, BytesIO
from datetime import datetime
import pandas as pd
from django.utils.timezone import now
import io

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

def form_create(request):
    if request.method == 'POST':
        form = FormForm(request.POST, request.FILES)
        files = request.FILES.getlist('images')
        if form.is_valid():
            notice = form.save()
            # 画像を保存
            for file in files:
                NoticeImage.objects.create(form=notice, image=file)
            return redirect('form_list')
    else:
        form = FormForm()
    
    context = {
        'page_title': '新規お知らせ作成',
        'form': form,
    }
    return render(request, 'ct_app/form_form.html', context)

def night_shift_create(request):
    if request.method == 'POST':
        form = NightShiftForm(request.POST, request.FILES)
        files = request.FILES.getlist('images')
        if form.is_valid():
            nightshift = form.save()
            # 画像を保存
            for file in files:
                NightShiftImage.objects.create(nightshift=nightshift, image=file)
            return redirect('nightshift_list')
    else:
        form = NightShiftForm()
    
    context = {
        'page_title': '新規夜勤対応作成',
        'form': form,
    }
    return render(request, 'ct_app/nightshift_form.html', context)

class IndexView(View):
    """ホームページ"""
    def get(self, request):
        forms = Form.objects.all().order_by('-created_at')
        sicks = Sick.objects.all()
        protocols = Protocol.objects.all()
        night_shifts = NightShift.objects.all().order_by('-created_at')
        nightshift_count = NightShift.objects.count()
        context = {
                'page_title': 'ホーム',
                'forms': forms,
                'sicks': sicks,
                'protocols': protocols,
                'night_shifts': night_shifts,
                'nightshift_count': nightshift_count,
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

            # 1. 疾患の基本情報を保存 (変数名を小文字のsickにして衝突を避ける)

            sick = form.save()



            # 2. カテゴリー（タブ）とフォームのフィールド名を紐付ける辞書

            # ※forms.pyで定義したフィールド名と一致させてください

            image_mappings = {

                'disease': 'disease_images',      # 疾患情報タブ

                'protocol': 'protocol_images',    # 撮影プロトコルタブ

                'contrast': 'contrast_images',    # 造影プロトコルタブ

                'processing': 'processing_images',# 画像処理タブ

            }



            # 3. ループでそれぞれのボタンから来た画像を保存

            for category, field_name in image_mappings.items():

                files = request.FILES.getlist(field_name) # 各フィールドからファイルリストを取得

                for f in files:

                    SickImage.objects.create(

                        sick=sick, 

                        image=f, 

                        category=category  # ここで「どのタブか」を保存！

                    )



            return redirect('sick_detail', pk=sick.pk)



        context = {

            'page_title': '新規疾患作成',

            'form': form,

        }

        return render(request, 'ct_app/sick_form.html', context)



# views.py の SickUpdateView を修正



class SickUpdateView(View):

    """疾患編集"""

    def get(self, request, pk):

        # (get メソッドはそのまま)

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

            sick = form.save()



            image_mappings = {

                'disease': 'disease_images',

                'protocol': 'protocol_images',

                'contrast': 'contrast_images',

                'processing': 'processing_images',

            }



            for category, field_name in image_mappings.items():

                files = request.FILES.getlist(field_name)

                

                # ★★★ 重要：ここを追加！ ★★★

                if files:

                    # そのカテゴリーの古い画像をデータベースとファイルの両方から削除

                    images_to_delete = sick.images.filter(category=category)

                    for old_img in images_to_delete:

                        if old_img.image:

                            # 物理ファイルの削除

                            if os.path.isfile(old_img.image.path):

                                os.remove(old_img.image.path)

                    

                    # データベースのレコードを削除

                    images_to_delete.delete()



                # その後、新しい画像を保存

                for f in files:

                    SickImage.objects.create(sick=sick, image=f, category=category)

            

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

        context = {

            'page_title': '新規お知らせ作成',

            'form': FormForm(),

        }

        return render(request, 'ct_app/form_form.html', context)

    

    def post(self, request):

        form = FormForm(request.POST, request.FILES)

        files = request.FILES.getlist('images')

        if form.is_valid():

            notice = form.save()

            for file in files:

                NoticeImage.objects.create(notice=notice, image=file)

            return redirect('form_detail', pk=notice.pk)

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

            notice = form.save()

            files = request.FILES.getlist('images')

            for file in files:

                NoticeImage.objects.create(form=notice, image=file)



            return redirect('form_detail', pk=notice.pk)

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


def _backup_history_context(**extra):
    context = {
        'page_title': 'バックアップ管理',
        'histories': BackupHistory.objects.all()[:10],
    }
    context.update(extra)
    return context


def _safe_zip_extract(zipf, dest_dir):
    """ZIP Slip を避けて安全に展開する。"""
    base = os.path.realpath(dest_dir)
    for member in zipf.infolist():
        target = os.path.realpath(os.path.join(dest_dir, member.filename))
        if target != base and not target.startswith(base + os.sep):
            raise ValueError('不正なパスを含むZIPファイルです')
    zipf.extractall(dest_dir)


def _sqlite_db_path():
    db = settings.DATABASES.get('default', {})
    if db.get('ENGINE') != 'django.db.backends.sqlite3':
        return None
    return os.fspath(db.get('NAME'))


def _write_full_backup_zip(zip_path, include_json=True):
    """現在状態を ZIP に保存。SQLite DB本体 + db.json + media を含める。"""
    with tempfile.TemporaryDirectory() as temp_dir:
        db_json = os.path.join(temp_dir, 'db.json')
        if include_json:
            with open(db_json, 'w', encoding='utf-8') as f:
                # セッション等も含め、アプリ全体を復元できるバックアップにする。
                call_command('dumpdata', stdout=f)

        db_path = _sqlite_db_path()
        db_copy = None
        if db_path and os.path.exists(db_path):
            # SQLite の書込みを確実にディスクへ反映してからコピーする。
            connections.close_all()
            db_copy = os.path.join(temp_dir, 'database.sqlite3')
            shutil.copy2(db_path, db_copy)

        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            if include_json:
                zipf.write(db_json, arcname='db.json')
            if db_copy:
                zipf.write(db_copy, arcname='database.sqlite3')
            if os.path.exists(settings.MEDIA_ROOT):
                for root, dirs, files in os.walk(settings.MEDIA_ROOT):
                    for filename in files:
                        file_path = os.path.join(root, filename)
                        rel = os.path.relpath(file_path, settings.MEDIA_ROOT)
                        zipf.write(file_path, arcname=os.path.join('media', rel))


def _create_pre_restore_safety_backup():
    """復元直前の状態をサーバー内に自動退避して、ファイル名を返す。"""
    safety_dir = os.path.join(settings.BASE_DIR, 'restore_safety_backups')
    os.makedirs(safety_dir, exist_ok=True)
    stamp = now().strftime('%Y%m%d_%H%M%S')
    filename = f'auto_before_restore_{stamp}.zip'
    path = os.path.join(safety_dir, filename)
    _write_full_backup_zip(path)
    return filename, path


def _restore_media_from_dir(media_backup_dir):
    """media をバックアップ時点へ完全置換する。"""
    if os.path.exists(settings.MEDIA_ROOT):
        shutil.rmtree(settings.MEDIA_ROOT)
    if os.path.exists(media_backup_dir):
        shutil.copytree(media_backup_dir, settings.MEDIA_ROOT)
    else:
        os.makedirs(settings.MEDIA_ROOT, exist_ok=True)


def _restore_from_extracted_backup(temp_dir):
    """展開済みバックアップを復元。新形式(SQLite本体)を優先し旧形式JSONにも対応。"""
    sqlite_file = os.path.join(temp_dir, 'database.sqlite3')
    db_json = os.path.join(temp_dir, 'db.json')
    media_backup_dir = os.path.join(temp_dir, 'media')

    if os.path.exists(sqlite_file):
        db_path = _sqlite_db_path()
        if not db_path:
            raise RuntimeError('この完全バックアップはSQLite用ですが、現在のDBがSQLiteではありません')
        connections.close_all()
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        shutil.copy2(sqlite_file, db_path)
        connections.close_all()
        # 古いバックアップを新しいアプリ版へ戻した場合も、現在のmigrationまで安全に追従させる。
        call_command('migrate', interactive=False, verbosity=0)
    elif os.path.exists(db_json):
        # 旧バックアップも「その時点」に戻せるよう、先にDBを空にしてから読み込む。
        # inhibit_post_migrate=True により、fixture内のcontenttypes/permissionsとの重複を防ぐ。
        call_command(
            'flush',
            interactive=False,
            verbosity=0,
            reset_sequences=True,
            inhibit_post_migrate=True,
        )
        call_command('loaddata', db_json, verbosity=0)
    else:
        raise ValueError('バックアップファイル内に database.sqlite3 または db.json が見つかりません')

    _restore_media_from_dir(media_backup_dir)


class BackupPageView(View):
    """バックアップ管理ページ"""
    def get(self, request):
        safety_dir = os.path.join(settings.BASE_DIR, 'restore_safety_backups')
        safety_files = []
        if os.path.isdir(safety_dir):
            safety_files = sorted(
                [x for x in os.listdir(safety_dir) if x.endswith('.zip')],
                reverse=True,
            )[:5]
        return render(request, 'ct_app/backup.html', _backup_history_context(safety_files=safety_files))


class ExportBackupView(View):
    """バックアップ作成・ダウンロード"""
    def get(self, request):
        zip_filename = None
        export_format = request.GET.get('format', 'zip')
        timestamp = now().strftime('%Y%m%d_%H%M%S')
        if export_format == 'excel':
            try:
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    df_sick = pd.DataFrame(list(Sick.objects.all().values()))
                    df_form = pd.DataFrame(list(Form.objects.all().values()))
                    df_protocol = pd.DataFrame(list(Protocol.objects.all().values()))
                    df_nightshift = pd.DataFrame(list(NightShift.objects.all().values()))
                    df_question = pd.DataFrame(list(Question.objects.all().values()))

                    def remove_timezone(df):
                        if not df.empty:
                            for col in df.columns:
                                if pd.api.types.is_datetime64_any_dtype(df[col]):
                                    df[col] = df[col].dt.tz_localize(None)
                        return df

                    remove_timezone(df_sick).to_excel(writer, sheet_name='Sicks', index=False)
                    remove_timezone(df_form).to_excel(writer, sheet_name='Forms', index=False)
                    remove_timezone(df_protocol).to_excel(writer, sheet_name='Protocols', index=False)
                    remove_timezone(df_nightshift).to_excel(writer, sheet_name='NightShifts', index=False)
                    remove_timezone(df_question).to_excel(writer, sheet_name='Questions', index=False)
                output.seek(0)
                BackupHistory.objects.create(backup_type='export', filename=f'backup_{timestamp}.xlsx', status='success')
                response = HttpResponse(
                    output.read(),
                    content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                )
                response['Content-Disposition'] = f'attachment; filename="backup_{timestamp}.xlsx"'
                return response
            except Exception as e:
                BackupHistory.objects.create(backup_type='export', status='failed', error_message=str(e))
                return render(request, 'ct_app/backup.html', _backup_history_context(error=f'Excel作成失敗: {e}'))

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                zip_filename = f'backup_{timestamp}.zip'
                zip_path = os.path.join(temp_dir, zip_filename)
                _write_full_backup_zip(zip_path)
                with open(zip_path, 'rb') as f:
                    zip_content = f.read()
            BackupHistory.objects.create(backup_type='export', filename=zip_filename, status='success')
            response = HttpResponse(zip_content, content_type='application/zip')
            response['Content-Disposition'] = f'attachment; filename="{zip_filename}"'
            return response
        except Exception as e:
            BackupHistory.objects.create(
                backup_type='export', filename=zip_filename, status='failed', error_message=str(e)
            )
            return render(
                request,
                'ct_app/backup.html',
                _backup_history_context(error=f'バックアップ作成に失敗しました: {e}')
            )


class SafetyBackupDownloadView(View):
    """復元前に自動退避した安全バックアップをダウンロードする。"""
    def get(self, request, filename):
        safe_name = os.path.basename(filename)
        if safe_name != filename or not safe_name.startswith('auto_before_restore_') or not safe_name.endswith('.zip'):
            return HttpResponse('Invalid backup filename', status=400)
        path = os.path.join(settings.BASE_DIR, 'restore_safety_backups', safe_name)
        if not os.path.isfile(path):
            return HttpResponse('Backup not found', status=404)
        with open(path, 'rb') as f:
            content = f.read()
        response = HttpResponse(content, content_type='application/zip')
        response['Content-Disposition'] = f'attachment; filename="{safe_name}"'
        return response


class ImportBackupView(View):
    """バックアップ復元"""
    def post(self, request):
        backup_file = request.FILES.get('backup_file')
        if not backup_file:
            return render(request, 'ct_app/backup.html', _backup_history_context(error='ファイルが選択されていません'))

        if backup_file.name.lower().endswith('.xlsx'):
            try:
                # ExcelはCT情報の編集用。従来通り対象モデルだけを置換する。
                Sick.objects.all().delete()
                Form.objects.all().delete()
                Protocol.objects.all().delete()
                NightShift.objects.all().delete()
                Question.objects.all().delete()
                df_sicks = pd.read_excel(backup_file, sheet_name='Sicks').fillna('')
                df_forms = pd.read_excel(backup_file, sheet_name='Forms').fillna('')
                df_protocols = pd.read_excel(backup_file, sheet_name='Protocols').fillna('')
                df_nightshifts = pd.read_excel(backup_file, sheet_name='NightShifts').fillna('')
                df_questions = pd.read_excel(backup_file, sheet_name='Questions').fillna('')
                for _, row in df_sicks.iterrows(): Sick.objects.create(**row.to_dict())
                for _, row in df_forms.iterrows(): Form.objects.create(**row.to_dict())
                for _, row in df_protocols.iterrows(): Protocol.objects.create(**row.to_dict())
                for _, row in df_nightshifts.iterrows(): NightShift.objects.create(**row.to_dict())
                for _, row in df_questions.iterrows(): Question.objects.create(**row.to_dict())
                BackupHistory.objects.create(backup_type='import', filename=backup_file.name, status='success')
                return render(request, 'ct_app/backup.html', _backup_history_context(success='Excelから編集用データを反映しました'))
            except Exception as e:
                BackupHistory.objects.create(backup_type='import', filename=backup_file.name, status='failed', error_message=str(e))
                return render(request, 'ct_app/backup.html', _backup_history_context(error=f'Excel反映に失敗しました: {e}'))

        if not backup_file.name.lower().endswith('.zip'):
            return render(request, 'ct_app/backup.html', _backup_history_context(error='ZIPまたはExcelファイルを選択してください'))

        safety_filename = None
        safety_path = None
        with tempfile.TemporaryDirectory() as temp_dir:
            zip_path = os.path.join(temp_dir, 'incoming_backup.zip')
            try:
                with open(zip_path, 'wb') as f:
                    for chunk in backup_file.chunks():
                        f.write(chunk)
                with zipfile.ZipFile(zip_path, 'r') as zipf:
                    _safe_zip_extract(zipf, temp_dir)

                # 変更前に必ず完全バックアップを自動作成。
                safety_filename, safety_path = _create_pre_restore_safety_backup()

                try:
                    _restore_from_extracted_backup(temp_dir)
                except Exception:
                    # 復元途中に失敗した場合は、直前の自動退避から元へ戻す。
                    with tempfile.TemporaryDirectory() as rollback_dir:
                        with zipfile.ZipFile(safety_path, 'r') as zf:
                            _safe_zip_extract(zf, rollback_dir)
                        _restore_from_extracted_backup(rollback_dir)
                    raise

                BackupHistory.objects.create(backup_type='import', filename=backup_file.name, status='success')
                return render(
                    request,
                    'ct_app/backup.html',
                    _backup_history_context(
                        success='完全バックアップを復元しました。バックアップ時点の設定・勤務表・配置・画像へ置き換えました。',
                        safety_backup=safety_filename,
                    )
                )
            except Exception as e:
                try:
                    BackupHistory.objects.create(
                        backup_type='import', filename=backup_file.name, status='failed', error_message=str(e)
                    )
                except Exception:
                    pass
                msg = f'復元に失敗しました: {e}'
                if safety_filename:
                    msg += f'（復元前の状態は {safety_filename} に自動退避しています）'
                return render(request, 'ct_app/backup.html', _backup_history_context(error=msg, safety_backup=safety_filename))

# ===== 夜勤対応 =====

class NightShiftListView(View):
    """夜勤対応一覧"""
    def get(self, request):
        nightshifts = NightShift.objects.all()
        context = {
            'page_title': '夜勤対応',
            'nightshifts': nightshifts,
        }
        return render(request, 'ct_app/nightshift_list.html', context)


class NightShiftDetailView(View):
    """夜勤対応詳細"""
    def get(self, request, pk):
        nightshift = get_object_or_404(NightShift, pk=pk)
        context = {
            'page_title': nightshift.name,
            'nightshift': nightshift,
        }
        return render(request, 'ct_app/nightshift_detail.html', context)


class NightShiftCreateView(View):
    """夜勤対応作成"""
    def get(self, request):
        form = NightShiftForm()
        context = {
            'page_title': '新規夜勤対応作成',
            'form': form,
        }
        return render(request, 'ct_app/nightshift_form.html', context)
    
    def post(self, request):
        form = NightShiftForm(request.POST, request.FILES)
        if form.is_valid():
            nightshift = form.save()
            
            # 画像を保存
            files = request.FILES.getlist('images')
            for file in files:
                NightShiftImage.objects.create(nightshift=nightshift, image=file)
            
            return redirect('nightshift_detail', pk=nightshift.pk)
        
        context = {
            'page_title': '新規夜勤対応作成',
            'form': form,
        }
        return render(request, 'ct_app/nightshift_form.html', context)


class NightShiftUpdateView(View):
    """夜勤対応編集"""
    def get(self, request, pk):
        nightshift = get_object_or_404(NightShift, pk=pk)
        form = NightShiftForm(instance=nightshift)
        context = {
            'page_title': '夜勤対応編集',
            'form': form,
            'nightshift': nightshift,
        }
        return render(request, 'ct_app/nightshift_form.html', context)
    
    def post(self, request, pk):
        nightshift = get_object_or_404(NightShift, pk=pk)
        form = NightShiftForm(request.POST, request.FILES, instance=nightshift)
        if form.is_valid():
            nightshift = form.save()
            
            # 新しい画像がアップロードされた場合
            files = request.FILES.getlist('images')
            if files:
                # 古い画像をデータベースとファイルの両方から削除
                images_to_delete = nightshift.images.all()
                for old_img in images_to_delete:
                    if old_img.image:
                        # 物理ファイルの削除
                        if os.path.isfile(old_img.image.path):
                            os.remove(old_img.image.path)
                
                # データベースのレコードを削除
                images_to_delete.delete()
                
                # 新しい画像を保存
                for file in files:
                    NightShiftImage.objects.create(nightshift=nightshift, image=file)
            
            return redirect('nightshift_detail', pk=nightshift.pk)
        
        context = {
            'page_title': '夜勤対応編集',
            'form': form,
            'nightshift': nightshift,
        }
        return render(request, 'ct_app/nightshift_form.html', context)


class NightShiftDeleteView(View):
    """夜勤対応削除"""
    def post(self, request, pk):
        nightshift = get_object_or_404(NightShift, pk=pk)
        nightshift.delete()
        return redirect('nightshift_list')
    
# 1. 質問一覧画面
class QuestionListView(ListView):
    model = Question
    template_name = 'ct_app/question_list.html'
    context_object_name = 'questions'
    ordering = ['-created_at']  # 新しい質問を上に

# 2. 質問作成画面（ログイン必須）
class QuestionCreateView(CreateView):
    model = Question
    template_name = 'ct_app/question_form.html'
    fields = ['title', 'content']  # 'related_protocol' をコメントアウト
    success_url = reverse_lazy('question_list')

    def form_valid(self, form):
        self.object = form.save(commit=False)
        User = get_user_model()
        if not User.objects.exists():
            # ユーザーが存在しない場合はエラーを返す
            default_user = User.objects.create_superuser(
                username='admin',
                email='admin@example.com',
                password='adminpassword'
            )
        else:
            default_user = User.objects.first()  # 最初のユーザーを取得
        if self.request.user.is_authenticated:
            self.object.author = self.request.user
        else:
            # ログインしていない場合は最初のユーザー（管理者など）をセット
            self.object.author = User.objects.first()
        related_protocol_id = self.request.POST.get('related_protocol')
        if related_protocol_id:
            self.object.related_protocol = get_object_or_404(Protocol, id=related_protocol_id)
            
        self.object.save()    

        return redirect(self.get_success_url())

    # 3. 質問詳細 ＆ 回答投稿画面
def question_detail(request, pk):
    question = get_object_or_404(Question, pk=pk)
    
    if request.method == 'POST':
        # 🚨 ログインしていない場合の安全装置（テストユーザーを割り当てる）
        User = get_user_model()
        if request.user.is_authenticated:
            author = request.user
        else:
            # データベースにユーザーがいなければ作成し、いれば最初のユーザーを使う
            if not User.objects.exists():
                author = User.objects.create_user(
                    username='test_user', 
                    email='test@example.com', 
                    password='password123'
                )
            else:
                author = User.objects.first()

        content = request.POST.get('content')
        suggested_protocol_id = request.POST.get('suggested_protocol')
        
        # 回答オブジェクトの作成と保存
        answer = Answer(
            question=question,
            author=author,  # ログイン制限をなくし、判別したauthorをセット
            content=content
        )
        
        if suggested_protocol_id:
            answer.suggested_protocol_id = suggested_protocol_id
            
        answer.save()
        return redirect('question_detail', pk=pk)

    # GETリクエスト時の処理
    answers = question.answers.all()  # 質問に紐づく回答一覧を取得（リレーション名に合わせて調整してください）
    # もし protocols をテンプレートに渡す必要がある場合
    from .models import Protocol # 必要に応じてインポート
    protocols = Protocol.objects.all() 

    context = {
        'question': question,
        'answers': answers,
        'protocols': protocols,
    }
    return render(request, 'ct_app/question_detail.html', context)
    
def index(request):
    questions = Question.objects.all().order_by('-created_at')
    sicks_count = Sick.objects.count()
    forms_count = Form.objects.count()
    protocols_count = Protocol.objects.count()
    night_shifts_count = NightShift.objects.count()
    question_count = questions.count()
    sicks_list = Sick.objects.all().order_by('-created_at')
    forms_list = Form.objects.all().order_by('-created_at')
    protocols_list = Protocol.objects.all().order_by('-created_at')
    nightshift_list = NightShift.objects.all().order_by('-created_at') if hasattr(NightShift, 'created_at') else NightShift.objects.all()

    today_assignment = None
    today_assignment_exists = False
    today_duties = []
    early_assignments = []
    tomorrow_early_assignments = []
    try:
        from datetime import timedelta
        from django.utils import timezone
        from daily_assignment.models import DailyBoard, AssignmentCell

        def get_early_assignments(board):
            """指定日の7:30配置を「早出」として大分類付きで返す。"""
            if not board:
                return []

            qs = (
                AssignmentCell.objects
                .filter(board=board, row_key="0730")
                .exclude(value="")
                .select_related("area")
                .order_by("area__display_order", "area__id", "slot_index")
            )
            by_staff = {}
            for cell in qs:
                raw_value = str(cell.value or "").strip()
                if not raw_value:
                    continue

                names = [raw_value]
                for sep in ("\n", "、", ","):
                    expanded = []
                    for item in names:
                        expanded.extend(item.split(sep))
                    names = expanded

                category = (cell.area.category or cell.area.name or "その他").strip()
                for name in (item.strip() for item in names):
                    if not name:
                        continue
                    by_staff.setdefault(name, [])
                    if category not in by_staff[name]:
                        by_staff[name].append(category)

            return [
                {"name": name, "categories": categories}
                for name, categories in by_staff.items()
            ]

        today = timezone.localdate()
        tomorrow = today + timedelta(days=1)

        today_assignment = DailyBoard.objects.filter(board_date=today).first()
        tomorrow_assignment = DailyBoard.objects.filter(board_date=tomorrow).first()

        if today_assignment:
            today_assignment_exists = True
            for label, value, icon in [
                ('夜勤', today_assignment.night_shift, 'bi-moon-stars'),
                ('明け', today_assignment.night_shift_after, 'bi-sunrise'),
                ('休日日勤', today_assignment.holiday_day_shift, 'bi-calendar-check'),
            ]:
                if str(value or '').strip():
                    today_duties.append({'label': label, 'value': value, 'icon': icon})

        early_assignments = get_early_assignments(today_assignment)
        tomorrow_early_assignments = get_early_assignments(tomorrow_assignment)

    except Exception:
        pass

    context = {
        'page_title': 'ホーム',
        'sick_list': sicks_list, 'form_list': forms_list, 'protocol_list': protocols_list,
        'nightshift_list': nightshift_list, 'questions': questions,
        'sicks_count': sicks_count, 'forms_count': forms_count, 'protocols_count': protocols_count,
        'night_shifts_count': night_shifts_count, 'question_count': question_count,
        'today_assignment': today_assignment, 'today_assignment_exists': today_assignment_exists,
        'today_duties': today_duties, 'early_assignments': early_assignments,
        'tomorrow_early_assignments': tomorrow_early_assignments,
    }
    return render(request, 'ct_app/index.html', context)

class QuestionUpdateView(UpdateView):
    def get(self, request, pk):
        question = get_object_or_404(Question, pk=pk)
        form = QuestionForm(instance=question)
        context = {
            'page_title': '掲示板編集',
            'form': form,
            'question': question,
        }
        return render(request, 'ct_app/question_form.html', context)

class QuestionDeleteView(DeleteView):
    def post(self, request, pk):
        question = get_object_or_404(Question, pk=pk)
        question.delete()
        return redirect('question_list')
    
def answer_create(request, question_id):
    question = get_object_or_404(Question, pk=question_id)
    
    if request.method == 'POST':
        form = AnswerForm(request.POST)
        if form.is_valid():
            answer = form.save(commit=False)
            answer.question = question  # 紐付ける質問を設定
            answer.save()
            
    return redirect('ct_app:question_detail', pk=question_id)    