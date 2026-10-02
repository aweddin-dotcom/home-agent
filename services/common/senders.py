"""Does an email's sender name match where it actually came from?

Phishing mail usually looks official: the display name says "PayPal
Security" while the address is some unrelated domain. This compares the
business named in the display name with the sending domain, in code, using
config/sender_checks.yaml:

  "phishing"  the name is a well-known brand (alone or with words like
              "Support"), but the domain isn't one of that brand's; or the
              name contains a different email address; or the domain uses a
              brand's name without being that brand's (paypal-alerts.com).
  "mismatch"  the name looks like a business ("Support Team", "Riverside
              Dental Office") but has nothing in common with the domain, and
              the domain isn't a known sending service or trusted.

People's names aren't checked: a person's name rarely matches their domain.
"""

import re
from dataclasses import dataclass
from email.utils import parseaddr

# Words that make a display name look like a business, and that may sit
# next to a brand name in genuine mail ("Chase Alerts", "Apple Support").
BUSINESS_WORDS = {
    "inc", "llc", "ltd", "co", "corp", "company", "bank", "banking", "online", "credit", "union", "financial",
    "insurance", "support", "security", "service", "services", "customer", "care", "billing", "account",
    "accounts", "team", "department", "dept", "notification", "notifications", "alert", "alerts", "delivery",
    "deliveries", "shipping", "store", "shop", "pharmacy", "rewards", "payment", "payments", "invoice",
    "invoices", "help", "helpdesk", "desk", "admin", "administrator", "office", "clinic", "dental", "medical",
    "school", "district", "county", "city", "town", "church", "association", "hoa", "airlines", "airline",
    "hotel", "hotels", "rentals", "card", "cards", "member", "members", "membership", "verification", "verify",
    "center", "centre", "official", "info", "information", "no-reply", "noreply", "do-not-reply",
    "prime", "pay", "wallet", "id", "orders", "order", "receipt", "receipts", "update",
    "updates", "fraud", "protection", "group",
}
# Words that may appear in a name without making it a business or a person:
# "Bank of America", "Amazon.com", "PayPal | Security".
FILLER = {"the", "of", "and", "&", "-", "|", ":", "us", "usa", "com", "net", "org"}
FREE_MAIL = {
    "gmail.com", "googlemail.com", "yahoo.com", "ymail.com", "hotmail.com", "outlook.com", "live.com",
    "msn.com", "aol.com", "icloud.com", "me.com", "mac.com", "proton.me", "protonmail.com", "gmx.com",
    "mail.com", "zoho.com", "yandex.com", "comcast.net", "verizon.net", "att.net",
}
EMAIL_IN_NAME = re.compile(r"[\w.+-]+@([\w-]+\.)+[\w-]+")
# Domains that look like a mail-sending service (businesses often send
# alerts and newsletters through one): not a mismatch on their own. Brand
# impersonation is checked before this, so "PayPal" from paypal-alerts.com
# is still caught.
SENDING_SERVICE_HINT = re.compile(
    r"(^|[.\-\d])(e?mail|mailer|mailing|mailgun|send|sender|sendgrid|notify|notif\w*|alerts?|msg|messag\w*|"
    r"news|newsletter|email|em|bounce\w*|mta|comms?|communications?|info|reply|noreply|marketing|mkt)"
    r"\d*(?=[.\-\d]|$)")


@dataclass
class SenderCheck:
    level: str   # "phishing" or "mismatch"
    reason: str  # one plain sentence, for the user


def _domain_is(domain, roots):
    return any(domain == r or domain.endswith("." + r) for r in roots)


def _words(text):
    return [w for w in re.split(r"[^a-z0-9&-]+", text.lower()) if w]


def _rules():
    from services.common import settings

    try:
        config = settings.load_config("sender_checks.yaml") or {}
    except FileNotFoundError:
        config = {}
    return config


