"""Read the transcript exposed by a Tencent recording share; stdlib only.

No browser, cookies, model, or credential discovery. Does not download video.
Frontend endpoints are undocumented and may change. Keep raw responses.
"""
import argparse
import base64
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path


def get(url, referer=None):
    headers = {"User-Agent": "Mozilla/5.0"}
    if referer:
        headers["Referer"] = referer
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=45) as r:
        return r.read()


def parse_share(html):
    # Decode JSON literals in the Next.js stream; never execute remote JavaScript.
    for raw in re.findall(r'self\.__next_f\.push\((\[1,.*?\])\)</script>', html):
        try:
            fragment = json.loads(raw)[1]
            _, payload = fragment.split(":", 1)
            obj = json.loads(payload)
            if isinstance(obj, list) and len(obj) > 3 and isinstance(obj[3], dict):
                data = obj[3].get("data", {})
                if "serverData" in data and "query" in data:
                    return data
        except (ValueError, TypeError, IndexError):
            continue
    raise ValueError("Share metadata not found; frontend changed or access is required")


def stamp(seconds):
    seconds = int(seconds)
    return "%02d:%02d:%02d" % (seconds // 3600, seconds // 60 % 60, seconds % 60)


def write_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(source, output, offline=False):
    parsed = urllib.parse.urlparse(source)
    if parsed.scheme != "https" or parsed.hostname != "meeting.tencent.com":
        raise ValueError("Expected an HTTPS meeting.tencent.com share URL")
    output.mkdir(parents=True, exist_ok=True)
    page = output / "page.html"
    if not offline:
        page.write_bytes(get(source))
    data = parse_share(page.read_text("utf-8"))
    server = data["serverData"]
    if server.get("code") != 0:
        raise ValueError("Share access failed: code=%s" % server.get("code"))
    recordings = server.get("recordings", [])
    if len(recordings) != 1:
        raise ValueError("Expected one recording; choose and handle each recording explicitly")
    rec = recordings[0]
    info = server.get("meeting_info", {})
    query = urllib.parse.parse_qs(data.get("fullLongUrlSearch", "").lstrip("?"))
    share_id = query.get("id", [None])[0]
    if not share_id:
        raise ValueError("Missing share id")
    params = {
        "meeting_id": info["meeting_id"], "recording_id": rec["id"],
        "share_id": share_id, "id": share_id,
        "is_single": query.get("is-single", ["false"])[0],
        "is_meetlog": "false", "short_url_code": data.get("shortLink", ""),
        "lang": "zh", "pwd": "", "return_ori": "0",
    }
    raw_path = output / "minutes-response.json"
    if not offline:
        url = "https://meeting.tencent.com/wemeet-cloudrecording-webapi/v1/minutes/detail?"
        raw_path.write_bytes(get(url + urllib.parse.urlencode(params), source))
    response = json.loads(raw_path.read_text("utf-8"))
    if response.get("code") != 0:
        raise ValueError("Transcript access failed: code=%s" % response.get("code"))
    minutes = response.get("minutes", {})
    paragraphs = minutes.get("paragraphs", [])
    # Do not silently present an initial page, trial, or processing result as complete.
    complete = (response.get("more") is False and bool(paragraphs)
                and not minutes.get("is_awaiting_ready"))
    rows = []
    for p in paragraphs:
        speaker = p.get("speaker", {}).get("user_name") or "未标注"
        for sentence in p.get("sentences", []):
            start, end = sentence.get("start_time"), sentence.get("end_time")
            if start is None or end is None or end < start:
                raise ValueError("Invalid sentence timing at pid=%s" % p.get("pid"))
            text = "".join(w.get("text", "") for w in sentence.get("words", []))
            rows.append({
                "id": "p%s-s%s" % (p["pid"], sentence["sid"]),
                "paragraph_id": p["pid"], "start_s": start / 1000,
                "end_s": end / 1000, "speaker": speaker,
                "speaker_verified": False, "text": text,
                "source": "platform", "timing_status": "source",
            })
    if len({r["id"] for r in rows}) != len(rows):
        raise ValueError("Duplicate sentence ids")
    title = base64.b64decode(info.get("subject", "")).decode("utf-8") or "组会"
    manifest = {
        "source": source, "title": title, "recording_id": rec["id"],
        "duration_s": int(rec["duration"]) / 1000,
        "allow_download": server.get("allow_download"),
        "minutes_exportable": server.get("minutes_exportable"),
        "complete_as_returned": complete, "more": response.get("more"),
        "trial_duration_field": minutes.get("trial_duration"),
        "paragraphs": len(paragraphs), "sentences": len(rows),
        "first_s": min((r["start_s"] for r in rows), default=None),
        "last_s": max((r["end_s"] for r in rows), default=None),
        "note": "No more API pages is not proof every audible word was transcribed. Speaker labels are platform labels, not verified identities.",
    }
    with (output / "transcript.jsonl").open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    lines = ["# " + title + "：平台逐字稿", "", "自动识别原文；术语及发言归属未经人工核验。", ""]
    for row in rows:
        lines.append("[%s–%s] **%s**：%s\n" % (
            stamp(row["start_s"]), stamp(row["end_s"]), row["speaker"], row["text"]))
    (output / "transcript.md").write_text("\n".join(lines), encoding="utf-8")
    write_json(output / "manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    if not complete:
        raise SystemExit("Saved a partial/non-final response; do not treat as a complete transcript")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--offline", action="store_true", help="Normalize previously saved HTML and JSON without network")
    args = parser.parse_args()
    run(args.source, args.out, args.offline)
