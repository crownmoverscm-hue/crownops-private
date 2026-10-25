# Owner pricing decisions — source audit, not live price approval

Source: `config/workbook_pricing_snapshot.json`; marked approved_for_pilot=false. The numbers below are copied, NOT approved or deployed rates. Live database rates have not been read in this workstream.

## Owner decision — October 25, 2026 (latest direct instruction)

- Local Moving fuel default: **CAD $35.99 per truck**, separate from labour and transportation. This direct clarification supersedes the attached completed PDF / pasted directive's once-per-booking wording.
- Route scope: 700 rue Hodge → all pickups and destinations → 700 rue Hodge. Standard fuel covers total distance up to 40 km. Keep $35.99 as the default even when entered distance is larger; staff may manually adjust the per-truck amount. No automatic kilometre surcharge, threshold fee or 5% local distance buffer is approved. The owner mentioned $0.94/km for multi-stop routes; do not automatically infer whether that replaces the fee or is added.
- Qualifying afternoon local Moving labour minimum: **one hour**, plus separately reviewed transportation and fuel, before applicable taxes. Additional service time remains billable under reviewed terms.
- Precise afternoon cutoff / expanded eligibility, transportation formula and morning/general minimum remain pending. Preserve existing explicit small-afternoon opt-in rather than automatically treating every afternoon service as eligible. Packing and long-distance policy are not changed.
- Approved direction recorded October 25, 2026; activate for new quote calculations only after an approved deployment. Never reprice stored accepted quote snapshots.
- Implementation switch: `CROWNOPS_LOCAL_PRICING_POLICY=2026-10-25`, default off. Both local calculators use the approved fee and afternoon minimum when enabled. Results carry a policy version snapshot. This does not approve labour rates or any 2027 price table.

## Decisions to approve

| Decision | Current source/code | What owner must approve |
|---|---|---|
| Labour | Seasonal source table below; dated rates and daily overrides also exist | Exact 2026 effective periods and 2–6 mover rates; separate 2027 periods and rates |
| Local fuel | Approved $35.99/truck; 40 km complete base-to-base route; manual override | Deployment activation only; any automatic mileage formula remains unapproved |
| Local transportation | Quote supports entered fixed amount ($35 placeholder), or team hourly base travel; extra truck $25 in hourly mode | Fixed vs hourly method and whether fuel/extra-truck fee is additional |
| Minimum hours | Legacy source general 2.5h; CRM standard >=3h, qualifying small afternoon 1.5h before the new policy | New one-hour Moving exception approved; general minimum, eligibility expansion and rounding changes remain pending |
| Crew/truck rule | CRM requires 3 trucks for 6 movers; legacy calculator accepts 2 for 6 | Confirm the canonical rule; no staffing-policy change made in this branch |
| Long distance | Code driving multiplier 1.15; round-trip km buffer 5%; $0.94/km/truck | Each multiplier/rate and whether both time and distance charges apply |
| Deposit | Auto-draft default $300 | Amount/rule, due timing, exceptions and human confirmation process |
| Materials | Existing SKU sell/cost values; no dated material price book | Approved current list and separate future effective-date requirements |
| QuickBooks | Reviewed service/tax/discount-account mapping | Correct sandbox company, combined QC tax code, discounts-given account, low/high range endpoint |

Do not reply simply approve all: labour and conflicting fees need explicit amounts and dates. No next-year rates are inferred. Automatically migrated 2027 annual rows are not owner approval.

## Unapproved seasonal source (CAD/hour)

| Period | 2 movers | 3 movers | 4 movers | 5 movers | 6 movers |
|---|---:|---:|---:|---:|---:|
| Low season: Nov 1 - Mar 31 | 125 | 150 | 200 | 245 | 300 |
| Mid season: Apr 1 - May 31 | 135 | 175 | 220 | 260 | 315 |
| June 1 - June 10 | 140 | 195 | 250 | 295 | 340 |
| June 11 - June 19 | 160 | 225 | 285 | 340 | 395 |
| June 20 - June 23 | 170 | 255 | 325 | 390 | 440 |
| June 24 - June 27 | 180 | 270 | 340 | 440 | 495 |
| June 28 - June 29 | 190 | 285 | 360 | 450 | 540 |
| June 30 and July 1 | 250 | 360 | 480 | 550 | 660 |
| July 2 | 180 | 250 | 320 | 400 | 480 |
| July 3 | 150 | 225 | 300 | 375 | 450 |
| July 4 | 140 | 210 | 280 | 350 | 420 |
| July 5 - July 23 | 140 | 195 | 260 | 325 | 390 |
| July 24 - July 27 | 140 | 210 | 280 | 350 | 420 |
| July 28 - July 30 | 150 | 225 | 300 | 375 | 450 |
| July 31 and Aug 1 | 160 | 240 | 320 | 400 | 480 |
| Aug 2 - Aug 14 | 140 | 195 | 260 | 325 | 390 |
| Mid season: Aug 15 - Oct 31 | 135 | 175 | 220 | 260 | 315 |

## 2027

Unapproved and deliberately unspecified. Provide exact date ranges, 2–6 mover hourly rates, fuel/transport/minimum rules, and approval reference. Existing 2026 accepted quotes keep their saved price snapshots; this work does not reprice them.

Tax contract: fully taxable Quebec moves use TPS 5% and TVQ 9.975% independently on the pretax discounted base; $100 yields $114.98. Special exemptions or non-Quebec treatment require explicit accounting review, not automatic use of this normal-move check.


## Transportation comparison — illustration only, not a rate approval

Let R be the approved crew rate, H reviewed transport hours, and T a separately
agreed fixed fee. For a one-truck, one-hour eligible afternoon move:

| Method | Transport | Pretax minimum |
|---|---|---|
| Fixed | T, explicitly entered | R + T + $35.99 |
| Hourly (existing code) | R × H; existing extra-truck amount requires review | R + (R × H) + $35.99 |

Pure technical example using fictional R=$100/h, H=0.5h and T=$35: fixed method
would yield $170.99 pretax; hourly method $185.99 pretax. These are not approved
customer rates. Both must show fuel separately and must not also include that
same fuel in a transportation bundle. Base travel and travel between client stops
must not be billed twice. Do not select a new method until the owner approves it.
