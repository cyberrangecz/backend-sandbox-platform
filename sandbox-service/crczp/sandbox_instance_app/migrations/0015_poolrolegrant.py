import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('sandbox_instance_app', '0014_sandboxnetbirdresources'),
    ]

    operations = [
        migrations.CreateModel(
            name='PoolRoleGrant',
            fields=[
                (
                    'id',
                    models.AutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name='ID'
                    ),
                ),
                (
                    'user',
                    models.IntegerField(help_text='User id from the user-and-group service.'),
                ),
                (
                    'role',
                    models.CharField(db_index=True, max_length=128),
                ),
                (
                    'pool',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='role_grants',
                        to='sandbox_instance_app.pool',
                    ),
                ),
            ],
        ),
        migrations.AlterUniqueTogether(
            name='poolrolegrant',
            unique_together={('pool', 'user', 'role')},
        ),
    ]
