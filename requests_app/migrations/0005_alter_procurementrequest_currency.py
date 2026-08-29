from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("requests_app", "0004_procurementrequest_short_description")]

    operations = [
        migrations.AlterField(
            model_name="procurementrequest",
            name="currency",
            field=models.CharField(
                choices=[
                    ("EUR", "EUR — Euro"),
                    ("USD", "USD — US Dollar"),
                    ("GBP", "GBP — British Pound"),
                    ("CHF", "CHF — Swiss Franc"),
                    ("CAD", "CAD — Canadian Dollar"),
                    ("JPY", "JPY — Japanese Yen"),
                ],
                default="EUR",
                max_length=3,
            ),
        ),
    ]
