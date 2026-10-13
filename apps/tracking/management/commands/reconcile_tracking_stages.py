"""Repair only the cached stage/custody summary from existing routing history."""

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.tracking.models import Status, TrackingRecord


class Command(BaseCommand):
    help = "Preview cached tracking stage/custody corrections; pass --apply to save them."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--record", help="Limit to one exact tracking number.")

    @transaction.atomic
    def handle(self, *args, **options):
        records = TrackingRecord.objects.filter(status__in=[
            Status.PENDING_RECEIPT, Status.RECEIVED, Status.IN_PROCESS,
        ]).order_by("pk")
        if options["record"]:
            records = records.filter(tracking_number=options["record"])
        if options["apply"]:
            records = records.select_for_update()
        changed = 0
        for record in records:
            before = (record.status, record.current_office_id, record.current_holder_id)
            record.recalculate_status(save=False)
            after = (record.status, record.current_office_id, record.current_holder_id)
            if before == after:
                continue
            # Orphaned official records require investigation, not redrafting.
            if record.status == Status.DRAFT:
                self.stderr.write(f"Skipped {record.tracking_number}: no current routing steps.")
                continue
            changed += 1
            self.stdout.write(f"{record.tracking_number}: {before} -> {after}")
            if options["apply"]:
                record.save(update_fields=["status", "current_office", "current_holder", "updated_at"])
        mode = "Updated" if options["apply"] else "Would update"
        self.stdout.write(f"{mode} {changed} record(s). Routing history and files are unchanged.")
