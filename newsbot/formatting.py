"""Signal-native formatting; offsets are UTF-16 units, not Python code points."""

# signal-cli 0.14.x turns excess over this size into a long-text attachment.
SIGNAL_INLINE_BYTES = 2000
EXPAND_EMOJI = "🔎"


def reaction_emoji(value):
    return "".join(ch for ch in value if ch not in "\ufe0e\ufe0f" and not 0x1f3fb <= ord(ch) <= 0x1f3ff)


def utf16_units(text):
    return len(text.encode("utf-16-le", errors="surrogatepass")) // 2


def duration_label(seconds):
    """Exact seconds with a compact equivalent when there is a larger unit."""
    left = max(0, int(seconds))
    original = f"{left:,}s"
    if left < 60:
        return original
    parts = []
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        n, left = divmod(left, size)
        if n:
            parts.append(f"{n}{unit}")
    return f"{original} ({' '.join(parts)})"


class SignalText(str):
    def __new__(cls, text, styles=()):
        value = super().__new__(cls, text)
        value.styles = tuple(styles)
        return value


class Message:
    def __init__(self):
        self.parts = []
        self.styles = []
        self.units = 0

    def add(self, text, style=None):
        text = str(text)
        size = utf16_units(text)
        if style and size:
            if style not in {"BOLD", "ITALIC", "MONOSPACE"}:
                raise ValueError("Unsupported Signal style")
            self.styles.append(f"{self.units}:{size}:{style}")
        self.parts.append(text)
        self.units += size
        return self

    def line(self, text="", style=None):
        return self.add(text, style).add("\n")

    def build(self):
        text = "".join(self.parts).rstrip("\n")
        units = utf16_units(text)
        styles = []
        for span in self.styles:
            start, size, style = span.split(":")
            size = min(int(size), units - int(start))
            if size > 0:
                styles.append(f"{start}:{size}:{style}")
        return SignalText(text, styles)


def insert_styled(message, offset, text, style=None):
    """Insert at a character boundary, maintaining Signal's UTF-16 ranges."""
    start = utf16_units(message[:offset])
    width = utf16_units(text)
    spans = []
    for span in message.styles:
        pos, size, kind = span.split(":")
        pos, size = int(pos), int(size)
        if pos >= start:
            pos += width
        elif pos + size > start:
            size += width
        spans.append(f"{pos}:{size}:{kind}")
    if style:
        spans.append(f"{start}:{width}:{style}")
    spans.sort(key=lambda span: int(span.partition(":")[0]))
    return SignalText(message[:offset] + text + message[offset:], spans)


def article_age(published, now):
    if not published or published > now:
        return "Publication time unknown"
    seconds = int(now - published)
    if seconds < 60:
        return "Published less than 1 min ago"
    if seconds < 3600:
        return f"Published {seconds // 60} min ago"
    if seconds < 86400:
        return f"Published {seconds // 3600} hr ago"
    return f"Published {seconds // 86400} days ago"


def notice(title, note=""):
    message = Message().line(title, "BOLD")
    if note:
        message.line(note, "ITALIC")
    return message.build()


def toggle(value):
    return "✅ On" if value else "❌ Off"
