# Daily Digest Spec

What the assistant sends each morning. This drives digest_prompt.md, the
digest formatter, and the digest checks in the question set.

<!--
How to fill this in:
- The defaults below are suggestions. Edit, delete, or reorder anything.
- Refer to people by role ("my manager", "family", "kids' school"), not by
  name. Names and addresses belong in data/profile/about-me.md. This file is
  committed to GitHub; it must not contain personal data.
- Lines marked DECIDE need a choice from you.
-->

<!--
Implemented (version 1): services/digest/build.py, with the schedule, limits
and rules in config/digest.yaml; keep those in step with this file. Not yet:
Waiting on others, links to each source, phone notifications, feedback, and
the between-digest alerts (section 5).
-->

## 1. Delivery

- **When:** 6:30am, weekdays, 7:00 am weekends. <!-- DECIDE: time; different on weekends? skip weekends? -->
- **Where:** a phone notification with a one-line summary, linking to the
  full digest in the assistant's web page. <!-- DECIDE: notification + web page, email to yourself, or both -->
- **Length:** readable in under 2 minutes. At most about 15 items across all
  sections.
- **Quiet days:** if nothing needs attention, say so in one line. Never pad.

## 2. Sections, in order

Each section is left out when empty.

### Needs your attention

Things you have to act on today or soon.
a bill that is due that week that needs to be paid
an event im registered for has changed schedule or been cancelled

### Today

- Calendar events, in time order
- For each meeting: one line of related context from recent email, if any
  ("last email from them was about the budget revision")
- Conflicts or tight gaps between events

### Coming up

- Next 7 days: events that need preparation, travel, or a decision
- RSVPs or registrations with deadlines
- **Max:** 5 items.

### Waiting on others

- Emails you sent that asked for something and have had no reply after 3
  days <!-- DECIDE: how many days -->
- **Max:** 3 items, oldest first.

### Worth knowing

- Updates that need no action but you'd want to know about (a delivery
  arriving, a school announcement, a change to a plan)
- **Max:** 3 items.

### What I did

Transparency about actions the assistant took without asking.
- Emails filed into folders, with counts per folder
- Drafts prepared
- Profile changes proposed (awaiting approval)
- Anything that failed (sync errors, skipped emails)

## 3. What counts as "needs attention"

<!-- DECIDE: edit this list. It's how the assistant ranks everything. -->

Always needs attention:
- direct questions from family members
- Anything with a deadline, payment due, or appointment change
- Anything involving money, health, or the kids' school

Never needs attention (file it, count it in "What I did"):
- Newsletters, promotions, marketing
- Automated notifications I haven't acted on in the past month

## 4. Style

- One line per item, starting with what it is, not who sent it: "Dentist
  moved to Thu 3pm (Dr. office)", not "Email from Dr. office about..."
- Every item links to its source email or event.
- No guessing. If something is unclear, say "unclear" and link it.
- Plain language, no greetings, no sign-off.

## 5. Between digests

What's urgent enough to send a notification right away instead of waiting:
- <!-- DECIDE: e.g. "same-day change to a calendar event", "message from
  family marked urgent", or "nothing; everything waits for the digest" -->

## 6. Feedback

- Each item can be marked "not important" or "wrong". The assistant records
  this and proposes rule changes weekly, for your approval.
  <!-- DECIDE: keep, or leave out for the first version -->

## Example (made up)

```
Good morning. 3 things need you today.

NEEDS YOUR ATTENTION
- Reply: Dana asks if Thanksgiving is at your place this year  [email]
- Due Fri: school field trip permission form  [email]
- Approve: draft reply to plumber confirming Tue 9am  [draft]

TODAY
- 9:00  Team standup
- 11:30 Budget review with Priya: her last email asked for Q3 numbers  [email]
- 12:00 Dentist: conflicts with budget review (ends 12:30)  [calendar]

COMING UP
- Sat: Lake trip; Dana's email says bring the kayak  [email]

WAITING ON OTHERS
- Contractor quote, asked 4 days ago  [email]

WHAT I DID
- Filed 23 emails: Newsletters 14, Receipts 6, Shipping 3
- Drafted 1 reply (plumber)
```
