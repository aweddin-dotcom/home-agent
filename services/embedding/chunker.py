"""Split emails into overlapping chunks sized for the embedding model."""


def chunk_text(text, size, overlap):
    """Split text into chunks of at most `size` characters, preferring to break
    at paragraphs, then lines, then spaces. Consecutive chunks share `overlap`
    characters so a sentence cut at a boundary still appears whole in one."""
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []
    chunks, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            window = text[start:end]
            for separator in ("\n\n", "\n", " "):
                cut = window.rfind(separator)
                if cut > size // 2:
                    end = start + cut
                    break
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [c for c in chunks if c]


def email_chunks(email, size, overlap):
    """Chunks of an email's body, each prefixed with its subject, sender, and
    date so every chunk can be found and understood on its own."""
    header = f"Subject: {email.subject}\nFrom: {email.sender}\nDate: {email.date[:10]}\n\n"
    body = email.body or email.snippet or "(no text)"
    return [header + chunk for chunk in chunk_text(body, size, overlap)]
