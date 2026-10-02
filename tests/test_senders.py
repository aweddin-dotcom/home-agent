"""Sender checks: a sender name that doesn't match where the mail came from.
All senders are invented (the example.* and .test domains are reserved)."""

import pytest
import yaml

from services.common.senders import check_sender, sender_note
from services.common import settings

CONFIG = yaml.safe_load((settings.CONFIG_DIR / "sender_checks.yaml").read_text(encoding="utf-8"))


def level(sender):
    check = check_sender(sender, CONFIG)
    return check.level if check else None


@pytest.mark.parametrize("sender", [
    "PayPal <service@paypal.com>",
    "PayPal Security <security@intl.paypal.com>",
    "Amazon.com <auto-confirm@amazon.com>",
    "Amazon Web Services <no-reply@aws.amazon.com>",
    "Chase <no-reply@alertsp.chase.com>",
    "Bank of America <onlinebanking@ealerts.bankofamerica.com>",
    "Apple <no_reply@email.apple.com>",
    "Google <no-reply@accounts.google.com>",
    "Vanguard <statements@vanguard.com>",
])
def test_genuine_brand_mail_passes(sender):
    assert level(sender) is None


@pytest.mark.parametrize("sender", [
    "Jordan Lee <jordan@example.net>",                       # a person
    "Chase Miller <chase.miller@gmail.com>",                 # a person who happens to share a bank's name
    "Grandma <nana1951@yahoo.com>",                          # a plain one-word name
    "Tom and Jerry Smith <tjsmith@gmail.com>",
    "Rick's Plumbing <office@ricksplumbing.example>",
    "Riverside Dental Office <frontdesk@riversidedental.example>",
    "Maple Elementary School <alerts@schoolmessenger.com>",  # a school's sending service
    "Applebee's <deals@applebees.example>",                  # "apple" inside another word
    "Google Calendar <calendar-notification@google.com>",
    "Customer Support <support@acme-widgets.example>",       # nothing to compare: fine
])
def test_everyday_mail_passes(sender):
    assert level(sender) is None


@pytest.mark.parametrize("sender", [
    "PayPal <service@paypa1-secure.example>",                # the brand, from someone else's domain
    "PayPal Security Team <alert@account-review.example>",
    "Amazon Support <amazon.support.desk@gmail.com>",
    "Chase Bank <alerts@chase-verify.example>",
    "Wells Fargo Online <noreply@wf-secure-login.example>",
    '"service@paypal.com" <notice@mailer.example>',         # a different address written into the name
    "Account Review <noreply@paypal-alerts.example>",        # the brand inside an unrelated domain
    "Netflix Billing <billing@netflix-payments.example>",
])
def test_impersonation_is_possible_phishing(sender):
    assert level(sender) == "phishing"


@pytest.mark.parametrize("sender", [
    "Riverside Dental Office <reminders@unrelated-host.example>",
    "Lakeview Credit Union Support <helpdesk@gmail.com>",
])
def test_business_name_unrelated_to_the_domain_is_a_mismatch(sender):
    assert level(sender) == "mismatch"


def test_trusted_senders_and_domains_pass():
    cfg = {**CONFIG, "trusted": ["unrelated-host.example", "helpdesk@gmail.com"]}
    assert check_sender("Riverside Dental Office <reminders@unrelated-host.example>", cfg) is None
    assert check_sender("Lakeview Credit Union Support <helpdesk@gmail.com>", cfg) is None


def test_notes_explain_in_plain_words():
    assert sender_note("PayPal <service@paypa1-secure.example>", CONFIG) == (
        "⚠ Possible phishing: the name says PayPal, but paypa1-secure.example isn't one of Paypal's domains.")
    assert sender_note("Amazon Support <amazon.support.desk@gmail.com>", CONFIG).startswith("⚠ Possible phishing")
    assert sender_note("Riverside Dental Office <reminders@unrelated-host.example>", CONFIG) == (
        "⚠ Sender doesn't match: the name says Riverside Dental Office, but it was sent from unrelated-host.example.")
    assert sender_note("Jordan Lee <jordan@example.net>", CONFIG) == ""
    assert sender_note("not an address", CONFIG) == ""


# --- the digest and chat use the check ---------------------------------------------

from datetime import datetime  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from services.digest import build as digest  # noqa: E402
from services.retrieval.ask import format_emails, format_received  # noqa: E402

TZ = ZoneInfo("America/New_York")


def row(sender, subject, account="outlook"):
    return {"account": account, "email_id": subject, "sender": sender, "subject": subject,
            "date": "2026-10-01T12:00:00+00:00", "body": "Your account is locked. Verify now.", "snippet": "",
            "folders": ["Inbox"], "kind": "other"}


class AlwaysAttention:
    def complete(self, system, user, schema=None, temperature=None):
        self.user = user
        return '{"category": "attention", "summary": "Verify your account today", "due": null}'


def test_a_mismatched_sender_is_never_a_to_do():
    chat = AlwaysAttention()
    r = row("Riverside Dental Office <reminders@unrelated-host.example>", "Appointment")
    check = check_sender(r["sender"], CONFIG)
    s = digest.sort_email(chat, "system", r, check)
    assert s.category == "worth_knowing"
    assert s.summary.startswith("⚠ Sender doesn't match (reminders@unrelated-host.example):")
    assert "Sender check: the name says Riverside Dental Office" in chat.user  # the model is told too


def test_possible_phishing_gets_its_own_section():
    r = row("PayPal <service@paypa1-secure.example>", "Your account is limited")
    config = {"max_items": {"needs_attention": 5, "coming_up": 5, "worth_knowing": 3, "suspicious": 5}}
    text = digest.compose(datetime(2026, 10, 2, 7, tzinfo=TZ), [], 0, [], TZ, config, [],
                          suspicious=[(r, check_sender(r["sender"], CONFIG))])
    assert "**Looks suspicious**" in text
    assert ('- ⚠ "Your account is limited" from service@paypa1-secure.example: the name says PayPal, '
            "but paypa1-secure.example isn't one of Paypal's domains  (outlook)") in text
    assert "Nothing needs your attention today." in text


def test_chat_shows_the_warning_with_the_email():
    hit = {**row("Amazon Support <amazon.support.desk@gmail.com>", "Order problem"),
           "text": "Subject: Order problem\nFrom: Amazon Support"}
    assert "[1]\n⚠ Possible phishing: the name says Amazon Support" in format_emails([hit])
    listing = format_received(datetime(2026, 10, 1).date(), datetime(2026, 10, 1).date(), 1, [hit])
    assert "Subject: Order problem\n     ⚠ Possible phishing" in listing
    assert "⚠" not in format_emails([{**hit, "sender": "Jordan Lee <jordan@example.net>"}])


@pytest.mark.parametrize("sender", [
    "Lakeview Credit Union <statements@lcu.example>",              # initials
    "Harbor City Alerts <noreply@alerts.cityservices-notify.example>",  # a notification service domain
    "Pinecrest Medical Center <reminders@em.patientportal.example>",
])
def test_initials_and_sending_services_are_not_mismatches(sender):
    assert level(sender) is None


def test_brands_are_still_caught_through_sending_service_domains():
    assert level("PayPal Alerts <noreply@alerts-mailer.example>") == "phishing"
