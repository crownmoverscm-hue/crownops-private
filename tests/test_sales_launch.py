import tempfile
import unittest
from pathlib import Path

from app.store import connect,initialize,transaction,insert,now
from app.seed import seed_fictional
from app.sales_launch import calculate,contact_for_lead,confirm_booking_after_deposit,TEMPLATES


class SalesLaunchTests(unittest.TestCase):
    def make_db(self):
        td=tempfile.TemporaryDirectory()
        path=Path(td.name)/"crownops.sqlite3"
        initialize(path)
        seed_fictional(path)
        self.addCleanup(td.cleanup)
        return path

    def test_launch_templates_include_blank_long_distance_english_and_french(self):
        by_key={x["key"]:x for x in TEMPLATES}
        self.assertTrue(by_key["long-en"]["blank"])
        self.assertTrue(by_key["long-fr"]["blank"])
        self.assertEqual(by_key["long-en"]["service_kind"],"Moving - Long Distance")
        self.assertEqual(by_key["long-fr"]["language"],"French")

    def test_local_quote_uses_saved_rate_and_simple_local_fees(self):
        path=self.make_db()
        with connect(path) as db:
            lead=dict(db.execute("SELECT * FROM leads WHERE id='DEMO-LEAD-1'").fetchone())
            inputs,result,suggestions=calculate(db,lead,{
                "lead_id":lead["id"],"move_date":lead["move_date"],"service_kind":"Moving - Local",
                "movers":2,"trucks":1,"hours_low":3,"hours_high":4,"minimum_hours":3,
                "transport_mode":"hours","transport_hours":1,"deposit_required":300,
            })
        self.assertGreater(result["hourly_rate_cents"],0)
        self.assertEqual(result["fuel_cents"],3500)
        self.assertEqual(result["transport_cents"],result["hourly_rate_cents"])
        self.assertIn("$0.60/lb",result["liability_note"])
        self.assertEqual(inputs["deposit_required"],300.0)
        self.assertGreater(suggestions["standard_rate_cents"],0)

    def test_two_truck_local_adds_second_gas_and_truck_transport_fee(self):
        path=self.make_db()
        with connect(path) as db:
            lead=dict(db.execute("SELECT * FROM leads WHERE id='DEMO-LEAD-1'").fetchone())
            _,r,_=calculate(db,lead,{
                "move_date":lead["move_date"],"service_kind":"Moving - Local","movers":3,"trucks":2,
                "hours_low":3,"hours_high":4,"minimum_hours":3,"transport_mode":"hours","transport_hours":1,
            })
        self.assertEqual(r["fuel_cents"],7000)
        self.assertEqual(r["transport_cents"],r["hourly_rate_cents"]+2500)

    def test_long_distance_supports_fixed_transport_and_094_per_km_per_truck(self):
        path=self.make_db()
        with connect(path) as db:
            lead=dict(db.execute("SELECT * FROM leads WHERE id='DEMO-LEAD-1'").fetchone())
            _,r,_=calculate(db,lead,{
                "move_date":lead["move_date"],"service_kind":"Moving - Long Distance","movers":3,"trucks":2,
                "hourly_rate_override":200,"hours_low":4,"hours_high":6,"minimum_hours":3,
                "transport_mode":"fixed","transport_amount":2800,"distance_km":100,"km_rate":0.94,
            })
        self.assertEqual(r["hourly_rate_cents"],20000)
        self.assertEqual(r["transport_cents"],280000)
        self.assertEqual(r["fuel_cents"],18800)
        self.assertEqual(r["transport_hours"],0)

    def test_junk_full_truck_and_extra_labor(self):
        path=self.make_db()
        with connect(path) as db:
            lead=dict(db.execute("SELECT * FROM leads WHERE id='DEMO-LEAD-1'").fetchone())
            _,r,_=calculate(db,lead,{
                "move_date":lead["move_date"],"service_kind":"Junk Removal",
                "junk_package":"full","junk_labor_blocks":2,
            })
        self.assertEqual(r["low_cents"],176000)
        self.assertEqual(r["high_cents"],176000)

    def test_same_email_groups_repeat_services_under_one_client(self):
        path=self.make_db()
        with transaction(path) as db:
            first=dict(db.execute("SELECT * FROM leads WHERE id='DEMO-LEAD-1'").fetchone())
            c1=contact_for_lead(db,first)
            second=dict(first)
            second.update(id="TEST-REPEAT",external_id=None,move_date="2027-01-10",created_at=now(),updated_at=now())
            cols=list(second)
            db.execute("INSERT INTO leads ("+",".join(cols)+") VALUES ("+",".join("?" for _ in cols)+")",tuple(second[k] for k in cols))
            c2=contact_for_lead(db,second)
        self.assertEqual(c1["id"],c2["id"])

    def test_confirmed_deposit_moves_job_to_confirmed_and_creates_assignment_task(self):
        path=self.make_db()
        with transaction(path) as db:
            insert(db,"payments",{"id":"TEST-DEPOSIT-2","lead_id":"DEMO-LEAD-2","amount_cents":30000,"method":"Interac","purpose":"Deposit","reference":"TEST-DEP-2","verified":1,"recorded_by":"unit-test","created_at":now()})
            job=confirm_booking_after_deposit(db,"DEMO-QUOTE-2","unit-test")
            self.assertIsNotNone(job)
            state=db.execute("SELECT status FROM jobs WHERE id=?",(job["id"],)).fetchone()[0]
            stage=db.execute("SELECT stage FROM leads WHERE id='DEMO-LEAD-2'").fetchone()[0]
            task=db.execute("SELECT count(*) FROM tasks WHERE lead_id='DEMO-LEAD-2' AND title='Assign crew and truck'").fetchone()[0]
            mail=db.execute("SELECT count(*) FROM outbound_messages WHERE event_key='booking-confirmation:DEMO-QUOTE-2'").fetchone()[0]
        self.assertEqual(state,"Confirmed")
        self.assertEqual(stage,"Booked")
        self.assertEqual(task,1)
        self.assertEqual(mail,1)

    def test_partial_deposit_does_not_confirm_booking(self):
        path=self.make_db()
        with transaction(path) as db:
            insert(db,"payments",{"id":"TEST-PARTIAL-2","lead_id":"DEMO-LEAD-2","amount_cents":10000,"method":"Interac","purpose":"Deposit","reference":"TEST-PARTIAL","verified":1,"recorded_by":"unit-test","created_at":now()})
            job=confirm_booking_after_deposit(db,"DEMO-QUOTE-2","unit-test")
            state=db.execute("SELECT status FROM jobs WHERE id='DEMO-JOB-2'").fetchone()[0]
        self.assertIsNone(job)
        self.assertEqual(state,"Tentative")


if __name__=="__main__":
    unittest.main()
