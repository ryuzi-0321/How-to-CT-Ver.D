from django.db import models
from django.utils import timezone
from django.contrib.auth import get_user_model
User = get_user_model()

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

class SickImage(models.Model):
    CATEGORY_CHOICES = [
        ('disease', '疾患情報'),
        ('protocol', '撮影プロトコル'),
        ('contrast', '造影プロトコル'),
        ('processing', '画像処理'),
    ]
    sick = models.ForeignKey(Sick, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to='sick_images/')
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)

class Form(models.Model):
    """お知らせモデル"""
    title = models.CharField(max_length=255)
    main = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    post_img = models.ImageField(upload_to='post_images/', null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title

class NoticeImage(models.Model):
    """お知らせ画像モデル"""
    form = models.ForeignKey(Form, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to='notice_images/')

    def __str__(self):
        return f"Image for {self.form.title}"

class ProtocolImage(models.Model):
    protocol = models.ForeignKey('Protocol', on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to='protocol_images/')

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


class NightShift(models.Model):
    """夜勤対応モデル"""
    name = models.CharField(max_length=255, unique=True)
    content = models.TextField()
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = "NightShifts"

    def __str__(self):
        return self.name


class NightShiftImage(models.Model):
    """夜勤対応画像モデル"""
    nightshift = models.ForeignKey(NightShift, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to='nightshift_images/')

    def __str__(self):
        return f"Image for {self.nightshift.name}"
    
class Question(models.Model):
    """質問モデル"""
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='questions')
    title = models.CharField(max_length=255, verbose_name="質問タイトル")
    content = models.TextField(verbose_name="質問内容")
    answer_text = models.TextField(blank=True)
    
    related_protocols = models.ForeignKey(
        Protocol,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='questions',
        verbose_name="関連するプロトコル"
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="投稿日時")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新日時")

    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = "Questions"

    def __str__(self):
        return self.title
    
class Answer(models.Model):
    """回答モデル"""
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='answers')
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='answers')
    content = models.TextField(verbose_name="回答内容")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="投稿日時")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新日時")
    suggested_protocols = models.ForeignKey(
        Protocol,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='suggested_answers',
        verbose_name="参考プロトコル"
    )

    class Meta:
        ordering = ['created_at']
        verbose_name_plural = "Answers"

    def __str__(self):
        return f"Answer by {self.author.username} to '{self.question.title}'"    

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
