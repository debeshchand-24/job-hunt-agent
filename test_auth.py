"""Smoke test for Google OAuth2 authentication."""
from auth.google_auth import get_credentials


def main():
    print("Authenticating with Google...")
    creds = get_credentials()

    assert creds and creds.valid, "Credentials are invalid after auth flow."

    print("Google auth successful!")
    print(f"  Token expiry : {creds.expiry}")
    print(f"  Scopes       : {', '.join(creds.scopes)}")


if __name__ == "__main__":
    main()
