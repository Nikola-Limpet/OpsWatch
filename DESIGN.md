# OpsWatch interface

The dashboard follows the Vercel dark dashboard reference supplied by the owner.
Use the [Geist design system](https://vercel.com/geist/introduction) as the visual
authority for colors, typography, spacing, controls, and component states.

The operator should be able to scan service health, find an incident, inspect its
evidence, and start a repair. The implementation uses recorded monitoring data only.
A compact health strip separates recorded service health, collection failures, and
actual agent/worker status. Incident selection opens an adjacent evidence workspace
on desktop, with an explicit back action on narrow screens. Starting a fix is an
explicit action in incident details, followed by job progress and a draft PR link.
Agent work has a persistent navigation destination for active jobs and recorded outcomes.
The Projects view groups related services and keeps disabled services available for
restoration. A project action names its scope; per-service controls apply to one row.
Monitored and Inactive tabs keep paused inventory out of daily monitoring. Compact
project groups, search, filters, pagination, and service details keep the list manageable.

Agent Studio uses adjacent session, conversation, and context panels on wide screens
and stacks them on smaller screens. Show pending, failed, interrupted, and cancelled
jobs explicitly. Evidence sources, coverage gaps, preference revisions, and the pinned
pstack source should remain inspectable without crowding the conversation. Reports
are saved documents; improvement results are proposals the owner reviews in Workflow.

- Use neutral black/graphite surfaces, near-black panels, and thin gray borders.
  Hover states use gray. Reserve green, amber, and red for service states; blue
  identifies the selected destination, primary action, links, and keyboard focus.
- Use locally served variable Geist Sans for UI text and Geist Mono for evidence,
  SQL, commands, and incident IDs. Use 30px page headings, 23px incident headings,
  16px section headings, and a compact 12–14px interface scale. Keep evidence legible
  and preserve full text through wrapping, scrolling, or disclosure.
- Use a 4px spacing base, 8px panel radii, and 6px control radii. Buttons use 40px
  height, with 32px compact controls where space is limited. Keep dense tables
  aligned and use tabular figures for measurements.
- Use outlined monochrome icons, restrained borders, and flat panels. Avoid colored
  gradients, decorative shadows, and artificial monitoring charts.
- Apply the same tokens to navigation, forms, tables, badges, dialogs, code blocks,
  empty states, and errors. All status colors also have text labels.
- On narrow screens, move navigation above the content, stack health information,
  and scroll wide tables inside their own containers. Below 1050px the incident
  workspace shows the list or the selected detail, with an explicit return button.
  Keep keyboard focus
  visible and text contrast at least 4.5:1.

Do not group failures across unknown or different provider accounts. Keep every open
incident reachable, even when the most recent 200 resolved records exclude it. Distinguish
failure evidence from recovery observations and make missing historical evidence explicit.
Worker readiness comes from its heartbeat, never from a decorative status label.

Sources: [colors](https://vercel.com/geist/colors),
[typography](https://vercel.com/geist/typography), and
[buttons](https://vercel.com/geist/button). Exact application tokens live in
`frontend/src/styles.css` and `frontend/src/studio.css`. React and TanStack own the
interface; FastAPI serves the compiled application and protected same-origin API.
