from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("requests_app", "0003_alter_procurementrequest_department")]

    operations = [
        migrations.AddField(
            model_name="procurementrequest",
            name="short_description",
            field=models.CharField(blank=True, max_length=500),
        ),
    ]
