"""Offline tests for the startup panel."""
import re

from nanoharness import banner


def plain(lines):
    return [re.sub(r"\x1b\[[0-9;]*m", "", l) for l in lines]


INFO = ["model-x", "~/project", "session 1"]
SECTIONS = [("Tools", [("files", "read_file, write_file")]),
            ("Safety", [("permissions", "!none asked (--yolo)")]),
            (None, "2 tools · /help for commands")]


def test_two_columns_fit_and_title_sits_in_the_top_border():
    lines = plain(banner.panel("NanoHarness v9", INFO, SECTIONS, None, 100))
    assert all(len(l) == 99 for l in lines)
    assert lines[0].endswith(" NanoHarness v9 ─╮")
    assert any("model-x" in l and "Tools" not in l for l in lines)
    assert any("files: read_file, write_file" in l for l in lines)
    assert len(lines) == banner.LOGO_SLOT[1] + 1 + len(INFO) + 2        # slot kept, no logo yet


def test_narrow_terminal_stacks_and_skips_an_empty_logo_slot():
    lines = plain(banner.panel("T", INFO, SECTIONS, None, 60))
    assert all(len(l) == 59 for l in lines)
    assert lines[1].strip(" │") == "model-x"                          # no reserved rows


def test_logo_is_trimmed_and_keeps_its_shape(tmp_path):
    art = tmp_path / "logo.txt"
    art.write_text("\n  /\\\n /  \\\n" + "x" * 50 + "\n" + "\n".join(["y"] * 20) + "\n\n")
    logo = banner.load_logo(art)
    assert logo[0] == "  /\\" and len(logo) == banner.LOGO_MAX[1]
    assert max(map(len, logo)) == banner.LOGO_MAX[0]
    block = plain(banner.logo_block(logo))
    assert len({len(l) for l in block}) == 1                        # one width: centred as a block
    assert banner.load_logo(tmp_path / "missing.txt") is None


def test_long_values_are_cut_and_warnings_are_red():
    lines = banner.section_lines([("S", [("k", "v" * 100)]), ("Safety", [("yolo", "!on")])], 30)
    assert all(banner.visible(l) <= 30 for l in lines) and plain(lines)[1].endswith("…")
    assert "\x1b[31mon" in lines[-1]


def test_panel_spans_the_full_width_on_a_wide_terminal():
    """Seen: capped at 140 columns, it stopped short of the input box below it."""
    lines = plain(banner.panel("T", INFO, SECTIONS, None, 210))
    assert {len(l) for l in lines} == {209}
