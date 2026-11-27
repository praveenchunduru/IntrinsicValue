# Changelog

User-visible changes, newest first. Update with `git pull`; `python3 -m intrinsicvalue --version` shows the version you have.

## 0.3.0 (2026-11-27)

- `python3 -m intrinsicvalue` and an installed `intrinsicvalue` command, beside the existing `investmentideas`.
- `--setup` saves the OpenRouter key and SEC email once in `.env`; every run reads it.
- Checks before any work: the ticker is in the SEC company list (a company name gets ticker suggestions), the SEC email is set, and OpenRouter accepts the key. A mistyped key now fails in a second.
- `--dry-run` collects evidence and checks inputs with no key and no charge. Without a key and without `--dry-run`, a run stops at once with the setup command.
- Progress shows plain steps ("Step 4 of 9: writing the forecast"), time and money so far. Every run ends with the value, the cost, the memo path and the next step.
- Each run writes `memo.html` beside `memo.md`: a readable, self-contained page that prints to PDF. `--open` opens it.
- `--list` lists past runs. `--revalue` prints bear, base and bull values beside the saved ones.
- An unfinished run resumes within 24 hours, across midnight UTC.
- A refused OpenRouter call (HTTP 400, 401, 402 or 403) no longer counts against the allowance.
- Ctrl+C stops cleanly and says how to resume. A busy port, a bad port and a Python without web certificates get one-line fixes.
- Browser form: shorter, with specific error messages, a $20 limit in the form and the server, a saved key and email for the session, a Stop button, the rendered memo, and progress that survives a reload. The form opens in your browser automatically (`--no-browser` to skip).
- Python 3.9 is supported.
