from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


class MultipleImageInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleImageField(forms.ImageField):
    widget = MultipleImageInput

    def clean(self, data, initial=None):
        if not data:
            return []
        files = data if isinstance(data, (list, tuple)) else [data]
        images = [super(MultipleImageField, self).clean(file, initial) for file in files]
        if len(images) > 5:
            raise ValidationError(_("Можно прикрепить не более 5 фотографий."))

        allowed_types = {"image/jpeg", "image/png", "image/webp"}
        for image in images:
            if image.size > 10 * 1024 * 1024:
                raise ValidationError(_("Каждая фотография должна быть не больше 10 МБ."))
            if image.content_type not in allowed_types:
                raise ValidationError(_("Поддерживаются JPEG, PNG и WebP."))
            if image.image.width * image.image.height > 40_000_000:
                raise ValidationError(_("Разрешение фотографии слишком большое."))
        return images


class MessageForm(forms.Form):
    client_id = forms.UUIDField(required=False, widget=forms.HiddenInput)
    images = MultipleImageField(
        required=False,
        widget=MultipleImageInput(
            attrs={
                "accept": "image/jpeg,image/png,image/webp",
                "class": "sr-only",
            }
        ),
    )
    body = forms.CharField(
        max_length=4000,
        label="",
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 1,
                "placeholder": _("Напишите сообщение…"),
                "autocomplete": "off",
                "class": (
                    "min-h-11 max-h-32 w-full resize-none rounded-2xl border "
                    "border-gray-300 bg-white px-4 py-3 text-sm outline-none "
                    "transition focus:border-gray-600"
                ),
            }
        ),
    )

    def clean_body(self):
        return self.cleaned_data.get("body", "").strip()

    def clean(self):
        cleaned_data = super().clean()
        if not cleaned_data.get("body") and not cleaned_data.get("images"):
            raise ValidationError(_("Введите сообщение или прикрепите фотографию."))
        return cleaned_data
