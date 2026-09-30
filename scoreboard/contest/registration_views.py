from django import forms
from django.contrib.auth.models import Group, User
from django.shortcuts import render, redirect

from .models import UserProfile


class RegistrationForm(forms.Form):
    username       = forms.CharField(max_length=150)
    first_name     = forms.CharField(max_length=150)
    last_name      = forms.CharField(max_length=150)
    email          = forms.EmailField()
    password       = forms.CharField(widget=forms.PasswordInput)
    password2      = forms.CharField(widget=forms.PasswordInput, label='Confirm password')
    requested_role = forms.ChoiceField(choices=[
        ('judge',       'Judge'),
        ('organiser',   'Organiser'),
        ('participant', 'Participant / Team'),
    ])
    organisation = forms.CharField(max_length=200, required=False)
    country      = forms.CharField(max_length=100, required=False)
    team_name    = forms.CharField(
        max_length=100,
        required=False,
        label='Team name',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'e.g. RoboSTEAM BG Team A',
        }),
        help_text='Required for Participant accounts. Appears in all competition results.',
    )
    consent = forms.BooleanField(
        required=True,
        label='I agree to the processing of my data',
        help_text='See our privacy policy for details.',
        error_messages={'required': 'You must agree to the data processing terms to register.'},
    )

    def clean_username(self):
        username = self.cleaned_data['username']
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError('This username is already taken.')
        return username

    def clean_email(self):
        email = self.cleaned_data['email']
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('An account with this email already exists.')
        return email

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('password') != cleaned.get('password2'):
            raise forms.ValidationError('Passwords do not match.')
        role      = cleaned.get('requested_role')
        team_name = cleaned.get('team_name', '').strip()
        if role == 'participant' and not team_name:
            self.add_error('team_name', 'Team name is required for Participant accounts.')
        return cleaned


def register(request):
    if request.method == 'POST':
        form = RegistrationForm(request.POST)
        if form.is_valid():
            d              = form.cleaned_data
            requested_role = d['requested_role']

            if requested_role == 'participant':
                user = User.objects.create_user(
                    username   = d['username'],
                    email      = d['email'],
                    password   = d['password'],
                    first_name = d['first_name'],
                    last_name  = d['last_name'],
                    is_active  = True,
                )
                group = Group.objects.get(name='Participant')
                user.groups.add(group)
                UserProfile.objects.create(
                    user           = user,
                    requested_role = requested_role,
                    organisation   = d.get('organisation', ''),
                    country        = d.get('country', ''),
                    team_name      = d.get('team_name', '').strip(),
                )
                return redirect('contest:register_participant_done')
            else:
                user = User.objects.create_user(
                    username   = d['username'],
                    email      = d['email'],
                    password   = d['password'],
                    first_name = d['first_name'],
                    last_name  = d['last_name'],
                    is_active  = False,
                )
                UserProfile.objects.create(
                    user           = user,
                    requested_role = requested_role,
                    organisation   = d.get('organisation', ''),
                    country        = d.get('country', ''),
                )
                return redirect('contest:register_pending')
    else:
        form = RegistrationForm()
    return render(request, 'contest/register.html', {'form': form})


def register_pending(request):
    return render(request, 'contest/register_pending.html')


def register_participant_done(request):
    return render(request, 'contest/register_participant_done.html')
