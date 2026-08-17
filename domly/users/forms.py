import re

from django import forms
from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .models import User


def normalize_phone(value):
    value = value.strip()
    if value.startswith("00"):
        value = "+" + value[2:]

    digits = re.sub(r"\D", "", value)
    if not value.startswith("+") or not 8 <= len(digits) <= 15:
        raise ValidationError(
            "Введите номер в международном формате, например +992900001122."
        )
    return "+" + digits


INPUT_CLASS = (
    "w-full rounded-lg border border-gray-300 px-3 py-2 outline-none "
    "focus:border-green-500 focus:ring-2 focus:ring-green-100"
)


class RegistrationForm(forms.Form):
    username = forms.CharField(
        label="Ник",
        max_length=150,
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "autocomplete": "username"}),
    )
    email = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(attrs={"class": INPUT_CLASS, "autocomplete": "email"}),
    )
    password1 = forms.CharField(
        label="Пароль",
        strip=False,
        widget=forms.PasswordInput(
            attrs={"class": INPUT_CLASS, "autocomplete": "new-password"}
        ),
    )
    password2 = forms.CharField(
        label="Повторите пароль",
        strip=False,
        widget=forms.PasswordInput(
            attrs={"class": INPUT_CLASS, "autocomplete": "new-password"}
        ),
    )
    accept_terms = forms.BooleanField(
        label=_("Я принимаю Пользовательское соглашение и Правила публикации."),
        required=True,
        error_messages={"required": _("Для регистрации необходимо принять условия сервиса.")},
    )
    privacy_consent = forms.BooleanField(
        label=_("Я согласен на обработку персональных данных согласно Политике конфиденциальности."),
        required=True,
        error_messages={"required": _("Для регистрации необходимо дать согласие на обработку данных.")},
    )

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if "@" in username:
            raise ValidationError("Ник не должен содержать символ @.")
        if re.fullmatch(r"\+?\d+", username):
            raise ValidationError("Ник не должен выглядеть как номер телефона.")
        if not re.fullmatch(r"[\w.+-]+", username, flags=re.UNICODE):
            raise ValidationError("Используйте буквы, цифры и символы . + - _.")
        if User.objects.filter(username__iexact=username).exists():
            raise ValidationError("Этот ник уже занят.")
        return username

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("Этот email уже используется.")
        return email

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("password1")
        password2 = cleaned_data.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "Пароли не совпадают.")
        if password1:
            candidate = User(
                username=cleaned_data.get("username", ""),
                email=cleaned_data.get("email"),
            )
            try:
                password_validation.validate_password(password1, candidate)
            except ValidationError as error:
                self.add_error("password1", error)
        return cleaned_data


class LegalAcceptanceForm(forms.Form):
    accept_terms = forms.BooleanField(
        label=_("Я принимаю Пользовательское соглашение и Правила публикации."),
        required=True,
        error_messages={"required": _("Необходимо принять действующие условия сервиса.")},
    )
    privacy_consent = forms.BooleanField(
        label=_("Я согласен на обработку персональных данных согласно Политике конфиденциальности."),
        required=True,
        error_messages={"required": _("Необходимо подтвердить согласие на обработку данных.")},
    )


class VerificationCodeForm(forms.Form):
    code = forms.RegexField(
        label="Код из письма",
        regex=r"^\d{6}$",
        error_messages={"invalid": "Введите шестизначный код."},
        widget=forms.TextInput(
            attrs={
                "class": INPUT_CLASS,
                "autocomplete": "one-time-code",
                "inputmode": "numeric",
                "maxlength": "6",
            }
        ),
    )


