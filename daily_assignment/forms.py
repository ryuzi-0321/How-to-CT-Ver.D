from django import forms
from .models import DailyBoard


class DailyBoardMetaForm(forms.ModelForm):
    class Meta:
        model = DailyBoard
        fields = (
            "conference", "two_shift", "annual_leave_1", "annual_leave_2",
            "staffing_am", "staffing_pm", "free_text", "comment",
            "unassigned", "reception_leave",
        )
        widgets = {
            "conference": forms.Textarea(attrs={"rows": 3}),
            "two_shift": forms.Textarea(attrs={"rows": 3}),
            "annual_leave_1": forms.Textarea(attrs={"rows": 3}),
            "annual_leave_2": forms.Textarea(attrs={"rows": 3}),
            "free_text": forms.Textarea(attrs={"rows": 3}),
            "comment": forms.Textarea(attrs={"rows": 5}),
            "unassigned": forms.Textarea(attrs={"rows": 3}),
            "reception_leave": forms.Textarea(attrs={"rows": 3}),
        }
