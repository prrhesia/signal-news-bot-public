from pathlib import Path


VERSION = (Path(__file__).resolve().parent.parent / "VERSION").read_text().strip()
RELEASES = {
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
