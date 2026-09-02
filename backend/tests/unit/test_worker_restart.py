
from tests.conftest import make_event, make_organizer, make_user
from app.services.scheduler import claim_due_jobs_sync, schedule_event_jobs_sync


def test_worker_restart_does_not_reclaim_running(db):
    host = make_user(db, 70)
    org = make_organizer(db, host)
    event = make_event(db, org)
    schedule_event_jobs_sync(db, event)
    first = claim_due_jobs_sync(db, "worker-a")
    # simulate restart: running jobs are not pending
    again = claim_due_jobs_sync(db, "worker-b")
    running = [j for j in first]
    assert all(j.id not in {x.id for x in again} for j in running)
