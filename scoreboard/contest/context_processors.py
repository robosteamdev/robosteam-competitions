def user_roles(request):
    """Inject role flags into every template context."""
    user = request.user
    if not user.is_authenticated:
        return {
            'is_judge': False, 'is_organiser': False,
            'is_admin': False, 'is_participant': False,
        }
    groups = set(user.groups.values_list('name', flat=True))
    return {
        'is_judge':       user.is_superuser or 'Judge' in groups or 'Organiser' in groups or 'Administrator' in groups,
        'is_organiser':   user.is_superuser or 'Organiser' in groups or 'Administrator' in groups,
        'is_admin':       user.is_superuser or 'Administrator' in groups,
        'is_participant': 'Participant' in groups,
    }
