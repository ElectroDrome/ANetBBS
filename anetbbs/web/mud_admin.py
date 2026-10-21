# anetbbs/web/mud_admin.py
"""Admin UI for The Shattered Realm's world content -- rooms/exits,
items, and mob templates. Plain Flask CRUD routes, modeled directly on
menu_admin.py's own list/new/edit/delete shape (plain request.form.get()
reads, not WTForms -- matching that file's own established convention
rather than introducing a second admin-form pattern for no reason).

No visual room-graph/map editor -- confirmed there isn't one anywhere
in this codebase to model after (the ANSI editor is a character-cell
canvas, not a node graph; the BBS menu tree admin page is a plain
nested <ul>). A sysop manages the room graph the same way they already
manage the BbsMenu tree: a flat list plus per-room exit rows, no visual
map needed.

Routes mounted under /admin/mud/.
"""
from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required

from ..models import (db, MudRoom, MudExit, MudItemTemplate, MudMobTemplate,
                      MudQuestTemplate)
from ..features.mud.classes import CLASS_SLUGS
from .access_control import require_admin as _admin_required

mud_admin_bp = Blueprint('mud_admin', __name__, url_prefix='/admin/mud')

ITEM_TYPE_CHOICES = ['weapon', 'armor', 'consumable', 'quest', 'misc']
FEATURE_CHOICES = ['', 'shop', 'healer']
REQUIRED_CLASS_CHOICES = [''] + list(CLASS_SLUGS)


@mud_admin_bp.route('/')
@login_required
@_admin_required
def index():
    return render_template(
        'mud_admin/index.html',
        room_count=MudRoom.query.count(),
        item_count=MudItemTemplate.query.count(),
        mob_count=MudMobTemplate.query.count(),
        quest_count=MudQuestTemplate.query.count())


# ---------------------------------------------------------------------------
# Rooms + exits
# ---------------------------------------------------------------------------

@mud_admin_bp.route('/rooms')
@login_required
@_admin_required
def list_rooms():
    rooms = MudRoom.query.order_by(MudRoom.zone, MudRoom.name).all()
    return render_template('mud_admin/rooms_list.html', rooms=rooms)


@mud_admin_bp.route('/rooms/new', methods=['GET', 'POST'])
@login_required
@_admin_required
def new_room():
    if request.method == 'POST':
        slug = (request.form.get('slug') or '').strip()
        name = (request.form.get('name') or '').strip()
        if not slug or not name:
            flash('Slug and name are required.', 'danger')
            return redirect(url_for('mud_admin.new_room'))
        if MudRoom.query.filter_by(slug=slug).first():
            flash(f'A room with slug "{slug}" already exists.', 'danger')
            return redirect(url_for('mud_admin.new_room'))
        mob_template_id = request.form.get('mob_template_id', type=int) or None
        room = MudRoom(
            slug=slug, name=name,
            zone=(request.form.get('zone') or 'village').strip(),
            description=(request.form.get('description') or '').strip(),
            mob_template_id=mob_template_id,
            feature=(request.form.get('feature') or '').strip() or None)
        db.session.add(room)
        db.session.commit()
        flash(f'Room "{name}" created.', 'success')
        return redirect(url_for('mud_admin.edit_room', room_id=room.id))
    mobs = MudMobTemplate.query.order_by(MudMobTemplate.name).all()
    return render_template('mud_admin/room_form.html', room=None, mobs=mobs,
                           feature_choices=FEATURE_CHOICES, all_rooms=[])


