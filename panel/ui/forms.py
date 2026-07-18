from django import forms


class LoginForm(forms.Form):
    """Login en un solo paso: usuario + contraseña + token TOTP."""

    username = forms.CharField(max_length=150)
    password = forms.CharField(widget=forms.PasswordInput)
    token = forms.CharField(max_length=6, label="Código TOTP")
