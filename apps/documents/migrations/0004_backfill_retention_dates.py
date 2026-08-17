from datetime import date

from django.db import migrations


def backfill_retention_dates(apps, schema_editor):
    Document = apps.get_model("documents", "Document")
    queryset = Document.objects.filter(
        retention_until__isnull=True,
        document_type__isnull=False,
        document_type__retention_years__gt=0,
    ).select_related("document_type")
    for document in queryset.iterator(chunk_size=500):
        base = document.document_date or date(document.year, 12, 31)
        target_year = base.year + document.document_type.retention_years
        try:
            retention_until = base.replace(year=target_year)
        except ValueError:
            retention_until = base.replace(year=target_year, month=2, day=28)
        Document.objects.filter(pk=document.pk, retention_until__isnull=True).update(
            retention_until=retention_until
        )


class Migration(migrations.Migration):
    dependencies = [("documents", "0003_searchresultclick")]

    operations = [migrations.RunPython(backfill_retention_dates, migrations.RunPython.noop)]
