from django.db import migrations


def backfill(apps, schema_editor):
    document = apps.get_model("documents", "Document")
    contribution = apps.get_model("documents", "TagContribution")
    through = document.tags.through
    batch = []
    for document_id, tag_id in through.objects.using(schema_editor.connection.alias).values_list("document_id", "tag_id").iterator(chunk_size=1000):
        batch.append(contribution(document_id=document_id, tag_id=tag_id, kind="human", source="legacy"))
        if len(batch) == 1000:
            contribution.objects.using(schema_editor.connection.alias).bulk_create(batch, ignore_conflicts=True)
            batch = []
    contribution.objects.using(schema_editor.connection.alias).bulk_create(batch, ignore_conflicts=True)


class Migration(migrations.Migration):
    dependencies = [("documents", "0019_tag_provenance")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
