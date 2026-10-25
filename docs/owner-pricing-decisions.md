# Owner pricing decisions — source audit, not live price approval

Source: `config/workbook_pricing_snapshot.json`; marked approved_for_pilot=false. The numbers below are copied, NOT approved or deployed rates. Live database rates have not been read in this workstream.

## Owner decision — October 25, 2026

Owner approved the following direction for new quotes going forward:
- Standard local fuel amount: CAD $35.99.
- Afternoon local moving minimum: one hour of labour, plus transportation, plus CAD $35.99 fuel. This supersedes the previous 1.5-hour afternoon proposal.
- These are separate charges; one hour is a labour minimum, not a promise that the move takes one hour. Actual additional service remains billable under the reviewed quote terms.
- Existing accepted quotes are not repriced. No production pricing was changed by recording this decision.
- Still to confirm before implementation: whether the fuel amount applies once per service or per truck; the transportation method/amount; whether the reduced minimum applies to all afternoon local moves or only the previously qualifying small jobs. Morning/general minimum and 2027 labour rates remain unapproved.
- Record approval date separately from deployment date. No retroactive changes or implicit approval of other source rates.

## Decisions to approve

| Decision | Current source/code | What owner must approve |
|---|---|---|
| Labour | Seasonal source table below; dated rates and daily overrides also exist | Exact 2026 effective periods and 2–6 mover rates; separate 2027 periods and rates |
| Local fuel | Source normal $35, extended/gas $39.99; CRM defaults $35/truck | Which fee applies, scope, effective dates, and whether per truck |
| Local transportation | Quote supports entered fixed amount ($35 placeholder), or team hourly base travel; extra truck $25 in hourly mode | Fixed vs hourly method and whether fuel/extra-truck fee is additional |
| Minimum hours | Legacy source general 2.5h; CRM standard >=3h, qualifying small afternoon 1.5h | One standard minimum, qualifying afternoon exception and rounding policy |
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
