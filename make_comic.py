#!/usr/bin/env python3
"""Make a comic on ComicStyles from a one-line idea.

Dependency-free: Python 3.8+ standard library only (urllib, json,
http.cookiejar, base64, argparse). No pip install, no API key.

The script is a thin HTTP client. Every creative decision — how the idea is
expanded, how panels are written, which art style and image model are used —
is made by the ComicStyles server. This file deliberately contains no prompt
text of its own: it passes the user's idea through unchanged.

Usage:
    python3 make_comic.py --idea "a raccoon opens a noodle shop on Mars"
    python3 make_comic.py --idea "..." --panels 6 --language de --out ./mycomic

Environment:
    COMICSTYLES_BASE_URL        default https://comicstyles.com
    COMICSTYLES_SESSION_TOKEN   act as an existing account (sess_...)
                                omit it and the server hands out an
                                anonymous trial account (a daily allowance,
                                reported by GET /api/create-eligibility)

Exit codes: 0 success, 1 failure, 2 bad usage.
"""

import argparse
import base64
import http.cookiejar
import json
import os
import random
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE_URL = "https://comicstyles.com"
USER_AGENT = "comicstyles-make-a-comic-skill/1.0 (+https://comicstyles.com/agent)"

MIN_PANELS = 4
MAX_PANELS = 12
DEFAULT_PANELS = 4
LANGUAGES = ("en", "de", "es", "fr")

# Cloudflare sits in front of production with a ~100s proxy budget. The
# panel-breakdown call is the one long enough to be at risk, so 502/520-524
# are treated as mid-flight cuts, not real failures — retried like any 5xx.
RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524}

START_TIME = time.time()


class ComicError(Exception):
    """Anything the caller can act on. Never surfaces as a traceback."""


def log(message):
    """Progress goes to stderr so stdout stays machine-readable."""
    elapsed = int(time.time() - START_TIME)
    sys.stderr.write("[make-a-comic %3ds] %s\n" % (elapsed, message))
    sys.stderr.flush()


def slugify(text, fallback="story"):
    slug = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return slug[:40] or fallback


