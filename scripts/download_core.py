"""Bounded-memory direct downloads with verified, atomic resume checkpoints."""
import hashlib
import http.client
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

BLOCK_SIZE = 4 * 1024 * 1024
BUFFER_SIZE = 256 * 1024


class TransferError(Exception):
    pass


class ExpiredURL(TransferError):
    pass


def open_media(net, url, headers=None, method=None):
    req = urllib.request.Request(url, headers=net.headers(headers), method=method)
    try:
        return net.opener.open(req, timeout=60)
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        # Do not put signed URLs or their tokens into diagnostics.
        if status in (401, 403, 410):
            raise ExpiredURL("媒体地址 HTTP %s" % status) from None
        raise TransferError("媒体请求 HTTP %s" % status) from None


def probe_media(net, url):
    # Read headers only, even if the server ignores Range and returns 200.
    with open_media(net, url, {"Range": "bytes=0-0"}) as resp:
        headers = resp.headers
        status = resp.getcode()
        match = re.fullmatch(r"bytes 0-0/(\d+)", headers.get("Content-Range", ""))
        ranged = status == 206 and match is not None
        size = int(match.group(1)) if ranged else int(headers.get("Content-Length") or 0)
        if status == 206 and not ranged:
            raise TransferError("大小探测返回无效 Content-Range")
        return {"size": size, "ranged": ranged,
                "etag": headers.get("ETag"), "modified": headers.get("Last-Modified")}


def atomic_json(path, data):
    staged = path + ".tmp"
    with open(staged, "w", encoding="utf-8") as output:
        json.dump(data, output, sort_keys=True)
        output.flush()
        os.fsync(output.fileno())
    os.replace(staged, path)


def hash_block(handle, start, count):
    handle.seek(start)
    digest = hashlib.sha256()
    while count:
        data = handle.read(min(BUFFER_SIZE, count))
        if not data:
            return None
        digest.update(data)
        count -= len(data)
    return digest.hexdigest()


