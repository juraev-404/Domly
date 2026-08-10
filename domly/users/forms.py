import re

from django import forms
from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError

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
    phone = forms.CharField(
        label="Номер телефона",
        max_length=20,
        widget=forms.TextInput(
            attrs={
                "class": INPUT_CLASS,
                "autocomplete": "tel",
                "placeholder": "+992900001122",
                "inputmode": "tel",
            }
        ),
    )
    email = forms.EmailField(
        label="Email (необязательно)",
        required=False,
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

    def clean_phone(self):
        phone = normalize_phone(self.cleaned_data["phone"])
        if User.objects.filter(phone=phone).exists():
            raise ValidationError("Этот номер уже зарегистрирован.")
        return phone

    def clean_email(self):
        email = self.cleaned_data.get("email")
        if not email:
            return None
        email = email.strip().lower()
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
                phone=cleaned_data.get("phone", ""),
                email=cleaned_data.get("email"),
            )
            try:
                password_validation.validate_password(password1, candidate)
            except ValidationError as error:
                self.add_error("password1", error)
        return cleaned_data


class VerificationCodeForm(forms.Form):
    code = forms.RegexField(
        label="Код из SMS",
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
        label="Ник, телефон или email",
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
        fields = ("avatar", "username", "first_name", "last_name", "email")
        labels = {
            "avatar": "Фото профиля",
            "username": "Ник",
            "first_name": "Имя",
            "last_name": "Фамилия",
            "email": "Email",
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
            "email": forms.EmailInput(
                attrs={"class": INPUT_CLASS, "autocomplete": "email"}
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

    def clean_email(self):
        email = self.cleaned_data.get("email")
        if not email:
            return None
        email = email.strip().lower()
        if User.objects.exclude(pk=self.instance.pk).filter(email__iexact=email).exists():
            raise ValidationError("Этот email уже используется.")
        return email

    def clean_avatar(self):
        avatar = self.cleaned_data.get("avatar")
        if avatar and getattr(avatar, "size", 0) > 5 * 1024 * 1024:
            raise ValidationError("Фотография не должна превышать 5 МБ.")
        return avatar
