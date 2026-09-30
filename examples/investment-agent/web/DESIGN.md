# Meridian — Investment Research Agent: design brief

Owner: Design division (UX research → UX architecture → UI → brand → build → finish gate),
paired with Frontend + Data Visualization engineering. Scope: `web/` only. API: `docs/CONTRACT.md`.

---

## 1. Product lens (UX Researcher)

**Name.** *Meridian*. A meridian is a line you take bearings from: a steady reference
rather than a signal to act on. The name is neutral and not "alpha", "moon" or "rocket".
The full lockup is "Meridian — Investment Research Agent". Space-constrained surfaces show only "Meridian".

### Target users (assumption-labelled; there has been no primary research yet)

| Persona | Context | What they need from the screen | What breaks trust |
|---|---|---|---|
| **Self-directed investor** ("Priya", 38, manages her own ISA/401k, checks in weekly) | Evening sessions, laptop or phone. Holds 5–15 positions and ETFs. | A plain-English verdict with the evidence *next to* it. She must be able to see what the agent looked at. | Hype, "buy now" language, confident numbers with no sources, casino-like color flashing |
| **Analyst / associate** ("Marco", 29, equity research at a small fund) | Desktop, second monitor, many tickers a day | Speed, dense and scannable output, exportable Markdown reports, and a record of which tools and data were used | Opaque reasoning, reports he can't copy into his own notes, a layout that wastes space |

### Jobs-to-be-done
1. **When** I'm considering a position, **I want** a structured research report on one ticker
   (fundamentals, technicals/risk, news sentiment, key risks) **so I can** decide whether to dig deeper.
2. **When** I'm choosing between names, **I want** a side-by-side comparison **so I can** see trade-offs quickly.
3. **When** I review my holdings, **I want** a portfolio critique (concentration, correlation, drawdown)
   **so I can** see the risks I haven't noticed.
4. **While** the agent works, **I want** to see its plan and which specialist is doing what
   **so I can** trust (or stop) the process.
5. **After** the run, **I want** the deliverable as a file (copy / download `.md`) **so I can** keep it.

### High-frequency vs high-risk
- *Daily/frequent:* glance at the watchlist, ask a question, read the answer.
- *Rare but high-risk:* acting on a wrong or stale number. Mitigations: the "data may be delayed"
  disclaimer is always visible, the tool outputs can be inspected, and the persona colors never
  mean "good" or "bad".

---

## 2. Information architecture (UX Architect)

```
Meridian
├── Header ─ brand · model badge (/api/health) · New research · theme (System/Light/Dark)
├── Watchlist (left rail)      localStorage; rows → /api/quote/{t}
│     ticker · name · price · Δ% (glyph+sign+color) · 30-close sparkline · remove
├── Conversation (center)      primary workspace
│     empty state: purpose line · 4 prompt chips · specialist roster
│     turns: user query → streamed Markdown answer (sanitized)
│     composer: textarea · Send / Stop (AbortController) · live status (aria-live)
├── Context panel (right, tabs)
│     Plan      ← `todos` events (pending / in progress / done, progress count)
│     Activity  ← `tool_start` / `tool_end` grouped by agent, expandable args/output
│     Reports   ← `files` events + GET /threads/{id}/files; viewer, Copy, Download .md
└── Footer ─ "Research only. Not investment advice. Data may be delayed." (always visible)
```

### Desktop wireframe (≥ 901px)

```
┌───────────────────────────────────────────────────────────────────────────────────────┐
│ ◐ Meridian  Investment Research Agent        [model: claude-…]  [+ New research] [◐☼☾] │
├──────────────┬───────────────────────────────────────────────┬────────────────────────┤
│ WATCHLIST    │                                               │ [Plan][Activity][Reports]│
│ [Add ticker+]│  You: Full research report on NVDA            │ Plan · 3 of 6 done      │
│ NVDA  ╱╲╱‾   │                                               │ ✓ Pull fundamentals     │
│ 181.20 ▲1.8% │  ## NVIDIA (NVDA) — research summary          │ ◔ Price & risk metrics  │
│ MSFT  ‾╲╱╲   │  Streaming markdown… tables, lists            │ ○ News sentiment        │
│ 512.04 ▼0.4% │                                               │                         │
│ …            │                                               │ Activity (grouped)      │
│              │                                               │ [QA] quant-analyst  2   │
│              │  ─────────────────────────────────────────    │  ▸ get_price_history ✓  │
│              │  ● Quant analyst is running get_price_history │  ▸ compute_risk… ⟳      │
│              │  ┌──────────────────────────────────┐ [Send]  │                         │
│              │  │ Ask about a ticker or portfolio… │ [Stop]  │                         │
│              │  └──────────────────────────────────┘         │                         │
├──────────────┴───────────────────────────────────────────────┴────────────────────────┤
│ ⓘ Research only. Not investment advice. Data may be delayed.                           │
└───────────────────────────────────────────────────────────────────────────────────────┘
  264px rail              fluid (answer max 72ch)                    minmax(320px, 380px)
```