def download(net, url, out_path, threads=8, size_hint=0, refresh=None,
             source_id=None, progress_factory=None):
    if threads < 1:
        raise TransferError("并发数必须大于 0")
    # A stable resource ID is supplied for signed Tencent URLs. Generic URLs
    # include the query in their identity to avoid conflating different media.
    identity = hashlib.sha256((source_id or url).encode("utf-8")).hexdigest()
    url_lock = threading.Lock()
    current_url = [url]

    def request(headers=None, method=None):
        attempted_url = current_url[0]
        try:
            return open_media(net, attempted_url, headers, method)
        except ExpiredURL:
            if refresh is None:
                raise
            with url_lock:
                if current_url[0] == attempted_url:
                    current_url[0] = refresh()
            return open_media(net, current_url[0], headers, method)

    # Probe through the same refresh path as workers.
    with request({"Range": "bytes=0-0"}) as resp:
        h = resp.headers
        match = re.fullmatch(r"bytes 0-0/(\d+)", h.get("Content-Range", ""))
        ranged = resp.getcode() == 206 and match is not None
        if resp.getcode() == 206 and not ranged:
            raise TransferError("大小探测返回无效 Content-Range")
        size = int(match.group(1)) if ranged else int(h.get("Content-Length") or size_hint or 0)
        validators = {"etag": h.get("ETag"), "modified": h.get("Last-Modified")}
    if size_hint and size and size != size_hint:
        raise TransferError("媒体大小与轨道元数据不一致")
    tmp, state_path = out_path + ".part", out_path + ".state.json"
    transient = (TransferError, urllib.error.URLError, socket.timeout,
                 ConnectionError, http.client.HTTPException)
    if not ranged or size <= 0:
        # Whole-file streaming fallback, with retries and length validation.
        for attempt in range(4):
            try:
                with request() as resp, open(tmp, "wb") as output:
                    if resp.getcode() != 200:
                        raise TransferError("完整下载未返回 HTTP 200")
                    expected = int(resp.headers.get("Content-Length") or size or 0)
                    count = 0
                    while True:
                        data = resp.read(BUFFER_SIZE)
                        if not data:
                            break
                        output.write(data)
                        count += len(data)
                    if (expected and count != expected) or (size and count != size):
                        raise TransferError("完整下载长度不符")
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(tmp, out_path)
                if os.path.exists(state_path):
                    os.remove(state_path)
                return out_path
            except ExpiredURL:
                raise
            except transient:
                if attempt == 3:
                    raise
                time.sleep(.5 * 2 ** attempt)

    ranges = [(start, min(size, start+BLOCK_SIZE)-1) for start in range(0, size, BLOCK_SIZE)]
    header = {"version": 2, "identity": identity, "size": size,
              "block_size": BLOCK_SIZE, "validators": validators}
    done = {}
    can_resume = bool(validators["etag"] or validators["modified"])
    if can_resume and os.path.isfile(tmp) and os.path.getsize(tmp) == size and os.path.isfile(state_path):
        try:
            with open(state_path, encoding="utf-8") as source:
                saved = json.load(source)
            if all(saved.get(k) == value for k, value in header.items()):
                with open(tmp, "rb") as source:
                    for key, digest in saved.get("done", {}).items():
                        if not key.isdigit() or not isinstance(digest, str):
                            continue
                        index = int(key)
                        if index < len(ranges):
                            start, end = ranges[index]
                            if hash_block(source, start, end-start+1) == digest:
                                done[key] = digest
        except (OSError, ValueError, TypeError, AttributeError):
            done = {}
    with open(tmp, "r+b" if os.path.exists(tmp) else "w+b") as output:
        output.truncate(size)
    state_lock, stop = threading.Lock(), threading.Event()
    atomic_json(state_path, dict(header, done=done))
    prog = progress_factory(len(ranges), "分块", size) if progress_factory else None
    if prog:
        for key in done:
            start, end = ranges[int(key)]
            prog.add(end-start+1)

    def worker(index):
        if stop.is_set():
            return
        start, end = ranges[index]
        for attempt in range(4):
            if stop.is_set():
                return
            try:
                headers = {"Range": "bytes=%d-%d" % (start, end)}
                validator = validators["etag"] or validators["modified"]
                if validator and not validator.startswith("W/"):
                    headers["If-Range"] = validator
                with request(headers) as resp:
                    expected = "bytes %d-%d/%d" % (start, end, size)
                    if resp.getcode() != 206 or resp.headers.get("Content-Range") != expected:
                        raise TransferError("分块响应状态或 Content-Range 不符")
                    for name, saved in (("ETag", validators["etag"]), ("Last-Modified", validators["modified"])):
                        if saved and resp.headers.get(name) != saved:
                            raise TransferError("下载过程中媒体版本已变化")
                    digest, remaining = hashlib.sha256(), end-start+1
                    # Each worker owns its file handle; buffers stay bounded.
                    with open(tmp, "r+b") as output:
                        output.seek(start)
                        while remaining:
                            if stop.is_set():
                                return
                            data = resp.read(min(BUFFER_SIZE, remaining))
                            if not data:
                                raise TransferError("分块提前结束")
                            output.write(data)
                            digest.update(data)
                            remaining -= len(data)
                        if resp.read(1):
                            raise TransferError("分块包含多余数据")
                        output.flush()
                        os.fsync(output.fileno())
                with state_lock:
                    done[str(index)] = digest.hexdigest()
                    atomic_json(state_path, dict(header, done=done))
                if prog:
                    prog.add(end-start+1)
                return
            except ExpiredURL:
                raise
            except transient:
                if attempt == 3:
                    raise
                time.sleep(.5 * 2 ** attempt)

    pool = ThreadPoolExecutor(max_workers=threads)
    futures = []
    try:
        futures = [pool.submit(worker, i) for i in range(len(ranges)) if str(i) not in done]
        for future in as_completed(futures):
            future.result()
        if len(done) != len(ranges):
            raise TransferError("下载完成块数不符")
    finally:
        stop.set()
        for future in futures:
            future.cancel()
        pool.shutdown(wait=True)
        if prog:
            prog.end()
    os.replace(tmp, out_path)
    os.remove(state_path)
    return out_path
