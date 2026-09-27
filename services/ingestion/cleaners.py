"""Turn email bodies into clean text: no HTML, quoted replies, or signatures.

Forwarded messages are kept: their content is usually the point of the email.
"""

import re

from bs4 import BeautifulSoup

# "On Mon, Jan 5, 2026 at 9:00 AM Dana <dana@example.com> wrote:", which
# mail clients often wrap onto a second line.
REPLY_HEADER = re.compile(r"^On\b[^\n]*(?:\n[^\n]*)?\bwrote:[ \t]*$", re.MULTILINE)
ORIGINAL_MESSAGE = re.compile(r"^-{2,}\s*Original Message\s*-{2,}\s*$", re.MULTILINE | re.IGNORECASE)
SIGNATURE_DELIMITER = re.compile(r"^-- ?$", re.MULTILINE)
SENT_FROM = re.compile(r"^Sent from my \w+.*$", re.MULTILINE | re.IGNORECASE)


def html_to_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "head"]):
        tag.decompose()
    # Quoted earlier messages in HTML replies.
    for tag in soup.select("blockquote, div.gmail_quote"):
        tag.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")
    return soup.get_text("\n")


def clean_body(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    for pattern in (REPLY_HEADER, ORIGINAL_MESSAGE, SIGNATURE_DELIMITER):
        match = pattern.search(text)
        if match:
            text = text[: match.start()]
    text = SENT_FROM.sub("", text)
    lines = [line.rstrip() for line in text.split("\n") if not line.lstrip().startswith(">")]
    text = "\n".join(lines)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
