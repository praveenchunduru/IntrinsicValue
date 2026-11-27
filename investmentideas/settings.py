"""Saved settings: the two values a run needs, kept in a local `.env` file.

The file belongs to the user. A run only reads it: it never prints a value and never
replaces a value the environment already holds. Only the names in NAMES are read; every
other line is ignored. The file is written in one place, `--setup`, which the user runs
on purpose; it writes only those names, keeps every other line, and makes the file
readable by its owner alone.
"""
import os
from pathlib import Path

ENV_FILE = '.env'
NAMES = ('OPENROUTER_API_KEY', 'SEC_CONTACT_EMAIL')
MAX_BYTES = 16_384


def _assigned_name(raw):
    """The setting a `.env` line assigns, or '' for a comment, a blank or anything else."""
    line = raw.strip()
    if not line or line.startswith('#'):
        return ''
    if line.startswith('export ') or line.startswith('export\t'):
        line = line[6:].lstrip()
    name, separator, _ = line.partition('=')
    return name.strip() if separator else ''


def read_env_file(path=ENV_FILE):
    """Return {name: value} for the supported names in a `.env` file, or {} if there is none.

    A line is NAME=value, optionally after `export`, optionally quoted. Blank lines,
    comments, other names and empty values are skipped. A file that cannot be used
    raises ValueError naming the file and the line, never the value.
    """
    file = Path(path)
    if not file.is_file():
        return {}
    try:
        if file.stat().st_size > MAX_BYTES:
            raise ValueError(f'{file} is larger than {MAX_BYTES // 1024} KB; it should hold only a few settings')
        text = file.read_text(encoding='utf-8-sig')
    except UnicodeDecodeError:
        raise ValueError(f'{file} is not UTF-8 text') from None
    except OSError as exc:
        raise ValueError(f'{file} cannot be read: {exc.strerror or type(exc).__name__}') from None
    values = {}
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export ') or line.startswith('export\t'):
            line = line[6:].lstrip()
        name, separator, value = line.partition('=')
        name = name.strip()
        if not separator or name not in NAMES:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
            value = value[1:-1]
        else:
            # An unquoted value ends at a comment: KEY=abc  # my key
            for marker in (' #', '\t#'):
                if marker in value:
                    value = value.split(marker, 1)[0].rstrip()
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError(f'{file} line {number}: {name} contains a control character')
        if value:
            values[name] = value
    return values


def apply_env_file(environ=None, path=ENV_FILE):
    """Fill the supported names the environment does not already set; return the names filled."""
    environ = os.environ if environ is None else environ
    filled = []
    for name, value in read_env_file(path).items():
        if not environ.get(name, '').strip():
            environ[name] = value
            filled.append(name)
    return filled


def describe(filled, path=ENV_FILE):
    """One line saying which settings came from the file, without their values."""
    return f'Settings: {" and ".join(filled)} read from {path}' if filled else ''


def save_env_file(values, path=ENV_FILE):
    """Write the given settings into the `.env` file and return its full path.

    Lines for other names, comments and blank lines are kept as they are. The file is
    replaced in one step and left readable by its owner only.
    """
    file = Path(path)
    if file.is_dir():
        raise ValueError(f'{file} is a folder; settings need a file of that name')
    for name, value in values.items():
        if name not in NAMES:
            raise ValueError(f'{name} is not a setting this program saves')
        if not value or any(ord(char) < 33 or ord(char) == 127 or char in '"\'#\\' for char in value):
            raise ValueError(f'{name} is empty or contains a space, quote, backslash, # or control character; '
                             f'to use such a value, put it in {file} by hand inside quotes')
    kept = []
    if file.is_file():
        if file.stat().st_size > MAX_BYTES:
            raise ValueError(f'{file} is larger than {MAX_BYTES // 1024} KB; it should hold only a few settings')
        try:
            existing = file.read_text(encoding='utf-8-sig')
        except UnicodeDecodeError:
            raise ValueError(f'{file} is not UTF-8 text; move it aside and run --setup again') from None
        kept = [line for line in existing.splitlines() if _assigned_name(line) not in values]
    text = '\n'.join(kept + [f'{name}={value}' for name, value in values.items()]) + '\n'
    temporary = file.with_name(file.name + '.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
        handle.write(text)
    try:
        os.chmod(temporary, 0o600)  # the mode above is ignored when a stale temporary file already existed
    except OSError:
        pass  # a filesystem without owner-only permissions
    os.replace(temporary, file)
    return file.resolve()


def run_setup(path=ENV_FILE, *, ask=None, ask_secret=None, interactive=None):
    """Ask for the key and the email once and save them; return the line to show the user.

    Pressing Enter at a prompt keeps the value already saved. Nothing typed is echoed back.
    """
    import getpass
    import sys
    from .sec_http import _contact
    if interactive is None:
        interactive = sys.stdin.isatty()
    if not interactive:
        raise ValueError('--setup needs a terminal to type into. Without one, copy .env.example to .env '
                         'and fill in the two values.')
    saved = read_env_file(path)
    ask, ask_secret = ask or input, ask_secret or getpass.getpass

    def keep(name):
        return ' (press Enter to keep the one already saved)' if saved.get(name) else ''

    key = ask_secret('OpenRouter API key, from https://openrouter.ai/settings/keys. '
                     f'Typing is hidden{keep("OPENROUTER_API_KEY")}: ').strip()
    email = ask('Your email. The SEC asks automated requests for a contact; it is sent only to sec.gov'
                f'{keep("SEC_CONTACT_EMAIL")}: ').strip()
    values = {}
    if key:
        values['OPENROUTER_API_KEY'] = key
    if email:
        values['SEC_CONTACT_EMAIL'] = _contact(email)
    if not values:
        raise ValueError('nothing was entered, so nothing was saved')
    file = save_env_file(values, path)
    missing = [name for name in NAMES if name not in values and not saved.get(name)]
    still = f' Still missing: {" and ".join(missing)}; run --setup again to add it.' if missing else ''
    return (f'Saved {" and ".join(values)} in {file}, readable only by you.{still}\n'
            'Next, name a ticker: python3 -m investmentideas MSFT')
