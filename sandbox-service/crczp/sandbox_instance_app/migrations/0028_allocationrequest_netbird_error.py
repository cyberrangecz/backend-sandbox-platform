from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('sandbox_instance_app', '0027_request_group_backfill'),
    ]

    operations = [
        migrations.AddField(
            model_name='allocationrequest',
            name='netbird_error',
            field=models.TextField(
                blank=True,
                help_text=(
                    'Why the latest NetBird job left the VPN incomplete; null when it '
                    'completed or none ran.'
                ),
                null=True,
            ),
        ),
    ]
