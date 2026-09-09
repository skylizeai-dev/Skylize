import { useCallback, useEffect, useRef, useState } from 'react';

function mapScen(v) {
  return ({ 'day-one': 'dayone', 'quiet-night': 'quiet', 'everything-blocked': 'blocked', 'long-absence': 'absence' }[v]) || v;
}

const ITEMS = {
  meridian: { time: '23:41', title: 'Send the Q3 renewal quote to your renewing client — $4,800', short: 'Send the client renewal quote — $4,800',
    why: 'Quotes over $2,500 wait for a person — that’s true for you too; it just means a click. Everything is drafted and checked.',
    attempted: 'The client’s renewal is due Friday. Your agent drafted their Q3 quote at $4,800 — up 6% on last year, matching the 12 seats they added in June — with terms unchanged.',
    draft: '“Hi there — ahead of Friday, here’s your Q3 renewal at $4,800/quarter, reflecting the seats you added in June. Terms unchanged…”',
    limit: 'OVER THE SOLO SEND LINE', limitBody: 'Quotes above $2,500 always get a human click before they leave the building. Under that line, your agent sends on its own; above it, the decision is yours.',
    yes: 'The quote emails to the client contact within a minute. You’ll find it tonight under Done, with the sent copy attached to the trace.',
    no: 'Nothing is sent and the client sees nothing. The draft is kept, and your agent will surface it again Thursday if it’s still open.',
    approveL: 'APPROVE & SEND', rejectL: 'DON’T SEND', approvedNote: 'Approved — sending now. It’ll appear in tonight’s Done.', declinedNote: 'Set aside — nothing sent, draft kept.',
    trace: [['23:38', 'Read the renewal reminder in your inbox'], ['23:39', 'Pulled last year’s quote and June’s seat change from the CRM'], ['23:41', 'Drafted the quote — $4,800, terms unchanged'], ['23:41', 'Checked it against what it can send alone — over the line'], ['23:41', 'Stopped on purpose and put it on your desk']] },
  linkedin: { time: '06:54', title: 'Post the launch teaser to the company LinkedIn', short: 'Post the launch teaser to LinkedIn',
    why: 'Anything published under the company’s name waits for a person to say go — no matter who drafts it.',
    attempted: 'The teaser video and copy were approved in Friday’s review. Your agent queued the post for 9:00 with the campaign link and tracking already in place.',
    draft: '“Something new lands on Thursday. Here’s 20 seconds of it. →”',
    limit: 'PUBLIC, IN THE COMPANY’S NAME', limitBody: 'Posts that speak for the whole company always get a human OK first. Drafting, scheduling and tracking are all within your authority — publishing is the one step that waits.',
    yes: 'It publishes at 9:00 with tracking attached. Early numbers show up in tomorrow’s brief.',
    no: 'The post stays queued as a draft. The launch plan is unaffected until Thursday.',
    approveL: 'APPROVE & POST', rejectL: 'HOLD IT', approvedNote: 'Approved — publishing at 9:00.', declinedNote: 'Held — still queued as a draft.',
    trace: [['06:52', 'Pulled Friday’s approved teaser cut and copy'], ['06:54', 'Queued the post for 9:00 with campaign tracking'], ['06:54', 'Public post — needs a person’s go'], ['06:54', 'Stopped on purpose and put it on your desk']] },
  booth: { time: '03:21', title: 'Pay the $1,950 booth deposit for AutumnFest', short: 'Pay the AutumnFest booth deposit — $1,950',
    why: 'It would take this week’s spending past your $250 headroom, so it waited for you.',
    attempted: 'The organizer’s invoice arrived overnight and the early-bird rate ends Friday. Payment details are filled in from the company card on file, ready to submit.',
    draft: '', limit: 'PAST THIS WEEK’S HEADROOM', limitBody: 'You have $112.60 of spending headroom left this week; the invoice is $1,950. Your click covers it — or your access owner can raise the ceiling if this becomes routine.',
    yes: 'The deposit pays today and locks the early-bird rate. It shows in Cost tonight, clearly marked as your approval.',
    no: 'Nothing is paid. Your agent will flag it once more on Thursday, a day before the rate expires.',
    approveL: 'APPROVE & PAY', rejectL: 'NOT NOW', approvedNote: 'Approved — paying today.', declinedNote: 'Set aside — nothing paid.',
    trace: [['03:20', 'Read the AutumnFest invoice from the organizer'], ['03:21', 'Filled the payment from the company card on file'], ['03:21', 'Checked this week’s headroom — $112.60 left, invoice is $1,950'], ['03:21', 'Stopped on purpose and put it on your desk']] },
  bulk: { time: '02:44', title: 'Email all 3,800 customers about the price change', short: 'Email all 3,800 customers about pricing',
    why: 'A message to everyone at once always waits for a person. No exceptions, including you.',
    attempted: 'The pricing page went live yesterday and support is already fielding questions. Your agent drafted a plain, friendly note explaining exactly what changes and when.',
    draft: '“We’re updating prices on September 1. Here’s exactly what changes for you — and what doesn’t…”',
    limit: 'REACHES EVERYONE AT ONCE', limitBody: 'One send, 3,800 inboxes. That scale of reach is reserved for a human decision every time — it’s about blast radius, not trust.',
    yes: 'The note goes out over the next hour in batches, and support gets the FAQ five minutes before the first batch.',
    no: 'Nothing sends. Support keeps answering one-by-one and the draft is kept for whenever you’re ready.',
    approveL: 'APPROVE & SEND', rejectL: 'DON’T SEND', approvedNote: 'Approved — sending in batches over the next hour.', declinedNote: 'Set aside — nothing sent.',
    trace: [['02:41', 'Noticed rising support questions about the new pricing page'], ['02:43', 'Drafted the customer note and a support FAQ'], ['02:44', 'Send reaches all 3,800 customers — needs a person'], ['02:44', 'Stopped on purpose and put it on your desk']] },
  tooltrial: { time: '04:12', title: 'Start a trial of Attentio, a new analytics tool', short: 'Start an Attentio trial (new vendor)',
    why: 'Agreeing to a new vendor’s terms is a person’s call — even for a free trial.',
    attempted: 'Three of your weekly reports would take minutes instead of an hour with it. The 14-day trial is free and the sign-up form is filled in under your work email.',
    draft: '', limit: 'NEW VENDOR, NEW TERMS', limitBody: 'Signing terms of service with a vendor your company hasn’t used before needs a human signature — yours, or a colleague’s if you’d rather pass it up.',
    yes: 'The trial starts today under your work email. Your agent reports what it’s actually worth before day 14 — with a recommendation.',
    no: 'No account is created, no terms accepted. The reports keep being built the long way.',
    approveL: 'APPROVE TRIAL', rejectL: 'SKIP IT', approvedNote: 'Approved — trial starting today.', declinedNote: 'Skipped — no account created.',
    trace: [['04:10', 'Timed the weekly reports — 3 hrs/week of assembly'], ['04:12', 'Filled the Attentio trial form, unsubmitted'], ['04:12', 'New vendor terms — needs a person’s signature'], ['04:12', 'Stopped on purpose and put it on your desk']] },
};

