# Security

If you find a security problem in Gobo, please don't open a public issue. Use **Report a vulnerability** on this repo's Security tab (GitHub's private reporting), and we'll answer you there.

Gobo handles Comfy API keys. On a public demo (`STUDIO_KEYS=visitor`) a visitor's key is held in the server's memory only: never on disk, in a cookie or in a log. Anything that could expose a key, spend someone else's credits, or let one visitor see another's work is a security problem, and we want to hear about it.
