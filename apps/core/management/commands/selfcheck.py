"""Runs the entire workflow end to end against the real database, then rolls it back.

    python manage.py selfcheck

This exists because static analysis cannot catch runtime bugs. It creates a
document, routes it, confirms receipt, forwards it, completes it, archives it,
and searches for it — exactly what a person does — then rolls the whole thing
back so nothing is left behind.

Every failure prints the step that broke and the exact error, which is far more
useful than discovering it live in front of an audience.

Exit code is 0 when everything passes, 1 when anything fails, so CI can use it.
"""

from __future__ import annotations

import traceback

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import connection, transaction

User = get_user_model()


class StepFailed(Exception):
    pass


class Command(BaseCommand):
    help = "Exercise the whole document workflow end to end, then roll it back."

    def add_arguments(self, parser):
        parser.add_argument("--keep", action="store_true", help="Keep the test data instead of rolling back.")
        parser.add_argument("--verbose-errors", action="store_true", help="Print full tracebacks.")

    def handle(self, *args, **options):
        self.verbose = options["verbose_errors"]
        self.passed = 0
        self.failed = 0

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("UDM DocTrack — end-to-end self check"))
        self.stdout.write("")

        try:
            with transaction.atomic():
                self._run_all()
                if not options["keep"]:
                    # Everything above was real database work; undo it.
                    transaction.set_rollback(True)
        except StepFailed:
            pass  # already reported

        self.stdout.write("")
        if self.failed:
            self.stdout.write(self.style.ERROR(f"{self.failed} check(s) failed, {self.passed} passed."))
            self.stdout.write("")
            self.stdout.write("Fix the first failure before the others — they usually cascade.")
            raise SystemExit(1)

        self.stdout.write(self.style.SUCCESS(f"All {self.passed} checks passed."))
        self.stdout.write("The full workflow works: create → route → receive → forward → complete → archive → search.")
        if options["keep"]:
            self.stdout.write(self.style.WARNING("Test data was KEPT (--keep). Run seed_demo --wipe to clear it."))

    # -- helpers -----------------------------------------------------------
    def ok(self, label, detail=""):
        self.passed += 1
        suffix = f"  {detail}" if detail else ""
        self.stdout.write(self.style.SUCCESS(f"  PASS  {label}") + suffix)

    def bad(self, label, exc):
        self.failed += 1
        self.stdout.write(self.style.ERROR(f"  FAIL  {label}"))
        self.stdout.write(f"        {type(exc).__name__}: {exc}")
        if self.verbose:
            self.stdout.write(traceback.format_exc())

    def step(self, label, func):
        try:
            result = func()
            self.ok(label)
            return result
        except Exception as exc:
            self.bad(label, exc)
            raise StepFailed from exc

    # -- the actual workflow -----------------------------------------------
    def _run_all(self):
        from apps.accounts.models import Office
        from apps.core.models import DocumentType
        from apps.documents.models import Document
        from apps.documents.services import archive_tracking_record
        from apps.search.services import search_documents
        from apps.tracking import services
        from apps.tracking.models import TrackingRecord

        # --- 0. Environment ------------------------------------------------
        self.stdout.write(self.style.MIGRATE_LABEL("Environment"))

        def check_db():
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1;")
            if connection.vendor != "postgresql":
                raise RuntimeError(f"Expected PostgreSQL, found '{connection.vendor}'. Search will be degraded.")
            return True

        self.step("Database connection (PostgreSQL)", check_db)

        def check_trgm():
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm';")
                if cursor.fetchone() is None:
                    raise RuntimeError("pg_trgm is not installed — run: python manage.py init_db")
            return True

        try:
            self.step("Fuzzy search extension (pg_trgm)", check_trgm)
        except StepFailed:
            self.failed -= 1  # downgrade to a warning: search still works without it
            self.stdout.write(self.style.WARNING("        Not fatal — misspelling tolerance is simply off."))

        # --- 1. Prerequisites in the data ----------------------------------
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_LABEL("Required data"))

        offices = self.step(
            "At least two active offices exist",
            lambda: self._require(list(Office.active.all()[:2]), 2, "offices", "python manage.py seed_demo"),
        )
        sender_office, receiver_office = offices[0], offices[1]

        def get_users():
            sender = User.objects.filter(office=sender_office, is_active=True).first()
            receiver = User.objects.filter(office=receiver_office, is_active=True).first()
            if not sender or not receiver:
                raise RuntimeError(
                    f"Need an active user in both {sender_office.code} and {receiver_office.code}. "
                    "Run: python manage.py seed_demo"
                )
            return sender, receiver

        sender, receiver = self.step("Active users in both offices", get_users)
        doc_type = DocumentType.active.first()

        # --- 2. Create -------------------------------------------------------
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_LABEL("Tracking workflow"))

        record = self.step(
            "Create a draft record",
            lambda: services.create_draft_record(
                user=sender,
                subject="Self check — automated workflow test record",
                instructions="Created by manage.py selfcheck. Rolled back automatically.",
                document_type=doc_type,
            ),
        )

        def check_number():
            parts = record.tracking_number.split("-")
            if len(parts) != 6 or not parts[-1].isdigit():
                raise RuntimeError(f"Malformed tracking number: {record.tracking_number}")
            return record.tracking_number

        number = self.step("Tracking number has the documented format", check_number)
        self.stdout.write(f"        {number}")

        # --- 3. Route --------------------------------------------------------
        self.step(
            "Route it to the receiving office",
            lambda: services.route_record(
                record, [receiver_office], user=sender, instructions="For action.", due_days=3
            ),
        )

        def check_in_transit():
            record.refresh_from_db()
            if record.status != "IN_TRANSIT":
                raise RuntimeError(f"Expected IN_TRANSIT after routing, got {record.status}")
            return True

        self.step("Status is In transit — sending is not receiving", check_in_transit)

        def check_inbox():
            inbox = services.inbox_for(receiver)
            if record.pk not in {item.pk for item in inbox}:
                raise RuntimeError("The record did not appear in the receiving office's inbox.")
            return True

        self.step("It appears in the receiver's inbox", check_inbox)

        def check_sender_cannot_confirm():
            if record.can_user_confirm_receipt(sender):
                raise RuntimeError("The SENDER was allowed to confirm receipt — permission bug.")
            return True

        self.step("The sender cannot confirm receipt on the receiver's behalf", check_sender_cannot_confirm)

        # --- 4. Receive ------------------------------------------------------
        step_obj = self.step(
            "Receiving office confirms receipt",
            lambda: services.confirm_receipt(record, user=receiver, note="Self check."),
        )

        def check_receipt_recorded():
            if not step_obj.received_at or step_obj.received_by_id != receiver.pk:
                raise RuntimeError("Receipt did not record who and when.")
            record.refresh_from_db()
            if record.current_office_id != receiver_office.pk:
                raise RuntimeError("Custody did not move to the receiving office.")
            return True

        self.step("Receipt recorded who, which office, and the server time", check_receipt_recorded)

        # --- 5. Act ----------------------------------------------------------
        self.step(
            "Add a remark",
            lambda: services.add_remark(record, user=receiver, remark="Self check remark."),
        )

        def check_history_appends():
            before = record.routing_steps.count()
            services.route_record(record, [sender_office], user=receiver, action="FORWARD")
            after = record.routing_steps.count()
            if after <= before:
                raise RuntimeError("Forwarding did not append a new routing step.")
            step_obj.refresh_from_db()
            if step_obj.received_at is None:
                raise RuntimeError("Forwarding erased an earlier receipt — history is not append-only.")
            return True

        self.step("Forwarding appends history without erasing the earlier receipt", check_history_appends)

        # --- 6. Complete and archive -----------------------------------------
        self.step(
            "Confirm receipt back at the originating office",
            lambda: services.confirm_receipt(record, user=sender, note="Returned."),
        )
        self.step(
            "Mark it completed",
            lambda: services.complete_record(record, user=sender, note="Self check complete."),
        )

        document = self.step(
            "Archive it into the document repository",
            lambda: archive_tracking_record(record, user=sender),
        )

        def check_archived():
            if not isinstance(document, Document):
                raise RuntimeError("Archiving did not return a Document.")
            if document.tracking_record_id != record.pk:
                raise RuntimeError("The archived document is not linked back to its tracking record.")
            return True

        self.step("The archived document links back to its tracking record", check_archived)

        # --- 7. Search --------------------------------------------------------
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_LABEL("Search"))

        def check_search():
            document.rebuild_index()
            response = search_documents(
                user=sender, query="automated workflow test", min_relevance=0, log=False
            )
            hits = [item for item in response.results if item.document.pk == document.pk]
            if not hits:
                raise RuntimeError("The archived document was not found by search.")
            hit = hits[0]
            if not 0 <= hit.relevance <= 100:
                raise RuntimeError(f"Relevance out of range: {hit.relevance}")
            return hit

        hit = self.step("The archived document is findable by search", check_search)
        self.stdout.write(f"        relevance {hit.relevance}%, matched in: {', '.join(hit.matched_in) or 'body'}")

        # --- 8. Permissions ----------------------------------------------------
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_LABEL("Permissions"))

        def check_isolation():
            outsider = (
                User.objects.filter(is_active=True, role="USER")
                .exclude(office__in=[sender_office, receiver_office])
                .exclude(is_superuser=True)
                .first()
            )
            if not outsider:
                raise RuntimeError("skip")
            visible = TrackingRecord.objects.visible_to(outsider)
            if record.pk in {item.pk for item in visible}:
                raise RuntimeError(
                    f"{outsider.username} ({outsider.office.code}) can see a record never routed to them."
                )
            return True

        try:
            self.step("An unrelated office cannot see the record", check_isolation)
        except StepFailed as exc:
            if "skip" in str(exc.__cause__):
                self.failed -= 1
                self.stdout.write(self.style.WARNING("        Skipped — no third office with a user to test against."))
            else:
                raise

    def _require(self, items, minimum, what, fix):
        if len(items) < minimum:
            raise RuntimeError(f"Found {len(items)} {what}, need at least {minimum}. Fix: {fix}")
        return items
