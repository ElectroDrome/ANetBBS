# anetbbs/features/file_areas.py
"""Boot-time self-healing for file-area numbering -- mirrors
anetbbs/features/games.py's own resolve_sort_order_collisions() exactly,
scoped to FileArea/FileAreaCategory instead of Game/GameCategory. See
that function's own docstring for the full real-bug writeup (a
bulk-imported network leaving every area at the sort_order column's own
0 default) and the games-vs-categories policy split this mirrors:

AREAS: only a GENUINE sort_order collision within the same category gets
fixed, and even then only within an as_submenu=True category's own
submenu page does the whole category get a full clean 1, 2, 3, ...
resequence (areas not in a submenu category keep the more conservative
bump-the-duplicates fix, since a top-level area's own number could in
principle be hardcoded into a sysop's own door/menu art the same way a
game's can).

CATEGORIES: every as_submenu=True category that currently shows a
numbered stand-in line at all (has at least one visible active area) is
always resequenced to a clean 1, 2, 3, ... run on every boot.
"""


def resolve_file_area_sort_order_collisions():
    from ..models import db, FileArea, FileAreaCategory
    changed = False

    active_areas = FileArea.query.filter_by(is_active=True).order_by(
        FileArea.sort_order, FileArea.tag).all()
    submenu_cat_slugs = {c.slug for c in FileAreaCategory.query.filter_by(
        as_submenu=True).all()}
    areas_by_category = {}
    for a in active_areas:
        areas_by_category.setdefault(a.category or 'other', []).append(a)
    for cat_slug, areas in areas_by_category.items():
        if cat_slug in submenu_cat_slugs:
            for i, a in enumerate(areas, start=1):
                if a.sort_order != i:
                    a.sort_order = i
                    changed = True
            continue
        orders = [a.sort_order or 0 for a in areas]
        if len(set(orders)) == len(orders):
            continue
        seen = set()
        next_free = max(orders)
        for a in areas:
            so = a.sort_order or 0
            if so in seen:
                next_free += 10
                a.sort_order = next_free
                changed = True
            else:
                seen.add(so)

    active_cat_slugs = {a.category for a in active_areas}
    numbered_cats = [c for c in FileAreaCategory.query.filter_by(as_submenu=True)
                     .order_by(FileAreaCategory.sort_order, FileAreaCategory.name).all()
                     if c.slug in active_cat_slugs]
    for i, c in enumerate(numbered_cats, start=1):
        if c.sort_order != i:
            c.sort_order = i
            changed = True

    if changed:
        db.session.commit()
    return changed
