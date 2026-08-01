from django.core.management.base import BaseCommand
from daily_assignment.models import AssignmentArea, Staff


AREAS = [
    ("一般撮影", "胸部", 1), ("一般撮影", "乳腺", 1), ("一般撮影", "第3", 1),
    ("一般撮影", "第4", 1), ("一般撮影", "第5", 1), ("一般撮影", "病棟・出力室", 1),
    ("その他", "Po", 1), ("その他", "TV", 1), ("その他", "骨塩", 1), ("その他", "ESWL", 1),
    ("IVR", "血管", 1), ("IVR", "Hybrid", 1),
    ("CT", "Drive", 1), ("CT", "CXL", 1), ("CT", "画像1", 1), ("CT", "画像2", 1), ("CT", "画像3", 1),
    ("MRI", "1.5T", 1), ("MRI", "3.0T", 1), ("MRI", "画像4", 1),
    ("RI", "SPECT/CT", 1), ("RI", "PET/CT", 1),
    ("RT", "VersaHD", 1), ("RT", "LB", 1), ("その他", "感染症", 1),
]


class Command(BaseCommand):
    help = "当日配置表の初期配置場所と仮スタッフを登録します"

    def handle(self, *args, **options):
        for order, (category, name, slots) in enumerate(AREAS, start=1):
            AssignmentArea.objects.update_or_create(
                category=category, name=name,
                defaults={"slot_count": slots, "display_order": order, "is_active": True},
            )
        for order in range(1, 6):
            Staff.objects.get_or_create(name=f"スタッフ{order}", defaults={"display_order": order})
        self.stdout.write(self.style.SUCCESS("初期データを登録しました。管理画面で名称を変更してください。"))