@mud_admin_bp.route('/rooms/<int:room_id>/edit', methods=['GET', 'POST'])
@login_required
@_admin_required
def edit_room(room_id):
    room = MudRoom.query.get_or_404(room_id)
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        if not name:
            flash('Name is required.', 'danger')
            return redirect(url_for('mud_admin.edit_room', room_id=room.id))
        room.name = name
        room.zone = (request.form.get('zone') or room.zone).strip()
        room.description = (request.form.get('description') or '').strip()
        room.mob_template_id = request.form.get('mob_template_id', type=int) or None
        room.feature = (request.form.get('feature') or '').strip() or None
        db.session.commit()
        flash('Room saved.', 'success')
        return redirect(url_for('mud_admin.edit_room', room_id=room.id))
    mobs = MudMobTemplate.query.order_by(MudMobTemplate.name).all()
    all_rooms = (MudRoom.query.filter(MudRoom.id != room.id)
                .order_by(MudRoom.zone, MudRoom.name).all())
    return render_template('mud_admin/room_form.html', room=room, mobs=mobs,
                           feature_choices=FEATURE_CHOICES, all_rooms=all_rooms)


@mud_admin_bp.route('/rooms/<int:room_id>/delete', methods=['POST'])
@login_required
@_admin_required
def delete_room(room_id):
    room = MudRoom.query.get_or_404(room_id)
    name = room.name
    MudExit.query.filter(
        (MudExit.room_id == room.id) | (MudExit.target_room_id == room.id)
    ).delete(synchronize_session=False)
    db.session.delete(room)
    db.session.commit()
    flash(f'Room "{name}" (and its exits) deleted.', 'success')
    return redirect(url_for('mud_admin.list_rooms'))


@mud_admin_bp.route('/rooms/<int:room_id>/exits/new', methods=['POST'])
@login_required
@_admin_required
def new_exit(room_id):
    room = MudRoom.query.get_or_404(room_id)
    direction = (request.form.get('direction') or '').strip().lower()
    target_room_id = request.form.get('target_room_id', type=int)
    if not direction or not target_room_id:
        flash('Direction and target room are required.', 'danger')
        return redirect(url_for('mud_admin.edit_room', room_id=room.id))
    if MudExit.query.filter_by(room_id=room.id, direction=direction).first():
        flash(f'This room already has a "{direction}" exit.', 'danger')
        return redirect(url_for('mud_admin.edit_room', room_id=room.id))
    db.session.add(MudExit(room_id=room.id, direction=direction,
                           target_room_id=target_room_id))
    db.session.commit()
    flash('Exit added.', 'success')
    return redirect(url_for('mud_admin.edit_room', room_id=room.id))


@mud_admin_bp.route('/rooms/<int:room_id>/exits/<int:exit_id>/delete', methods=['POST'])
@login_required
@_admin_required
def delete_exit(room_id, exit_id):
    exit_row = MudExit.query.filter_by(id=exit_id, room_id=room_id).first_or_404()
    db.session.delete(exit_row)
    db.session.commit()
    flash('Exit removed.', 'success')
    return redirect(url_for('mud_admin.edit_room', room_id=room_id))


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------

@mud_admin_bp.route('/items')
@login_required
@_admin_required
def list_items():
    items = MudItemTemplate.query.order_by(MudItemTemplate.name).all()
    return render_template('mud_admin/items_list.html', items=items)


def _populate_item(item, form):
    item.name = (form.get('name') or '').strip()
    item.description = (form.get('description') or '').strip()
    item.item_type = form.get('item_type') or 'misc'
    item.attack_bonus = form.get('attack_bonus', 0, type=int)
    item.defense_bonus = form.get('defense_bonus', 0, type=int)
    item.heal_amount = form.get('heal_amount', 0, type=int)
    item.value = form.get('value', 0, type=int)
    item.is_shop_stock = bool(form.get('is_shop_stock'))
    item.required_class = (form.get('required_class') or '').strip() or None


@mud_admin_bp.route('/items/new', methods=['GET', 'POST'])
@login_required
@_admin_required
def new_item():
    if request.method == 'POST':
        slug = (request.form.get('slug') or '').strip()
        if not slug or not (request.form.get('name') or '').strip():
            flash('Slug and name are required.', 'danger')
            return redirect(url_for('mud_admin.new_item'))
        if MudItemTemplate.query.filter_by(slug=slug).first():
            flash(f'An item with slug "{slug}" already exists.', 'danger')
            return redirect(url_for('mud_admin.new_item'))
        item = MudItemTemplate(slug=slug)
        _populate_item(item, request.form)
        db.session.add(item)
        db.session.commit()
        flash(f'Item "{item.name}" created.', 'success')
        return redirect(url_for('mud_admin.list_items'))
    return render_template('mud_admin/item_form.html', item=None,
                           item_type_choices=ITEM_TYPE_CHOICES,
                           required_class_choices=REQUIRED_CLASS_CHOICES)