class LoginForm(forms.Form):
    identifier = forms.CharField(
        label="Ник или email",
        max_length=254,
        widget=forms.TextInput(
            attrs={"class": INPUT_CLASS, "autocomplete": "username"}
        ),
    )
    password = forms.CharField(
        label="Пароль",
        strip=False,
        widget=forms.PasswordInput(
            attrs={"class": INPUT_CLASS, "autocomplete": "current-password"}
        ),
    )
    remember_me = forms.BooleanField(label="Запомнить меня", required=False)


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("avatar", "username", "first_name", "last_name")
        labels = {
            "avatar": "Фото профиля",
            "username": "Ник",
            "first_name": "Имя",
            "last_name": "Фамилия",
        }
        widgets = {
            "avatar": forms.ClearableFileInput(
                attrs={
                    "class": (
                        "block w-full rounded-xl border border-gray-300 bg-white "
                        "px-3 py-2 text-sm file:mr-3 file:rounded-lg file:border-0 "
                        "file:bg-black file:px-3 file:py-2 file:text-white"
                    ),
                    "accept": "image/jpeg,image/png,image/webp",
                }
            ),
            "username": forms.TextInput(
                attrs={"class": INPUT_CLASS, "autocomplete": "username"}
            ),
            "first_name": forms.TextInput(
                attrs={"class": INPUT_CLASS, "autocomplete": "given-name"}
            ),
            "last_name": forms.TextInput(
                attrs={"class": INPUT_CLASS, "autocomplete": "family-name"}
            ),
        }

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if "@" in username:
            raise ValidationError("Ник не должен содержать символ @.")
        if re.fullmatch(r"\+?\d+", username):
            raise ValidationError("Ник не должен выглядеть как номер телефона.")
        if not re.fullmatch(r"[\w.+-]+", username, flags=re.UNICODE):
            raise ValidationError("Используйте буквы, цифры и символы . + - _.")
        if User.objects.exclude(pk=self.instance.pk).filter(username__iexact=username).exists():
            raise ValidationError("Этот ник уже занят.")
        return username

    def clean_avatar(self):
        avatar = self.cleaned_data.get("avatar")
        if avatar and getattr(avatar, "size", 0) > 5 * 1024 * 1024:
            raise ValidationError("Фотография не должна превышать 5 МБ.")
        if avatar and avatar.content_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise ValidationError("Поддерживаются JPEG, PNG и WebP.")
        if avatar and avatar.image.width * avatar.image.height > 40_000_000:
            raise ValidationError("Разрешение фотографии слишком большое.")
        return avatar


class EmailForm(forms.Form):
    email = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(
            attrs={"class": INPUT_CLASS, "autocomplete": "email"}
        ),
    )

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()


class EmailChangeForm(EmailForm):
    current_password = forms.CharField(
        label="Текущий пароль",
        strip=False,
        widget=forms.PasswordInput(
            attrs={"class": INPUT_CLASS, "autocomplete": "current-password"}
        ),
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_email(self):
        email = super().clean_email()
        if self.user.email and self.user.email.casefold() == email.casefold():
            raise ValidationError("Это ваш текущий email.")
        if User.objects.exclude(pk=self.user.pk).filter(email__iexact=email).exists():
            raise ValidationError("Этот email уже используется.")
        return email

    def clean_current_password(self):
        password = self.cleaned_data["current_password"]
        if not self.user.check_password(password):
            raise ValidationError("Неверный текущий пароль.")
        return password


class NewPasswordForm(forms.Form):
    password1 = forms.CharField(
        label="Новый пароль",
        strip=False,
        widget=forms.PasswordInput(
            attrs={"class": INPUT_CLASS, "autocomplete": "new-password"}
        ),
    )
    password2 = forms.CharField(
        label="Повторите новый пароль",
        strip=False,
        widget=forms.PasswordInput(
            attrs={"class": INPUT_CLASS, "autocomplete": "new-password"}
        ),
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("password1")
        password2 = cleaned_data.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "Пароли не совпадают.")
        if password1:
            try:
                password_validation.validate_password(password1, self.user)
            except ValidationError as error:
                self.add_error("password1", error)
        return cleaned_data
