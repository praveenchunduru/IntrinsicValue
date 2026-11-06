# Portable runner

Owner-authorized OpenRouter API integration. Python standard library only at
runtime. Keep the local interface on loopback; preserve host checks and CSRF token.
Never save API keys or echo provider response bodies on errors. Persist spending
reservations before requests and preserve them if billing is uncertain. Mock model
calls in CI; live paid calls require an explicitly supplied OpenRouter key.
