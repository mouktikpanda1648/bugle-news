# The Daily Bugle — Civic Trust Engine

A citizen incident-reporting platform with an explainable AI trust engine:
reports cluster spatially, corroboration and evidence build a confidence
score with visible reasons, and human editors triage the queue.

## What changed in this rebuild

- **Supabase is gone.** It didn't reliably support email/phone OTP for this
  use case, so authentication is now fully self-contained:
  - Passwordless OTP login (email **or** phone) — no external identity
    provider required.
  - **Dev mode** (default): when `DEBUG=True` and no SMTP is configured, the
    OTP is printed to the terminal *and* returned to the browser, so sign-in
    always works out of the box — ideal for a hackathon demo with zero setup.
  - **Production mode**: fill in `SMTP_HOST` / `SMTP_USER` / `SMTP_PASSWORD`
    (e.g. a Gmail App Password) and the app automatically sends real emails
    instead, with the dev-mode code hidden from the response.
- **Editor role is no longer guessable.** Previously, typing "editor" into
  your email address silently granted newsroom (EDITOR) privileges — a real
  privilege-escalation bug. Editor clearance now requires a correct
  `EDITOR_INVITE_CODE` (set in `.env`) entered at sign-in.
- **Rate limiting & lockout**: OTP requests are capped per identifier per
  time window, and repeated wrong codes lock out that OTP after 5 attempts.
- **New "My Trust File" page** (`/profile`): reporters can see their own
  trust score, strike count, and the outcome of every report they've filed.
- **Full visual redesign**: new type system (Space Grotesk + Inter + JetBrains
  Mono), refreshed color system, toast notifications instead of `alert()`,
  skeleton loaders, and a search/filter box on The Wire.
- All previously-broken backend logic is fixed: DB connections, duplicate
  function definitions, `.env` load ordering, missing schema columns, and a
  reporter-diversity calculation that compared the wrong values.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
```

Open `.env` and set at minimum:

```
JWT_SECRET_KEY=<run: python -c "import secrets; print(secrets.token_hex(32))">
EDITOR_INVITE_CODE=<pick your own secret>
```

Everything else has sane defaults. Then run:

```bash
python main.py
```

Visit `http://127.0.0.1:8000`.

## Demo accounts

No passwords — just sign in with either identifier below and use the code
shown on screen (dev mode) or printed to the terminal:

| Role    | Identifier                |
|---------|----------------------------|
| Editor  | `editor@dailybugle.com`   (enter the invite code from `.env`) |
| Citizen | `peter@dailybugle.com`    |

Any new email/phone you sign in with is auto-provisioned as a CITIZEN.

## Optional: real AI scoring

Set `GEMINI_API_KEY` in `.env` to have the trust engine use Gemini for
title/summary generation and credibility scoring. Without it, a heuristic
fallback runs automatically — the app is fully functional either way.

## Optional: real email delivery

Set `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD` in `.env` (a Gmail App
Password works well). The app detects this automatically and switches from
dev-mode codes to real email delivery.