const EVID = {
  meridian: { checked: 'YOUR SENT MAIL · THE CRM · LAST YEAR’S QUOTE', ready: 3, readyLabel: 'DRAFTED AND CHECKED — NOTHING MISSING', ifIgnored: 'If you never answer, nothing is sent. The draft simply waits — silence is not a yes.' },
  linkedin: { checked: 'FRIDAY’S APPROVED CUT · CAMPAIGN TRACKING', ready: 3, readyLabel: 'QUEUED AND TRACKED — NOTHING MISSING', ifIgnored: 'If you never answer, the post stays a draft. It will not publish itself at 9:00.' },
  booth: { checked: 'THE ORGANIZER’S INVOICE · COMPANY CARD · THIS WEEK’S SPEND', ready: 2, readyLabel: 'READY TO PAY — EARLY RATE ENDS FRIDAY', ifIgnored: 'If you never answer, nothing is paid and the early-bird rate lapses on Friday.' },
  bulk: { checked: 'THE NEW PRICING PAGE · SUPPORT QUEUE · 3,800 CONTACTS', ready: 2, readyLabel: 'DRAFTED — WORTH READING FIRST', ifIgnored: 'If you never answer, nothing sends and support keeps replying one by one.' },
  tooltrial: { checked: 'YOUR WEEKLY REPORT TIMINGS · ATTENTIO’S TERMS', ready: 2, readyLabel: 'FORM FILLED, UNSUBMITTED', ifIgnored: 'If you never answer, no account is created and no terms are accepted.' },
};

const PAST = [
  { label: 'Mon · Aug 3', cost: '$3.42', rows: [
    { t: '19:08', h: 'Rebuilt the weekly funnel report and sent it to the leadership list', k: 'agent' },
    { t: '16:20', h: 'Sent the AutumnFest booth deposit — $1,950', k: 'approved' },
    { t: '14:02', h: 'Answered 14 partner emails in your voice', k: 'agent' },
    { t: '11:35', h: 'Email all 3,800 customers about the price change', k: 'declined' },
    { t: '09:12', h: 'Filed 38 webinar leads into the CRM and removed 5 duplicates', k: 'agent' },
  ] },
  { label: 'Sun · Aug 2', cost: '$0.98', rows: [
    { t: '21:40', h: 'Watched the campaign queue hourly — nothing needed doing', k: 'agent' },
    { t: '10:15', h: 'Summarized three competitor launches from the weekend', k: 'agent' },
  ] },
  { label: 'Sat · Aug 1', cost: '$1.12', rows: [
    { t: '18:22', h: 'Renewed the design-tool subscription — $86, inside your limit', k: 'agent' },
    { t: '12:04', h: 'Cleared 22 newsletter sign-ups into the nurture list', k: 'agent' },
  ] },
  { label: 'Fri · Jul 31', cost: '$4.05', rows: [
    { t: '17:22', h: 'Posted the launch teaser to the company LinkedIn', k: 'approved' },
    { t: '15:48', h: 'Closed out the July campaign report and filed it', k: 'agent' },
    { t: '13:10', h: 'Started a trial of Attentio, a new analytics tool', k: 'declined' },
    { t: '08:55', h: 'Drafted the September campaign brief from Monday’s notes', k: 'agent' },
  ] },
  { label: 'Thu · Jul 30', cost: '$3.60', rows: [
    { t: '20:30', h: 'Sent Harbourline’s renewal quote — $2,150, inside your limit', k: 'agent' },
    { t: '16:44', h: 'Refreshed the lead list from the webinar sign-ups', k: 'agent' },
    { t: '10:08', h: 'Booked the Q4 planning room for the whole team', k: 'agent' },
  ] },
];

