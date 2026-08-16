"""Creates everything needed to demo the system in about ten seconds.

    python manage.py seed_demo

Safe to run more than once — it updates rather than duplicates. Use
--wipe only on a scratch database; it deletes tracking and document data.

Every demo account uses the same password so nobody has to memorise a list.
Change DEMO_PASSWORD before running this anywhere real.
"""

from __future__ import annotations

import random
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Office
from apps.core.models import DocumentType, MetadataFieldDefinition, Tag, TagRule
from apps.documents.models import Document
from apps.documents.services import archive_tracking_record, set_tags
from apps.tracking import services as tracking_services
from apps.tracking.models import RoutingStep, TrackingRecord

User = get_user_model()

DEMO_PASSWORD = "DocTrack2026!"

OFFICES = [
    ("OVPA", "Office of the Vice President for Administration", "OVPA", "Atty. R. Bautista"),
    ("MED", "Mechanical and Engineering Department", "OVPA", "Engr. L. Fernandez"),
    ("SEC", "Security Services Office", "OVPA", "Mr. A. Dela Cruz"),
    ("SUP", "Supply and Property Management", "OVPA", "Ms. G. Ramos"),
    ("HR", "Human Resource Management Office", "OVPA", "Ms. C. Villanueva"),
    ("PAY", "Payroll Section", "OVPA", "Mr. J. Santos"),
    ("PROC", "Procurement Management Office", "OVPA", "Ms. T. Aquino"),
    ("REC", "Records Management Office", "OVPA", "Ms. M. Lorenzo"),
    ("LND", "Learning and Development Office", "OVPA", "Dr. P. Mercado"),
]

DOCUMENT_TYPES = [
    ("MEMO", "Memorandum", 5, "Internal instruction or announcement."),
    ("LETTER", "Letter", 5, "Correspondence to or from an external party."),
    ("WO", "Work Order", 3, "Request for repair, maintenance or service."),
    ("PR", "Purchase Request", 10, "Request to procure goods or services."),
    ("ENDORSE", "Endorsement", 5, "Forwarding a matter to another office for action."),
    ("REPORT", "Report", 10, "Accomplishment, incident or inventory report."),
    ("NOTICE", "Notice", 3, "Advisory or notice of meeting."),
    ("CERT", "Certification", 10, "Certificate of employment, enrolment or clearance."),
    ("VOUCHER", "Disbursement Voucher", 10, "Supporting document for payment."),
]

TAGS = [
    ("urgent", "priority"), ("for signature", "action"), ("for information", "action"),
    ("budget", "subject"), ("maintenance", "subject"), ("personnel", "subject"),
    ("procurement", "subject"), ("engineering", "subject"), ("security", "subject"),
    ("training", "subject"), ("inventory", "subject"), ("payroll", "subject"),
    ("clearance", "subject"), ("incident report", "subject"), ("2026", "year"),
]

TAG_RULES = [
    ("Urgent wording", "urgent", "CONTAINS", "FULL_TEXT", "urgent", None, 0.85, 10),
    ("Signature request", "for signature", "CONTAINS", "FULL_TEXT", "for signature", None, 0.9, 10),
    ("Work order form", "maintenance", "CONTAINS", "TITLE", "work order", "WO", 0.9, 20),
    ("Purchase request", "procurement", "CONTAINS", "FULL_TEXT", "purchase request", "PR", 0.88, 20),
    ("Memorandum header", "for information", "CONTAINS", "FIRST_PAGE", "memorandum", "MEMO", 0.8, 30),
    ("Engineering works", "engineering", "ANY_WORD", "FULL_TEXT", "electrical mechanical plumbing aircon generator", None, 0.75, 40),
    ("Security incident", "incident report", "ALL_WORDS", "FULL_TEXT", "incident report", "REPORT", 0.82, 40),
    ("Training activity", "training", "ANY_WORD", "FULL_TEXT", "seminar training workshop", None, 0.7, 50),
    ("Payroll matter", "payroll", "ANY_WORD", "FULL_TEXT", "payroll salary honorarium", None, 0.78, 50),
]