@mud_admin_bp.route('/items/<int:item_id>/edit', methods=['GET', 'POST'])
@login_required
@_admin_required
def edit_item(item_id):
    item = MudItemTemplate.query.get_or_404(item_id)
    if request.method == 'POST':
        _populate_item(item, request.form)
        db.session.commit()
        flash('Item saved.', 'success')
        return redirect(url_for('mud_admin.list_items'))
    return render_template('mud_admin/item_form.html', item=item,
                           item_type_choices=ITEM_TYPE_CHOICES,
                           required_class_choices=REQUIRED_CLASS_CHOICES)


@mud_admin_bp.route('/items/<int:item_id>/delete', methods=['POST'])
@login_required
@_admin_required
def delete_item(item_id):
    item = MudItemTemplate.query.get_or_404(item_id)
    name = item.name
    db.session.delete(item)
    db.session.commit()
    flash(f'Item "{name}" deleted.', 'success')
    return redirect(url_for('mud_admin.list_items'))


# ---------------------------------------------------------------------------
# Mobs
# ---------------------------------------------------------------------------

@mud_admin_bp.route('/mobs')
@login_required
@_admin_required
def list_mobs():
    mobs = MudMobTemplate.query.order_by(MudMobTemplate.level, MudMobTemplate.name).all()
    return render_template('mud_admin/mobs_list.html', mobs=mobs)


def _populate_mob(mob, form):
    mob.name = (form.get('name') or '').strip()
    mob.description = (form.get('description') or '').strip()
    mob.level = form.get('level', 1, type=int)
    mob.hp = form.get('hp', 10, type=int)
    mob.attack = form.get('attack', 3, type=int)
    mob.defense = form.get('defense', 0, type=int)
    mob.xp_reward = form.get('xp_reward', 10, type=int)
    mob.gold_reward = form.get('gold_reward', 5, type=int)


@mud_admin_bp.route('/mobs/new', methods=['GET', 'POST'])
@login_required
@_admin_required
def new_mob():
    if request.method == 'POST':
        slug = (request.form.get('slug') or '').strip()
        if not slug or not (request.form.get('name') or '').strip():
            flash('Slug and name are required.', 'danger')
            return redirect(url_for('mud_admin.new_mob'))
        if MudMobTemplate.query.filter_by(slug=slug).first():
            flash(f'A mob with slug "{slug}" already exists.', 'danger')
            return redirect(url_for('mud_admin.new_mob'))
        mob = MudMobTemplate(slug=slug)
        _populate_mob(mob, request.form)
        db.session.add(mob)
        db.session.commit()
        flash(f'Mob "{mob.name}" created.', 'success')
        return redirect(url_for('mud_admin.list_mobs'))
    return render_template('mud_admin/mob_form.html', mob=None)


@mud_admin_bp.route('/mobs/<int:mob_id>/edit', methods=['GET', 'POST'])
@login_required
@_admin_required
def edit_mob(mob_id):
    mob = MudMobTemplate.query.get_or_404(mob_id)
    if request.method == 'POST':
        _populate_mob(mob, request.form)
        db.session.commit()
        flash('Mob saved.', 'success')
        return redirect(url_for('mud_admin.list_mobs'))
    return render_template('mud_admin/mob_form.html', mob=mob)


@mud_admin_bp.route('/mobs/<int:mob_id>/delete', methods=['POST'])
@login_required
@_admin_required
def delete_mob(mob_id):
    mob = MudMobTemplate.query.get_or_404(mob_id)
    if MudRoom.query.filter_by(mob_template_id=mob.id).first():
        flash(f'Cannot delete "{mob.name}" -- at least one room still '
             f'spawns it. Update those rooms first.', 'danger')
        return redirect(url_for('mud_admin.list_mobs'))
    name = mob.name
    db.session.delete(mob)
    db.session.commit()
    flash(f'Mob "{name}" deleted.', 'success')
    return redirect(url_for('mud_admin.list_mobs'))