const DONE = [
  { t: '23:12', h: 'Drafted the September campaign brief from Monday’s kickoff notes' },
  { t: '23:47', h: 'Rebuilt the weekly funnel report — sign-ups up 8% week over week' },
  { t: '00:31', h: 'Answered 11 partner emails in your voice; flagged 2 for a tone check' },
  { t: '01:15', h: 'Filed 42 event leads into the CRM and removed 7 duplicates' },
  { t: '02:03', h: 'Renewed the design-tool subscription — $86, inside your limit' },
  { t: '05:12', h: 'Summarized four competitor launches from yesterday' },
  { t: '06:40', h: 'Scheduled this week’s three social posts you approved on Friday' },
];
const OWN = [
  { t: 'YD 17:22', h: 'You approved the trade-show budget' },
  { t: 'YD 16:05', h: 'You tightened the launch email subject line' },
  { t: 'YD 09:41', h: 'You asked for shorter weekly reports' },
];
const DAYS = [
  { label: 'Fri · Aug 1', n: 34, cost: '$4.20', rows: [{ t: '22:10', h: 'Closed out the July campaign report and filed it' }, { t: '23:05', h: 'Answered 9 partner emails in your voice' }, { t: '02:40', h: 'Refreshed the lead list from the webinar sign-ups' }] },
  { label: 'Thu · Jul 31', n: 29, cost: '$3.60', rows: [] },
  { label: 'Wed · Jul 30', n: 31, cost: '$4.05', rows: [] },
  { label: 'Tue · Jul 29', n: 27, cost: '$3.42', rows: [] },
  { label: 'Mon · Jul 28', n: 25, cost: '$3.15', rows: [] },
  { label: 'Sun · Jul 27', n: 9, cost: '$0.98', rows: [] },
  { label: 'Sat · Jul 26', n: 11, cost: '$1.12', rows: [] },
  { label: 'Fri · Jul 25', n: 28, cost: '$3.55', rows: [] },
  { label: 'Thu · Jul 24', n: 18, cost: '$2.60', rows: [] },
];
const NOTES = [
  { n: '1', title: 'SAME SHAPE, EVERY DAY', body: 'The four sections never reorder, appear, or collapse based on content — empty sections stay put and say so. People build a five-second scanning habit around a stable layout; anything adaptive breaks the habit.' },
  { n: '2', title: 'DECISIONS INTERRUPT, NOTHING ELSE DOES', body: 'The only section allowed to demand attention. Framed as the agent deferring — “waiting on you”, never “denied” or “blocked”. Amber means a person is needed; red is never used, because stopping is the system working.' },
  { n: '3', title: 'DONE CARRIES THE WEIGHT', body: 'The reason anyone opens this page. Plain-language headlines, a timestamp, and a quiet trace link — the heaviest visual section after decisions.' },
  { n: '4', title: 'YOUR OWN ACTIONS STAY QUIET', body: 'Collapsed by default, no card, tertiary color. It exists so the timeline is complete — never to grade the person. The moment it reads as a score, this page is a surveillance tool.' },
  { n: '5', title: 'COST IS A FOOTNOTE', body: 'Small, tabular, factual — shown as headroom remaining, not consumption. Knowing what the agent costs builds trust; making it a headline turns the brief into a performance review.' },
  { n: '6', title: 'SEEN IS A CHOICE', body: 'Unseen dots clear only when the person says so — never on scroll. An abandoned tab must not silently consume a day of work. The action is designed to feel like clearing a desk, not submitting a form.' },
  { n: '7', title: 'AUTHORITY IS ALWAYS ON SCREEN', body: 'One chip, never hidden, answers “what can it do right now?” in under five seconds. ⏸ means it is holding at a boundary; ⏵⏵ means it is free to act. Scope is shown, not offered as a toggle — authority comes from the role, not from this page.' },
  { n: '8', title: 'A DECISION CARRIES ITS OWN EVIDENCE', body: 'Every waiting item shows what was attempted, how complete the context is, what was checked, why it stopped, and what happens if you never answer — in that order. Ten seconds should be enough, without leaving the brief. Silence never approves anything.' },
  { n: '9', title: 'ONE SCREEN HOLDS THE WHOLE THING', body: 'The map is the outermost zoom: perimeter, the night end to end, nine nights of record, and the live state of every surface — read in one pass, no scrolling between screens. It invents no numbers; every figure is the same record the brief and the log are close-ups of, and every panel is a door into that screen.' },
];

function initialState(props) {
  return {
    view: 'brief', scen: mapScen(props.scenario) || 'typical',
    sidebarOpen: true, ownOpen: false, annotate: false,
    acked: {}, items: {}, detailId: 'meridian', expanded: { 0: true },
    pastFilter: 'all', pastOpen: { 0: true },
    whyOpen: {}, toolOpen: {}, decAllOpen: false,
    chatInput: '', busy: false,
    thread: [
      { k: 'agent', text: 'Morning. Your brief is ready — seven things done overnight, two waiting on you. The client renewal quote is the time-sensitive one.', chip: 'THIS MORNING’S BRIEF', go: 'brief' },
      { k: 'user', text: 'Did the funnel report go out?' },
      { k: 'tool', label: 'CHECKED YOUR SENT MAIL', time: '08:02', body: 'Found it — “Weekly funnel report” left at 07:00 to the leadership list, as scheduled. Sign-ups up 8% week over week.' },
      { k: 'agent', text: 'Yes — 07:00, to the leadership list. Someone on the list already replied asking about the paid channel; I can draft an answer whenever you want.' },
      { k: 'limit', id: 'meridian', time: '08:03', title: 'The client renewal quote — $4,800', body: 'It’s over the $2,500 I can send alone, so it’s waiting on you. Drafted, checked against last year, ready to go.' },
    ],
  };
}

