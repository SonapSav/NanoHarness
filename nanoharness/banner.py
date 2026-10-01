"""The startup panel: a logo slot and who-am-I on the left, sections on the right.

    ╭───────────────────────────────────────────────── NanoHarness v0.1.0 ─╮
    │          (logo)            │  Tools                                 │
    │                            │  files: read_file, write_file, ...     │
    │  aeroadvisor-agent:latest  │  Safety                                │
    │  ~/Development/testllm     │  sandbox: only the workdir is writable │
    │  session 20261001-152013   │  8 tools · /help for commands          │
    ╰──────────────────────────────────────────────────────────────────────╯

The right column is a list of sections (a title, then label: value rows), so more can be
added later (skills, flows) without touching the layout. Pure text in, lines out.
"""
import re
from pathlib import Path

ORANGE, BOLD, GREY, RED, RESET = "\033[38;5;208m", "\033[1m", "\033[90m", "\033[31m", "\033[0m"
ANSI = re.compile(r"\x1b\[[0-9;]*m")

LOGO_MAX = (36, 16)      # columns, rows: art bigger than this is trimmed
LOGO_SLOT = (30, 10)     # the space kept when there is no logo yet
TWO_COLUMNS = 90         # narrower terminals get one column


def visible(text):
    return len(ANSI.sub("", text))


def fit(text, width):
    """Cut plain text to `width` columns with an ellipsis."""
    return text if len(text) <= width else text[:max(0, width - 1)] + "…"


def load_logo(path):
    """The art as a list of lines, trimmed to LOGO_MAX; None if there is no file."""
    try:
        text = Path(path).expanduser().read_text(errors="replace")
    except OSError:
        return None
    lines = [ANSI.sub("", l).expandtabs().rstrip() for l in text.splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    while lines and not lines[0]:
        lines.pop(0)
    if not lines:
        return None
    width, rows = LOGO_MAX
    return [l[:width] for l in lines[:rows]]


def logo_block(logo):
    """The logo's lines, or the reserved slot with a small mark in the middle."""
    if logo:   # padded to one width, so centring moves the art as a block and keeps its shape
        width = max(len(l) for l in logo)
        return [f"{ORANGE}{l.ljust(width)}{RESET}" for l in logo]
    width, rows = LOGO_SLOT
    block = [""] * rows
    block[rows // 2] = f"{ORANGE}{'✻'.center(width)}{RESET}"
    return block


def section_lines(sections, width):
    """[(title, [(label, value), ...]), ...] and a summary -> coloured lines, cut to `width`."""
    out = []
    for i, (title, rows) in enumerate(sections):
        if title is None:                            # the summary line
            out += ["", f"{GREY}{fit(rows, width)}{RESET}"]
            continue
        if i:
            out.append("")
        out.append(f"{ORANGE}{BOLD}{fit(title, width)}{RESET}")
        for label, value in rows:
            plain = fit(f"{label}: {value}", width)
            cut = len(label) + 2
            colour = RED if value.startswith("!") else ""
            out.append(f"{GREY}{plain[:cut]}{RESET}{colour}{plain[cut:].lstrip('!')}{RESET}")
    return out


def panel(title, info, sections, logo, columns):
    """The whole panel as lines. `info` is the left column's text under the logo."""
    width = max(40, columns - 1)            # full width, like the input box; never the last column
    inner = width - 4                                # "│ " and " │"
    art = logo_block(logo)
    art_width = max([visible(l) for l in art] + [LOGO_SLOT[0] if not logo else 0])
    if width >= TWO_COLUMNS:
        left_width = max(art_width, max(visible(l) for l in info)) + 2
        left_width = min(left_width, inner // 2)
        right_width = inner - left_width - 2
        left = [centre(l, left_width) for l in art] + [""] + [centre(fit_visible(l, left_width), left_width)
                                                              for l in info]
        right = section_lines(sections, right_width)
        rows = max(len(left), len(right))
        left += [""] * (rows - len(left))
        right += [""] * (rows - len(right))
        body = [pad(l, left_width) + "  " + pad(r, right_width) for l, r in zip(left, right)]
    else:
        body = []
        if logo and art_width <= inner:    # stacked, an empty slot would only cost rows
            body += [centre(l, inner) for l in art] + [""]
        body += [fit_visible(l, inner) for l in info] + [""] + section_lines(sections, inner)
    label = f" {title} "
    top = f"{ORANGE}╭{'─' * (width - 3 - len(label))}{BOLD}{label}{RESET}{ORANGE}─╮{RESET}"
    rows = [f"{ORANGE}│{RESET} {pad(line, inner)} {ORANGE}│{RESET}" for line in body]
    bottom = f"{ORANGE}╰{'─' * (width - 2)}╯{RESET}"
    return [top, *rows, bottom]


def pad(text, width):
    return text + " " * max(0, width - visible(text))


def centre(text, width):
    space = max(0, width - visible(text))
    return " " * (space // 2) + text + " " * (space - space // 2)


def fit_visible(text, width):
    """Like fit, for a line that may carry colour: cut on its plain text, keep its colour."""
    plain = ANSI.sub("", text)
    if len(plain) <= width:
        return text
    codes = "".join(ANSI.findall(text)[:1])
    return f"{codes}{fit(plain, width)}{RESET}"