class ComicStylesClient:
    """Cookie jar + CSRF token + retrying JSON transport."""

    def __init__(self, base_url, session_token=None, verbose=True):
        self.base_url = base_url.rstrip("/")
        self.session_token = session_token or None
        self.verbose = verbose
        self.csrf_token = None
        self.cookie_jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookie_jar)
        )

    # -- transport ---------------------------------------------------------

    def _headers(self, has_body):
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        if has_body:
            headers["Content-Type"] = "application/json"
        if self.csrf_token:
            headers["X-CSRF-Token"] = self.csrf_token
        if self.session_token:
            headers["X-Session-Token"] = self.session_token
        return headers

    def request(self, method, path, payload=None, timeout=90, attempts=4, label=None):
        url = self.base_url + path
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        what = label or "%s %s" % (method, path)
        delay = 3.0
        last_error = None

        for attempt in range(1, attempts + 1):
            request = urllib.request.Request(
                url, data=body, method=method, headers=self._headers(body is not None)
            )
            try:
                with self.opener.open(request, timeout=timeout) as response:
                    raw = response.read()
                return self._decode(raw, what)
            except urllib.error.HTTPError as err:
                detail = self._error_detail(err)
                if err.code in RETRYABLE_STATUS and attempt < attempts:
                    last_error = "HTTP %s (%s)" % (err.code, detail)
                    self._sleep(what, last_error, attempt, attempts, delay)
                    delay = min(delay * 2, 45.0)
                    continue
                raise ComicError(self._explain_http(err.code, detail, what))
            except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as err:
                # Includes read timeouts and connections dropped mid-response,
                # which is how a proxy cut looks from this side.
                reason = getattr(err, "reason", err)
                if attempt < attempts:
                    last_error = "network error (%s)" % reason
                    self._sleep(what, last_error, attempt, attempts, delay)
                    delay = min(delay * 2, 45.0)
                    continue
                raise ComicError(
                    "%s failed after %d attempts: %s. Check the network and that %s is reachable."
                    % (what, attempts, reason, self.base_url)
                )

        raise ComicError("%s failed after %d attempts: %s" % (what, attempts, last_error))

    def _sleep(self, what, reason, attempt, attempts, delay):
        if self.verbose:
            log(
                "%s: %s — retrying in %ds (attempt %d/%d)"
                % (what, reason, int(delay), attempt + 1, attempts)
            )
        time.sleep(delay)

    @staticmethod
    def _decode(raw, what):
        text = raw.decode("utf-8", "replace").strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except ValueError:
            raise ComicError(
                "%s returned a non-JSON response (first 200 chars): %s" % (what, text[:200])
            )

    @staticmethod
    def _error_detail(err):
        try:
            raw = err.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - the body is best-effort context only
            return err.reason or "no detail"
        try:
            parsed = json.loads(raw)
        except ValueError:
            return (raw.strip() or str(err.reason))[:200]
        if isinstance(parsed, dict):
            return str(parsed.get("error") or parsed.get("message") or parsed)[:300]
        return str(parsed)[:200]

    @staticmethod
    def _explain_http(status, detail, what):
        hints = {
            401: "The server wants an account. Set COMICSTYLES_SESSION_TOKEN to a session "
                 "token from an earlier run, or open the site and request an invite.",
            402: "This deployment charges for agent generation via the x402 protocol "
                 "(https://x402.org): the 402 body carries the exact price, network and "
                 "receiving address, and /llms.txt states the terms. This script is "
                 "standard-library-only and cannot sign crypto payments — use an "
                 "x402-capable client (e.g. x402-fetch with a funded wallet; testnet "
                 "deployments take faucet USDC on base-sepolia) to drive the same "
                 "endpoints, or run against a deployment with payments off. "
                 "Do not retry unpaid: the answer will be 402 every time.",
            403: "The CSRF token was rejected. This usually means the cookie jar was lost "
                 "mid-run; re-run the script from scratch.",
            429: "Rate limited. Wait a minute and try again.",
        }
        hint = hints.get(status, "")
        return "%s failed: HTTP %s — %s%s" % (what, status, detail, (" " + hint) if hint else "")

    # -- flow --------------------------------------------------------------

    def bootstrap(self):
        data = self.request("GET", "/api/csrf-token", timeout=30, label="CSRF handshake")
        self.csrf_token = data.get("csrfToken")
        if not self.csrf_token:
            raise ComicError(
                "The server did not return a CSRF token. Is %s the ComicStyles app?" % self.base_url
            )

    def check_eligibility(self):
        data = self.request(
            "GET", "/api/create-eligibility", timeout=30, label="Eligibility check"
        )
        if data.get("canCreate"):
            return data.get("reason", "open")
        raise ComicError(
            "This caller cannot create a comic right now (reason: %s). The free anonymous "
            "trial is a daily allowance per visitor (GET /api/create-eligibility reports it). "
            "Wait for the next UTC day, set COMICSTYLES_SESSION_TOKEN to an existing "
            "account's session token, or ask the site owner for an invite link."
            % data.get("reason", "unknown")
        )

    def expand_story(self, idea, language):
        # The idea is passed through verbatim. The server owns the expansion.
        data = self.request(
            "POST",
            "/api/agent/generate-story",
            {"roughStory": idea, "language": language},
            timeout=240,
            label="Story expansion",
        )
        expanded = (data.get("expandedStory") or "").strip()
        if not expanded:
            raise ComicError("The server returned an empty expanded story. Try a longer idea.")
        return expanded

    def make_title(self, story_text):
        try:
            data = self.request(
                "POST",
                "/api/generate-title",
                {"storyText": story_text},
                timeout=120,
                attempts=2,
                label="Title",
            )
            return (data.get("title") or "").strip()
        except ComicError as err:
            log("Title generation failed (%s) — falling back to the idea text." % err)
            return ""

    def make_style(self, story_text):
        """Art style is chosen server-side; an empty style is a valid outcome."""
        try:
            data = self.request(
                "POST",
                "/api/generate-style",
                {"storyText": story_text},
                timeout=120,
                attempts=2,
                label="Art style",
            )
            return (data.get("style") or "").strip()
        except ComicError as err:
            log("Style generation failed (%s) — continuing with the server default." % err)
            return ""

    def breakdown(self, expanded_story, panel_count, language):
        data = self.request(
            "POST",
            "/api/agent/generate-panels",
            {
                "expandedStory": expanded_story,
                "panelCount": panel_count,
                "language": language,
            },
            # The slowest call in the flow, and the one Cloudflare cuts.
            timeout=300,
            attempts=4,
            label="Panel breakdown",
        )
        panels = data.get("panels") or {}
        if not panels:
            raise ComicError("The server returned no panels. Try again, or use a simpler idea.")
        return data

    def save_story(self, story):
        data = self.request(
            "POST", "/api/stories", story, timeout=120, label="Saving the comic"
        )
        if not data.get("success"):
            raise ComicError("Saving the comic failed: %s" % (data.get("error") or data))
        # An anonymous caller is handed a fresh trial account here.
        token = data.get("sessionToken")
        if token:
            self.session_token = token
        return data

    def render_panel(self, prompt, panel_id, save_as, style, character_bible):
        payload = {
            "prompt": prompt,
            "provider": "pollinations",
            "panelId": panel_id,
            "saveAs": save_as,
        }
        # `model` is intentionally omitted: the server picks it.
        if style:
            payload["style"] = style
        if character_bible:
            payload["characterBible"] = character_bible
        data = self.request(
            "POST",
            "/api/agent/generate",
            payload,
            timeout=180,
            attempts=3,
            label="Rendering %s" % panel_id,
        )
        if not data.get("success") or not data.get("imageData"):
            raise ComicError(data.get("error") or "the server returned no image data")
        return data