export function useMyDayState(props) {
  const [state, setStateRaw] = useState(() => initialState(props));
  const stateRef = useRef(state);
  stateRef.current = state;
  const chatRefObj = useRef(null);
  const replyTRef = useRef(null);
  const prevScenarioRef = useRef(props.scenario);

  const setState = useCallback((patch) => {
    setStateRaw((prev) => {
      const next = typeof patch === 'function' ? patch(prev) : patch;
      return { ...prev, ...next };
    });
  }, []);

  useEffect(() => {
    if (prevScenarioRef.current !== props.scenario) {
      prevScenarioRef.current = props.scenario;
      const m = mapScen(props.scenario) || 'typical';
      setState((s) => (m !== s.scen ? { ...s, scen: m, view: 'brief', annotate: false } : s));
    }
  }, [props.scenario, setState]);

  useEffect(() => {
    const keyH = (e) => { if ((e.metaKey || e.ctrlKey) && e.key === 'k') { e.preventDefault(); setState({ view: 'chat', annotate: false }); } };
    window.addEventListener('keydown', keyH);
    return () => { window.removeEventListener('keydown', keyH); clearTimeout(replyTRef.current); };
  }, [setState]);

  const scrollChat = useCallback(() => { const el = chatRefObj.current; if (el) el.scrollTop = el.scrollHeight; }, []);

  const scen = useCallback(() => {
    const s = stateRef.current;
    // Mobile glance has no loading treatment — the brief is generated on the laptop; the glance shows the last good brief.
    const S = (s.scen === 'loading' && s.view === 'mobile') ? 'typical' : s.scen;
    const base = { dec: ['meridian', 'linkedin'], done: DONE, own: OWN, grouped: false, spent: '$3.84', spentLabel: 'OVERNIGHT', week: '$137.40 of $250', left: '$112.60', pct: '45%', summary: 'A quiet, productive night. I finished seven pieces of work, spent $3.84, and stopped twice where I wanted your judgment — those two are first, below.' };
    if (S === 'quiet') return { ...base, dec: [], done: [], own: [], spent: '$0.61', week: '$137.40 of $250', summary: 'A quiet night — nothing needed doing, and nothing needed you. I checked the inbox at 23:00, 02:00 and 05:30 and the campaign queue hourly; all clear.' };
    if (S === 'dayone') return { ...base, dec: [], done: [], own: [], spent: '$0.00', week: '$0.00 of $250', left: '$250.00', pct: '100%', summary: '' };
    if (S === 'blocked') return { ...base, dec: ['meridian', 'linkedin', 'booth', 'bulk', 'tooltrial'], done: [], own: OWN.slice(0, 1), spent: '$2.10', summary: 'I kept reaching the edge of what I can do alone last night — five pieces of work are staged and waiting on your OK. Nothing is lost; each one runs the moment you decide.' };
    if (S === 'absence') return { ...base, dec: ['meridian', 'linkedin', 'booth'], grouped: true, spent: '$31.70', spentLabel: '9 DAYS', week: '$188.85 of $250', left: '$61.15', pct: '24%', summary: 'Welcome back. Over nine days I finished 212 routine pieces of work and kept just three for you. Days are grouped below — Friday was the busiest.' };
    return base;
  }, []);

  const itemState = useCallback((id) => stateRef.current.items[id] || 'pending', []);
  const pendingCount = useCallback(() => scen().dec.filter((id) => itemState(id) === 'pending').length, [scen, itemState]);
  const nav = useCallback((view) => setState({ view, annotate: false }), [setState]);
  const decide = useCallback((id, verdict) => setState((s) => ({ items: { ...s.items, [id]: verdict } })), [setState]);

  const askAbout = useCallback((id) => {
    const it = ITEMS[id];
    setState((s) => ({ view: 'chat', annotate: false, thread: [...s.thread, { k: 'user', text: 'About “' + it.short + '” — walk me through it?' }], busy: true }));
    replyTRef.current = setTimeout(() => {
      setState((s) => ({ busy: false, thread: [...s.thread, { k: 'agent', text: it.attempted + ' It’s paused because: ' + it.limitBody.charAt(0).toLowerCase() + it.limitBody.slice(1) + ' Approve it here, or take your time — nothing moves until you do.' }] }));
    }, 900);
    setTimeout(scrollChat, 950);
  }, [setState, scrollChat]);

  const askOwnerFn = useCallback(() => {
    setState((s) => ({ view: 'chat', annotate: false, thread: [...s.thread, { k: 'user', text: 'Can you draft a note to my access owner about raising my weekly headroom?' }], busy: true }));
    replyTRef.current = setTimeout(() => {
      setState((s) => ({ busy: false, thread: [...s.thread, { k: 'agent', text: 'Drafted: “Hi — my agent paused five times last night on routine marketing spend. Could we look at raising my weekly headroom from $250, or adding a standing exception for event deposits?” Want me to send it? Messages to your access owner are within your authority — it goes the moment you say so.' }] }));
    }, 1000);
    setTimeout(scrollChat, 1050);
  }, [setState, scrollChat]);

  const sendChatMsg = useCallback(() => {
    const t = stateRef.current.chatInput.trim();
    if (!t || stateRef.current.busy) return;
    setState((s) => ({ chatInput: '', busy: true, thread: [...s.thread, { k: 'user', text: t }] }));
    setTimeout(scrollChat, 50);
    replyTRef.current = setTimeout(() => {
      setState((s) => ({ busy: false, thread: [...s.thread, { k: 'tool', label: 'PICKED UP', time: '08:04', body: 'Added to this morning’s queue, running under your authority. Each step will appear here as it happens.' }, { k: 'agent', text: 'On it. If any part of this crosses a line I can’t cross alone, I’ll pause right here and ask — same as the brief.' }] }));
      setTimeout(scrollChat, 60);
    }, 1100);
  }, [setState, scrollChat]);

  const decVm = useCallback(() => {
    const s = stateRef.current;
    return scen().dec.map((id) => {
      const it = ITEMS[id], st = itemState(id), ev = EVID[id] || { checked: '', ready: 3, readyLabel: '', ifIgnored: '' };
      const fill = '#55595E', dim = '#E4E6EA';
      return {
        key: id, chipText: st === 'pending' ? 'WAITING ON YOU' : (st === 'approved' ? 'APPROVED' : 'SET ASIDE'),
        chipC: st === 'pending' ? '#B45309' : (st === 'approved' ? '#0F7B3A' : '#6E7378'),
        chipBg: st === 'pending' ? 'rgba(180,83,9,0.07)' : (st === 'approved' ? 'rgba(15,123,58,0.07)' : 'rgba(8,9,10,0.04)'),
        chipBd: st === 'pending' ? 'rgba(180,83,9,0.35)' : (st === 'approved' ? 'rgba(15,123,58,0.35)' : '#DADDE2'),
        title: it.title, why: it.why, approveL: it.approveL, rejectL: it.rejectL,
        actionsShow: st === 'pending', resolvedShow: st !== 'pending',
        resolvedText: st === 'approved' ? it.approvedNote : it.declinedNote,
        resolvedC: st === 'approved' ? '#0F7B3A' : '#8A8F96',
        checked: ev.checked, readyLabel: ev.readyLabel, ifIgnored: ev.ifIgnored,
        g1: fill, g2: ev.ready >= 2 ? fill : dim, g3: ev.ready >= 3 ? fill : dim,
        limitLabel: it.limit, limitBody: it.limitBody,
        whyDisp: s.whyOpen[id] ? 'flex' : 'none',
        whyLabel: s.whyOpen[id] ? 'HIDE THE BOUNDARY' : 'WHY AM I BEING ASKED?',
        whyChev: s.whyOpen[id] ? 'rotate(180deg)' : 'rotate(0deg)',
        toggleWhy: () => setState((st2) => ({ whyOpen: { ...st2.whyOpen, [id]: !st2.whyOpen[id] } })),
        open: () => setState({ view: 'detail', detailId: id, annotate: false }),
        approve: () => decide(id, 'approved'), reject: () => decide(id, 'declined'), ask: () => askAbout(id),
      };
    });
  }, [scen, itemState, setState, decide, askAbout]);

  return buildViewModel({ props, state, setState, chatRefObj, scen, itemState, pendingCount, nav, decide, askAbout, askOwnerFn, sendChatMsg, decVm });
}

