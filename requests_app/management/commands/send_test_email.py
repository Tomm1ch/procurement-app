from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Send a local test email to Mailpit."

    def add_arguments(self, parser):
        parser.add_argument("--to", default="employee@example.com", help="Recipient shown in Mailpit")

    def handle(self, *args, **options):
        recipient = options["to"]
        send_mail(
            subject="Lio Intake test email",
            message="Mail delivery is configured correctly. This message was sent by the Lio Intake application.",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[recipient],
            fail_silently=False,
        )
        self.stdout.write(self.style.SUCCESS(f"Test email sent to {recipient}. Open Mailpit to view it."))