def panel_order(panels):
    def key(name):
        match = re.search(r"(\d+)$", name)
        return (int(match.group(1)) if match else 0, name)

    return sorted(panels.keys(), key=key)


def build_story_id(title):
    return "%s_%d_%04d" % (slugify(title), int(time.time() * 1000), random.randint(0, 9999))


def make_comic(args):
    base_url = (os.environ.get("COMICSTYLES_BASE_URL") or DEFAULT_BASE_URL).strip()
    if not base_url.startswith(("http://", "https://")):
        raise ComicError("COMICSTYLES_BASE_URL must start with http:// or https:// (got %r)" % base_url)

    token = (os.environ.get("COMICSTYLES_SESSION_TOKEN") or "").strip() or None
    client = ComicStylesClient(base_url, session_token=token)

    log("Target: %s" % base_url)
    log("Account: %s" % ("existing session token" if token else "anonymous trial"))
    log("This takes 2-4 minutes. Two of the calls are slow by nature; retries are normal.")

    client.bootstrap()
    reason = client.check_eligibility()
    log("Eligibility: ok (%s)" % reason)

    log("Step 1/4 — expanding the idea into a story (~30s)…")
    expanded = client.expand_story(args.idea, args.language)

    title = (args.title or "").strip() or client.make_title(expanded) or args.idea.strip()[:60]
    log("Title: %s" % title)

    log("Step 2/4 — breaking the story into %d panels (~2 min, may retry)…" % args.panels)
    breakdown = client.breakdown(expanded, args.panels, args.language)
    panels = breakdown.get("panels") or {}
    captions = breakdown.get("captions") or {}
    sfx = breakdown.get("sfx") or {}
    character_bible = breakdown.get("characterBible") or None
    keys = panel_order(panels)
    log("Got %d panels." % len(keys))

    style = client.make_style(expanded)

    story_id = build_story_id(title)
    owner = args.owner or ("agent_" + str(random.randint(1000, 9999)))

    log("Step 3/4 — saving the comic…")
    saved = client.save_story(
        {
            "id": story_id,
            "title": title,
            "roughStory": args.idea,
            "expandedStory": expanded,
            "panels": panels,
            "captions": captions,
            "panelCaptions": captions,
            "sfx": sfx,
            "characterBible": character_bible,
            "style": style,
            "owner": owner,
        }
    )

    if saved.get("recoveryPassword"):
        log("")
        log("A trial account was created for this comic. Show these to the user ONCE:")
        log("  username:          %s" % ((saved.get("user") or {}).get("username") or owner))
        log("  recovery password: %s" % saved["recoveryPassword"])
        log("  session token:     %s" % (saved.get("sessionToken") or ""))
        log("  (export COMICSTYLES_SESSION_TOKEN=<session token> to reuse the account)")
        log("")

    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)

    log("Step 4/4 — rendering %d panel images (~3s each)…" % len(keys))
    written = []
    failed = []
    for index, key in enumerate(keys, start=1):
        prompt = panels[key]
        if style:
            # Mechanical join only — the style text itself comes from the server.
            prompt = "%s, %s" % (prompt, style)
        try:
            result = client.render_panel(
                prompt, key, "%s_%s.png" % (story_id, key), style, character_bible
            )
        except ComicError as err:
            log("  %s (%d/%d) failed: %s" % (key, index, len(keys), err))
            failed.append(key)
            continue
        path = os.path.join(out_dir, "%s.png" % key)
        with open(path, "wb") as handle:
            handle.write(base64.b64decode(result["imageData"]))
        written.append(path)
        log("  %s (%d/%d) ✓" % (key, index, len(keys)))

    comic_url = "%s/gallery?story=%s" % (base_url, urllib.parse.quote(story_id))
    manifest = {
        "id": story_id,
        "title": title,
        "url": comic_url,
        "language": args.language,
        "panelOrder": keys,
        "captions": captions,
        "sfx": sfx,
        "images": {os.path.basename(p).replace(".png", ""): p for p in written},
        "failedPanels": failed,
    }
    with open(os.path.join(out_dir, "comic.json"), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False)

    if failed:
        log("%d of %d panels failed to render; the comic is still viewable." % (len(failed), len(keys)))
    log("Images written to %s" % out_dir)
    log("Done in %ds." % int(time.time() - START_TIME))

    # stdout: the one thing a caller always wants.
    print(comic_url)
    return 0


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="make_comic.py",
        description="Turn a one-line idea into an illustrated comic on ComicStyles.",
    )
    parser.add_argument("--idea", required=True, help="The user's idea, passed through unchanged.")
    parser.add_argument(
        "--panels",
        type=int,
        default=DEFAULT_PANELS,
        help="Panel count (%d-%d, default %d)." % (MIN_PANELS, MAX_PANELS, DEFAULT_PANELS),
    )
    parser.add_argument(
        "--language", default="en", choices=list(LANGUAGES), help="Caption language (default en)."
    )
    parser.add_argument("--title", default="", help="Comic title (the server invents one if omitted).")
    parser.add_argument("--out", default="comic", help="Directory for the panel images (default ./comic).")
    parser.add_argument("--owner", default="", help="Display name shown as the comic's author.")
    args = parser.parse_args(argv)

    if not args.idea.strip():
        parser.error("--idea must not be empty")

    if args.panels < MIN_PANELS or args.panels > MAX_PANELS:
        clamped = max(MIN_PANELS, min(MAX_PANELS, args.panels))
        log("--panels %d is out of range; using %d." % (args.panels, clamped))
        args.panels = clamped
    return args


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return make_comic(args)
    except ComicError as err:
        log("ERROR: %s" % err)
        return 1
    except KeyboardInterrupt:
        log("Interrupted.")
        return 130
    except Exception as err:  # noqa: BLE001 - a traceback is not an actionable message
        log("ERROR: unexpected failure: %s: %s" % (type(err).__name__, err))
        log("If this keeps happening, report it at %s/agent" % DEFAULT_BASE_URL)
        return 1


if __name__ == "__main__":
    sys.exit(main())
