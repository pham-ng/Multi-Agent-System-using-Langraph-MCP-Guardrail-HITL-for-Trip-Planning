from auth import hash_password, normalize_email, verify_password


def test_password_hash_round_trip():
    encoded = hash_password("correct horse battery")
    assert verify_password("correct horse battery", encoded)
    assert not verify_password("incorrect", encoded)


def test_email_normalization():
    assert normalize_email("  USER@Example.COM ") == "user@example.com"
