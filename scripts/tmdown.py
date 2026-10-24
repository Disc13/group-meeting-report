#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tmdown.py —— 给一个腾讯会议录制分享链接（或任意网页/HLS 链接），直接下载成可播放文件。

腾讯会议录制链接（https://meeting.tencent.com/crm/xxxx、/cw/xxxx、/crw/xxxx）
走专用解析链：
    页面 RSC 数据 -> record_id / sharing_id -> get-multi-record-file
    -> sign-multi-record-file（返回带签名的 MP4 地址）-> 分块并发下载
其它网站走通用链：静态抓 m3u8/mp4 -> 找不到再用浏览器嗅探 -> HLS 分片下载（支持 AES-128）。

用法
----
    python tmdown.py "https://meeting.tencent.com/crm/DemoShareId000"
    python tmdown.py "分享链接" --type screen          # 只下共享屏幕
    python tmdown.py "分享链接" --all --out ./downloads  # 全部轨道（录制/屏幕/发言人/音频）
    python tmdown.py "分享链接" --list                 # 只看有哪些轨道和大小
    python tmdown.py --media-url "https://x/y.m3u8" --name 小组会
    python tmdown.py --selftest

退出码：0 成功；2 参数错误；3 没找到媒体；4 鉴权失败；5 下载失败
"""

import argparse
import base64
import json
import os
import random
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import http.cookiejar
import http.client
from concurrent.futures import ThreadPoolExecutor, as_completed

VERSION = "2.1.0"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

TM_BASE = "https://meeting.tencent.com"
TM_API = TM_BASE + "/wemeet-tapi/v2/meetlog/public/record-detail/"

# 腾讯会议录制轨道的 resource_type
TRACK_LABEL = {
    0: ("video", "录制"),
    1: ("screen", "共享屏幕"),
    2: ("speaker", "发言人"),
    3: ("audio", "音频"),
}

MEDIA_EXT = ("m3u8", "mp4", "flv", "m4a", "ts")
RE_MEDIA = re.compile(
    r'https?://[^\s"\'<>\\)\]}]+?\.(?:' + "|".join(MEDIA_EXT) +
    r')(?:\?(?:[^\s"\'<>\\)\]}]*))?', re.I)


# ==========================================================================
# 通用 HTTP
# ==========================================================================

class AuthError(Exception):
    pass


class MediaError(Exception):
    pass


def normalize_cookie(value):
    """接受 'a=b; c=d'、cookies.txt 路径、Playwright storage_state.json 路径。"""
    if not value:
        return ""
    if os.path.isfile(value):
        with open(value, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        try:
            state = json.loads(text)
        except ValueError:
            state = None
        if isinstance(state, dict) and isinstance(state.get("cookies"), list):
            return "; ".join("%s=%s" % (c["name"], c["value"])
                             for c in state["cookies"]
                             if c.get("name") and c.get("value") is not None)
        if state is None:
            parts = []
            for line in text.splitlines():
                if not line.strip() or line.startswith("#"):
                    continue
                f = line.split("\t")
                if len(f) >= 7:
                    parts.append("%s=%s" % (f[5], f[6]))
            return "; ".join(parts)
        return ""
    return value.strip()


class Net(object):
    def __init__(self, cookie="", referer="", proxy="", insecure=False):
        self.cookie = normalize_cookie(cookie)
        self.referer = referer
        self.jar = http.cookiejar.CookieJar()
        handlers = [urllib.request.HTTPCookieProcessor(self.jar)]
        if proxy:
            handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        ctx = ssl.create_default_context()
        if insecure:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
        self.opener = urllib.request.build_opener(*handlers)

    def headers(self, extra=None):
        h = {
            "User-Agent": UA,
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "identity",
        }
        if self.referer:
            h["Referer"] = self.referer
        if self.cookie:
            h["Cookie"] = self.cookie
        if extra:
            h.update(extra)
        return h

    def fetch(self, url, headers=None, data=None, method=None, timeout=40, retries=4):
        last = None
        for attempt in range(retries):
            req = urllib.request.Request(url, data=data, method=method,
                                         headers=self.headers(headers))
            try:
                with self.opener.open(req, timeout=timeout) as resp:
                    return getattr(resp, "status", 200) or 200, dict(resp.headers), resp.read()
            except urllib.error.HTTPError as exc:
                body = b""
                try:
                    body = exc.read()[:400]
                except Exception:  # noqa: BLE001
                    pass
                if exc.code in (401, 403):
                    raise AuthError("%s -> HTTP %s" % (url, exc.code))
                if exc.code in (408, 425, 429, 500, 502, 503, 504) and attempt < retries - 1:
                    last = exc
                    time.sleep(0.8 * (2 ** attempt) + random.random() * 0.3)
                    continue
                raise MediaError("%s -> HTTP %s %s" % (url, exc.code,
                                                       body[:160].decode("utf-8", "replace")))
            except (urllib.error.URLError, socket.timeout, ConnectionResetError) as exc:
                last = exc
                if attempt < retries - 1:
                    time.sleep(0.8 * (2 ** attempt) + random.random() * 0.3)
                    continue
                raise MediaError("%s -> %s" % (url, exc))
        raise MediaError("%s -> %s" % (url, last))

    def text(self, url, headers=None, timeout=40):
        _, hdrs, body = self.fetch(url, headers, timeout=timeout)
        return body.decode("utf-8", "replace")

    def json(self, url, headers=None, timeout=40):
        txt = self.text(url, headers, timeout=timeout)
        try:
            return json.loads(txt)
        except ValueError:
            raise MediaError("接口返回的不是 JSON: %r" % txt[:200])

    def bytes(self, url, headers=None, timeout=60, retries=4):
        return self.fetch(url, headers, timeout=timeout, retries=retries)[2]


# ==========================================================================
# 腾讯会议专用解析
# ==========================================================================

def tencent_short_link(url):
    """从各种形态的分享链接里取出短链 id。"""
    if not url:
        return None
    m = re.search(r"/(?:crm|cw|crw|cr|ctw|ct|cm)/([A-Za-z0-9]{6,32})", url)
    if m:
        return m.group(1)
    m = re.search(r"[?&]short_link=([A-Za-z0-9]{6,32})", url)
    if m:
        return m.group(1)
    return None


def is_tencent_meeting(url):
    parsed = urllib.parse.urlparse(url or "")
    return parsed.hostname == "meeting.tencent.com" and bool(tencent_short_link(url))


def _balanced_json(text, start):
    depth, i, in_str, esc = 0, start, False, False
    while i < len(text):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        i += 1
    return None


def parse_server_data(html):
    """从 Next.js RSC 流里挖出 serverData（含 recordings/sharing_id/subject）。"""
    for candidate in (html.replace('\\"', '"'), html):
        idx = candidate.find('"serverData":')
        if idx < 0:
            continue
        start = candidate.find("{", idx)
        blob = _balanced_json(candidate, start)
        if not blob:
            continue
        try:
            return json.loads(blob)
        except ValueError:
            continue
    # 兜底：正则抠关键字段
    m_id = re.search(r'"pk_meeting_info_id"\s*:\s*"?(\d{10,})', html)
    m_sub = re.search(r'"subject"\s*:\s*"([A-Za-z0-9+/=]{4,})"', html)
    m_share = re.search(r'"sharing_id"\s*:\s*"([0-9a-f\-]{20,})"', html)
    if m_id and m_share:
        return {"pk_meeting_info_id": m_id.group(1),
                "subject": m_sub.group(1) if m_sub else "",
                "recordings": [{"id": m_id.group(1), "sharing_id": m_share.group(1)}]}
    return None


def decode_subject(value):
    if not value:
        return ""
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4))
        return raw.decode("utf-8")
    except Exception:  # noqa: BLE001
        return ""


def tm_api_get(net, path, params):
    url = TM_API + path + "?" + urllib.parse.urlencode(params)
    data = net.json(url, headers={"Accept": "application/json, text/plain, */*"})
    if data.get("code") not in (0, "0"):
        raise MediaError("接口 %s 失败: code=%s %s" % (
            path.split("/")[-1], data.get("code"),
            data.get("err_detail") or data.get("msg") or ""))
    return data.get("data") or {}


def resolve_tencent(net, url, pwd="", want=None):
    """返回 (标题, [ {key,label,url,size,ext} ... ])。"""
    short = tencent_short_link(url)
    page_url = TM_BASE + "/cw/" + short
    net.referer = page_url
    status, _, body = net.fetch(page_url, timeout=45)
    html = body.decode("utf-8", "replace")
    data = parse_server_data(html)
    if not data:
        raise MediaError("页面里没有解析到录制信息（可能需要登录，或链接已失效）")

    subject = decode_subject(data.get("subject"))
    share_pwd = pwd or data.get("sharing_pwd") or data.get("pwd") or ""
    recordings = data.get("recordings") or []
    if not recordings:
        raise MediaError("该分享下没有录制文件（可能还在转码）")

    tracks = []
    for rec in recordings:
        rid = rec.get("id") or data.get("pk_meeting_info_id")
        sid = rec.get("sharing_id") or data.get("sharing_id") or ""
        if not rid:
            continue
        files = tm_api_get(net, "get-multi-record-file",
                           {"record_id": rid, "auth_share_id": sid, "pwd": share_pwd})
        for f in files.get("files") or []:
            rtype = f.get("resource_type")
            key, label = TRACK_LABEL.get(rtype, ("type%s" % rtype, "其它"))
            if want and key not in want:
                continue
            size = int(f.get("size") or 0)
            if size == 0:
                continue
            try:
                sign = tm_api_get(net, "sign-multi-record-file", {
                    "record_id": rid,
                    "resource_id": f.get("resource_id") or rid,
                    "resource_type": rtype,
                    "auth_share_id": sid,
                    "pwd": share_pwd,
                })
            except MediaError as exc:
                sys.stderr.write("  跳过 %s 轨道: %s\n" % (label, exc))
                continue
            media_url = sign.get("url") or ""
            if not media_url:
                continue
            ext = os.path.splitext(urllib.parse.urlparse(media_url).path)[1] or ".mp4"
            tracks.append({"key": key, "label": label, "url": media_url,
                           "size": size, "ext": ext,
                           "record_id": rid, "sharing_id": sid,
                           "pwd": share_pwd,
                           "resource_type": rtype, "resource_id": f.get("resource_id")})
    if not tracks:
        raise MediaError("没有拿到可下载的轨道（可能只允许在线观看）")
    return subject, tracks


def resign_tencent(net, track):
    """签名过期时重新签名。"""
    sign = tm_api_get(net, "sign-multi-record-file", {
        "record_id": track["record_id"],
        "resource_id": track.get("resource_id") or track["record_id"],
        "resource_type": track["resource_type"],
        "auth_share_id": track["sharing_id"],
        "pwd": track.get("pwd", ""),
    })
    if not sign.get("url"):
        raise MediaError("重新签名未返回媒体地址")
    return sign["url"]


# ==========================================================================
# 通用解析（静态 + 浏览器嗅探 + HLS）
# ==========================================================================

def extract_media_urls(text):
    if not text:
        return []
    text = (text.replace("\\u002F", "/").replace("\\u002f", "/")
                .replace("\\/", "/").replace("&amp;", "&"))
    urls, seen = [], set()
    for m in RE_MEDIA.finditer(text):
        u = m.group(0).rstrip("',;")
        if u not in seen:
            seen.add(u)
            urls.append(u)

    def rank(u):
        low = u.lower()
        score = 0
        if ".m3u8" in low:
            score -= 30
        if "preview" in low or "thumb" in low or "cover" in low or "poster" in low:
            score += 40
        return score

    return sorted(urls, key=rank)


def parse_attrs(text):
    out, cur, quote, parts = {}, "", False, []
    for ch in text:
        if ch == '"':
            quote = not quote
            cur += ch
            continue
        if ch == "," and not quote:
            parts.append(cur)
            cur = ""
            continue
        cur += ch
    parts.append(cur)
    for p in parts:
        if "=" in p:
            k, v = p.split("=", 1)
            out[k.strip()] = v.strip().strip('"')
    return out


def parse_playlist(text, base_url):
    variants, segments, media_seq, cur_key = [], [], 0, None
    init_uri = None
    pending = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-STREAM-INF"):
            pending = parse_attrs(line.split(":", 1)[1])
        elif line.startswith("#EXT-X-MEDIA-SEQUENCE"):
            try:
                media_seq = int(line.split(":", 1)[1].strip())
            except ValueError:
                media_seq = 0
        elif line.startswith("#EXT-X-KEY"):
            attrs = parse_attrs(line.split(":", 1)[1])
            if (attrs.get("METHOD") or "NONE").upper() == "NONE" or not attrs.get("URI"):
                cur_key = None
            else:
                cur_key = {"method": attrs["METHOD"].upper(),
                           "uri": urllib.parse.urljoin(base_url, attrs["URI"]),
                           "iv": attrs.get("IV"),
                           "keyformat": attrs.get("KEYFORMAT", "identity")}
        elif line.startswith("#EXT-X-MAP"):
            attrs = parse_attrs(line.split(":", 1)[1])
            if attrs.get("URI"):
                init_uri = urllib.parse.urljoin(base_url, attrs["URI"])
        elif line.startswith("#"):
            continue
        else:
            if pending is not None:
                variants.append({"url": urllib.parse.urljoin(base_url, line),
                                 "bandwidth": int(pending.get("BANDWIDTH") or 0),
                                 "resolution": pending.get("RESOLUTION", "")})
                pending = None
            else:
                segments.append({"url": urllib.parse.urljoin(base_url, line),
                                 "key": cur_key, "seq": media_seq + len(segments)})
    return {"is_master": bool(variants) and not segments, "variants": variants,
            "segments": segments, "init": init_uri, "endlist": "#EXT-X-ENDLIST" in text}


def select_variant(variants, quality=None):
    if not variants:
        return None
    ordered = sorted(variants, key=lambda v: v["bandwidth"], reverse=True)
    if quality in (None, "", "best", "max"):
        return ordered[0]
    if quality in ("worst", "min"):
        return ordered[-1]
    m = re.match(r"^(\d{3,4})[pP]?$", str(quality))
    if m:
        target = int(m.group(1))
        scored = []
        for v in ordered:
            h = 0
            res = v.get("resolution") or ""
            if "x" in res:
                try:
                    h = int(res.split("x")[1])
                except (ValueError, IndexError):
                    h = 0
            scored.append((abs(h - target) if h else 10 ** 6, v))
        scored.sort(key=lambda t: t[0])
        return scored[0][1]
    try:
        idx = int(quality)
    except (TypeError, ValueError):
        return ordered[0]
    return ordered[max(0, min(idx, len(ordered) - 1))]


# ==========================================================================
# AES-128-CBC（HLS 分片解密，纯标准库）
# ==========================================================================

def _gmul(a, b):
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        hi = a & 0x80
        a = (a << 1) & 0xFF
        if hi:
            a ^= 0x1B
        b >>= 1
    return p


def _gpow(a, n):
    r = 1
    while n:
        if n & 1:
            r = _gmul(r, a)
        a = _gmul(a, a)
        n >>= 1
    return r


def _rotl8(x, n):
    return ((x << n) | (x >> (8 - n))) & 0xFF


def _build_sbox():
    sbox = [0] * 256
    for i in range(256):
        inv = _gpow(i, 254) if i else 0
        sbox[i] = (inv ^ _rotl8(inv, 1) ^ _rotl8(inv, 2) ^ _rotl8(inv, 3)
                   ^ _rotl8(inv, 4) ^ 0x63) & 0xFF
    inv_sbox = [0] * 256
    for i, v in enumerate(sbox):
        inv_sbox[v] = i
    return sbox, inv_sbox


SBOX, INV_SBOX = _build_sbox()


def _expand_key(key):
    nk = len(key) // 4
    nr = nk + 6
    w = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
    rcon = 1
    for i in range(nk, 4 * (nr + 1)):
        temp = list(w[i - 1])
        if i % nk == 0:
            temp = temp[1:] + temp[:1]
            temp = [SBOX[x] for x in temp]
            temp[0] ^= rcon
            rcon = _gmul(rcon, 2)
        elif nk > 6 and i % nk == 4:
            temp = [SBOX[x] for x in temp]
        w.append([w[i - nk][j] ^ temp[j] for j in range(4)])
    return w, nr


def _ark(s, words):
    for c in range(4):
        for r in range(4):
            s[4 * c + r] ^= words[c][r]


def _inv_shift_rows(s):
    s[1], s[5], s[9], s[13] = s[13], s[1], s[5], s[9]
    s[2], s[6], s[10], s[14] = s[10], s[14], s[2], s[6]
    s[3], s[7], s[11], s[15] = s[7], s[11], s[15], s[3]


def _inv_mix_columns(s):
    for c in range(4):
        i = 4 * c
        a0, a1, a2, a3 = s[i], s[i + 1], s[i + 2], s[i + 3]
        s[i] = _gmul(a0, 14) ^ _gmul(a1, 11) ^ _gmul(a2, 13) ^ _gmul(a3, 9)
        s[i + 1] = _gmul(a0, 9) ^ _gmul(a1, 14) ^ _gmul(a2, 11) ^ _gmul(a3, 13)
        s[i + 2] = _gmul(a0, 13) ^ _gmul(a1, 9) ^ _gmul(a2, 14) ^ _gmul(a3, 11)
        s[i + 3] = _gmul(a0, 11) ^ _gmul(a1, 13) ^ _gmul(a2, 9) ^ _gmul(a3, 14)


def _decrypt_block(block, w, nr):
    s = list(block)
    _ark(s, w[4 * nr:4 * nr + 4])
    for rnd in range(nr - 1, 0, -1):
        _inv_shift_rows(s)
        s = [INV_SBOX[x] for x in s]
        _ark(s, w[4 * rnd:4 * rnd + 4])
        _inv_mix_columns(s)
    _inv_shift_rows(s)
    s = [INV_SBOX[x] for x in s]
    _ark(s, w[0:4])
    return bytes(s)


def aes_cbc_decrypt(key, iv, data):
    w, nr = _expand_key(key)
    prev, out = iv, bytearray()
    usable = len(data) - (len(data) % 16)
    for i in range(0, usable, 16):
        blk = data[i:i + 16]
        dec = _decrypt_block(blk, w, nr)
        out += bytes(a ^ b for a, b in zip(dec, prev))
        prev = blk
    return bytes(out)


def pkcs7_unpad(data):
    if not data:
        return data
    n = data[-1]
    if 1 <= n <= 16 and data[-n:] == bytes([n]) * n:
        return data[:-n]
    return data


def _iv_from(key_info, seq):
    iv = (key_info or {}).get("iv")
    if iv:
        iv = iv[2:] if iv.lower().startswith("0x") else iv
        try:
            return bytes.fromhex(iv.rjust(32, "0"))[:16]
        except ValueError:
            pass
    return seq.to_bytes(16, "big")


# ==========================================================================
# 下载器
# ==========================================================================

class Progress(object):
    def __init__(self, total, label="进度", total_bytes=0):
        self.total = max(1, total)
        self.total_bytes = total_bytes
        self.done = 0
        self.bytes = 0
        self.t0 = time.time()
        self.label = label
        self.lock = threading.Lock()

    def add(self, n):
        with self.lock:
            self.done += 1
            self.bytes += n
            if self.done % 3 == 0 or self.done >= self.total:
                elapsed = max(0.001, time.time() - self.t0)
                rate = self.bytes / elapsed / 1048576.0
                pct = ""
                if self.total_bytes:
                    pct = " %5.1f%%" % (100.0 * self.bytes / self.total_bytes)
                sys.stderr.write("\r  %s %d/%d%s  %.1f MB  %.2f MB/s   " %
                                 (self.label, self.done, self.total, pct,
                                  self.bytes / 1048576.0, rate))
                sys.stderr.flush()

    def end(self):
        sys.stderr.write("\n")
        sys.stderr.flush()


def probe_size(net, url):
    from download_core import probe_media, TransferError, ExpiredURL
    try:
        info = probe_media(net, url)
        return info["size"], info["ranged"]
    except ExpiredURL as exc:
        raise AuthError(str(exc)) from None
    except TransferError as exc:
        raise MediaError(str(exc)) from None


def download_direct(net, url, out_path, threads=8, size_hint=0,
                    refresh=None, source_id=None):
    from download_core import download, TransferError, ExpiredURL
    try:
        return download(net, url, out_path, threads, size_hint, refresh,
                        source_id, Progress)
    except ExpiredURL as exc:
        raise AuthError(str(exc)) from None
    except (TransferError, OSError, urllib.error.URLError,
            http.client.HTTPException) as exc:
        raise MediaError(str(exc)) from None


def download_hls(net, playlist_url, out_base, threads=8, quality=None,
                 remux=True, keep_parts=False):
    text = net.text(playlist_url)
    if "#EXTM3U" not in text[:4000]:
        raise MediaError("不是有效的 m3u8: %r" % text[:160])
    pl = parse_playlist(text, playlist_url)
    if pl["is_master"]:
        v = select_variant(pl["variants"], quality)
        if not v:
            raise MediaError("master 列表没有可用码率")
        sys.stderr.write("  码率: %s %s\n" % (v["bandwidth"], v.get("resolution") or ""))
        playlist_url = v["url"]
        pl = parse_playlist(net.text(playlist_url), playlist_url)

    segs = pl["segments"]
    if not segs:
        raise MediaError("播放列表没有分片")

    seg_dir = out_base + ".parts"
    shutil.rmtree(seg_dir, ignore_errors=True)
    os.makedirs(seg_dir)
    keys, key_lock = {}, threading.Lock()

    def get_key(uri):
        with key_lock:
            if uri in keys:
                return keys[uri]
        raw = net.bytes(uri)
        if len(raw) not in (16, 24, 32):
            raise MediaError("AES key 长度异常: %d" % len(raw))
        with key_lock:
            keys[uri] = raw
        return raw

    prog = Progress(len(segs), "分片")

    def grab(item):
        idx, seg = item
        data = net.bytes(seg["url"], timeout=120, retries=5)
        key = seg.get("key")
        if key and key["method"] == "AES-128":
            k = get_key(key["uri"])
            data = pkcs7_unpad(aes_cbc_decrypt(k, _iv_from(key, seg["seq"]), data))
        with open(os.path.join(seg_dir, "%06d.bin" % idx), "wb") as fh:
            fh.write(data)
        prog.add(len(data))

    sys.stderr.write("  分片: %d 并发: %d\n" % (len(segs), threads))
    with ThreadPoolExecutor(max_workers=max(1, threads)) as pool:
        for fut in as_completed([pool.submit(grab, (i, s)) for i, s in enumerate(segs)]):
            fut.result()
    prog.end()

    ext = ".mp4" if pl["init"] else ".ts"
    final = out_base if out_base.lower().endswith(ext) else out_base + ext
    tmp = final + ".tmp"
    with open(tmp, "wb") as out:
        if pl["init"]:
            out.write(net.bytes(pl["init"]))
        for i in range(len(segs)):
            with open(os.path.join(seg_dir, "%06d.bin" % i), "rb") as src:
                shutil.copyfileobj(src, out, 1048576)
    os.replace(tmp, final)
    if not keep_parts:
        shutil.rmtree(seg_dir, ignore_errors=True)
    if final.endswith(".ts") and remux:
        mp4 = remux_to_mp4(final)
        if mp4:
            return mp4
        sys.stderr.write("  没找到 ffmpeg，保留 .ts（VLC/PotPlayer 可直接播放）\n")
    return final


def find_ffmpeg():
    p = shutil.which("ffmpeg")
    if p:
        return p
    here = os.path.dirname(os.path.abspath(__file__))
    for rel in ("ffmpeg.exe", os.path.join("bin", "ffmpeg.exe")):
        cand = os.path.join(here, rel)
        if os.path.isfile(cand):
            return cand
    return None


def remux_to_mp4(ts_path):
    ff = find_ffmpeg()
    if not ff:
        return None
    mp4 = os.path.splitext(ts_path)[0] + ".mp4"
    try:
        subprocess.run([ff, "-y", "-loglevel", "error", "-i", ts_path,
                        "-c", "copy", "-bsf:a", "aac_adtstoasc",
                        "-movflags", "+faststart", mp4],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except (OSError, subprocess.CalledProcessError) as exc:
        sys.stderr.write("  ffmpeg 转封装失败: %s\n" % exc)
        return None
    if os.path.isfile(mp4) and os.path.getsize(mp4) > 4096:
        try:
            os.remove(ts_path)
        except OSError:
            pass
        return mp4
    return None


# ==========================================================================
# 浏览器嗅探（可选）
# ==========================================================================

def sniff_with_playwright(url, state_path=None, headless=True, wait_seconds=8,
                          timeout_ms=45000, allow_login=False):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise MediaError(
            "页面里没有直接暴露视频地址，需要浏览器嗅探，但没装 playwright。\n"
            "    pip install playwright && python -m playwright install chromium\n"
            "或者用 F12 网络面板找到 .m3u8/.mp4，用 --media-url 传进来。")

    found, seen = [], set()

    def note(u):
        low = u.lower()
        if any(e in low for e in (".m3u8", ".mp4", ".flv")):
            if u not in seen:
                seen.add(u)
                found.append(u)

    html = ""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=(headless and not allow_login))
        ctx = browser.new_context(user_agent=UA, locale="zh-CN",
                                 viewport={"width": 1440, "height": 900})
        if state_path and os.path.isfile(state_path):
            try:
                with open(state_path, "r", encoding="utf-8") as fh:
                    ctx.add_cookies(json.load(fh).get("cookies", []))
            except (ValueError, OSError):
                pass
        page = ctx.new_page()
        page.on("request", lambda req: note(req.url))
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        if allow_login:
            sys.stderr.write("\n  浏览器已打开：登录并点到能播放的页面，回来按回车……\n")
            try:
                input()
            except EOFError:
                time.sleep(wait_seconds)
        else:
            time.sleep(wait_seconds)
        try:
            page.evaluate("() => document.querySelectorAll('video')"
                          ".forEach(v => { v.muted = true; v.play().catch(() => {}); })")
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2)
        try:
            html = page.content()
        except Exception:  # noqa: BLE001
            pass
        if state_path:
            try:
                with open(state_path, "w", encoding="utf-8") as fh:
                    json.dump(ctx.storage_state(), fh, ensure_ascii=False, indent=2)
            except OSError:
                pass
        browser.close()
    return found, html


# ==========================================================================
# 主流程
# ==========================================================================

def sanitize(name, default="meeting_video"):
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", (name or "").strip())
    name = re.sub(r"\s+", " ", name).strip(" .")
    return (name or default)[:100]


def build_parser():
    p = argparse.ArgumentParser(
        prog="tmdown.py",
        description="输入腾讯会议录制分享链接（或任意视频页），下载成可播放文件。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n"
               "  python tmdown.py https://meeting.tencent.com/crm/DemoShareId000\n"
               "  python tmdown.py 分享链接 --type screen\n"
               "  python tmdown.py 分享链接 --all\n"
               "  python tmdown.py --media-url https://x/y.m3u8 --name 小组会\n")
    p.add_argument("url", nargs="?", help="分享链接")
    p.add_argument("--media-url", help="直接给播放地址（m3u8/mp4）")
    p.add_argument("-o", "--out", default="downloads", help="输出目录（默认 downloads）")
    p.add_argument("-n", "--name", help="输出文件名前缀")
    p.add_argument("--type", default="video",
                   help="腾讯会议轨道：video(合成)/screen/speaker/audio，逗号分隔")
    p.add_argument("--all", action="store_true", help="下载全部轨道")
    p.add_argument("--pwd", default="", help="分享密码")
    p.add_argument("--cookie", help="Cookie 字符串或 cookies.txt / state.json 路径")
    p.add_argument("--login", action="store_true", help="打开浏览器手动登录后再嗅探")
    p.add_argument("--login-state", help="登录态保存路径（默认脚本旁 cookies.json）")
    p.add_argument("--browser", action="store_true", help="强制浏览器嗅探")
    p.add_argument("--no-browser", action="store_true", help="只做静态抓取")
    p.add_argument("--quality", default="best", help="HLS 码率：best/worst/720/1080/序号")
    p.add_argument("-t", "--threads", type=int, default=8, help="并发数（默认 8）")
    p.add_argument("--proxy", default="", help="代理，如 http://127.0.0.1:7890")
    p.add_argument("--insecure", action="store_true", help="跳过证书校验")
    p.add_argument("--keep-parts", action="store_true", help="HLS 分片目录保留")
    p.add_argument("--no-remux", action="store_true", help="不做 ts→mp4")
    p.add_argument("--list", action="store_true", help="只列出轨道/地址，不下载")
    p.add_argument("--selftest", action="store_true", help="自检")
    p.add_argument("--version", action="version", version="tmdown %s" % VERSION)
    return p


def resolve_generic(net, url, args):
    page_url = url if re.match(r"^https?://", url, re.I) else "https://" + url
    net.referer = page_url
    html, title = "", ""
    try:
        status, _, body = net.fetch(page_url, timeout=45)
        html = body.decode("utf-8", "replace")
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        if m:
            title = m.group(1).strip()
    except (AuthError, MediaError) as exc:
        sys.stderr.write("  页面抓取异常: %s\n" % exc)

    urls = extract_media_urls(html)
    if urls:
        return pick(urls, args.quality), title, []
    if args.no_browser:
        raise MediaError("静态页面里没有媒体地址")
    sys.stderr.write("  静态页面没有媒体地址，改用浏览器嗅探\n")
    state = args.login_state or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                             "cookies.json")
    sniffed, html2 = sniff_with_playwright(page_url, state_path=state,
                                           headless=True, wait_seconds=8,
                                           allow_login=args.login)
    urls = sniffed + extract_media_urls(html2)
    if not urls:
        raise MediaError("没找到视频地址（可能需要登录，或页面结构变了）")
    return pick(urls, args.quality), title, []


def pick(urls, quality=None):
    m3u8 = [u for u in urls if ".m3u8" in u.lower()]
    mp4 = [u for u in urls if ".mp4" in u.lower()]
    if quality == "mp4" and mp4:
        return mp4[0]
    return (m3u8 or mp4 or [urls[0]])[0]


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.threads < 1:
        build_parser().error("--threads 必须大于 0")
    if args.selftest:
        return selftest()
    if not args.url and not args.media_url:
        build_parser().print_help()
        return 2
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    net = Net(cookie=args.cookie or "", proxy=args.proxy, insecure=args.insecure)
    want = None if args.all else set(x.strip() for x in args.type.split(",") if x.strip())

    os.makedirs(args.out, exist_ok=True)

    # ---- 腾讯会议 ----
    if args.url and is_tencent_meeting(args.url) and not args.media_url:
        try:
            subject, tracks = resolve_tencent(net, args.url, pwd=args.pwd, want=want)
        except AuthError as exc:
            sys.stderr.write("鉴权失败: %s\n" % exc)
            return 4
        except MediaError as exc:
            sys.stderr.write("解析失败: %s\n" % exc)
            return 3
        sys.stderr.write("会议: %s\n" % (subject or "(未知)"))
        for t in tracks:
            sys.stderr.write("  [%s] %-6s %8.1f MB  %s\n" %
                             (t["key"], t["label"], t["size"] / 1048576.0,
                              os.path.splitext(t["url"].split("?")[0])[-1]))
        if args.list:
            for t in tracks:
                print(t["url"])
            return 0
        prefix = sanitize(args.name or subject or "meeting")
        t0 = time.time()
        finals = []
        for t in tracks:
            out = os.path.join(args.out, "%s-%s%s" % (prefix, t["label"], t["ext"]))
            sys.stderr.write("下载 %s -> %s\n" % (t["label"], out))
            try:
                finals.append(download_direct(
                    net, t["url"], out, threads=args.threads, size_hint=t["size"],
                    refresh=lambda track=t: resign_tencent(net, track),
                    source_id="tencent:%s:%s:%s" % (
                        t["record_id"], t["resource_type"], t.get("resource_id") or t["record_id"])))
            except (AuthError, MediaError) as exc:
                sys.stderr.write("  下载失败: %s\n" % exc)
                return 4 if isinstance(exc, AuthError) else 5
        for f in finals:
            print("完成: %s (%.1f MB)" % (f, os.path.getsize(f) / 1048576.0))
        print("总用时 %.1fs" % (time.time() - t0))
        return 0

    # ---- 通用 ----
    media_url = args.media_url
    title = ""
    if not media_url:
        try:
            media_url, title, _ = resolve_generic(net, args.url, args)
        except AuthError as exc:
            sys.stderr.write("鉴权失败: %s\n" % exc)
            return 4
        except MediaError as exc:
            sys.stderr.write("解析失败: %s\n" % exc)
            return 3
    sys.stderr.write("播放地址: %s\n" % media_url)
    if args.list:
        print(media_url)
        return 0

    base = sanitize(args.name or title or "video")
    out_base = os.path.join(args.out, base)
    try:
        if ".m3u8" in media_url.lower():
            final = download_hls(net, media_url, out_base, threads=args.threads,
                                 quality=args.quality, remux=not args.no_remux,
                                 keep_parts=args.keep_parts)
        else:
            ext = os.path.splitext(urllib.parse.urlparse(media_url).path)[1] or ".mp4"
            final = download_direct(net, media_url, out_base + ext, threads=args.threads)
    except AuthError as exc:
        sys.stderr.write("鉴权失败: %s\n" % exc)
        return 4
    except MediaError as exc:
        sys.stderr.write("下载失败: %s\n" % exc)
        return 5
    print("完成: %s (%.1f MB)" % (final, os.path.getsize(final) / 1048576.0))
    return 0


# ==========================================================================
# 自检
# ==========================================================================

def _selftest_aes():
    key = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
    pt = bytes.fromhex("00112233445566778899aabbccddeeff")
    w, nr = _expand_key(key)
    assert _decrypt_block(bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a"), w, nr) == pt

    key = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
    iv = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
    ct = bytes.fromhex("7649abac8119b246cee98e9b12e9197d"
                       "5086cb9b507219ee95db113a917678b2")
    exp = bytes.fromhex("6bc1bee22e409f96e93d7e117393172a"
                        "ae2d8a571e03ac9c9eb76fac45af8e51")
    assert aes_cbc_decrypt(key, iv, ct) == exp
    assert SBOX[0] == 0x63 and SBOX[1] == 0x7C
    assert pkcs7_unpad(b"abc" + b"\x05" * 5) == b"abc"
    print("[ok] AES-128-CBC / PKCS7")


def _selftest_parse():
    master = ("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360\n"
              "low/i.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=2400000,RESOLUTION=1280x720\n"
              "high/i.m3u8\n")
    pl = parse_playlist(master, "https://cdn.x/v/master.m3u8")
    assert pl["is_master"] and len(pl["variants"]) == 2
    assert select_variant(pl["variants"], "best")["bandwidth"] == 2400000
    assert select_variant(pl["variants"], "360")["bandwidth"] == 800000

    media = ("#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:7\n"
             "#EXT-X-KEY:METHOD=AES-128,URI=\"key.bin\",IV=0x0102030405060708090a0b0c0d0e0f10\n"
             "#EXTINF:10,\nseg0.ts\n#EXTINF:10,\nseg1.ts\n#EXT-X-ENDLIST\n")
    pl = parse_playlist(media, "https://cdn.x/v/m/index.m3u8")
    assert len(pl["segments"]) == 2 and pl["segments"][0]["seq"] == 7
    assert pl["segments"][0]["key"]["uri"] == "https://cdn.x/v/m/key.bin"
    assert _iv_from(pl["segments"][0]["key"], 7).hex() == "0102030405060708090a0b0c0d0e0f10"

    urls = extract_media_urls('{"u":"https:\\/\\/v.x\\/a\\/index.m3u8?sign=1"}')
    assert urls and "index.m3u8" in urls[0]
    print("[ok] m3u8 解析 / 码率选择 / 地址提取")


def _selftest_tencent():
    assert tencent_short_link("https://meeting.tencent.com/crm/DemoShareId000") == "DemoShareId000"
    assert tencent_short_link("https://meeting.tencent.com/cw/AbC123xyz") == "AbC123xyz"
    assert tencent_short_link("https://meeting.tencent.com/shares?short_link=Qq123456") == "Qq123456"
    assert is_tencent_meeting("https://meeting.tencent.com/crm/DemoShareId000")
    assert not is_tencent_meeting("https://example.com/x")

    payload = ('<script>self.__next_f.push([1,"17:[\\"$\\",\\"$L18\\",null,{\\"data\\":'
               '{\\"serverData\\":{\\"code\\":0,\\"pk_meeting_info_id\\":\\"demo-meeting\\",'
               '\\"subject\\":\\"RGVtbyBtZWV0aW5n\\",\\"sharing_id\\":\\"\\",'
               '\\"recordings\\":[{\\"id\\":\\"demo-recording\\",\\"sharing_id\\":\\"demo-sharing\\",'
               '\\"record_type\\":\\"cloud_record\\"}]}}}"])</script>')
    data = parse_server_data(payload)
    assert data and data["recordings"][0]["id"] == "demo-recording", data
    assert decode_subject("RGVtbyBtZWV0aW5n") == "Demo meeting"
    print("[ok] 腾讯会议链接解析 / serverData 提取 / 标题解码")


def _selftest_e2e():
    import http.server
    import socketserver

    segs = [os.urandom(4096) for _ in range(5)]
    master = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000\nv/index.m3u8\n"
    media = "".join(["#EXTM3U\n#EXT-X-TARGETDURATION:4\n"]
                    + ["#EXTINF:4,\nseg%d.ts\n" % i for i in range(len(segs))]
                    + ["#EXT-X-ENDLIST\n"])
    blob = b"".join(segs) * 8

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            rng = self.headers.get("Range")
            if path.endswith("master.m3u8"):
                body, status, extra = master.encode(), 200, {}
            elif path.endswith("index.m3u8"):
                body, status, extra = media.encode(), 200, {}
            elif re.search(r"seg\d\.ts$", path):
                body, status, extra = segs[int(re.search(r"seg(\d)", path).group(1))], 200, {}
            elif path.endswith("blob.mp4"):
                body, extra = blob, {}
                if rng:
                    m = re.match(r"bytes=(\d+)-(\d+)", rng)
                    a, b = int(m.group(1)), min(int(m.group(2)), len(blob) - 1)
                    body = blob[a:b + 1]
                    status = 206
                    extra = {"Content-Range": "bytes %d-%d/%d" % (a, b, len(blob))}
                else:
                    status = 200
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                for k, v in extra.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)
                return
            else:
                self.send_error(404)
                return
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = "http://127.0.0.1:%d" % srv.server_address[1]
        net = Net()
        with tempfile.TemporaryDirectory() as tmp:
            final = download_hls(net, base + "/master.m3u8", os.path.join(tmp, "v"),
                                 threads=4, remux=False)
            with open(final, "rb") as fh:
                assert fh.read() == b"".join(segs), "HLS 拼接不一致"
        print("[ok] HLS 端到端（本地 HTTP）")

        with tempfile.TemporaryDirectory() as tmp:
            final = download_direct(net, base + "/blob.mp4", os.path.join(tmp, "b.mp4"),
                                    threads=4)
            with open(final, "rb") as fh:
                assert fh.read() == blob, "分块下载内容不一致"
        print("[ok] 直链分块+续传")
    finally:
        srv.shutdown()
        srv.server_close()


def selftest():
    print("tmdown %s 自检" % VERSION)
    _selftest_aes()
    _selftest_parse()
    _selftest_tencent()
    _selftest_e2e()
    print("全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
