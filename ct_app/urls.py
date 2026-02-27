from django.urls import path
from . import views

urlpatterns = [
    # ホーム
    path('', views.IndexView.as_view(), name='index'),
    
    # 疾患関連
    path('sick/', views.SickListView.as_view(), name='sick_list'),
    path('sick/search/', views.SickSearchView.as_view(), name='sick_search'),
    path('sick/<int:pk>/', views.SickDetailView.as_view(), name='sick_detail'),
    path('sick/create/', views.SickCreateView.as_view(), name='sick_create'),
    path('sick/<int:pk>/update/', views.SickUpdateView.as_view(), name='sick_update'),
    path('sick/<int:pk>/delete/', views.SickDeleteView.as_view(), name='sick_delete'),
    
    # お知らせ関連
    path('form/', views.FormListView.as_view(), name='form_list'),
    path('form/<int:pk>/', views.FormDetailView.as_view(), name='form_detail'),
    path('form/create/', views.FormCreateView.as_view(), name='form_create'),
    path('form/<int:pk>/update/', views.FormUpdateView.as_view(), name='form_update'),
    path('form/<int:pk>/delete/', views.FormDeleteView.as_view(), name='form_delete'),
    
    # CTプロトコル関連
    path('protocol/', views.ProtocolListView.as_view(), name='protocol_list'),
    path('protocol/<int:pk>/', views.ProtocolDetailView.as_view(), name='protocol_detail'),
    path('protocol/create/', views.ProtocolCreateView.as_view(), name='protocol_create'),
    path('protocol/<int:pk>/update/', views.ProtocolUpdateView.as_view(), name='protocol_update'),
    path('protocol/<int:pk>/delete/', views.ProtocolDeleteView.as_view(), name='protocol_delete'),
    
    # バックアップ・復元関連
    path('backup/', views.BackupPageView.as_view(), name='backup'),
    path('backup/export/', views.ExportBackupView.as_view(), name='backup_export'),
    path('backup/import/', views.ImportBackupView.as_view(), name='backup_import'),
    
    # urls.py
    path('api/get_protocol_data/', views.get_protocol_data, name='get_protocol_data'),
    path('api/protocol_autocomplete/', views.protocol_autocomplete, name='protocol_autocomplete'),
]
