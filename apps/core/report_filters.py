"""Legacy record filters retained for permission-scoped tracking CSV exports."""

from datetime import datetime

from django.contrib import messages
from django.db.models import Q

from apps.tracking.models import Status, overdue_q


def filter_report_records(records, request, *, repository=False, type_values=None, remainder=None):
    status = request.GET.get("record_status", "") if not repository else ""
    kind = request.GET.get("record_type", "")
    if type_values is None:
        type_values = {
            str(pk or "none") for pk in records.order_by().values_list("document_type_id", flat=True)
        } if kind else set()
    month = request.GET.get("record_month", "")
    query = request.GET.get("record_q", "").strip()
    overdue = request.GET.get("record_overdue", "") if not repository else ""
    if overdue == "yes":
        records = records.filter(overdue_q())
    elif overdue:
        messages.warning(request, "Unknown overdue filter. Showing all deadlines.")
        overdue = ""
    if status:
        if status in Status.values:
            records = records.filter(status=status)
        else:
            messages.warning(request, "Unknown record status. Showing all statuses.")
            status = ""
    if kind:
        if kind in type_values:
            if kind == "other" and remainder:
                ids = remainder["type_ids"]
                match = Q(document_type_id__in=[pk for pk in ids if pk is not None])
                if None in ids:
                    match |= Q(document_type__isnull=True)
                records = records.filter(match)
            else:
                records = records.filter(document_type_id=None if kind == "none" else int(kind))
        else:
            messages.warning(request, "Unknown document type. Showing all types.")
            kind = ""
    if month:
        try:
            chosen = datetime.strptime(month, "%Y-%m").date()
            if len(month) != 7 or not month.isascii() or not 2 <= chosen.year <= 9998:
                raise ValueError("Month is outside the supported date range")
        except ValueError:
            messages.warning(request, "Invalid record month. Showing all months.")
            month = ""
        else:
            records = records.filter(created_at__year=chosen.year, created_at__month=chosen.month)
    if query:
        match = (
            Q(title__icontains=query) | Q(reference_number__icontains=query)
            if repository else Q(subject__icontains=query) | Q(tracking_number__icontains=query)
        )
        records = records.filter(match)
    return records.distinct(), {
        "record_status": status, "record_type": kind, "record_month": month, "record_q": query,
        "record_overdue": overdue,
    }
