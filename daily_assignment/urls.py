from django.urls import path
from . import views

app_name = "daily_assignment"

urlpatterns = [
    # 当日配置表
    path("", views.board_view, name="board"),
    path("save/", views.save_board, name="save"),
    path("copy/", views.copy_board, name="copy"),
    path("clear/", views.clear_board, name="clear"),
    path("excel/", views.export_excel, name="excel"),

    # ログイン不要の設定画面
    path("settings/", views.settings_view, name="settings"),

    # スタッフ設定
    path(
        "settings/staff/add/",
        views.staff_add,
        name="staff_add",
    ),
    path(
        "settings/staff/<int:staff_id>/update/",
        views.staff_update,
        name="staff_update",
    ),
    path(
        "settings/staff/<int:staff_id>/delete/",
        views.staff_delete,
        name="staff_delete",
    ),

    # 配置場所設定
    path(
        "settings/area/add/",
        views.area_add,
        name="area_add",
    ),
    path(
        "settings/area/<int:area_id>/update/",
        views.area_update,
        name="area_update",
    ),
    path(
        "settings/area/<int:area_id>/delete/",
        views.area_delete,
        name="area_delete",
    ),
]