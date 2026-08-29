from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create local employee, procurement and administrator demo accounts."

    def handle(self, *args, **options):
        employee_group, _ = Group.objects.get_or_create(name="Employees")
        procurement_group, _ = Group.objects.get_or_create(name="Procurement")
        users = [
            ("employee", "employee@example.com", "Employee", "Demo", False, employee_group),
            ("procurement", "procurement@example.com", "Procurement", "Manager", False, procurement_group),
            ("admin", "admin@example.com", "Admin", "User", True, procurement_group),
        ]
        for username, email, first, last, admin, group in users:
            user, created = User.objects.get_or_create(username=username, defaults={"email": email})
            user.email, user.first_name, user.last_name = email, first, last
            user.is_staff = admin
            user.is_superuser = admin
            if created:
                user.set_password(f"{username}123")
            user.save()
            user.groups.add(group)
        self.stdout.write(self.style.SUCCESS("Demo users are ready. Passwords are <username>123."))
