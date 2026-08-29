from django.test import TestCase


class HomePageTests(TestCase):
    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get("/")
        self.assertRedirects(response, "/accounts/login/?next=/")
