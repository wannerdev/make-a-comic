---
name: make-a-comic
description: Turn a story, idea, or piece of text into an illustrated multi-panel comic strip using the ComicStyles public API, and give the user a link to the finished comic. Use when the user says "make a comic", "make this into a comic", "turn this into a comic strip", "illustrate this story", "draw this as panels", "comic strip of ...", or asks for an illustrated version of something they wrote. Not for single illustrations, memes, or editing an existing image.
---

# Make a comic

This skill sends the user's idea to ComicStyles, which writes the panel
breakdown, picks the art style and the image model, and renders every panel.
Your job is to collect the idea, run one script, and report the result.

**You do not write the comic.** Do not expand, rewrite, "improve", translate,
or add art direction to the user's idea before sending it. The service does all
of that server-side and is tuned for it; a pre-chewed prompt makes the output
worse. Pass the user's words through unchanged.

## Run it

```bash
python3 make_comic.py --idea "<the user's idea, verbatim>" --panels 4 --out ./comic
```

Python 3.8+ with no third-party packages. Flags:

| Flag | Default | Notes |
|------|---------|-------|
| `--idea` | required | The user's text, unchanged. Quote it. |
| `--panels` | `4` | 4–12. More panels = proportionally longer. |
| `--language` | `en` | `en`, `de`, `es`, `fr` — the language of the captions. |
| `--title` | server-invented | Only pass one if the user named their comic. |
| `--out` | `./comic` | Directory for the panel PNGs and `comic.json`. |
| `--owner` | random | Display name shown as the comic's author. |

Environment:

- `COMICSTYLES_BASE_URL` — defaults to `https://comicstyles.com`.
- `COMICSTYLES_SESSION_TOKEN` — a `sess_…` token to create the comic under an
  existing account. Without it the server issues an anonymous trial account,
  which is good for a small daily allowance of comics (see Limits;
  `GET /api/create-eligibility` reports what is left).

## How long it takes

**2–4 minutes**, and that is normal, not a hang. Tell the user up front, then
let the script run to completion — do not kill it and retry.

1. Expanding the idea into a story — about 30 seconds.
2. Breaking the story into panels — up to about a minute and a half; the
   server caps its own text work per request. This is the slow one.
3. Saving the comic — a few seconds.
4. Rendering the panels — roughly 3 seconds each.

The script streams progress to stderr, so you can show the user where it is.

## Retries and the 502/524 problem

Production sits behind Cloudflare, which cuts a connection at roughly 100
seconds. The panel call is the one long enough to be at risk, so a `502`,
`524`, or a dropped connection there is a **mid-flight cut**, not a failure.
The script already retries those with exponential backoff (and every other
5xx, plus `408`/`429`). A retry line on stderr is not an error — only a final
`ERROR:` line is.

If the script does exit non-zero, read the last stderr line: it says what to do
(wrong base URL, trial already used, rate limited). Do not re-run blindly; an
exhausted trial will fail identically every time.

**`402` means this deployment charges — also terminal for this script.** The
generation endpoints are x402-paid on deployments that enable it
(https://x402.org): the 402 body carries the price, network and receiving
address, and the deployment's `/llms.txt` states the terms. This script is
standard-library-only and cannot sign crypto payments, so on a paying
deployment tell the user what the terms are and either drive the same
endpoints with an x402-capable client (e.g. `x402-fetch`; testnet deployments
take faucet USDC on `base-sepolia`) or use a deployment with payments off.
Never retry unpaid — the answer is 402 every time.

## Paid deployments (x402)

Whether a given deployment charges is discoverable before spending any work:
fetch `/llms.txt` — a paying deployment has a "Paid agent access (x402)"
section naming the price and network; a free one has no such section. The
default price is $0.0001 USDC per generation call.

## The API, if you must call it yourself

Same-origin JSON over HTTPS. Keep one cookie jar for the whole flow, and send
the CSRF token on every POST.

1. `GET /api/csrf-token` → `{csrfToken}`. Send it back as `X-CSRF-Token`.
2. `GET /api/create-eligibility` → `{canCreate, reason}`. Stop if `canCreate`
   is false.
3. `POST /api/agent/generate-story` `{roughStory, language}` → `{expandedStory}`.
   `roughStory` is the user's idea, verbatim.
4. `POST /api/agent/generate-panels` `{expandedStory, panelCount, language}` →
   `{panels, captions, sfx, characterBible}`. Slow; tolerate 502/524 and retry.
5. `POST /api/stories` `{id, title, roughStory, expandedStory, panels,
   captions, panelCaptions, sfx, style, owner}` → `{success, story}` plus, for
   an anonymous caller, `sessionToken` and `recoveryPassword`. Send this
   **before** rendering images so the images attach to the comic.
6. For each panel: `POST /api/agent/generate` `{prompt, provider:
   "pollinations", panelId, saveAs}` → `{success, imageData, filename}`, where
   `imageData` is base64. Send the session token from step 5 as
   `X-Session-Token`.

The three `/api/agent/*` calls are the agent surface. On deployments that
charge for agent access they answer HTTP 402 with x402 payment terms (see
"Paid deployments" above); everywhere else they behave exactly like the
corresponding free routes.

Optional helpers, both server-side: `POST /api/generate-title` and
`POST /api/generate-style` with `{storyText}`. Use them instead of writing a
title or an art-style description yourself.

Leave the image `model` out of the request. The server chooses it, and its
choice is better informed than a guess from here.

## Reporting the result

The script prints exactly one line to stdout: the public comic URL, of the form
`https://comicstyles.com/gallery?story=<id>`. Anyone with that link can
view the comic.

Give the user:

1. The URL, as a clickable link, first.
2. The panel images from `--out` (`panel_1.png`, `panel_2.png`, …) if your
   runtime can display images. `comic.json` in the same directory has the
   title, panel order, and captions.
3. If a trial account was created, the username, recovery password, and session
   token printed on stderr — **once**, so the user can keep the comic. Tell
   them to save the recovery password; it is not shown again. Do not write
   these to a file the user did not ask for.

If some panels failed (`failedPanels` in `comic.json`), say so plainly and
point at the URL anyway — the comic is still viewable.

## Limits

- A few free comics per UTC day for an anonymous visitor (three by default).
  After that the caller needs a session token or an invite; the eligibility
  check reports the exact allowance before any work runs.
- Comics created this way are **public** and appear in the gallery.
- Panel count is capped at 12.
- The images are AI-generated and carry an embedded "AI-generated" marker.
