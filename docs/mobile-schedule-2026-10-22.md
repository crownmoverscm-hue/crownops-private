# Mobile navigation and calendar views

Prepared changes, not a production deployment.

- The mobile/tablet header stays fixed at the top at widths up to 1023px, with reserved space and safe-area padding.
- Schedule offers Day, Week and Month. Weeks and month grids begin Monday and end Sunday.
- Date jump preserves the chosen view. Today and arrows act on the selected period. Selecting a month date opens that day.
- My Jobs / All Jobs retain existing server-side scope and permissions.
- Fully booked days, approved time off and imported planning services remain visible in the appropriate dates.
- Fleet cards, truck legends, truck groups, assignment selections and multi-truck labels use case-insensitive alphabetical ordering with natural numbers (Truck 2 before Truck 10). Unassigned services follow assigned trucks.
- Month on phones shows dates, service counts and fully booked indicators. Tap a date for full readable details.

Validation: pure calendar/truck tests plus isolated Chromium checks at 320, 390, 768 and 1440 pixels for owner, manager, sales and crew. The browser fixture contains fictional records and mocks provider-independent schedule reads. Physical-device verification remains separate. See the pull request CI for actual results.

Deployment must be verified independently. Credential recovery from the Railway incident and existing QuickBooks work remain separate tasks.