# ---------------------------------------------------------------------------
# Quests
# ---------------------------------------------------------------------------

@mud_admin_bp.route('/quests')
@login_required
@_admin_required
def list_quests():
    quests = MudQuestTemplate.query.order_by(MudQuestTemplate.name).all()
    return render_template('mud_admin/quests_list.html', quests=quests)


def _populate_quest(quest, form):
    quest.name = (form.get('name') or '').strip()
    quest.description = (form.get('description') or '').strip()
    quest.giver_room_id = form.get('giver_room_id', type=int)
    quest.target_mob_slug = (form.get('target_mob_slug') or '').strip()
    quest.target_count = form.get('target_count', 1, type=int)
    quest.reward_xp = form.get('reward_xp', 0, type=int)
    quest.reward_gold = form.get('reward_gold', 0, type=int)
    quest.reward_item_slug = (form.get('reward_item_slug') or '').strip() or None


@mud_admin_bp.route('/quests/new', methods=['GET', 'POST'])
@login_required
@_admin_required
def new_quest():
    if request.method == 'POST':
        slug = (request.form.get('slug') or '').strip()
        if not slug or not (request.form.get('name') or '').strip():
            flash('Slug and name are required.', 'danger')
            return redirect(url_for('mud_admin.new_quest'))
        if MudQuestTemplate.query.filter_by(slug=slug).first():
            flash(f'A quest with slug "{slug}" already exists.', 'danger')
            return redirect(url_for('mud_admin.new_quest'))
        if not request.form.get('giver_room_id', type=int):
            flash('A giver room is required.', 'danger')
            return redirect(url_for('mud_admin.new_quest'))
        quest = MudQuestTemplate(slug=slug)
        _populate_quest(quest, request.form)
        db.session.add(quest)
        db.session.commit()
        flash(f'Quest "{quest.name}" created.', 'success')
        return redirect(url_for('mud_admin.list_quests'))
    rooms = MudRoom.query.order_by(MudRoom.zone, MudRoom.name).all()
    mobs = MudMobTemplate.query.order_by(MudMobTemplate.name).all()
    items = MudItemTemplate.query.order_by(MudItemTemplate.name).all()
    return render_template('mud_admin/quest_form.html', quest=None,
                           rooms=rooms, mobs=mobs, items=items)


@mud_admin_bp.route('/quests/<int:quest_id>/edit', methods=['GET', 'POST'])
@login_required
@_admin_required
def edit_quest(quest_id):
    quest = MudQuestTemplate.query.get_or_404(quest_id)
    if request.method == 'POST':
        if not request.form.get('giver_room_id', type=int):
            flash('A giver room is required.', 'danger')
            return redirect(url_for('mud_admin.edit_quest', quest_id=quest.id))
        _populate_quest(quest, request.form)
        db.session.commit()
        flash('Quest saved.', 'success')
        return redirect(url_for('mud_admin.list_quests'))
    rooms = MudRoom.query.order_by(MudRoom.zone, MudRoom.name).all()
    mobs = MudMobTemplate.query.order_by(MudMobTemplate.name).all()
    items = MudItemTemplate.query.order_by(MudItemTemplate.name).all()
    return render_template('mud_admin/quest_form.html', quest=quest,
                           rooms=rooms, mobs=mobs, items=items)


@mud_admin_bp.route('/quests/<int:quest_id>/delete', methods=['POST'])
@login_required
@_admin_required
def delete_quest(quest_id):
    quest = MudQuestTemplate.query.get_or_404(quest_id)
    name = quest.name
    db.session.delete(quest)
    db.session.commit()
    flash(f'Quest "{name}" deleted.', 'success')
    return redirect(url_for('mud_admin.list_quests'))
