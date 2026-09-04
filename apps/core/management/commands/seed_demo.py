"""Creates everything needed to demo the system, in about a minute.

    python manage.py seed_demo
    python manage.py seed_demo --records 0    # accounts and master data only

Two kinds of sample data. Eight narrative records written to be read — the
walkthrough at the end of this file refers to them by name — and on top of that
a year of generated traffic, because the dashboard's charts need history to
have any shape: a trend line wants twelve months of completions, the two rings
want every status occupied at once, and the per-office lists want more than one
office in them. With only the narrative eight, every chart is one bar tall.

`--records` sets how much of that generated traffic to make. It is the only
slow part; drop it to 0 when you just need accounts.

Safe to run more than once — it updates rather than duplicates, and the
generated traffic is skipped once the records are there. Use --wipe only on a
scratch database; it deletes tracking and document data.

Every demo account uses the same password so nobody has to memorise a list.
Change DEMO_PASSWORD before running this anywhere real.
"""

from __future__ import annotations

import random
from datetime import datetime, time, timedelta

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

# Roles: SYSTEM_ADMIN reaches every office; ADMIN is the head of one office and
# administers only its accounts; USER does the day's work; VIEWER may read the
# office's documents and print a slip, nothing more.
#
# The records officer and the executive assistant are USERs, not ADMINs. Being
# the office's records person is not the same as being its head — the head is
# who hires, suspends and resets passwords — and the migration that retired the
# SECRETARY role mapped it to USER for exactly that reason. `med.head` is here
# so the office-administrator role has somebody to demonstrate it.
USERS = [
    ("admin", "System", "Administrator", "REC", "SYSTEM_ADMIN", "Records Officer IV", True),
    ("records", "Maricel", "Lorenzo", "REC", "USER", "Records Officer III", False),
    ("ovpa.sec", "Angeline", "Reyes", "OVPA", "USER", "Executive Assistant", False),
    ("med.head", "Rodrigo", "Bautista", "MED", "ADMIN", "Department Head", False),
    ("med.viewer", "Ana", "Cruz", "MED", "VIEWER", "Administrative Aide", False),
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

#: Subjects for the generated backlog, grouped by document type so a Work Order
#: does not come out titled like a payroll voucher. Twelve months of traffic
#: needs more than the eight narrative records below, and a demo where every
#: chart is one bar tall shows nothing about the charts.
BULK_SUBJECTS = {
    "MEMO": [
        "Reminder on the submission of monthly accomplishment reports",
        "Advisory on the revised office hours during the semestral break",
        "Guidelines for the use of the multi-purpose hall",
        "Instruction on the annual physical inventory count",
        "Memorandum on energy conservation measures",
        "Directive on the updating of office property cards",
    ],
    "LETTER": [
        "Letter of request for a courtesy visit",
        "Reply to the query on procurement timelines",
        "Endorsement letter for the accreditation review",
        "Letter of intent for the campus greening partnership",
    ],
    "WO": [
        "Work order — replacement of ceiling lights at the annex building",
        "Work order — repair of the water pump at the science laboratory",
        "Work order — repainting of the corridor on the second floor",
        "Work order — servicing of the standby generator",
        "Work order — repair of the perimeter fence at gate 3",
    ],
    "PR": [
        "Purchase request for janitorial and cleaning supplies",
        "Purchase request for laboratory glassware",
        "Purchase request for printing of official forms",
        "Purchase request for network switches and patch cables",
        "Purchase request for office furniture replacement",
    ],
    "ENDORSE": [
        "Endorsement of the request for facility use",
        "Endorsement of the scholarship applications for review",
        "Endorsement of the incident findings to the legal office",
    ],
    "REPORT": [
        "Monthly accomplishment report of the maintenance section",
        "Quarterly inventory report of consumable supplies",
        "Incident report — power interruption at the main building",
        "Accomplishment report on the preventive maintenance programme",
        "Report on the utilisation of the maintenance and operating budget",
    ],
    "NOTICE": [
        "Notice of meeting — administrative council",
        "Notice of scheduled water interruption",
        "Notice of bidding for the supply of office equipment",
        "Notice of orientation for newly hired personnel",
    ],
    "CERT": [
        "Certification of employment and compensation",
        "Certification of no pending accountability",
        "Certification of service record for retirement processing",
    ],
    "VOUCHER": [
        "Disbursement voucher for utilities for the month",
        "Disbursement voucher for the repair of service vehicles",
        "Disbursement voucher for training and seminar expenses",
        "Disbursement voucher for the purchase of office supplies",
    ],
}

#: Historical uploads, the repository's other half. Titles are templated per
#: year so twelve months of filing does not read as the same five documents.
BULK_ARCHIVE_TITLES = [
    ("REC", "MEMO", "Records disposal authority", ["for information"]),
    ("REC", "REPORT", "Annual records inventory", ["inventory"]),
    ("HR", "CERT", "Consolidated service records", ["personnel"]),
    ("HR", "MEMO", "Personnel movement summary", ["personnel"]),
    ("PROC", "PR", "Consolidated procurement plan", ["procurement", "budget"]),
    ("PROC", "NOTICE", "Notice of award — office equipment", ["procurement"]),
    ("MED", "REPORT", "Preventive maintenance log", ["engineering", "maintenance"]),
    ("MED", "WO", "Completed work orders digest", ["maintenance"]),
    ("SEC", "REPORT", "Gate incident log", ["security", "incident report"]),
    ("SUP", "REPORT", "Property and equipment ledger", ["inventory"]),
    ("PAY", "VOUCHER", "Payroll register summary", ["payroll"]),
    ("LND", "REPORT", "Training accomplishment digest", ["training"]),
    ("OVPA", "MEMO", "Administrative issuances compilation", ["for information"]),
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
        parser.add_argument(
            "--records", type=int, default=280,
            help="Generated tracking records spread over the last year. 0 skips them.",
        )
        parser.add_argument(
            "--months", type=int, default=12,
            help="How far back the generated history reaches. Matches the reports window.",
        )

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
            if options["records"]:
                # Outside the two blocks above so a failure here cannot roll
                # back the narrative records the walkthrough refers to.
                with transaction.atomic():
                    self._bulk_workload(
                        offices, types, users,
                        target=options["records"], months=options["months"],
                    )
                with transaction.atomic():
                    self._bulk_archive(offices, types, users, months=options["months"])
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
        self.stdout.write("   4. Open the dashboard as admin — a year of traffic sits behind the charts.")

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
            sent = now - age
            TrackingRecord.objects.filter(pk=record.pk).update(
                created_at=sent, last_movement_at=sent
            )
            RoutingStep.objects.filter(record=record).update(sent_at=sent)

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

                    # The services stamp receipt and completion with server
                    # time, which is right everywhere except here: leaving them
                    # at `now` beside a sent_at backdated by a week made these
                    # eight records look like the slowest in the system, and
                    # they all landed in the current month — enough to bend the
                    # turnaround trend's last point on their own.
                    received = min(sent + timedelta(hours=random.randint(3, 9)), now)
                    RoutingStep.objects.filter(record=record, received_at__isnull=False).update(
                        received_at=received
                    )
                    stamps = {"first_received_at": received, "last_movement_at": received}
                    completed = TrackingRecord.objects.filter(
                        pk=record.pk, completed_at__isnull=False
                    ).exists()
                    if completed:
                        done = min(received + timedelta(days=random.uniform(0.5, 2)), now)
                        stamps["completed_at"] = done
                        stamps["last_movement_at"] = done
                        Document.objects.filter(tracking_record=record).update(created_at=done)
                    TrackingRecord.objects.filter(pk=record.pk).update(**stamps)
            created += 1

        # One deliberately overdue item so the red card on the dashboard is real.
        overdue = TrackingRecord.objects.filter(status="PENDING_RECEIPT").order_by("created_at").first()
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

    # -- generated history -------------------------------------------------
    #
    # The narrative records above are eight documents written to be read. The
    # dashboard's charts need something else: twelve months of traffic, every
    # status occupied, and completions spread across the months so the
    # turnaround trend has more than one point to join up.
    #
    # Timestamps are written afterwards with queryset updates. The services set
    # them to `now` on purpose — receipt in particular is server time and never
    # a value a user supplies — so backdating goes around them rather than
    # through them, which is also why this lives in a seed command and nowhere
    # near application code.
    def _bulk_workload(self, offices, types, users, *, target, months):
        existing = TrackingRecord.objects.count()
        if existing >= target:
            self.stdout.write(f"Generated workload: skipped, {existing} records already present")
            return

        by_office = {}
        for user in users.values():
            if user.office_id and not user.is_viewer:
                by_office.setdefault(user.office.code, user)
        codes = [code for code in by_office if code in offices]

        now = timezone.now()
        rng = random.Random(20260904)  # fixed, so two demos look the same
        wanted = target - existing
        per_month = max(1, round(wanted / months))
        made = 0

        for age in range(months - 1, -1, -1):
            # Roughly even per month, with the current month lighter because it
            # is still in progress rather than finished.
            # The last two months carry the live statuses, so they are not
            # thinned: everything Incoming, In process and Pending receipt show
            # comes from here, against a year of completed work behind it.
            count = per_month if age > 1 else round(per_month * 1.25)
            for _ in range(count):
                if made >= wanted:
                    break
                origin = rng.choice(codes)
                destinations = rng.sample(
                    [code for code in codes if code != origin], rng.choice([1, 1, 1, 2])
                )
                type_code = rng.choice(list(BULK_SUBJECTS))
                author = by_office[origin]

                # The current month is drawn from the last fortnight rather
                # than the last 27 days. Anything older than that has already
                # passed a three-to-ten-day deadline, so spreading it wide made
                # almost every live record overdue on arrival and left Incoming
                # — which is RECEIVED and *not* overdue — showing three.
                if age:
                    start = now - timedelta(
                        days=age * 30 + rng.randint(1, 27), hours=rng.randint(0, 20)
                    )
                else:
                    start = now - timedelta(days=rng.randint(0, 13), hours=rng.randint(0, 20))
                # Longer deadlines on the newest work, so a fair share of it is
                # still in date. Incoming is defined as received and *not*
                # overdue, so three-day deadlines on everything leave that
                # slice empty by construction rather than by circumstance.
                due_days = rng.choice([5, 7, 10, 14] if age == 0 else [3, 5, 5, 7, 10])

                record = tracking_services.create_draft_record(
                    user=author,
                    subject=f"{rng.choice(BULK_SUBJECTS[type_code])} ({start:%b %Y})",
                    document_type=types.get(type_code),
                    instructions="For appropriate action.",
                    priority="URGENT" if rng.random() < 0.18 else "NORMAL",
                )
                steps = tracking_services.route_record(
                    record,
                    [offices[code] for code in destinations],
                    user=author,
                    instructions="For appropriate action.",
                    due_days=due_days,
                )

                sent = start + timedelta(hours=rng.randint(1, 5))
                due = sent + timedelta(days=due_days)

                # An improving trend rather than noise around a flat line: a
                # chart whose only story is randomness says nothing about what
                # the chart is for.
                speed = 0.6 + (age / max(1, months - 1)) * 0.8
                received = completed = None

                outcome = rng.random()
                # Older months are nearly settled; the recent ones carry the
                # live statuses, which is what the two rings are made of. The
                # band between `settled` and `settled + working` is the slice
                # that was received and not yet finished — it has to be wide in
                # the recent months or Incoming and In process come out at two
                # or three records against a hundred completed ones.
                settled, working = {
                    0: (0.22, 0.58),
                    1: (0.55, 0.30),
                }.get(age, (0.93, 0.05))

                if outcome < settled + working:
                    receipt_lag = timedelta(hours=rng.uniform(2, 40) * speed)
                    received = min(sent + receipt_lag, now - timedelta(minutes=5))
                    receiver = by_office[destinations[0]]
                    tracking_services.confirm_receipt(
                        record, user=receiver, note="Received at the office."
                    )
                    if rng.random() < 0.5:
                        tracking_services.add_remark(
                            record, user=receiver, remark="Endorsed to the concerned staff."
                        )

                    if outcome < settled:
                        processing = timedelta(days=rng.uniform(0.5, 9) * speed)
                        completed = min(received + processing, now - timedelta(minutes=1))
                        tracking_services.complete_record(
                            record, user=receiver, note="Action completed."
                        )
                        # Most filed, a few left waiting for approval so the
                        # awaiting-upload slice is not empty.
                        if rng.random() < 0.82:
                            archive_tracking_record(record, user=receiver)
                    elif rng.random() < 0.4:
                        tracking_services.mark_in_process(record, user=receiver)

                last = completed or received or sent
                TrackingRecord.objects.filter(pk=record.pk).update(
                    created_at=start,
                    last_movement_at=last,
                    due_at=due,
                    first_received_at=received,
                    completed_at=completed,
                )
                RoutingStep.objects.filter(pk__in=[step.pk for step in steps]).update(
                    sent_at=sent, due_at=due
                )
                if received:
                    RoutingStep.objects.filter(record=record, received_at__isnull=False).update(
                        received_at=received
                    )
                if completed:
                    Document.objects.filter(tracking_record=record).update(
                        created_at=completed, document_date=timezone.localdate(completed)
                    )
                made += 1

        # Overdue deliberately, across several offices and at several ages: one
        # office holding one late document draws a chart with one bar.
        # Only from the older half. A document sent three days ago against a
        # five-day deadline is not late, and dating it late anyway ate the
        # records that make up Incoming — the ring showed two.
        live = list(
            TrackingRecord.objects.exclude(
                status__in=("COMPLETED", "COMPLETED_PENDING_UPLOAD")
            )
            .filter(created_at__lt=now - timedelta(days=14))
            .order_by("created_at")[: max(6, made // 8)]
        )
        for index, record in enumerate(live):
            TrackingRecord.objects.filter(pk=record.pk).update(
                due_at=now - timedelta(days=[1, 2, 3, 5, 8, 13, 21, 30][index % 8])
            )

        self.stdout.write(f"Generated workload: {made} records across {months} months")

    def _bulk_archive(self, offices, types, users, *, months):
        """Historical uploads — the repository's other half.

        Everything the tracking side files arrives as source=DTS. Without these
        the Repository ring is a single colour, and "Added to the repository"
        credits only the offices that happened to complete something.
        """
        rng = random.Random(20260905)
        first_of_this_month = timezone.localdate().replace(day=1)
        made = 0

        for back in range(months):
            month_day = first_of_this_month - timedelta(days=back * 30)
            month_day = month_day.replace(day=1)
            for office_code, type_code, stem, tag_names in BULK_ARCHIVE_TITLES:
                # Every draw for this row happens before either `continue`, so
                # the stream lands in the same place whether or not the row is
                # skipped. Skipping mid-draw desynchronised it, and a second
                # run then chose a different 45% and filed 41 more documents —
                # which is the opposite of "safe to run more than once".
                wanted = rng.random() <= 0.45
                day, hour = rng.randint(1, 28), rng.randint(8, 17)
                title = f"{stem} — {month_day:%B %Y}"
                if not wanted or Document.objects.filter(title=title).exists():
                    continue
                document = Document.objects.create(
                    title=title,
                    description=f"Filed by the {offices[office_code].name}.",
                    office=offices.get(office_code),
                    document_type=types.get(type_code),
                    year=month_day.year,
                    source="UPLOAD",
                    uploaded_by=users.get("records"),
                    ocr_status="SKIPPED",
                    ocr_text=title,
                )
                set_tags(document, tag_names, user=users.get("records"))
                document.rebuild_index()
                stamp = timezone.make_aware(
                    datetime.combine(month_day.replace(day=day), time(hour=hour))
                )
                Document.objects.filter(pk=document.pk).update(
                    created_at=stamp, document_date=timezone.localdate(stamp)
                )
                made += 1

        self.stdout.write(f"Generated archive: {made} historical documents")
