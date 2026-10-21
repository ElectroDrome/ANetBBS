"""File Areas section (anetbbs-cfg) -- file areas and their categories.

`network_id` (which echomail network owns a given file echo) is shown
read-only in the list but not editable here -- reassigning it is rare and
interacts with TIC routing, left to the web admin for now.

Categories mirror anetbbs/cfg/sections/games.py's own GameCategory panel
exactly (same field shape, same [+]/[-] adjacent-swap reorder convention)
-- added because file areas had no grouping concept at all until a sysop
pointed out the gap.
"""
from sqlalchemy.exc import IntegrityError

from anetbbs.cfg import ui
from anetbbs.models import db, FileArea, FileAreaCategory

FIELDS = [
    {"key": "tag", "label": "Tag", "kind": "text"},
    {"key": "name", "label": "Name", "kind": "text_nullable"},
    {"key": "description", "label": "Description", "kind": "text_nullable"},
    {"key": "storage_path", "label": "Storage Path", "kind": "text_nullable"},
    {"key": "category", "label": "Category (slug)", "kind": "text_nullable"},
    {"key": "sort_order", "label": "Sort Order", "kind": "int"},
    {"key": "min_access_level", "label": "Min Access Level", "kind": "int"},
    {"key": "min_write_level", "label": "Min Upload Level", "kind": "int_nullable"},
    {"key": "upload_permission", "label": "Upload Permission", "kind": "choice",
     "choices": ["users", "sysop", "none"]},
    {"key": "is_active", "label": "Active", "kind": "bool"},
    {"key": "is_subscribed", "label": "Subscribed (receive from upstream)", "kind": "bool"},
    {"key": "is_sysop_only", "label": "Sysop Only", "kind": "bool"},
    {"key": "password", "label": "Area Password", "kind": "text_nullable"},
    {"key": "freq_enabled", "label": "Allow WaZOO FREQ", "kind": "bool"},
    {"key": "freq_password", "label": "FREQ Password (blank = none required)", "kind": "text_nullable"},
]

HELP = [
    "Min Upload Level blank = same as Min Access Level.",
    "Network assignment (TIC routing): web admin only.",
    "FREQ: lets other BBSes pull files from this area via WaZOO",
    "file requests (FTS-0006) over BinkP -- see docs/06-echomail.md.",
]

NEW_DEFAULTS = {
    "tag": "", "name": None, "description": None, "storage_path": None,
    "category": "other", "sort_order": 0,
    "min_access_level": 10, "min_write_level": None, "upload_permission": "users",
    "is_active": True, "is_subscribed": True, "is_sysop_only": False, "password": None,
    "freq_enabled": False, "freq_password": None,
}

COLUMNS = [
    ("Order", 6, lambda a: a.sort_order),
    ("Tag", 18, lambda a: a.tag),
    ("Name", 20, lambda a: a.name or ""),
    ("Category", 12, lambda a: a.category or ""),
    ("MinLvl", 7, lambda a: a.min_access_level),
    ("Active", 6, lambda a: "Yes" if a.is_active else "No"),
]

CATEGORY_FIELDS = [
    {"key": "name", "label": "Name", "kind": "text"},
    {"key": "slug", "label": "Slug (URL-safe)", "kind": "text"},
    {"key": "sort_order", "label": "Sort Order", "kind": "int"},
    {"key": "as_submenu", "label": "Show As Submenu Section", "kind": "bool"},
]

CATEGORY_NEW_DEFAULTS = {"name": "", "slug": "", "sort_order": 0, "as_submenu": False}

CATEGORY_COLUMNS = [
    ("Order", 6, lambda c: c.sort_order),
    ("Name", 24, lambda c: c.name),
    ("Slug", 24, lambda c: c.slug),
    ("Submenu", 8, lambda c: "Yes" if c.as_submenu else "No"),
]


def list_file_areas():
    return FileArea.query.order_by(FileArea.sort_order, FileArea.tag).all()


def values_from_area(a):
    return {f["key"]: getattr(a, f["key"]) for f in FIELDS}


def create_file_area(data):
    a = FileArea(**data)
    db.session.add(a)
    db.session.commit()
    return a


def update_file_area(a, data):
    for k, v in data.items():
        setattr(a, k, v)
    db.session.commit()


def delete_file_area(a):
    db.session.delete(a)
    db.session.commit()


def list_categories():
    return FileAreaCategory.query.order_by(
        FileAreaCategory.sort_order, FileAreaCategory.name).all()


def values_from_category(c):
    return {f["key"]: getattr(c, f["key"]) for f in CATEGORY_FIELDS}