METADATA_FIELDS = [
    ("control_no", "Control number", "TEXT", "", "Office's own reference, if different from the tracking number.", True, True, 10),
    ("fund_source", "Fund source", "CHOICE", "General Fund,Trust Fund,Special Education Fund,STF", "Needed for anything with a peso value.", True, False, 20),
    ("amount", "Amount (PHP)", "NUMBER", "", "Leave blank when the document has no monetary value.", True, False, 30),
    ("period_covered", "Period covered", "TEXT", "", "For reports and payroll, e.g. 'January–June 2026'.", True, False, 40),
    ("physical_location", "Physical file location", "TEXT", "", "Cabinet, drawer or box where the paper copy sits.", False, True, 50),
    ("confidential", "Contains personal data", "BOOLEAN", "", "Tick for anything covered by the Data Privacy Act.", False, True, 60),
]

USERS = [
    ("admin", "System", "Administrator", "REC", "ADMIN", "Records Officer IV", True),
    ("records", "Maricel", "Lorenzo", "REC", "SECRETARY", "Records Officer III", False),
    ("ovpa.sec", "Angeline", "Reyes", "OVPA", "SECRETARY", "Executive Assistant", False),
    ("med.staff", "Liza", "Fernandez", "MED", "USER", "Engineer II", False),
    ("hr.staff", "Carmela", "Villanueva", "HR", "USER", "HR Management Officer II", False),
    ("supply.staff", "Grace", "Ramos", "SUP", "USER", "Supply Officer II", False),
    ("proc.staff", "Teresa", "Aquino", "PROC", "USER", "Procurement Officer", False),
    ("sec.staff", "Alberto", "Dela Cruz", "SEC", "USER", "Security Head", False),
    ("pay.staff", "Jomar", "Santos", "PAY", "USER", "Payroll Clerk", False),
    ("lnd.staff", "Paolo", "Mercado", "LND", "USER", "Training Specialist", False),
]

# The last column is a TrackingRecord.Priority code. There are only two —
# NORMAL and URGENT. "HIGH" used to appear here and was stored verbatim,
# because Model.objects.create() does not police choices; the record page then
# showed the raw code "HIGH" where a priority label belongs.
SAMPLE_RECORDS = [
    ("MED", ["SUP", "PROC"], "MEMO", "Request for replenishment of electrical and plumbing supplies",
     "Please act within three days. Attach the current inventory count.", "URGENT"),
    ("SEC", ["OVPA"], "REPORT", "Incident report — gate 2 CCTV outage, 28 July 2026",
     "For information and appropriate instruction.", "URGENT"),
    ("HR", ["PAY"], "MEMO", "Submission of overtime summary for July 2026 payroll",
     "Kindly reconcile with the DTR before processing.", "NORMAL"),
    ("PROC", ["SUP", "OVPA"], "PR", "Purchase request for 20 units of office chairs",
     "For canvass and approval. Fund source: General Fund.", "NORMAL"),
    ("LND", ["HR", "OVPA"], "NOTICE", "Notice of in-service training for administrative staff",
     "Please confirm the list of participants per office.", "NORMAL"),
    ("SUP", ["MED"], "WO", "Work order — repair of air-conditioning unit at the registrar office",
     "Scheduled inspection on Thursday. Coordinate with maintenance.", "URGENT"),
    ("PAY", ["OVPA"], "VOUCHER", "Disbursement voucher for honoraria of part-time lecturers",
     "For review and signature.", "NORMAL"),
    ("REC", ["HR", "MED", "SEC"], "MEMO", "Reminder on records disposal schedule and retention periods",
     "For information of all offices under OVPA.", "NORMAL"),
]

ARCHIVE_DOCUMENTS = [
    ("REC", "MEMO", "Records retention schedule 2025", 2025,
     "Approved retention periods for administrative records under OVPA.",
     ["for information", "clearance"]),
    ("HR", "CERT", "Certificate of employment template 2025", 2025,
     "Standard template issued by the Human Resource Management Office.",
     ["personnel"]),
    ("PROC", "PR", "Purchase request — laboratory consumables 2025", 2025,
     "Consolidated request for the second semester of academic year 2025.",
     ["procurement", "budget"]),
    ("MED", "REPORT", "Annual preventive maintenance report 2025", 2025,
     "Summary of scheduled maintenance carried out on campus facilities.",
     ["engineering", "maintenance"]),
    ("SEC", "REPORT", "Security incident log — first semester 2025", 2025,
     "Consolidated log of incidents recorded at all gates.",
     ["security", "incident report"]),
]


