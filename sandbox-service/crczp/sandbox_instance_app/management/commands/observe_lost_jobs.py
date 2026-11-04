"""Django management command that hands work whose job was lost to the failure path."""

from datetime import timedelta
from typing import Any, override

import structlog
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from crczp.sandbox_instance_app.lib import lost_jobs

LOG = structlog.get_logger()


class Command(BaseCommand):
    """Fail the sandbox work whose RQ job vanished or whose worker died unnoticed."""

    help = (
        'Hand stages whose job is lost from Redis or dead past lost_job_observer.grace_seconds, '
        'and requests whose stage creation was lost, to the normal failure path. Does nothing '
        'when lost_job_observer.enabled is off. Meant to run periodically, e.g. as a '
        'Kubernetes CronJob; it fails when a recovery failed.'
    )
    requires_migrations_checks = True

    @override
    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Only report the lost work that would be recovered.',
        )

    @override
    def handle(self, *args: Any, **options: Any) -> None:
        config = settings.CRCZP_CONFIG.lost_job_observer
        if not config.enabled:
            self.stdout.write('The lost job observer is disabled (lost_job_observer).')
            return

        lost_work = lost_jobs.find_lost_work(timedelta(seconds=config.grace_seconds))
        failed = lost_jobs.recover_lost_work(lost_work, dry_run=options['dry_run'])
        verb = 'Would recover' if options['dry_run'] else 'Recovered'
        self.stdout.write(
            f'{verb} {len(lost_work) - len(failed)} lost work item(s), {len(failed)} failed.'
        )
        if failed:
            units = sorted({work.unit_id for work in failed})
            raise CommandError(f'Recovering the lost work of allocation units {units} failed.')