function buildViewModel(ctx) {
  const { props, state: s, setState, chatRefObj, scen: scenFn, itemState, pendingCount: pendingCountFn, nav, decide, askAbout, askOwnerFn, sendChatMsg, decVm: decVmFn } = ctx;
  const sc = scenFn();
  const S = (s.scen === 'loading' && s.view === 'mobile') ? 'typical' : s.scen;
  const name = props.personName ?? 'Y Combinator';
  const acked = !!s.acked[S];
  const pending = pendingCountFn();
  const isLoading = S === 'loading';
  const hasNew = !isLoading && (sc.done.length > 0 || sc.grouped);
  const viewNames = { brief: 'Morning brief', chat: 'Co-work', auth: 'What your agent can do', map: 'Agent map', past: 'Past tasks', detail: 'Paused item', mobile: 'Mobile glance' };
  const pKind = { agent: { dot: 'var(--accent,#0047FF)', chip: '', c: '', bg: '', bd: '' }, approved: { dot: '#0F7B3A', chip: 'YOU APPROVED', c: '#0F7B3A', bg: 'rgba(15,123,58,0.07)', bd: 'rgba(15,123,58,0.35)' }, declined: { dot: '#9AA0AC', chip: 'YOU SAID NO', c: '#6E7378', bg: 'rgba(8,9,10,0.04)', bd: '#DADDE2' } };
  const pMatch = (r) => s.pastFilter === 'all' || (s.pastFilter === 'agent' ? r.k === 'agent' : r.k !== 'agent');
  const pDays = PAST.map((d, i) => ({ d, i, rows: d.rows.filter(pMatch) })).filter((g) => g.rows.length > 0);
  const pShown = pDays.reduce((n, g) => n + g.rows.length, 0);
  const pAll = PAST.reduce((n, d) => n + d.rows.length, 0);
  const pYours = PAST.reduce((n, d) => n + d.rows.filter((r) => r.k !== 'agent').length, 0);
  const pastDays = pDays.map((g) => ({
    label: g.d.label, cost: g.d.cost,
    meta: g.rows.length + (g.rows.length === 1 ? ' entry' : ' entries'),
    toggle: () => setState((st) => ({ pastOpen: { ...st.pastOpen, [g.i]: !st.pastOpen[g.i] } })),
    chev: s.pastOpen[g.i] ? 'rotate(180deg)' : 'rotate(0deg)',
    rowsDisp: s.pastOpen[g.i] ? 'flex' : 'none',
    rows: g.rows.map((r) => ({
      t: r.t, h: r.h, dot: pKind[r.k].dot, chip: pKind[r.k].chip,
      chipDisp: r.k === 'agent' ? 'none' : 'inline-block',
      chipC: pKind[r.k].c, chipBg: pKind[r.k].bg, chipBd: pKind[r.k].bd,
      open: () => setState({ view: 'detail', detailId: 'meridian', annotate: false }),
    })),
  }));
  const hour = S === 'dayone' ? 'Welcome' : 'Good morning';
  const dotOp = acked ? '0' : '1';
  const doneRows = sc.done.map((r) => ({ ...r, dotOp, open: () => setState({ view: 'detail', detailId: 'meridian', annotate: false }) }));
  const doneGroups = DAYS.map((d, i) => ({
    label: d.label, meta: d.n + ' pieces of work — all inside your authority', cost: d.cost, dotOp,
    toggle: () => setState((st) => ({ expanded: { ...st.expanded, [i]: !st.expanded[i] } })),
    chev: s.expanded[i] ? 'rotate(180deg)' : 'rotate(0deg)',
    rowsDisp: s.expanded[i] ? 'flex' : 'none',
    rows: d.rows, moreShow: s.expanded[i], moreLabel: d.rows.length ? '+ ' + (d.n - d.rows.length) + ' MORE FROM ' + d.label.split(' ·')[0].toUpperCase() + ' — OPEN THE FULL DAY →' : 'OPEN THE FULL DAY →',
  }));
  const agentByScen = { typical: ['#0F7B3A', 'pulseDot 2.2s ease-in-out infinite', 'agent on watch'], quiet: ['#9AA0AC', 'none', 'agent resting'], dayone: ['#9AA0AC', 'none', 'ready when you are'], blocked: ['#B45309', 'pulseDot 2.2s ease-in-out infinite', 'waiting on you · ' + pending], absence: ['#0F7B3A', 'pulseDot 2.2s ease-in-out infinite', 'agent on watch'], loading: ['#0047FF', 'pulseDot 1.2s ease-in-out infinite', 'writing your brief…'] };
  const ag = agentByScen[S] || agentByScen.typical;
  const modeSpec = isLoading
    ? ['⏵⏵', 'WORKING NOW', 'var(--accent,#0047FF)', 'color-mix(in srgb, var(--accent,#0047FF) 5%, #FFFFFF)', 'color-mix(in srgb, var(--accent,#0047FF) 28%, #FFFFFF)', 'Your agent is writing this morning’s brief — acting inside your authority. Nothing here needs you yet.']
    : S === 'dayone'
      ? ['⏸', 'NOT STARTED YET', '#6E7378', 'rgba(8,9,10,0.025)', '#DADDE2', 'Your agent already carries your authority but hasn’t begun. It starts tonight, and this chip will say so.']
      : pending > 0
        ? ['⏸', 'HOLDING · ' + pending + ' AT A BOUNDARY', '#B45309', 'rgba(180,83,9,0.05)', 'rgba(180,83,9,0.35)', 'Still acting freely inside your authority — but ' + pending + (pending === 1 ? ' piece of work reached' : ' pieces of work reached') + ' the edge of what it can do alone and is waiting on you. Nothing failed.']
        : ['⏵⏵', 'ACTING WITH YOUR AUTHORITY', '#0F7B3A', 'rgba(15,123,58,0.05)', 'rgba(15,123,58,0.3)', 'Free to act — within your role’s reach and your weekly spending headroom, checked before every action. Nothing is waiting on you.'];
  const dates = { typical: 'TUESDAY · AUGUST 4 · 07:58', quiet: 'WEDNESDAY · AUGUST 5 · 07:31', dayone: 'MONDAY · AUGUST 3 · 09:12', blocked: 'THURSDAY · AUGUST 6 · 07:44', absence: 'MONDAY · AUGUST 4 · 08:15', loading: 'TUESDAY · AUGUST 4 · 07:58' };
  const greetings = { dayone: hour + ', ' + name + '.', absence: 'Welcome back, ' + name + '.', typical: 'Good morning, ' + name + '.', quiet: 'Good morning, ' + name + '.', blocked: 'Good morning, ' + name + '.', loading: 'Good morning, ' + name + '.' };
  const decEmptyCopy = { quiet: ['Nothing is waiting on you.', 'Your agent stayed inside its lines all night.'], dayone: ['Nothing yet.', 'When your agent needs your OK, the item waits right here — with the choice in your hands.'], typical: ['Nothing is waiting on you.', ''], absence: ['', ''], blocked: ['', ''], loading: ['', ''] };
  const doneEmptyCopy = { quiet: ['A quiet night — and that’s a good one.', 'Your agent kept watch: inbox at 23:00, 02:00 and 05:30, campaign queue hourly. Nothing needed doing, so nothing was done.'], dayone: ['Tomorrow morning, the night’s work lands here.', 'One plain line per finished piece, with the time and a full trace behind each.'], blocked: ['Nothing finished overnight.', 'Each piece of work your agent started reached a line that needs you — decide above and it all runs.'], typical: ['', ''], absence: ['', ''], loading: ['', ''] };
  const ownEmptyCopy = { quiet: 'Nothing here — you were off. That’s the point.', dayone: 'Your own actions will round out the timeline, so the story is whole.', blocked: 'Just one action yesterday — shown when expanded.', typical: '', absence: '', loading: '' };
  const it = ITEMS[s.detailId] || ITEMS.meridian;
  const dSt = itemState(s.detailId);
  const decAll = isLoading ? [] : decVmFn();
  const genStamps = { typical: 'BRIEF WRITTEN AT 6:12 AM', quiet: 'BRIEF WRITTEN AT 6:04 AM', blocked: 'BRIEF WRITTEN AT 6:20 AM', absence: 'BRIEF WRITTEN AT 6:08 AM, COVERING 9 DAYS', loading: 'BRIEF WRITTEN AT 6:12 AM', dayone: '' };
  const unseen = sc.grouped ? '215 entries across 9 days are new.' : sc.done.length + ' entries are new since you last looked.';
  const authGroups = [
    { label: 'COMMUNICATE', rows: [
      { s: 'Write and send email as you.', note: 'Same address, same signature — recipients see you.', noteShow: true, prov: 'WITH YOUR ROLE', dot: '#0F7B3A' },
      { s: 'Post to the company LinkedIn and blog.', note: 'Public posts always wait for your OK first — like this morning’s teaser.', noteShow: true, prov: 'WITH YOUR ROLE', dot: '#B45309' },
      { s: 'Message your team and partners in Slack.', note: '', noteShow: false, prov: 'WITH EVERY SEAT', dot: '#0F7B3A' },
    ] },
    { label: 'SPEND', rows: [
      { s: 'Buy and renew marketing tools — up to $250 a week.', note: 'Bigger amounts pause and ask you.', noteShow: true, prov: 'WITH YOUR ROLE', dot: '#0F7B3A' },
      { s: 'Send quotes up to $2,500 on its own.', note: 'Above that, quotes wait for your click — like this morning’s renewal quote.', noteShow: true, prov: 'WITH YOUR ROLE', dot: '#B45309' },
    ] },
    { label: 'REACH', rows: [
      { s: 'Read and organize the Marketing folder in Drive.', note: '', noteShow: false, prov: 'WITH YOUR ROLE', dot: '#0F7B3A' },
      { s: 'Read the CRM; edit campaign fields only.', note: '', noteShow: false, prov: 'WITH YOUR ROLE', dot: '#0F7B3A' },
      { s: 'See the company calendar and book rooms.', note: '', noteShow: false, prov: 'WITH EVERY SEAT', dot: '#0F7B3A' },
    ] },
  ];
  const mapDoneN = sc.grouped ? 212 : sc.done.length;
  const mapNights = sc.grouped ? 9 : 1;
  const nOr = (n) => (isLoading ? '…' : String(n));
  const mapFlow = [
    { value: nOr(mapNights === 9 ? '9' : (S === 'dayone' ? '0' : '3')), label: mapNights === 9 ? 'NIGHTS WATCHED' : 'INBOX CHECKS', sub: S === 'dayone' ? 'starts tonight' : (mapNights === 9 ? 'while you were away' : 'plus the queue, hourly'), c: '#55595E', bd: '#E4E6EA', bg: '#FFFFFF', view: 'past' },
    { value: nOr(mapDoneN), label: 'DONE ALONE', sub: 'inside your authority, no asking', c: 'var(--accent,#0047FF)', bd: 'color-mix(in srgb, var(--accent,#0047FF) 30%, #FFFFFF)', bg: 'color-mix(in srgb, var(--accent,#0047FF) 4%, #FFFFFF)', view: 'brief' },
    { value: nOr(decAll.length), label: 'REACHED A BOUNDARY', sub: 'stopped on purpose, nothing failed', c: '#08090A', bd: '#E4E6EA', bg: '#FFFFFF', view: 'brief' },
    { value: nOr(pending), label: 'WAITING ON YOU', sub: pending > 0 ? 'nothing moves until you decide' : 'your desk is clear', c: pending > 0 ? '#B45309' : '#0F7B3A', bd: pending > 0 ? 'rgba(180,83,9,0.35)' : '#E4E6EA', bg: pending > 0 ? 'rgba(180,83,9,0.05)' : '#FFFFFF', view: 'brief' },
  ].map((f, i) => ({ ...f, arrowDisp: i === 0 ? 'none' : 'inline', click: () => nav(f.view) }));
  const mapInside = [
    { t: 'Writes and sends email as you' },
    { t: 'Buys and renews tools up to $250 a week' },
    { t: 'Sends quotes up to $2,500 on its own' },
    { t: 'Reads Marketing’s Drive, the CRM and the calendar' },
  ];
  const edgeN = {};
  decAll.forEach((d) => { edgeN[d.limitLabel] = (edgeN[d.limitLabel] || 0) + 1; });
  const mapEdges = [
    ['OVER THE SOLO SEND LINE', 'Quotes over $2,500'],
    ['PUBLIC, IN THE COMPANY’S NAME', 'Anything public in the company’s name'],
    ['PAST THIS WEEK’S HEADROOM', 'Spending past this week’s headroom'],
    ['REACHES EVERYONE AT ONCE', 'A message that reaches everyone at once'],
    ['NEW VENDOR, NEW TERMS', 'Terms with a company we haven’t used'],
  ].map(([k, t]) => ({ t, c: edgeN[k] ? '#26292D' : '#8A8F96', n: edgeN[k] ? edgeN[k] + ' WAITING' : 'CLEAR', nC: edgeN[k] ? '#B45309' : '#C6CAD1' }));
  const peak = DAYS.reduce((m, d) => (d.n > m.n ? d : m), DAYS[0]);
  const mapDays = DAYS.slice().reverse().map((d) => ({
    n: String(d.n), label: d.label.split('·')[1].trim(),
    h: Math.round(14 + (d.n / peak.n) * 48) + 'px',
    bg: d.n === peak.n ? 'var(--accent,#0047FF)' : 'color-mix(in srgb, var(--accent,#0047FF) 26%, #E4E6EA)',
  }));
  const daysSum = DAYS.reduce((n, d) => n + d.n, 0);
  const mapSurfaces = [
    { label: 'Morning brief', dot: pending > 0 ? '#B45309' : '#0F7B3A', status: isLoading ? 'Being written right now' : (pending > 0 ? pending + ' waiting on you · ' + mapDoneN + ' finished' : mapDoneN + ' finished · nothing waiting'), view: 'brief' },
    { label: 'Co-work', dot: 'var(--accent,#0047FF)', status: s.thread.length + ' messages · every tool call inline', view: 'chat' },
    { label: 'What it can do', dot: '#0F7B3A', status: '3 areas · 8 standing permissions · read-only', view: 'auth' },
    { label: 'Past tasks', dot: '#9AA0AC', status: pAll + ' pieces of work · ' + pYours + ' your call', view: 'past' },
  ].map((x) => ({ ...x, click: () => nav(x.view) }));
  const toolLines = (b) => Math.max(2, Math.round((b || '').length / 40));
  const threadVm = s.thread.map((m, i) => ({
    isUser: m.k === 'user', isAgent: m.k === 'agent', isTool: m.k === 'tool', isLimit: m.k === 'limit',
    text: m.text || '', time: m.time || '', toolLabel: m.label || '', toolBody: m.body || '',
    chipShow: !!m.chip, chipLabel: m.chip || '', chipGo: () => nav(m.go || 'brief'),
    toolBodyDisp: s.toolOpen[i] ? 'block' : 'none',
    toolMoreLabel: s.toolOpen[i] ? 'COLLAPSE' : '+' + toolLines(m.body) + ' LINES',
    toolToggle: () => setState((st) => ({ toolOpen: { ...st.toolOpen, [i]: !st.toolOpen[i] } })),
    limitTitle: m.title || '', limitBody: m.body || '',
    limitActionsShow: m.k === 'limit' && itemState(m.id) === 'pending',
    limitResolvedShow: m.k === 'limit' && itemState(m.id) !== 'pending',
    limitResolvedText: m.k === 'limit' ? (itemState(m.id) === 'approved' ? ITEMS[m.id].approvedNote : ITEMS[m.id].declinedNote) : '',
    limitResolvedC: m.k === 'limit' && itemState(m.id) === 'approved' ? '#0F7B3A' : '#8A8F96',
    limitApprove: () => decide(m.id, 'approved'),
    limitDetail: () => setState({ view: 'detail', detailId: m.id, annotate: false }),
  }));
  const pill = (on) => (on ? { bg: '#08090A', c: '#FFFFFF', bd: '#08090A' } : { bg: 'transparent', c: '#6E7378', bd: 'transparent' });
  const navItem = (label, icon, view, badge) => ({
    label, icon, badge: badge || '', badgeDisp: badge && s.sidebarOpen ? 'inline-block' : 'none',
    bg: s.view === view ? 'rgba(8,9,10,0.055)' : 'transparent', c: s.view === view ? '#08090A' : '#55595E',
    click: () => nav(view),
  });

  return {
    accentVar: props.accent ?? '#0047FF',
    crumb: viewNames[s.view] || 'Morning brief', initials: name.slice(0, 1).toUpperCase() + 'R',
    agentDotC: ag[0], agentDotAnim: ag[1], agentStatus: ag[2], topBudget: sc.left,
    modeGlyph: modeSpec[0], modeLabel: modeSpec[1], modeC: modeSpec[2], modeBg: modeSpec[3], modeBd: modeSpec[4], modeTip: modeSpec[5],
    sidebarW: s.sidebarOpen ? '224px' : '56px', labelDisp: s.sidebarOpen ? 'block' : 'none',
    collapseTf: s.sidebarOpen ? 'rotate(0deg)' : 'rotate(180deg)',
    toggleSidebar: () => setState({ sidebarOpen: !s.sidebarOpen }),
    navGroups: [
      { label: 'TODAY', items: [navItem('Morning Brief', 'M8 1.6v1.9M8 12.5v1.9M1.6 8h1.9M12.5 8h1.9M3.5 3.5l1.3 1.3M11.2 11.2l1.3 1.3M12.5 3.5l-1.3 1.3M4.8 11.2l-1.3 1.3M8 5.4a2.6 2.6 0 1 0 0 5.2 2.6 2.6 0 0 0 0-5.2', 'brief', pending ? String(pending) : ''), navItem('Co-work', 'M3 2.5h10a1.5 1.5 0 0 1 1.5 1.5v5.5a1.5 1.5 0 0 1-1.5 1.5H7.2L4 13.5v-2.5H3a1.5 1.5 0 0 1-1.5-1.5V4A1.5 1.5 0 0 1 3 2.5z', 'chat', '')] },
      { label: 'YOUR AGENT', items: [navItem('Agent map', 'M8 1.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13M1.5 8h13M8 1.5c1.7 1.8 2.6 4.1 2.6 6.5S9.7 12.7 8 14.5c-1.7-1.8-2.6-4.1-2.6-6.5S6.3 3.3 8 1.5', 'map', ''), navItem('What it can do', 'M8 1.7l5.2 2v4.2c0 3.3-2.2 5.5-5.2 6.9-3-1.4-5.2-3.6-5.2-6.9V3.7l5.2-2zM5.8 8.2l1.6 1.6 2.9-3.2', 'auth', ''), navItem('Past tasks', 'M8 4.2V8l2.6 1.6M14 8A6 6 0 1 1 8 2a6 6 0 0 1 6 6z', 'past', '')] },
    ],
    isBrief: s.view === 'brief', isChat: s.view === 'chat', isAuth: s.view === 'auth', isPast: s.view === 'past', isDetail: s.view === 'detail', isMobile: s.view === 'mobile',
    isMap: s.view === 'map', goPast: () => nav('past'),
    mapStamp: genStamps[S] || 'NOTHING RECORDED YET',
    mapWaitShow: pending > 0, mapWaitText: pending === 1 ? 'One piece of work is waiting on you — everything else is done or moving.' : pending + ' pieces of work are waiting on you — everything else is done or moving.',
    mapFlowTitle: sc.grouped ? 'NINE NIGHTS, END TO END' : 'LAST NIGHT, END TO END',
    mapFlow, mapInside, mapEdges, mapDays, mapSurfaces,
    mapLeft: sc.left, mapSpent: sc.spent, mapSpentLabel: sc.spentLabel,
    mapDaysTotal: daysSum + ' PIECES OF WORK', mapDaysYours: pYours + ' NEEDED YOU', mapDaysPeak: peak.label.toUpperCase(),
    pastDays, pastEmptyShow: pastDays.length === 0, pastEmptyText: 'Nothing matches that filter in the last 90 days.',
    pastCount: pShown === pAll ? pAll + ' ENTRIES' : pShown + ' OF ' + pAll + ' ENTRIES',
    pastTotal: pAll + ' PIECES OF WORK · ' + pYours + ' YOUR CALL', pastCost: '$13.17',
    pastFilters: [['all', 'ALL'], ['agent', 'DONE ALONE'], ['you', 'YOUR DECISIONS']].map(([k, label]) => ({ label, ...pill(s.pastFilter === k), pick: () => setState({ pastFilter: k }) })),
    goChat: () => nav('chat'), goBrief: () => nav('brief'), goAuth: () => nav('auth'), askOwner: () => askOwnerFn(),
    isLoading, annotate: s.annotate, notes: NOTES,
    introShow: S === 'dayone', absenceShow: S === 'absence',
    dateLine: dates[S], greeting: greetings[S],
    summaryShow: !isLoading && !!sc.summary, summaryText: sc.summary,
    decItems: isLoading ? [] : (decAll.length > 3 && !s.decAllOpen ? decAll.slice(0, 3) : decAll),
    decMoreShow: !isLoading && decAll.length > 3,
    decMoreLabel: s.decAllOpen ? 'SHOW THREE AT A TIME' : '+ ' + (decAll.length - 3) + ' MORE WAITING — SHOW ALL ' + decAll.length,
    decMoreClick: () => setState({ decAllOpen: !s.decAllOpen }),
    genStampShow: !isLoading && S !== 'dayone', genStamp: genStamps[S] || genStamps.typical,
    decEmptyShow: !isLoading && sc.dec.length === 0, decEmptyTitle: (decEmptyCopy[S] || ['', ''])[0], decEmptyBody: (decEmptyCopy[S] || ['', ''])[1],
    decCountLabel: isLoading ? '…' : (pending > 0 ? pending + ' WAITING' : 'ALL CLEAR'), decCountC: pending > 0 ? '#B45309' : '#0F7B3A',
    blockedBandShow: S === 'blocked' && !isLoading,
    doneCountLabel: isLoading ? '…' : (sc.grouped ? '212 FINISHED · 9 DAYS' : (sc.done.length ? sc.done.length + ' FINISHED' : 'NONE')),
    doneFlatShow: !isLoading && !sc.grouped && sc.done.length > 0, doneRows,
    doneGroupedShow: !isLoading && sc.grouped, doneGroups,
    doneEmptyShow: !isLoading && !sc.grouped && sc.done.length === 0, doneEmptyTitle: (doneEmptyCopy[S] || ['', ''])[0], doneEmptyBody: (doneEmptyCopy[S] || ['', ''])[1],
    ownCountLabel: isLoading ? '…' : (sc.own.length ? sc.own.length + ' · ' + (s.ownOpen ? 'HIDE' : 'SHOW') : 'NONE'),
    ownToggle: () => setState({ ownOpen: !s.ownOpen }), ownChev: s.ownOpen ? 'rotate(180deg)' : 'rotate(0deg)',
    ownRowsShow: !isLoading && s.ownOpen && sc.own.length > 0, ownRows: sc.own,
    ownEmptyShow: !isLoading && (sc.own.length === 0 || !s.ownOpen), ownEmptyText: sc.own.length === 0 ? (ownEmptyCopy[S] || 'Nothing in this window.') : 'Collapsed — your own trail is here for completeness, not review.',
    costShow: !isLoading, costSpent: sc.spent, costSpentLabel: sc.spentLabel, costWeek: sc.week, costLeft: sc.left, costPct: sc.pct,
    ackBtnShow: hasNew && !acked, ackDoneShow: hasNew && acked,
    ackNoneShow: !hasNew && !isLoading, ackNoneText: S === 'dayone' ? 'NOTHING TO MARK YET — YOUR DESK IS ALREADY CLEAR' : 'NOTHING NEW TO MARK. YOU’RE CURRENT.',
    ackCountText: unseen, ackBtnLabel: sc.grouped ? 'MARK ALL 9 DAYS AS SEEN' : 'MARK MORNING AS SEEN',
    ackClick: () => setState((st) => ({ acked: { ...st.acked, [S]: true } })),
    ackSeenText: 'SEEN AT 8:41 — DESK CLEARED',
    threadVm, chatBusy: s.busy, chatRef: chatRefObj,
    chatInput: s.chatInput, onChatInput: (e) => setState({ chatInput: e.target.value }),
    chatKey: (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChatMsg(); } },
    sendChat: () => sendChatMsg(), sendOpacity: s.chatInput.trim() ? '1' : '0.45',
    glanceRows: [
      { t: 'Write and send email as you', dot: '#0F7B3A' },
      { t: 'Draft anywhere; public posts wait for your OK', dot: '#B45309' },
      { t: 'Spend up to $250 a week on tools and renewals', dot: '#0F7B3A' },
      { t: 'Read Marketing’s Drive, CRM and calendar', dot: '#0F7B3A' },
    ], glanceLeft: sc.left + ' LEFT', glancePct: sc.pct,
    authGroups, authLeft: sc.left, authCap: '$250', authPct: sc.pct,
    dChip: 'WAITING ON YOU', dTime: it.time, dTitle: it.title, dAttempted: it.attempted,
    dDraftShow: !!it.draft, dDraft: it.draft, dLimitLabel: it.limit, dLimitBody: it.limitBody,
    dYes: it.yes, dNo: it.no, dApproveL: it.approveL, dRejectL: it.rejectL,
    dTrace: it.trace.map((t, i) => ({ t: t[0], s: t[1], dot: i === it.trace.length - 1 ? '#B45309' : '#C6CAD1', c: i === it.trace.length - 1 ? '#B45309' : '#26292D' })),
    dActionsShow: dSt === 'pending', dStatusShow: dSt !== 'pending',
    dStatusText: dSt === 'approved' ? it.approvedNote + ' You can close this — it’s in motion.' : it.declinedNote + ' Nothing happened, and nothing will until you say so.',
    dStatusC: dSt === 'approved' ? '#0F7B3A' : '#9AA0AC',
    dApprove: () => decide(s.detailId, 'approved'), dReject: () => decide(s.detailId, 'declined'), dAsk: () => askAbout(s.detailId),
    mSummary: sc.summary || 'Your agent starts tonight. Tomorrow this glance shows the night’s work.',
    mDone: (sc.grouped ? DAYS.slice(0, 5).map((d) => ({ h: d.label + ' — ' + d.n + ' pieces of work', t: d.cost, dotOp })) : doneRows.slice(0, 5)),
  };
}
