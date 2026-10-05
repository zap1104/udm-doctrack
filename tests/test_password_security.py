"""Fixture speedups must leave real account password security exercised."""

import pytest
from django.contrib.auth.hashers import identify_hasher

from apps.accounts.models import User


@pytest.mark.django_db
def test_real_user_manager_hashes_passwords_with_argon2_and_unique_salts(offices):
    password = "RealHashSecurityCheck123!"
    first = User.objects.create_user(username="hash-security-one", password=password, office=offices["MED"])
    second = User.objects.create_user(username="hash-security-two", password=password, office=offices["MED"])
    assert identify_hasher(first.password).algorithm == "argon2"
    assert identify_hasher(second.password).algorithm == "argon2"
    assert first.password != password
    assert first.password != second.password
    assert first.check_password(password)
    assert not first.check_password("WrongPassword123!")
    assert User.objects.get_by_natural_key(first.username).pk == first.pk