### Mobile wireframe (≤ 900px, verified at 375px)

```
┌─────────────────────────────┐
│ ◐ Meridian        [+] [◐]   │  header (model badge hidden < 600px)
├─────────────────────────────┤
│ Research Watch Plan Act Rep │  sticky segmented view switcher (counts as badges)
├─────────────────────────────┤
│  one view at a time,        │
│  full width, own scroll     │
│                             │
│  [composer — Research view] │
├─────────────────────────────┤
│ ⓘ Research only. Not invest…│  footer (always visible, wraps)
└─────────────────────────────┘
```
Responsive priority: the conversation is the default view. The watchlist, plan, activity and
reports become sibling views instead of cards stacked under the chat, so each keeps its full
height and scanning behavior. Nothing scrolls horizontally, and wide tables inside answers scroll inside their own box.

### Design contract (Finish-Gate template)

- **User + job:** An investor or analyst asks for research and reads a sourced answer.
- **First-read object:** The answer document. In the empty state it is the prompt chips.
- **Primary action:** Send a research question, then Stop if needed.
- **Density decision:** *Balanced-compact.* The rails are dense (13px, tabular numbers) and the answer
  is roomy (16px, 1.6 line height, 72ch). Analysts get density where they scan and prose room where they read.
- **Hierarchy:** answer > live status > plan progress > activity detail > watchlist.
- **Interaction model:** a document feed next to an inspector (similar to an IDE's editor and side inspector).
- **References (pattern → lesson):** Bloomberg/Koyfin watchlists → a dense, right-aligned tabular
  numeric column. Perplexity → the answer is a document, and sources and steps live beside it. GitHub Actions
  logs → collapsible steps with status icons and durations. Linear side panel → a quiet inspector with tabs.
  None are copied. Only these patterns are taken.
- **Forbidden defaults:** no gradients or glassmorphism; no hero banner; no four-KPI card grid;
  no neon green/red; no confetti or celebratory motion; no "🚀"; no hue-only status; no
  chat bubbles for the long-form answer.
- **Finish evidence:** `web/screenshots/*.png` covers desktop light and dark, mid-stream and finished, plus 375px.

---

## 3. Design tokens (UI Designer)

All tokens are CSS custom properties on `:root`. Dark mode is applied by
`@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {…} }` and by
`:root[data-theme="dark"] {…}`, so the user's choice overrides the system setting.

### Color: neutrals are warm paper in light mode and graphite in dark mode; the accent is "meridian ink" blue

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | `#f5f5f2` | `#0e1116` | page |
| `--surface` | `#ffffff` | `#151a21` | panels, composer |
| `--surface-2` | `#efefea` | `#1b2129` | hover, code, chips |
| `--border` | `#dddcd4` | `#2a313b` | hairlines |
| `--border-strong` | `#8f8e85` | `#687282` | inputs (≥3:1 non-text contrast) |
| `--text` | `#15171c` | `#e8eaed` | body |
| `--text-2` | `#484d57` | `#b3b9c2` | secondary |
| `--text-3` | `#62676f` | `#8f96a0` | meta (≥4.5:1 on surface & bg) |
| `--accent` | `#2448a8` | `#8cadff` | primary action, links, focus |
| `--on-accent` | `#ffffff` | `#0b1020` | text on accent |
| `--up` | `#1a7342` | `#62c98f` | rise (always with ▲ and +) |
| `--down` | `#b1261c` | `#ff8e80` | fall (always with ▼ and −) |
| `--warn` | `#8a5700` | `#e8b45a` | errors/warnings (with ⚠ icon + text) |
| `--focus` | `#2448a8` | `#8cadff` | 2px outline + 2px offset |

**Direction is never carried by hue alone.** Every change shows an arrow glyph (▲/▼/■ flat),
an explicit sign (+/−) and color. Sparklines are drawn in **neutral ink** (`--text-2`) with a dashed
reference line at the first close and a dot on the last close. Their shape shows the 30-close trend, so they
are never tinted by *today's* change: an early build did this, and a stock that rose over 30 days but fell
today got a red, rising line, which misleads the reader.

**Agent identity** uses a categorical palette. Color is always paired with a two-letter monogram
and the agent's name, so identity survives CVD and grayscale. The colors mean "who", never "good or bad".

| Agent | Mono | Light fg / tint | Dark fg / tint |
|---|---|---|---|
| orchestrator | OR | `#3d4a5c` / `#e6e9ee` | `#c3ccd8` / `#262d37` |
| fundamental-analyst | FA | `#3f3fb5` / `#e7e7f8` | `#aeb0ff` / `#23244a` |
| quant-analyst | QA | `#0b6b66` / `#dcf1ef` | `#6fd6cc` / `#12302f` |
| news-sentiment-analyst | NS | `#865300` / `#f6ecd9` | `#e9bb6a` / `#33280f` |
| risk-manager | RM | `#a3284f` / `#f7e1e8` | `#ff9dbb` / `#3a1a25` |
| portfolio-strategist | PS | `#6b35b8` / `#eee5f8` | `#c9a6ff` / `#2c2040` |

Every foreground/background pair above was checked at ≥ 4.5:1 (see §5).

### Typography
- UI/prose: **Inter** (Google Fonts), falling back to `system-ui`.
- Numbers, tickers, code and tool args: **JetBrains Mono**, and all numbers use `font-variant-numeric: tabular-nums`.

| Token | Size / line-height | Use |
|---|---|---|
| `--fs-xs` | 12px / 16px | meta, badges |
| `--fs-sm` | 13px / 18px | rails, activity, tabs |
| `--fs-md` | 15px / 22px | UI body, composer |
| `--fs-lg` | 16px / 26px | answer prose |
| `--fs-xl` | 20px / 28px | answer h2, empty-state title |
| `--fs-2xl` | 26px / 32px | answer h1 |

### Spacing (4px base)
`--sp-1` 4 · `--sp-2` 8 · `--sp-3` 12 · `--sp-4` 16 · `--sp-5` 20 · `--sp-6` 24 · `--sp-8` 32 · `--sp-10` 40

### Radius
`--r-sm` 4px (badges, code) · `--r-md` 6px (buttons, inputs, rows) · `--r-lg` 10px (composer, panels)
· `--r-pill` 999px (chips). No giant rounded cards.

### Elevation
Borders do most of the work, and there are only two shadows:
`--shadow-1: 0 1px 2px rgb(16 18 24 / .06)` for the composer and active tab, and
`--shadow-2: 0 8px 24px rgb(16 18 24 / .12)` for the mobile sheet and toast. In dark mode shadows are replaced
with a lighter `--surface` step.

### Motion
120–180ms ease-out for hover and focus, a 1s linear spinner for running tools, and a blinking caret while
text streams. `prefers-reduced-motion: reduce` turns off all animation (the spinner becomes a static
"◔" glyph and the caret stops blinking).

---

## 4. Brand voice (Brand Guardian)

**Calm, precise, evidence-first.** Trust matters more than hype.

| Do | Don't |
|---|---|
| "Researching NVDA: quant analyst is pulling 1-year prices." | "Crunching the numbers! 🚀" |
| "Research only. Not investment advice. Data may be delayed." | "Find your next 10x" |
| "Couldn't find ticker ZZZZ." | "Oops! Something went wrong 😢" |
| Name the specialist and the tool | Anthropomorphized excitement |

The UI never uses buy or sell language. The chips ask for *research*, *comparison*, *review* and *risks*.
The primary color is ink blue rather than green, so the "go" color is never a market direction.

---

## 5. Accessibility commitments (Inclusive design + Frontend)

- **Contrast:** WCAG 2.2 AA in both themes. Text is ≥ 4.5:1, while UI boundaries and focus rings are ≥ 3:1. The token pairs
  were verified with a WCAG luminance script while this was built.
- **Landmarks:** `header`, `nav` (mobile view switcher), `aside` (watchlist, labelled), `main`,
  `aside` (context panel, labelled), `footer` (disclaimer, `role=contentinfo`).
- **Keyboard:** everything is reachable in DOM order. Tabs follow the ARIA APG tabs pattern (arrow keys, Home/End).
  In the composer, Enter sends and Shift+Enter adds a new line; IME composition is respected. Escape stops a running
  stream while focus is in the composer. Tool rows are native `<details>`.
- **Focus:** a visible 2px `--focus` outline with 2px offset on every interactive element; never removed.
- **Live regions:** one `aria-live="polite"` status line announces phase changes (thinking, delegating to
  X, complete, stopped, error). Individual tokens are **not** announced. The plan count has its own polite region.
- **Status is never color-only:** todo status uses an icon, a text label for screen readers and color. Tool state uses a spinner/✓/⚠
  plus text. Price change uses arrow, sign and color.
- **Charts:** each sparkline is an `<svg role="img">` with an `aria-label` stating the 30-close trend
  ("30-day closes, from 172.10 to 181.20, up 5.3%").
- **Motion:** `prefers-reduced-motion` is honored. **Touch:** targets are ≥ 40px on mobile.
- **Security:** all Markdown goes through `marked` → `DOMPurify.sanitize`. Any other untrusted text goes into the page through
  `textContent`. If the CDN scripts fail, Markdown falls back to escaped plain text.

## 6. Engineering notes (Frontend + Data Viz)

- No build step: `index.html`, `styles.css` and `app.js` (an IIFE with no globals). The only externals are marked 18 and
  DOMPurify 3 from cdnjs (loaded with SRI), plus the optional Google Fonts.
- **SSE over POST:** `fetch()` + `ReadableStream` + `TextDecoder({stream:true})`. A buffer handles
  chunk boundaries, normalizes CRLF, splits on blank lines, joins multi-line `data:` fields and ignores `:` comments.
- Markdown re-renders at most once per animation frame while streaming.
- `token` events with `agent === "orchestrator"` build the answer. Subagent tokens are shown as a
  "working notes" preview in that agent's Activity group, so the answer isn't interleaved with drafts.
- Threads: the id is created with `POST /api/threads` and stored in `sessionStorage` (`meridian.thread`). On reload the UI restores
  the conversation from `GET /messages` and the files from `GET /files`. **New research** aborts the run, clears state and creates a new thread.
- Storage access (`localStorage` / `sessionStorage`) is always wrapped in try/catch, and the UI works without it.

## 7. Finish gate: implementation review

**Decision: PASS.** This was reviewed against the mock stream at 1440×900 (light and dark) and 375×812 (light and dark).
The evidence is in `web/screenshots/`. axe-core (WCAG 2.1 A/AA + best-practice) reports 0 violations on every tab, in both
themes and at both widths. Nothing scrolls horizontally at 375px, and there are no console errors other than the expected 404 when adding an unknown ticker.

Findings were fixed during review, before PASS:
1. The disclaimer footer was taking half the viewport because the hidden mobile nav left an empty grid row. The grid rows are now set separately for desktop and mobile.
2. While subagents worked, the conversation column was empty. An **analyst trail** was added: a chip per delegated
   specialist (badge, name, spinner or ✓), which opens Activity. A **"Step n of N: …"** line mirrors the in-progress todo.
3. The running tool scrolled out of view in Activity. New calls are now revealed automatically unless the user scrolled in the last 4s.
4. Long inline code paths (`/reports/NVDA_research.md`) made the mobile layout 412px wide. Code now wraps (`overflow-wrap: anywhere`).
5. The streaming caret appeared on its own line after lists. It is now placed inside the deepest last text block.
6. The sparkline hue conflicted with today's change. It is now neutral ink (see §3).
7. The run-status icon had no size, and the new-research button had no name on mobile. Scrollable `<pre>` blocks weren't focusable. The heading order was wrong. All four are fixed.
8. Returning to Chat on mobile after the run finished didn't stay pinned to the bottom. The chat now re-pins when it is shown.

**Kept (these serve the product):** the answer reads as a document rather than a chat bubble, tabular numbers are right-aligned,
agent monograms carry identity, the plan's progress count is visible, and the disclaimer is always on screen.

**Optional refinements (not required for PASS):** a full-width reader mode for long reports, sources/citation
chips if the API ever exposes them, and a price-refresh indicator per watchlist row.
