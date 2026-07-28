# jarvis v1.0 — passwords and authentication secrets

A complete inventory of every secret the system manages, how to
rotate each one, and what happens when you change it.

## summary

| Secret                       | Where it lives                  | How to change it                  | When you need to                |
|------------------------------|---------------------------------|-----------------------------------|---------------------------------|
| Master passcode              | `~/.jarvis/config.json` (override) + binary (fallback) | `--change-passcode` (v1.0+) | forgot it / want to rotate    |
| API keys (OpenRouter etc.)   | `~/.jarvis/config.json`         | `--set sonnet_api_key=...`        | key leaked or rotated upstream |
| Cloud account password       | cloud backend (PBKDF2 + Fernet) | `--change-cloud-password` (v1.0+) | forgot it / want to rotate    |
| Pairing code (6 digits)      | in-memory only                  | just regenerate with `--pair`    | expired (10 min TTL)            |
| Webcam face photo            | `~/.jarvis/face.jpg`            | re-register with `--auth-setup`   | new haircut / different person  |

## master passcode (a.k.a. "the passcode")

This is the fallback when Windows Hello and webcam auth fail or
aren't available. It's what keeps the system locked down.

### how it works

The auth gate accepts **either**:

1. The `passcode_override` from `~/.jarvis/config.json` (if set),
   or
2. The hardcoded fallback `_MASTER_PASSCODE` in the binary
   (always available, even on a fresh install with no config)

Either one is enough. The gate doesn't reveal which one matched,
to keep the security model opaque.

### how to change it (v1.0+)

```bat
jarvis --change-passcode
```

You'll be prompted for the current passcode, then the new one
(twice). The new passcode is saved to
`~/.jarvis/config.json` as `passcode_override`. The hardcoded
fallback in the binary still works too.

### how to clear the override

If you want to fall back to the hardcoded passcode only (e.g. you
set an override, then forgot it):

```bat
jarvis --reset             REM nukes the whole config
```

or edit `~/.jarvis/config.json` by hand and remove the
`passcode_override` key.

### what if I forget BOTH the override and the hardcoded?

You can't. The hardcoded `Soulreaper1v2@22` is in the binary
and can't be removed by jarvis itself. It's `jarvis`'s last-line
fallback so a fresh install always works.

This is also a security weakness: anyone with the binary can
extract the hardcoded passcode with `strings`. The fix is to
ship only the override-based version, but that breaks
out-of-the-box usability. We're tracking it for v1.1.

### can I rotate the hardcoded passcode in the binary?

Not at runtime. The hardcoded value is in `jarvis.py` line 111
(`_MASTER_PASSCODE = "Soulreaper1v2@22"`). To change it, you
have to:

1. Edit `jarvis.py` and change the value.
2. Rebuild the binary: `python3 jarvis.py --build`.
3. Redistribute the new binary.

The CLI doesn't have a way to do this; it would require
re-shipping the binary. We're tracking a "hash-pinned" version
for v2.0 where the binary doesn't include a hardcoded value at
all.

## API keys (OpenRouter, etc.)

The model API keys live in `~/.jarvis/config.json` as
`sonnet_api_key` and `codex_api_key`. The file is mode `0o600`
(owner-read-only on Unix).

### how to change them

```bat
jarvis --set sonnet_api_key=sk-or-v1-NEW-KEY-HERE
jarvis --set codex_api_key=sk-or-v1-NEW-KEY-HERE
```

Or edit `~/.jarvis/config.json` directly with your favorite editor.
Either way, the new value is persisted on the next save.

### what if a key leaks?

1. Rotate the key upstream (in the OpenRouter dashboard).
2. Update jarvis with `--set sonnet_api_key=NEW`.
3. (Optional) If you used cloud sync, the old key may be in the
   cloud blob. The cloud sync logic does NOT overwrite local
   keys on login (see `--cloud-login` docs), but the cloud blob
   is encrypted with your cloud password. If you suspect the
   cloud blob was tampered with, do `--cloud-logout` then
   `--change-cloud-password` to force a re-encryption with a
   fresh salt.

## cloud account password

If you've used `--cloud-signup`, you have an account on the cloud
backend. Your password encrypts your config blob on the server.
The server only ever sees ciphertext; it cannot read your config
without your password.

### how it works

- Password + a per-account salt → PBKDF2 (200,000 iterations, SHA-256)
  → 32-byte key
- Key + your config (JSON) → Fernet-like encrypt (HMAC-SHA-256)
  → ciphertext blob
- Blob is stored on the cloud backend under your account id
  (derived from email).

### how to change it (v1.0+)

```bat
jarvis --change-cloud-password
```

You'll be prompted for the current password, then the new one
(twice). The flow:

1. Sign in with the current password → fetch the existing blob
   → decrypt → use the (now-decrypted) config as the basis
2. Generate a new salt
3. Derive a new key from new-password + new-salt
4. Encrypt the config with the new key
5. PUT the new blob back to the server
6. Verify by signing in with the new password

If any step fails, the old password is still valid (we never
wrote the new blob).

### what if I forget the cloud password?

There's no password-recovery flow. The cloud backend stores
opaque ciphertext; if you lose the password, your config is
unrecoverable.

**Workaround:** on a machine where you're still signed in, you
have the local config. Run `--show-config` to see it (with keys
masked). Re-signup with a new email if needed — the old account
just sits on the server as dead ciphertext forever.

## pairing code (6 digits)

A 6-digit code shown on the laptop when you run `--serve` or
`--pair`. The phone types this code on the pairing page to
authenticate.

### security properties

- **Expires after 10 minutes.** If the phone doesn't connect
  within 10 min, run `--pair` again to get a fresh code.
- **One-time use.** Once a device pairs with a code, the code
  is cleared. Pairing a second device requires a new code.
- **No persistent password.** The pairing code is a one-time
  token, not a long-lived secret. The actual auth between paired
  devices is a device id in the `~/.jarvis/pairing.json` state
  file.

### how to change it

You don't. It's auto-generated each time you run `--pair` or
`--serve`. If you want to invalidate an existing pairing, use
`--unpair <device-id>`.

## webcam face photo

The registered face at `~/.jarvis/face.jpg`. Used by the
webcam auth layer (if `opencv-python` is installed and the photo
is present).

### how to change it

```bat
jarvis --auth-setup
```

Choose "yes" to register a new face photo. This overwrites
`~/.jarvis/face.jpg`. The new photo is captured the next time
the wizard runs.

### how to remove it

Delete the file:

```bat
del %USERPROFILE%\.jarvis\face.jpg
```

The webcam auth layer will then return `False` for everyone,
forcing the gate to fall through to the passcode.