def create_category(data):
    c = FileAreaCategory(**data)
    db.session.add(c)
    db.session.commit()
    return c


def update_category(c, data):
    for k, v in data.items():
        setattr(c, k, v)
    db.session.commit()


def delete_category(c):
    db.session.delete(c)
    db.session.commit()


def _add(stdscr):
    data = ui.run_form(stdscr, "New File Area", FIELDS, dict(NEW_DEFAULTS), help_lines=HELP)
    if data is None:
        return
    if not data.get("tag"):
        ui.show_message(stdscr, "Tag is required.", error=True)
        return
    try:
        create_file_area(data)
    except IntegrityError:
        db.session.rollback()
        ui.show_message(stdscr, "A file area with that tag already exists.", error=True)


def _edit(stdscr, a):
    data = ui.run_form(stdscr, f"Edit File Area: {a.tag}", FIELDS, values_from_area(a), help_lines=HELP)
    if data is None:
        return
    try:
        update_file_area(a, data)
    except IntegrityError:
        db.session.rollback()
        ui.show_message(stdscr, "A file area with that tag already exists.", error=True)


def _delete(stdscr, a):
    text = (f"Delete file area '{a.tag}'?\n"
            "This removes the area definition only -- files already on disk\n"
            "under its storage path are NOT deleted.")
    if ui.confirm(stdscr, text):
        try:
            delete_file_area(a)
        except IntegrityError:
            db.session.rollback()
            ui.show_message(
                stdscr,
                "Could not delete: other records (uploads, TIC queue, subscriptions)\n"
                "still reference this area. Clear those first, or use the web admin.",
                error=True,
            )


def _reorder_area(stdscr, a, direction):
    """Move `a` one slot up (direction=-1) or down (+1) among areas
    CURRENTLY ORDERED BY sort_order -- swaps sort_order with whichever
    neighbor is there, same adjacent-swap convention games.py's own
    _reorder_game() already established. "Best we can for a TUI"
    equivalent of the web admin's mouse drag-and-drop."""
    ordered = list_file_areas()
    idx = ordered.index(a)
    new_idx = idx + direction
    if 0 <= new_idx < len(ordered):
        other = ordered[new_idx]
        a.sort_order, other.sort_order = other.sort_order, a.sort_order
        db.session.commit()


def _run_areas(stdscr):
    ui.run_list(
        stdscr, "File Areas", COLUMNS, list_file_areas,
        on_add=_add, on_edit=_edit, on_delete=_delete,
        on_reorder=_reorder_area,
    )


def _add_category(stdscr):
    data = ui.run_form(stdscr, "New Category", CATEGORY_FIELDS, dict(CATEGORY_NEW_DEFAULTS))
    if data is None:
        return
    if not data.get("name") or not data.get("slug"):
        ui.show_message(stdscr, "Name and Slug are required.", error=True)
        return
    try:
        create_category(data)
    except IntegrityError:
        db.session.rollback()
        ui.show_message(stdscr, "A category with that slug already exists.", error=True)


def _edit_category(stdscr, c):
    data = ui.run_form(stdscr, f"Edit Category: {c.name}", CATEGORY_FIELDS, values_from_category(c))
    if data is None:
        return
    try:
        update_category(c, data)
    except IntegrityError:
        db.session.rollback()
        ui.show_message(stdscr, "A category with that slug already exists.", error=True)


def _delete_category(stdscr, c):
    if ui.confirm(stdscr, f"Delete category '{c.name}'?"):
        delete_category(c)


def _reorder_category(stdscr, c, direction):
    """Same adjacent-swap convention as _reorder_area() above, scoped to
    FileAreaCategory.sort_order -- which matters because an
    as_submenu=True category's own sort_order is what's displayed as
    its menu number in the terminal file-area list (see
    features/bbs_ui.py's list_files())."""
    ordered = list_categories()
    idx = ordered.index(c)
    new_idx = idx + direction
    if 0 <= new_idx < len(ordered):
        other = ordered[new_idx]
        c.sort_order, other.sort_order = other.sort_order, c.sort_order
        db.session.commit()


def _run_categories(stdscr):
    ui.run_list(
        stdscr, "File Area Categories", CATEGORY_COLUMNS, list_categories,
        on_add=_add_category, on_edit=_edit_category, on_delete=_delete_category,
        on_reorder=_reorder_category,
    )


def run(stdscr):
    items = [("areas", "File Areas"), ("categories", "Categories")]
    while True:
        choice = ui.run_menu(stdscr, "File Areas", items)
        if choice is None:
            return
        if choice == "areas":
            _run_areas(stdscr)
        elif choice == "categories":
            _run_categories(stdscr)
