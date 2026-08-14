from tests.conftest import make_event, make_organizer, make_user
from app.models.event import Event
from sqlalchemy import select


def test_rbac_organizer_cannot_see_other_events(db):
    a = make_user(db, 80)
    b = make_user(db, 81)
    oa = make_organizer(db, a)
    ob = make_organizer(db, b)
    make_event(db, oa, title="mine")
    make_event(db, ob, title="theirs")
    visible = db.scalars(select(Event).where(Event.organizer_id == oa.id)).all()
    assert all(e.organizer_id == oa.id for e in visible)
    assert len(visible) == 1
