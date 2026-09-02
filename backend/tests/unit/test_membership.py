
from app.services.requirements import Checklist, CheckItem
from app.core.enums import RequirementStatus


def test_membership_checklist_incomplete():
    c = Checklist()
    c.add(CheckItem("channel_membership", "کانال", RequirementStatus.NOT_DONE, "عضو نیستید"))
    c.add(CheckItem("not_banned", "بن", RequirementStatus.DONE))
    assert c.all_ok is False
    assert any(i.status == RequirementStatus.NOT_DONE for i in c.items)


def test_membership_checklist_complete():
    c = Checklist()
    c.add(CheckItem("channel_membership", "کانال", RequirementStatus.DONE))
    assert c.all_ok is True
