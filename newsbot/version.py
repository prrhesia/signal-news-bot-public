from pathlib import Path


VERSION = (Path(__file__).resolve().parent.parent / "VERSION").read_text().strip()
RELEASES = {
    "1.1.0": ("Continuity", (
        "🎯 Compare clear rewrites against several days of coverage",
        "🔕 Check active story mutes before asking the editor",
        "🔎 Trace why an expansion fell back to web search or stopped",
        "📰 Keep the rolling post count useful when Signal returns mixed receipts",
        "🎬 Improve feed video discovery without borrowing media from other entries",
    )),
    "1.0.9": ("Lens", (
        "🎯 Find earlier coverage behind similar headlines",
        "↩️ Skip clear rewrites while retaining corrections and recoveries",
        "🔕 Apply story mutes without hiding a changed outcome",
        "🧾 Inspect which prior posts were supplied to the editor",
        "🎬 Separate media selected for sending from confirmed delivery",
    )),
    "1.0.8": ("Cadence", (
        "🗳️ Keep editable targets after partial group delivery",
        "🧹 Schedule cleanup for partially delivered bot replies",
        "🔕 Recognize repeat verdict and breach coverage",
        "🎬 Read galleries and clips from rich feed content",
        "⚡ Reuse recent story tokens and skip needless HTML parsing",
    )),
    "1.0.7": ("Relay", (
        "🎬 Optional feed and Nitter MP4 videos",
        "🖼️ More same-entry galleries and media diagnostics",
        "🔎 Optional cited web details when the feed has no more",
        "🎯 Stronger repeat checks and safe links on instant posts",
        "💬 Trusted-group direct-message test",
    )),
    "1.0.6": ("Clarity", (
        "📊 Usage bars in one reply",
        "🗳️ Score adjustments without a vote count",
        "🔎 Quoted details from the original feed",
        "🎯 Fewer repeat stories and duplicate pictures",
        "🤖 More opt-in free model choices",
    )),
    "1.0.5": ("Sift", (
        "🎯 Stricter relevance and fewer repeats",
        "🚨 Emergency and per-feed instant posts",
        "🗳️ 45-minute votes and reversible story mutes",
        "🖼️ Up to four photos from the right entry",
        "🧾 Clearer recaps, footers and replies",
    )),
}
