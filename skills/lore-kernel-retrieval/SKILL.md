---
name: lore-kernel-retrieval
description: Use when needing to fetch Linux kernel patches or mailing list threads from lore.kernel.org, especially when encountering Anubis anti-bot protection, or when converting email patches to git-am ready format. Trigger for any task involving kernel patch retrieval, lore.kernel.org access, public-inbox mbox downloads, or b4 tool usage.
---

# lore.kernel.org Patch Retrieval

## Overview

lore.kernel.org archives Linux kernel mailing lists via public-inbox. It is protected by Anubis, a proof-of-work anti-bot system. **Anubis whitelists curl/wget by default**, making direct CLI retrieval the most reliable path. Browser-like User-Agents trigger JavaScript challenges.

## When to Use

- Retrieving a single patch or patch series from lore.kernel.org
- Converting email-based patches to `git am` ready format
- Bypassing Anubis blocks on lore.kernel.org or other Anubis-protected public-inbox instances
- Batch-syncing kernel mailing list archives for offline review

## Quick Reference

| Goal | Command | Notes |
|------|---------|-------|
| Download thread mbox (gzip) | `curl -L "<url>/t.mbox.gz" \| gunzip` | Default curl UA bypasses Anubis |
| Get git-am ready mbox | `b4 am <msgid>` | Sorts patches, adds trailers |
| Apply directly to branch | `b4 shazam <msgid>` | Runs `git am` automatically |
| Raw single message | `wget -qO- "<url>/raw"` | Full headers included |
| Clone full list archive | `git clone https://lore.kernel.org/<list>/.git` | For offline bulk access |
| Incremental sync | `lei q -I https://lore.kernel.org/all ...` | Requires `public-inbox` package |

## URL Patterns

```
Thread mbox (gzip):  https://lore.kernel.org/<list>/<msgid>/t.mbox.gz
Raw message:         https://lore.kernel.org/<list>/<msgid>/raw
Web view:            https://lore.kernel.org/<list>/<msgid>/
Redirect (any list): https://lore.kernel.org/r/<msgid>
```

## Method 1: Direct Download (Simplest, Anubis-Bypassed)

```bash
# Do NOT override User-Agent with a browser string.
curl -L "https://lore.kernel.org/linux-mm/20260422005608.342028-1-fmayle@google.com/t.mbox.gz" \
  | gunzip > thread.mbox

# Alternative: raw single message
wget -qO- "https://lore.kernel.org/linux-mm/20260422005608.342028-1-fmayle@google.com/raw"
```

## Method 1b: Helper Script (`fetch-lore-patch.sh`)

A reusable script at `@fetch-lore-patch.sh` wraps both `b4` (preferred) and `curl` fallback:

```bash
# Automatic: uses b4 if available, else curl fallback
./fetch-lore-patch.sh 20260422005608.342028-1-fmayle@google.com ./output/

# Output when b4 present: ./output/<slug>.mbx (git-am ready)
# Output when b4 absent:  ./output/<msgid>.mbox (raw thread mbox)
```

## Method 2: b4 Tool (Recommended for Patch Series)

```bash
pip install b4

# Generate git-am ready mbox (sorted, trailers merged)
b4 am 20260422005608.342028-1-fmayle@google.com

# Directly apply to current branch
b4 shazam 20260422005608.342028-1-fmayle@google.com

# Download full thread mbox only
b4 mbox 20260422005608.342028-1-fmayle@google.com
```

b4 automatically:
- Fetches `t.mbox.gz` from lore
- Sorts patches by `[PATCH n/m]` counters
- Merges `Reviewed-by` / `Acked-by` trailers from replies
- Outputs `./<slug>.mbx` ready for `git am`

## Method 3: public-inbox git / lei (Bulk/Offline)

```bash
# Clone entire mailing list as git repository
git clone https://lore.kernel.org/linux-mm/.git

# Incremental search + sync (install public-inbox first)
lei q -I https://lore.kernel.org/all -o ~/Mail/linux-mm \
  'f:author@example.com AND rt:1.week.ago..'
```

## Anubis Bypass Details

Anubis rules (default configuration):
- User-Agent containing `Mozilla` → **Challenge issued**
- User-Agent `curl/*`, `wget/*`, `python-requests/*` → **Allowed**
- Missing `Accept-Encoding` or `Accept-Language` → Increases suspicion weight

After passing a challenge, Anubis sets cookie `within.website-x-cmd-anubis-auth` (JWT, ~7 days).

## Common Mistakes

| Mistake | Why It Fails |
|---------|--------------|
| `curl -A "Mozilla/5.0..."` | Triggers Anubis JS PoW challenge; fails in headless environments |
| Using `requests.get()` with default Python UA | Often blocked; use `requests` with no custom UA or use `curl` |
| Forgetting `gunzip` on `t.mbox.gz` | Returns binary gzip data instead of mbox text |
| Passing full URL to `b4 am` | b4 expects Message-ID or `https://lore.kernel.org/r/<msgid>`; full path may confuse parser |
| Using `1-a.patch` endpoint | Returns 403 Forbidden; use `/raw` or `/t.mbox.gz` instead |

## Example: Complete Retrieval Workflow

```bash
MSGID="20260422005608.342028-1-fmayle@google.com"

# Step 1: Fetch thread mbox
curl -L "https://lore.kernel.org/linux-mm/${MSGID}/t.mbox.gz" | gunzip > thread.mbox

# Step 2: Process with b4 into git-am format
b4 am "${MSGID}"
# Output: ./20260421_fmayle_mm_limit_filemap_fault_readahead_to_vma_boundaries.mbx

# Step 3: Apply
# git am ./20260421_fmayle_*.mbx
```
