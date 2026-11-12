from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0011_user_security_revision")]

    operations = [
        migrations.AlterField(
            model_name="loginlockout",
            name="stage",
            field=models.PositiveIntegerField(
                default=0, help_text="Number of lockouts recorded since this history last reset."
            ),
        ),
        migrations.AlterField(
            model_name="loginlockout",
            name="locked_until",
            field=models.DateTimeField(
                blank=True, null=True, help_text="Recorded end of the latest lockout when it began."
            ),
        ),
    ]
