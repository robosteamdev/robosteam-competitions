import hmac
import json
import logging

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import DeviceType, LapTimerDevice, RegistrationStatus

logger = logging.getLogger(__name__)


def _secret_ok(supplied):
    """
    True unless a DEVICE_REG_SECRET is configured and the payload doesn't match it.

    Unset (the default) keeps LAN setup friction-free: any device on the
    competition network may register and simply waits for admin approval.
    """
    expected = getattr(settings, 'DEVICE_REG_SECRET', '')
    if not expected:
        return True
    return hmac.compare_digest(str(supplied or ''), expected)


@csrf_exempt
@require_POST
def register_device(request):
    """
    POST /devices/register/
    Body (JSON): {mac, friendly_name, device_type, country, school, secret}

    Idempotent: creates the device on the first call, and on later calls with the
    same MAC updates only the operator-editable fields (friendly_name, country,
    school). registration_status is never granted here — a new device starts
    PENDING and an admin approves it. Changing device_type on an already-approved
    device sends it back to PENDING: a box that was approved as a lap timer must
    not become an unreviewed robot (or vice versa) just by re-registering.
    """
    try:
        body = json.loads(request.body)
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Invalid JSON body'}, status=400)

    mac           = str(body.get('mac', '')).strip().upper()
    friendly_name = str(body.get('friendly_name', '')).strip()
    country       = str(body.get('country', '')).strip()
    school        = str(body.get('school', '')).strip()

    # Absent means "unchanged" for an existing device, so leaving the field out of
    # a rename payload can't quietly demote an approved robot to a lap timer.
    device_type = str(body.get('device_type') or '').strip().upper() or None

    if not _secret_ok(body.get('secret')):
        logger.warning('Device registration rejected for %s: bad or missing secret', mac or '?')
        return JsonResponse({'error': 'Invalid registration secret'}, status=403)

    if not mac:
        return JsonResponse({'error': 'mac is required'}, status=400)
    if not friendly_name:
        return JsonResponse({'error': 'friendly_name is required'}, status=400)
    if device_type is not None and device_type not in DeviceType.values:
        return JsonResponse(
            {'error': f'device_type must be one of {DeviceType.values}'},
            status=400,
        )

    resolved_type = device_type or DeviceType.LAPTIMER

    device, created = LapTimerDevice.objects.get_or_create(
        device_id=mac,
        defaults={
            'friendly_name': friendly_name,
            'device_type': resolved_type,
            'country': country,
            'organisation': school,
            # A robot is a command receiver, never a scoring source, so approval
            # would gate nothing worth gating (Decision 041). Lap timers keep the
            # H4 gate and start PENDING.
            'registration_status': (
                RegistrationStatus.ACTIVE if resolved_type == DeviceType.ROBOT
                else RegistrationStatus.PENDING
            ),
        },
    )

    if not created:
        updated = ['friendly_name', 'country', 'organisation']
        device.friendly_name = friendly_name
        device.country       = country
        device.organisation  = school

        if device_type is not None and device_type != device.device_type:
            if device.owner_id is not None:
                # A CLAIMED device never changes type from this endpoint, which
                # is unauthenticated by design (Phase 5.21 G).
                #
                # Every participant view filters on device_type=ROBOT, so a
                # claimed robot re-registering as a LAPTIMER would keep its
                # owner AND its team binding — still receiving targeted START
                # commands — while vanishing from My Robots and becoming
                # unreachable by rename, release or test. Recoverable only in
                # Django admin. This needs no attacker: a student flashing lap
                # timer firmware onto the wrong board does it.
                #
                # The owner releases the device first, or an organiser
                # reassigns it. Then the type may change freely.
                logger.warning(
                    'Device %s is claimed (owner id %s) — refusing type change '
                    '%s -> %s. The owner must release it first.',
                    mac, device.owner_id, device.device_type, device_type,
                )
            else:
                # A device that changes type is always re-evaluated. Becoming a
                # ROBOT lands ACTIVE by Decision 041; becoming a LAPTIMER lands
                # PENDING, so re-registering a robot as a timer can never
                # launder an approval.
                new_status = (
                    RegistrationStatus.ACTIVE if device_type == DeviceType.ROBOT
                    else RegistrationStatus.PENDING
                )
                logger.warning(
                    'Device %s changed type %s -> %s; registration set to %s',
                    mac, device.device_type, device_type, new_status,
                )
                device.device_type          = device_type
                device.registration_status  = new_status
                updated += ['device_type', 'registration_status']

        device.save(update_fields=updated)

    return JsonResponse(
        {
            'device_id':           device.device_id,
            'friendly_name':       device.friendly_name,
            'device_type':         device.device_type,
            'registration_status': device.registration_status,
            'created':             created,
        },
        status=201 if created else 200,
    )
