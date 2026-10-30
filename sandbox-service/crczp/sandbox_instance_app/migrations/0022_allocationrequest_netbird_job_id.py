from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('sandbox_instance_app', '0021_stage_stop_requested_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='allocationrequest',
            name='netbird_job_id',
            field=models.TextField(
                blank=True,
                help_text=(
                    'The RQ job that provisions NetBird for the sandbox; null once it is settled.'
                ),
                null=True,
            ),
        ),
    ]