class Command(BaseCommand):
    help = "Create demo offices, users, master data and sample records."

    def add_arguments(self, parser):
        parser.add_argument("--wipe", action="store_true", help="Delete existing records first (scratch DBs only).")
        parser.add_argument("--password", default=DEMO_PASSWORD, help="Password for every demo account.")

    def handle(self, *args, **options):
        password = options["password"]

        if options["wipe"]:
            self.stdout.write(self.style.WARNING("Wiping tracking records and documents…"))
            with transaction.atomic():
                Document.objects.all().delete()
                RoutingStep.objects.all().delete()
                TrackingRecord.objects.all().delete()

        # Each phase commits on its own.
        #
        # This used to be one @transaction.atomic block wrapping everything,
        # which meant a failure while building the *sample records* rolled back
        # the *user accounts* created moments earlier — so the visible symptom
        # was "login details not working", several steps away from the real
        # cause. Accounts and master data must survive a later failure.
        with transaction.atomic():
            offices = self._offices()
            types = self._document_types()
            tags = self._tags()
            self._tag_rules(tags, types, offices)
            self._metadata_fields()

        with transaction.atomic():
            users = self._users(offices, password)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Accounts and master data are saved — you can sign in now."))

        # Sample records are demo garnish. If they fail, say so loudly but do
        # not take the working accounts down with them.
        try:
            with transaction.atomic():
                self._records(offices, types, users)
            with transaction.atomic():
                self._archive(offices, types, users)
        except Exception as exc:
            self.stderr.write("")
            self.stderr.write(self.style.ERROR(f"Sample records could not be created: {exc}"))
            self.stderr.write(
                "Your accounts and master data are fine — sign in and create a document by hand."
            )
            if options.get("traceback"):
                raise

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Demo data ready."))
        self.stdout.write("")
        self.stdout.write("  Sign in at /accounts/login/")
        self.stdout.write(f"  Administrator : admin / {password}")
        self.stdout.write(f"  Records officer: records / {password}")
        self.stdout.write(f"  Regular user   : med.staff / {password}")
        self.stdout.write("")
        self.stdout.write("  Try this for the walkthrough:")
        self.stdout.write("   1. Sign in as med.staff — one document is waiting for receipt.")
        self.stdout.write("   2. Confirm receipt, add a remark, then forward it to SUP.")
        self.stdout.write("   3. Sign in as admin and search for 'electrical supplies'.")

    # -- master data -------------------------------------------------------
    def _offices(self):
        offices = {}
        parent = None
        for code, name, cluster, head in OFFICES:
            office, _ = Office.objects.update_or_create(
                code=code,
                defaults={"name": name, "cluster": cluster, "head_name": head, "is_active": True},
            )
            offices[code] = office
            if code == "OVPA":
                parent = office
        for code, office in offices.items():
            if code != "OVPA" and parent and office.parent_id != parent.pk:
                office.parent = parent
                office.save(update_fields=["parent"])
        self.stdout.write(f"Offices: {len(offices)}")
        return offices

    def _document_types(self):
        types = {}
        for order, (code, name, retention, description) in enumerate(DOCUMENT_TYPES, start=1):
            obj, _ = DocumentType.objects.update_or_create(
                code=code,
                defaults={
                    "name": name,
                    "retention_years": retention,
                    "description": description,
                    "sort_order": order * 10,
                    "is_active": True,
                },
            )
            types[code] = obj
        self.stdout.write(f"Document types: {len(types)}")
        return types

    def _tags(self):
        tags = {}
        for name, category in TAGS:
            tag, _created = Tag.get_or_create_by_name(name, category=category)
            tags[name] = tag
        self.stdout.write(f"Tags: {len(tags)}")
        return tags

    def _tag_rules(self, tags, types, offices):
        created = 0
        for name, tag_name, match_type, field, pattern, type_code, confidence, priority in TAG_RULES:
            TagRule.objects.update_or_create(
                name=name,
                defaults={
                    "pattern": pattern,
                    "match_type": match_type,
                    "search_field": field,
                    "suggest_tag": tags.get(tag_name),
                    "suggest_document_type": types.get(type_code) if type_code else None,
                    "confidence": confidence,
                    "priority": priority,
                    "is_active": True,
                },
            )
            created += 1
        self.stdout.write(f"Metadata rules: {created}")

    def _metadata_fields(self):
        for key, label, field_type, choices, help_text, searchable, show_in_list, order in METADATA_FIELDS:
            MetadataFieldDefinition.objects.update_or_create(
                key=key,
                defaults={
                    "label": label,
                    "field_type": field_type,
                    "choices_csv": choices,
                    "help_text": help_text,
                    "is_searchable": searchable,
                    "show_in_list": show_in_list,
                    "sort_order": order,
                    "is_active": True,
                },
            )
        self.stdout.write(f"Metadata fields: {len(METADATA_FIELDS)}")

    # -- people ------------------------------------------------------------
    def _users(self, offices, password):
        users = {}
        for username, first, last, office_code, role, position, is_admin in USERS:
            user, _ = User.objects.update_or_create(
                username=username,
                defaults={
                    "first_name": first,
                    "last_name": last,
                    "email": f"{username}@udm.edu.ph",
                    "office": offices.get(office_code),
                    "role": role,
                    "position": position,
                    "is_staff": is_admin,
                    "is_superuser": is_admin,
                    "is_active": True,
                    "must_change_password": False,
                },
            )
            user.set_password(password)
            user.save()
            users[username] = user
        self.stdout.write(f"Users: {len(users)}")
        return users

    # -- sample workload ---------------------------------------------------
    def _records(self, offices, types, users):
        by_office = {}
        for user in users.values():
            if user.office_id:
                by_office.setdefault(user.office.code, user)

        now = timezone.now()
        created = 0

        for index, (origin, receivers, type_code, subject, instructions, priority) in enumerate(SAMPLE_RECORDS):
            author = by_office.get(origin) or users["admin"]
            if TrackingRecord.objects.filter(subject=subject).exists():
                continue

            record = tracking_services.create_draft_record(
                user=author,
                subject=subject,
                document_type=types.get(type_code),
                instructions=instructions,
                priority=priority,
            )
            targets = [offices[code] for code in receivers if code in offices]
            tracking_services.route_record(
                record,
                targets,
                user=author,
                instructions=instructions,
                due_days=3,
            )

            # Backdate so the dashboard is not a wall of "just now".
            age = timedelta(days=len(SAMPLE_RECORDS) - index, hours=random.randint(1, 6))
            TrackingRecord.objects.filter(pk=record.pk).update(
                created_at=now - age, last_movement_at=now - age
            )
            RoutingStep.objects.filter(record=record).update(sent_at=now - age)

            # Give the first few a realistic life: received, remarked, some completed.
            if index % 3 != 0:
                step = record.routing_steps.order_by("sequence").first()
                receiver = by_office.get(step.to_office.code) if step else None
                if receiver:
                    tracking_services.confirm_receipt(record, user=receiver, note="Received at the office.")
                    if index % 3 == 2:
                        tracking_services.add_remark(
                            record, user=receiver, remark="Noted and endorsed to the concerned staff."
                        )
                    if index >= 6:
                        tracking_services.complete_record(record, user=receiver, note="Action completed and filed.")
                        archive_tracking_record(record, user=receiver)
            created += 1

        # One deliberately overdue item so the red card on the dashboard is real.
        overdue = TrackingRecord.objects.filter(status="IN_TRANSIT").order_by("created_at").first()
        if overdue:
            TrackingRecord.objects.filter(pk=overdue.pk).update(due_at=now - timedelta(days=2))

        self.stdout.write(f"Tracking records: {created}")

    def _archive(self, offices, types, users):
        created = 0
        for office_code, type_code, title, year, description, tag_names in ARCHIVE_DOCUMENTS:
            if Document.objects.filter(title=title).exists():
                continue
            document = Document.objects.create(
                title=title,
                description=description,
                office=offices.get(office_code),
                document_type=types.get(type_code),
                year=year,
                source="UPLOAD",
                uploaded_by=users.get("records"),
                ocr_status="SKIPPED",
                ocr_text=f"{title}. {description}",
            )
            # set_tags(), not document.tags.set(): the helper is what keeps
            # Tag.usage_count in step. Assigning the m2m directly left every
            # seeded tag on zero, and the repository's popular-tags panel only
            # lists tags with a count above zero — so a freshly seeded demo
            # showed an empty panel next to five tagged documents.
            set_tags(document, tag_names, user=users.get("records"))
            document.rebuild_index()
            created += 1
        self.stdout.write(f"Archived documents: {created}")
