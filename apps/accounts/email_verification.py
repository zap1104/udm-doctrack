from django.contrib.auth.tokens import PasswordResetTokenGenerator


class EmailVerificationTokenGenerator(PasswordResetTokenGenerator):
    key_salt = "doctrack.email-verification"

    def _make_hash_value(self, user, timestamp):
        # A login must not invalidate the link the user is asked to sign in to
        # open. Bind to account, password, current address, and verification.
        return f"{user.pk}|{user.password}|{timestamp}|{user.email}|{user.verified_email}"


email_verification_token = EmailVerificationTokenGenerator()
