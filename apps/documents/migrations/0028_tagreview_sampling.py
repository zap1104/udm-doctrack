from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("documents", "0027_intelligence_lifecycle")]
    operations = [
        migrations.AddField(
            model_name="tagreview", name="sampling", field=models.JSONField(default=dict, editable=False)
        )
    ]
