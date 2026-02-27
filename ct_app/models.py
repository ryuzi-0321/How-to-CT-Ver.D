from django.db import models
from django.utils import timezone

class Sick(models.Model):
    """疾患データモデル"""
    diesease = models.CharField(max_length=255, unique=True)
    diesease_text = models.TextField()
    keyword = models.CharField(max_length=500, blank=True)
    protocol = models.CharField(max_length=255, blank=True)
    protocol_text = models.TextField(blank=True)
    processing = models.CharField(max_length=255, blank=True)
    processing_text = models.TextField(blank=True)
    contrast = models.CharField(max_length=255, blank=True)
    contrast_text = models.TextField(blank=True)
    diesease_img = models.ImageField(upload_to='sick_images/', null=True, blank=True)
    protocol_img = models.ImageField(upload_to='protocol_images/', null=True, blank=True)
    processing_img = models.ImageField(upload_to='processing_images/', null=True, blank=True)
    contrast_img = models.ImageField(upload_to='contrast_images/', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['diesease']
        verbose_name_plural = "Sicks"
    
    def __str__(self):
        return self.diesease

class Form(models.Model):
    """お知らせモデル"""
    title = models.CharField(max_length=255)
    main = models.TextField()
    post_img = models.ImageField(upload_to='notice_images/', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return self.title

class Protocol(models.Model):
    """CTプロトコルモデル"""
    CATEGORY_CHOICES = [
        ('頭部', '頭部'),
        ('頸部', '頸部'),
        ('胸部', '胸部'),
        ('腹部', '腹部'),
        ('下肢', '下肢'),
        ('上肢', '上肢'),
        ('特殊', '特殊'),
    ]
    
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES)
    title = models.CharField(max_length=255)
    content = models.TextField(verbose_name="概要", null=True, blank=True)
    protocol_detail = models.TextField(verbose_name="撮影詳細", null=True, blank=True)
    contrast_detail = models.TextField(verbose_name="造影詳細", null=True, blank=True)
    processing_detail = models.TextField(verbose_name="画像処理詳細", null=True, blank=True)
    protocol_img = models.ImageField(upload_to='protocol_images/', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['category', 'title']
        verbose_name_plural = "Protocols"
    
    def __str__(self):
        return f"[{self.category}] {self.title}"


class BackupHistory(models.Model):
    """バックアップ履歴モデル"""
    BACKUP_TYPE_CHOICES = [
        ('export', 'バックアップ実行'),
        ('import', 'バックアップ復元'),
    ]
    
    backup_type = models.CharField(
        max_length=20, 
        choices=BACKUP_TYPE_CHOICES, 
        default='export'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    filename = models.CharField(max_length=255, blank=True, null=True)
    status = models.CharField(
        max_length=20, 
        choices=[('success', '成功'), ('failed', '失敗')], 
        default='success'
    )
    error_message = models.TextField(blank=True, null=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = "Backup Histories"
    
    def __str__(self):
        return f"{self.get_backup_type_display()} - {self.created_at.strftime('%Y-%m-%d %H:%M:%S')}"
