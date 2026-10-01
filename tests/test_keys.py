"""Offline tests for keyboard input: byte parsing, the line editor, menus."""
from nanoharness.keys import Key, KeyParser, LineEditor, Menu


def names(keys):
    return [k.name if k.name != "char" else k.text for k in keys]


def test_plain_text_controls_and_utf8_split_across_reads():
    p = KeyParser()
    assert names(p.feed(b"hi\r")) == ["h", "i", "enter"]
    euro = "€".encode()
    assert p.feed(euro[:1]) == [] and names(p.feed(euro[1:])) == ["€"]
    assert names(p.feed(b"\x7f\x03\x04\n")) == ["backspace", "ctrl-c", "ctrl-d", "newline"]


def test_escape_sequences_whole_and_split():
    p = KeyParser()
    assert names(p.feed(b"\x1b[A\x1b[B\x1bOC\x1b[3~\x1b\r")) == ["up", "down", "right", "delete", "newline"]
    assert p.feed(b"\x1b") == []                 # might be the start of a sequence
    assert names(p.feed(b"[D")) == ["left"]
    assert p.feed(b"\x1b") == [] and names(p.flush()) == ["esc"]   # nothing followed: Esc
    assert names(p.feed(b"\x1b[99;5u")) == ["ignored"]             # unknown CSI, skipped whole


def test_bracketed_paste_keeps_newlines_and_is_one_key():
    p = KeyParser()
    keys = p.feed(b"\x1b[200~line one\r\nline")
    assert keys == []
    keys = p.feed(b" two\x1b[201~x")
    assert keys[0] == Key("paste", "line one\nline two") and names(keys[1:]) == ["x"]


def test_editor_typing_multiline_and_submit():
    e = LineEditor()
    for ch in "fix it":
        e.key(Key("char", ch))
    e.key(Key("newline"))
    e.key(Key("paste", "log a\nlog b"))
    assert e.text == "fix it\nlog a\nlog b" and (e.row, e.col) == (2, 5)
    assert e.key(Key("enter")) == "submit"
    assert LineEditor().key(Key("enter")) is None       # nothing to send


def test_editor_cursor_moves_and_deletes():
    e = LineEditor()
    e.set_text("ab\ncd")
    e.key(Key("home"))
    e.key(Key("backspace"))                              # joins the lines
    assert e.text == "abcd" and (e.row, e.col) == (0, 2)
    e.key(Key("delete"))
    assert e.text == "abd"
    e.set_text("one two  three")
    e.key(Key("word-backspace"))
    assert e.text == "one two  "
    e.key(Key("ctrl-u"))
    assert e.text == ""


def test_history_with_up_and_down_keeps_the_draft():
    e = LineEditor(history=["first", "second"])
    e.key(Key("char", "x"))
    e.key(Key("up"))
    assert e.text == "second"
    e.key(Key("up"))
    assert e.text == "first"
    e.key(Key("down"))
    e.key(Key("down"))
    assert e.text == "x"
    e.remember("third")
    e.remember("third")
    assert e.history == ["first", "second", "third"]


def test_menu_arrows_shortcuts_and_cancel():
    m = Menu(["Yes", "Yes, always", "No"], shortcuts={"y": 0, "a": 1, "n": 2}, cancel=2)
    assert m.key(Key("down")) is None and m.index == 1
    assert m.key(Key("up")) is None and m.key(Key("up")) is None and m.index == 2   # wraps
    assert m.key(Key("enter")) == 2
    assert m.key(Key("char", "A")) == 1 and m.key(Key("esc")) == 2 and m.key(Key("char", "1")) == 0


def test_ctrl_o_is_a_key():
    assert names(KeyParser().feed(b"\x0f")) == ["ctrl-o"]