def check_sender(sender, config=None):
    """A SenderCheck for a "Name <address>" sender that looks wrong, else None."""
    config = _rules() if config is None else config
    name, address = parseaddr(sender or "")
    address = address.lower()
    if "@" not in address:
        return None
    domain = address.rsplit("@", 1)[1]
    trusted = {t.lower() for t in config.get("trusted") or []}
    if address in trusted or _domain_is(domain, trusted):
        return None
    brands = {b.lower(): [d.lower() for d in ds] for b, ds in (config.get("brands") or {}).items()}
    name_l = name.lower().strip().strip('"')

    # A different address written into the name: "service@paypal.com" <x@other.example>
    shown = EMAIL_IN_NAME.search(name_l)
    if shown and shown.group(0) != address:
        return SenderCheck("phishing", f"the name shows {shown.group(0)}, but it was sent from {address}")

    words = _words(name_l)
    # The name is a brand, alone or with business words: "PayPal Security", "Chase Bank".
    for brand, domains in brands.items():
        brand_words = _words(brand)
        n = len(brand_words)
        for i in range(len(words) - n + 1):
            if words[i:i + n] == brand_words:
                rest = words[:i] + words[i + n:]
                ok = all(w in BUSINESS_WORDS or w in FILLER or w in brand_words for w in rest)
                if ok and not _domain_is(domain, domains):
                    return SenderCheck("phishing", f"the name says {name.strip()}, but {domain} isn't "
                                                   f"one of {brand.title()}'s domains")

    # A brand's name standing on its own inside an unrelated domain:
    # paypal-alerts.com, secure-paypal.net, paypal.co (not applebees.com).
    labels = domain.split(".")
    for brand, domains in brands.items():
        key = re.escape(brand.replace(" ", ""))
        if len(brand.replace(" ", "")) < 4 or _domain_is(domain, domains):
            continue
        if any(re.search(rf"(^|[-\d]){key}([-\d]|$)", label) for label in labels[:-1]):
            return SenderCheck("phishing", f"{domain} uses {brand.title()}'s name but isn't one of its domains")

    # A business-looking name with nothing in common with the domain.
    if not any(w in BUSINESS_WORDS for w in words):
        return None  # a person's name, or a plain one-word name
    if _domain_is(domain, config.get("sending_services") or []):
        return None
    if domain not in FREE_MAIL and SENDING_SERVICE_HINT.search(domain):
        return None
    meaningful = [w for w in words if len(w) >= 3 and w not in BUSINESS_WORDS and w not in FILLER]
    domain_text = "".join(labels[:-1])
    joined = "".join(w for w in words if w not in BUSINESS_WORDS and w not in FILLER)
    if any(w in domain_text for w in meaningful) or (len(joined) >= 4 and joined in domain_text):
        return None
    # Initials: "Lakeview Credit Union" from lcu.org, "Wells Fargo" from wf.com.
    initials = "".join(w[0] for w in words if w not in FILLER)
    if len(initials) >= 2 and any(label == initials or label.startswith(initials) for label in labels[:-1]):
        return None
    if not meaningful and domain not in FREE_MAIL:
        return None  # "Customer Support" from a company domain: nothing to compare
    where = f"a personal {domain} account" if domain in FREE_MAIL else domain
    return SenderCheck("mismatch", f"the name says {name.strip()}, but it was sent from {where}")


def sender_note(sender, config=None):
    """A short warning line for a flagged sender, or ""."""
    check = check_sender(sender, config)
    if not check:
        return ""
    label = "Possible phishing" if check.level == "phishing" else "Sender doesn't match"
    return f"⚠ {label}: {check.reason}."


def report(store, days=30, out=print):
    """Senders flagged over the last `days` days, for reviewing the rules:
    address domain, level, and how many emails. Personal: printed for the user
    in their terminal, never logged."""
    from collections import Counter
    from datetime import datetime, timedelta, timezone

    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    found = Counter()
    for row in store.emails_since(datetime.fromisoformat(since)):
        check = check_sender(row.get("sender"))
        if check:
            found[(check.level, parseaddr(row["sender"])[0], parseaddr(row["sender"])[1].rsplit("@", 1)[-1])] += 1
    if not found:
        out(f"No senders flagged in the last {days} days.")
        return
    out(f"Senders flagged in the last {days} days (level, name, domain, emails):")
    for (level, name, domain), n in sorted(found.items(), key=lambda kv: (kv[0][0], -kv[1])):
        out(f"  {level:<9} {name[:40]:<40} {domain:<35} {n}")
    out("Genuine ones can go under 'trusted:' in config/sender_checks.yaml (a domain or a whole address).")


def main():
    """  python -m services.common.senders [days]   (runs in the sync-worker container)"""
    import sys

    from services.common.containers import delegate_to_container

    delegate_to_container("sync-worker", "services.common.senders")
    from services.common import settings
    from services.ingestion.store import Store

    report(Store(settings.STRUCTURED_DB, readonly=True), int(sys.argv[1]) if len(sys.argv) > 1 else 30)


if __name__ == "__main__":
    main()
