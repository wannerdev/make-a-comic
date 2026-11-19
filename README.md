# make-a-comic — a portable agent skill for ComicStyles

Give your agent the ability to turn an idea into an illustrated comic strip.
The skill is three files and no dependencies: `SKILL.md` (when and how to use
it), `make_comic.py` (Python 3.8+, standard library only), and this README.

All the hard parts — expanding the idea, writing the panels, choosing the art
style and the image model, keeping characters recognisable across panels —
happen on the ComicStyles server. The skill just carries the user's idea there
and brings back a link.

- Takes **2–4 minutes** per comic.
- No account and no API key needed: an anonymous caller gets a small daily allowance of free comics (`GET /api/create-eligibility` reports it).
- The finished comic is public at `https://comicstyles.com/gallery?story=<id>`.

## Install (one-liner)

```bash
mkdir -p ~/.claude/skills && curl -fsSL https://comicstyles.com/skills/make-a-comic.tar.gz | tar -xz -C ~/.claude/skills
```

That drops `~/.claude/skills/make-a-comic/{SKILL.md,make_comic.py,README.md}`.
Point `-C` somewhere else for a different runtime (see below).

## Install for Claude Code / Claude agent skills

Skills live in `~/.claude/skills/<skill-name>/` (personal) or
`.claude/skills/<skill-name>/` inside a project. Either the one-liner above, or
by hand:

```bash
mkdir -p ~/.claude/skills/make-a-comic
cd ~/.claude/skills/make-a-comic
curl -fsSLO https://comicstyles.com/skills/make-a-comic/SKILL.md
curl -fsSLO https://comicstyles.com/skills/make-a-comic/make_comic.py
curl -fsSLO https://comicstyles.com/skills/make-a-comic/README.md
```

Start a new session and ask for a comic ("turn this into a comic strip"). The
`description` in the SKILL.md frontmatter is what makes the agent reach for it.

## Install for OpenClaw

OpenClaw reads skills from its own skills directory — `~/.openclaw/skills/` by
default, or whatever `skills` path your OpenClaw config sets. Same layout, same
files:

```bash
mkdir -p ~/.openclaw/skills && curl -fsSL https://comicstyles.com/skills/make-a-comic.tar.gz | tar -xz -C ~/.openclaw/skills
```

Then restart the agent so it re-scans the directory.

## Generic MCP server / CLI agent / cron job

There is nothing Claude-specific in the script. Any runtime that can execute a
shell command can use it: hand `SKILL.md` to the model as context (or paste its
instructions into your system prompt) and let the model shell out to
`make_comic.py`. Wrapping it as an MCP tool is a dozen lines — one string
parameter for the idea, one integer for the panel count, and return the URL the
script prints on stdout.

## Run it manually

```bash
python3 make_comic.py --idea "a raccoon opens a noodle shop on Mars" --panels 4 --out ./comic
```

```
[make-a-comic   0s] Target: https://comicstyles.com
[make-a-comic   0s] Account: anonymous trial
[make-a-comic   1s] Eligibility: ok (trial)
[make-a-comic   1s] Step 1/4 — expanding the idea into a story (~30s)…
[make-a-comic  34s] Step 2/4 — breaking the story into 4 panels (~2 min, may retry)…
[make-a-comic 149s] Step 3/4 — saving the comic…
[make-a-comic 151s] Step 4/4 — rendering 4 panel images (~3s each)…
https://comicstyles.com/gallery?story=raccoon_noodle_shop_1754...
```

Progress goes to stderr; the comic URL is the only thing on stdout, so
`URL=$(python3 make_comic.py --idea "…" 2>/dev/null)` works.

### Options

| Flag | Default | Notes |
|------|---------|-------|
| `--idea` | required | The idea, verbatim. Do not pre-write the comic for it. |
| `--panels` | `4` | 4–12. |
| `--language` | `en` | `en`, `de`, `es`, `fr`. |
| `--title` | server-invented | |
| `--out` | `./comic` | PNGs plus a `comic.json` manifest. |
| `--owner` | random | Author name on the comic. |

### Environment

| Variable | Default | Notes |
|----------|---------|-------|
| `COMICSTYLES_BASE_URL` | `https://comicstyles.com` | Point at a staging origin if you have one. |
| `COMICSTYLES_SESSION_TOKEN` | — | `sess_…` token, to create comics under an existing account instead of burning the anonymous trial. |

The first anonymous run prints a username, a recovery password and a session
token on stderr. Save the recovery password — it is shown once — and export the
session token to keep using the same account:

```bash
export COMICSTYLES_SESSION_TOKEN=sess_...
```

## Troubleshooting

| Symptom | What it means |
|---------|---------------|
| `retrying in 3s: HTTP 524` | Normal. Cloudflare cut a long call; the script retries. |
| `cannot create a comic right now (reason: trial-used)` | Today's free allowance is spent. Wait for the next UTC day, or use a session token or an invite. |
| `HTTP 429` | Rate limited. Wait a minute. |
| `HTTP 402` | This deployment charges for agent access (x402). Terminal for this script — see /llms.txt for the terms and use an x402 client to pay, or a deployment with payments off. |
| `did not return a CSRF token` | `COMICSTYLES_BASE_URL` is not pointing at the ComicStyles app. |

## Notes

- Comics made through the API are public and appear in the gallery.
- Images are AI-generated and carry an embedded "AI-generated" marker.
- More detail: <https://comicstyles.com/agent>
