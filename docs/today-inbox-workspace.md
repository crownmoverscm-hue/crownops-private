# Today, Inbox and Clients

## Behaviour

- Today opens with the server's Montréal calendar date and scheduled jobs, showing assigned crew/trucks or explicit assignment placeholders.
- Recent calls, forms and emails use the existing Inbox task source, ordered by received time. Unmatched inquiries are included. Completed/answered activity remains visible, while general office tasks do not displace arrivals before pagination.
- The recent-activity feed always covers Team. Mine/Unclaimed/Team filter the separate unfinished-task list, including older and future tasks. Operational exceptions remain below the task list.
- The view refreshes every 30 seconds while visible, plus when returning to the tab. It preserves focused sections and open dialogs; a failed refresh displays a stale-data warning. Upstream intake polling still has its own delay.
- Inbox precedes Clients in navigation. Clients remains the searchable client/service database. The duplicated inquiry-inbox heading, callback queue and suggested-action panel were removed there. The Today coaching banner was removed.
- Unmatched form payloads supply available name/contact/move-date and original receipt time for office display. This does not create a client match or change linked records.

## Validation

- Inbox server tests cover activity filtering before pagination, original timestamps and unmatched form details, role restrictions, task counts, and Mine including old/upcoming unfinished work.
- `today_workspace` browser checks cover three office roles at 320/390/768/1440 widths: date, jobs, ordering, answered call status, Mine isolation, refreshing arrivals, navigation order, and client database layout.
- Existing Inbox and schedule suites remain in CI.

## Publishing

Prepared on a separate feature branch based on main, independent of the pending QuickBooks release. No production deployment or live data changes are part of this work. Publish after the Railway deployment issue is resolved and the release checks pass.

A form reaching the company mailbox proves email receipt only. A record marked unmatched still needs a reviewed client/service match in Connections; this change makes that work visible rather than silently merging it.
