# Demo script: one walkthrough per role

The demo runs at localhost:8002, and every account's password is on the login page. Each role takes about a minute, so the full demo fits slide 7's three minutes. The order is SDR, then marketing, then sales manager, ending on Leadership, so you hand back to the money slide on the shared number. The pages update live, so read the number on screen rather than from this script; the figures below are as of 2 Nov 2026.

Before you start, log in once as each role in three browser tabs, so switching roles is a tab click instead of a login.

## 1. SDR: Alex Novak's queue (sdr1, about 70 seconds)

**Open** `/queue/900`. The top lead is Marcus Walsh, HR Coordinator at Redwood Dental.

**Say:** "This is all an SDR sees: one lead at a time, the best first. Marcus is number 1 of 580 in the pool, with a 78% chance of booking a demo if Alex works him."

**Point to "Why Marcus is ranked here."** "The reason is written from the model's own arithmetic, not by a chatbot. He came through a partner referral and visited the pricing page, which push him up. He's an individual contributor, which holds him back."

**Point to the signals table.** "Claude read the notes and found a timeline, a budget and a decision maker. Each one is a quote from the record: 'Budget already approved by the CFO'. Code checks every quote is really there, and together they can move the score 10 points at most."

**Scroll to the draft first email.** "The first email is already drafted from those same facts: the referral, the March start, the approved budget. Alex edits it if he wants and sends it. AI never contacts a prospect on its own."

**Click the "Touched · waiting to hear back" tab.** Laura Okafor is on top. "Laura replied that she's interested. The reply was triaged, and the demo was booked straight onto Alex's HubSpot calendar for Tuesday at 2. That's the follow-up and booking work that doubles how many leads an SDR can handle."

**Don't click** Send, Mark worked, Skip or Junk. They write to the demo portal, and Send may send a real email if mail is set up.

## 2. Marketing: Lead scoring (marketing, about 60 seconds)

**Open** `/model`. **Say:** "Marketing owns the model. One model is live, demo-v6, and it went live automatically because it passed the fixed rules."

**Point to the channel monitor**, which shows October, the last full month. "Every day this checks, channel by channel, whether leads book as often as their scores said. In 5 of 6 channels they now book above their scores, because they're reached sooner. That's the signal for the next retrain."

**Click "How it decides."** **Say:** "Every version is tested on months it never saw. v6's top picks book 23% more demos than HubSpot's points. And the odds can be believed: when it says 55%, 54% book." Point at the calibration table's 50–60% row.

**Point to v7 in the version table.** "v7 picked best of all, but its odds were off by 12.7 points, so the rules sent it back. Better picks with dishonest odds don't ship."

**Open** `/flagged` (Leads to check). "A second reader checks each record against its score and flags what disagrees, quoting the record. This one says 'Student project on HR tech vendors' at 36% odds. A flag never changes a score. Marketing decides." Don't click Agreed or Not a problem.

## 3. Sales manager: Overview, Held, Leadership (manager, about 60 seconds)

**Open** `/`. **Say:** "The manager sees the whole team. There are 462 unassigned leads in the pool, ranked. The line marks how far the team can reach in a week. Everything above it gets handed out, best first, and the rest waits."

**Point to the SDR table.** "Every SDR's numbers for the last 30 days: received, worked, booked, and hours to first touch. All twelve are working at roughly the same pace."

**Open** `/held`. **Say:** "Leads the model thinks an SDR would likely skip aren't dropped quietly. They're held here for the manager. Maria Kim is a 77% lead the team would probably pass over because she's an executive assistant. The manager can place her by hand, and the HubSpot task says why."

**Open** `/leadership`. **Say:** "And this is the one number we share with leadership: demos per 1,000 leads, from 28.6 to 108.2 with the same 12 SDRs. First touch went from 70 hours to 10." Point to "Where the gain comes from": "Better picks first, then speed, then the time the AI steps save." **Hand back** to slide 8.

## Watch-outs

- **Overdue and old leads.** The demo portal has been running unattended, so the Overview shows 10 overdue first touches and leads up to 85 days old in queues. If asked, say the SDR clicks on the demo are simulated, and in the simulated months the median wait was 10 hours.
- **Decisions page.** Its top table is mostly test clicks with no reason given. If you show it, scroll straight to Model versions, where v7's send-back is.
- **Leadership month table.** The "After" rows show only December and January, and the sequence table below labels every month "before". Stop at "Where the gain comes from" and don't scroll further.
- **Numbers that drift.** The Lead scoring page counts exploration "since routing began" (23% of 372). Leadership and the deck use the six routed months (20% of 235). Quote Leadership.
- **Call brief.** "Prepare the call brief" calls Claude live and takes a few seconds. Click it only if you want to show it and can wait.

## Questions to expect

- **"Is this real data?"** No. It's a mock HubSpot portal with synthetic leads, and the ask is one measured month on BrightPath's real leads.
- **"Can the AI change a score?"** By 10 points at most, and only with a quote code finds in the record. The probability itself comes from a statistical model.
- **"What if the model gets worse?"** It retrains monthly and only ships if its odds stay honest and it picks better. Otherwise two people have to sign it off.
- **"Does the SDR lose control?"** No. Rank and reason advise. The SDR can skip, move or junk any lead, and the manager sees every override.
