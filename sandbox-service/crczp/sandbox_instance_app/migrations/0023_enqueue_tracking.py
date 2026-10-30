from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('sandbox_instance_app', '0022_allocationrequest_netbird_job_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='allocationstage',
            name='job_requested_at',
            field=models.DateTimeField(
                default=None,
                help_text='When the stage was created for a job that is queued once its request '
                'commits.',
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='cleanupstage',
            name='job_requested_at',
            field=models.DateTimeField(
                default=None,
                help_text='When the stage was created for a job that is queued once its request '
                'commits.',
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='cleanuprequest',
            name='delete_pool',
            field=models.BooleanField(
                default=False,
                help_text='Whether the pool is deleted once this cleanup removes its last unit.',
            ),
        ),
        migrations.AddField(
            model_name='cleanuprequest',
            name='retry_count',
            field=models.PositiveSmallIntegerField(
                default=0,
                help_text='How many times the failed cleanup has been started again by itself.',
            ),
        ),
    ]
