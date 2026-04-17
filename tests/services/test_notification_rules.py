import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.domain.models import Base, Unit, Debt
from src.services.processor import get_units_pending_notification

def test_notification_rules():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    # Create Unit 1: Normal with contact info
    u1 = Unit(condominium_id="999", unit_name="101", owner_name="John", phones="1199999999", do_not_notify=False)
    db.add(u1)
    
    # Create Unit 2: Do Not Notify
    u2 = Unit(condominium_id="999", unit_name="102", owner_name="Jane", phones="1188888888", do_not_notify=True)
    db.add(u2)
    db.commit()

    today = datetime.date.today()
    # Debt 1: Old enough (>90 days), Unit 1 -> SHOULD NOTIFY UNIT 1
    d1 = Debt(unit_id=u1.id, doc_number="D1", due_date=today - datetime.timedelta(days=95), status="Vencido")
    db.add(d1)

    # Debt 2: NOT old enough (80 days), Unit 1 -> COULD BE IGNORED INDIVIDUALLY BUT UNIT IS ALREADY APPLICABLE
    d2 = Debt(unit_id=u1.id, doc_number="D2", due_date=today - datetime.timedelta(days=80), status="Vencido")
    db.add(d2)

    # Debt 3: Old enough, but Unit 2 has do_not_notify -> SHOULD NOT NOTIFY UNIT 2
    d3 = Debt(unit_id=u2.id, doc_number="D3", due_date=today - datetime.timedelta(days=100), status="Vencido")
    db.add(d3)
    db.commit()

    notifications = get_units_pending_notification(db, overdue_days=90)
    
    assert len(notifications) == 1, f"Expected 1 notification unit, got {len(notifications)}"
    assert notifications[0].unit_name == "101"
