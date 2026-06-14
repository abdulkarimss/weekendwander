from weekendwander import visa, airports
from weekendwander.finder import find_deals
from weekendwander.state import State
from weekendwander.notify import render
from datetime import date, timedelta

# --- 1) visa lookups (real dataset) ---
print("=== visa.check spot checks ===")
for pp, dest in [("SAU","ARE"),("SAU","SAU"),("SAU","EGY"),("SAU","TUR"),
                 ("SAU","GEO"),("YEM","ARE"),("IND","ARE"),("IND","THA"),
                 ("GBR","TUR"),("EGY","SAU")]:
    print(f"{pp}->{dest}: {visa.check(pp,dest)['label']}")

# --- 2) next Thu/Fri and Sat/Sun helpers to build realistic test dates ---
def next_weekday(wd):
    d = date.today()
    while d.weekday() != wd: d += timedelta(days=1)
    return d
fri = next_weekday(4)
thu = fri - timedelta(days=1)
sat = fri + timedelta(days=1)
sun = fri + timedelta(days=2)
mon = fri + timedelta(days=4)   # following Tuesday-ish weekday offer
def iso(d, h=18): return f"{d}T{h:02d}:00:00+03:00"

# --- 3) mock provider ---
class MockProvider:
    def discover(self, origin, currency, market):
        return ["JED","DXB","BAH","IST","TBS","BKK","CAI","DMM"]
    def dated_offers(self, origin, dest, month, currency, market, direct=False):
        # one weekend offer + one weekday offer per dest, varied prices
        table = {
            "JED": 380, "DXB": 540, "BAH": 290, "IST": 980,
            "TBS": 760, "BKK": 1850, "CAI": 690, "DMM": 250,
        }
        price = table.get(dest, 9999)
        return [
            {"origin":origin,"destination":dest,"price":price,"currency":"SAR",
             "departure_at": iso(fri), "return_at": iso(sun), "transfers":0,
             "airline":"XY","link":f"/search/{origin}{dest}"},
            {"origin":origin,"destination":dest,"price":price-40,"currency":"SAR",
             "departure_at": iso(mon), "return_at": iso(mon+timedelta(days=2)),
             "transfers":1,"airline":"XY","link":None},   # weekday -> must be filtered out
        ]

cfg = {
    "origin":"RUH","nationality":"SAU","currency":"sar","market":"sa",
    "max_price":1200,"direct_only":False,"easy_visa_only":False,
    "max_distance_km":3500,"max_destinations":30,
    "include_countries":[],"exclude_countries":[],"destinations":[],
    "weekend":{"depart_days":["thu","fri"],"return_days":["sat","sun"],
               "min_nights":1,"max_nights":3},
    "window_weeks":8,
}
print("\n=== find_deals (mock) ===")
deals = find_deals(MockProvider(), cfg, log=lambda *a: None)
for d in deals:
    print(f"{d['destination']:>3} {int(d['price']):>5} SAR  {d['distance_km'] and int(d['distance_km'])}km  visa={d['visa']['label']}")

print("\nASSERTIONS:")
dests = {d["destination"] for d in deals}
assert "BKK" not in dests, "BKK is >3500km, should be filtered as not nearby"
assert "JED" in dests and d, "domestic JED should appear"
assert all(d["price"]<=1200 for d in deals), "budget filter failed"
# all kept offers must be Fri->Sun (weekend), none weekday
for d in deals:
    assert d["departure_at"][:10]==str(fri), "non-weekend offer leaked through"
jed = next(x for x in deals if x["destination"]=="JED")
assert jed["visa"]["status"]=="domestic", "JED should be domestic"
print("  budget filter .......... OK")
print("  weekend-only filter .... OK")
print("  distance/nearby filter . OK (BKK excluded)")
print("  domestic visa tag ...... OK")

# --- 4) dedupe ---
import os
if os.path.exists("t.db"): os.remove("t.db")
st = State("t.db")
new1 = [x for x in deals if st.is_new(x)]
for x in new1: st.remember(x)
new2 = [x for x in deals if st.is_new(x)]      # nothing new now
assert len(new1)==len(deals) and len(new2)==0, "dedupe failed"
# now drop a price -> should be 'new' again
deals[0]["price"] -= 100
assert st.is_new(deals[0]), "cheaper price should re-notify"
st.close(); os.remove("t.db")
print("  dedupe + price-drop .... OK")

print("\n=== sample notification render ===\n")
print(render(deals[:3], header="Weekend flight deals from Riyadh"))
