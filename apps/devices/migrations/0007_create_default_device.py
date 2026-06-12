from django.db import migrations


def create_default_device(apps, schema_editor):
    Device = apps.get_model('devices', 'Device')
    if not Device.objects.filter(pk=1).exists():
        Device.objects.create(
            id       = 1,
            name     = 'Smart-Toilet-01',
            location = 'Main Entrance',
            status   = 'Online',
            icon     = 'faServer',
            color    = '#6366f1',
            wired    = True,
            wifi     = True,
        )


def reverse_default_device(apps, schema_editor):
    Device = apps.get_model('devices', 'Device')
    Device.objects.filter(pk=1, name='Smart-Toilet-01').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('devices', '0006_alter_device_icon'),
    ]

    operations = [
        migrations.RunPython(create_default_device, reverse_default_device),
    ]
