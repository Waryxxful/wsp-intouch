# Duralux — CRM Admin Dashboard Template — Style Reference

> crisp data surfaces on a cool slate frame

**Theme:** light (dark mode available via customizer)

> **Correction (verified against the running product, 2026-07-22):** every `#0f172a`
> "slate navigation/header" reference in this document describes the *optional*
> `app-navigation-dark` / `app-header-dark` variant. Neither `@duralux/ui` nor
> `grancrm-shell` ever sets those classes — the actual default (confirmed in
> `scss/themes/layouts/_nxl-navigation.scss:11`: `.nxl-navigation { background: $white; }`)
> is a **white** sidebar and header, matching the rest of the light canvas. Only the
> overall `app-skin-dark` mode (controlled by `ThemeProvider`) is wired up. Read every
> "dark slate frame" mention below as an available-but-unused option, not the shipped default.

Duralux is a light-first, data-dense CRM admin system built on Bootstrap 5. A cool gray-blue canvas (`#f0f2f8`) hosts white cards with hairline borders and feather-soft shadows, framed by a near-black slate navigation rail (`#0f172a`) that provides the only large dark surface in the UI. Typography is Inter at a deliberately compact 0.84rem body size — hierarchy comes from weight (400 body, 600 links/strong, 700 headings) and the dark ink color `#283c50` reserved for headings and emphasis. Color is functional, not decorative: a single royal blue primary (`#3454d1`) signals actions, while a full semantic set (green success, red danger, yellow warning, cyan info) drives badges, soft-tinted buttons, and chart series. Corners are tight (4px controls, 10px cards), spacing is a consistent 25px card padding / 30px content gutter rhythm, and every interactive element transitions at `all 0.3s ease`.

## Tokens — Colors

| Name | Value | Token | Role |
|------|-------|-------|------|
| Body Canvas | `#f0f2f8` | `--bs-body-bg` | Page background behind all cards and content — a cool gray-blue, never pure white and never dark gray |
| Surface White | `#ffffff` | `--bs-white` | Card backgrounds, content sidebars, modals, dropdowns, table rows — the working surface of the UI |
| Slate Ink | `#0f172a` | `$navigation-background` | Sidebar navigation and header background — the single dark anchor surface of the layout |
| Brand Dark | `#283c50` | `$brand-dark` | Headings, links, card titles, emphasized text — the "ink" color, used instead of black |
| Brand Body | `#4b5563` | `$brand-body` | Default body text color set on `body` — readable but visibly lighter than headings |
| Body Color | `#4b5563` | `--bs-body-color` | Bootstrap-level body color for utilities and components |
| Muted | `#4b5563` | `--bs-secondary` / `$text-muted` | Secondary text, captions, icons, breadcrumb chevrons, table meta |
| Primary Blue | `#3454d1` | `--bs-primary` | Buttons, active states, links on hover, progress bars, focus accents — the only action color |
| Success Green | `#17c666` | `--bs-success` | Positive badges, growth indicators, success alerts and toasts |
| Danger Red | `#ea4d4d` | `--bs-danger` | Destructive actions, error states, negative deltas |
| Warning Yellow | `#ffa21d` | `--bs-warning` | Warnings, pending states, attention badges |
| Info Cyan | `#3dc7be` | `--bs-info` | Informational badges, secondary chart series |
| Dark | `#283c50` | `--bs-dark` | Dark buttons/badges; same value as Brand Dark |
| Darken | `#001327` | `--bs-darken` | Extra-dark theme color for maximum-contrast fills |
| Light | `#eff0f6` | `--bs-light` / `$gray-100` | Light buttons, hover fills, subtle chip backgrounds |
| Border | `#e5e7eb` | `--bs-border-color` | Default hairline: card headers, table rows, list groups, dropdowns |
| Border Strong | `#dcdee4` | `$border-color-2` | Slightly harder border for avatars, inputs, separators that need presence |

Soft tints: every theme color has a `bg-soft-*` wash at 7.5% opacity (`rgb($color, 0.075)`) and a `btn-light-*` fill generated with `shift-color($value, -80%)` — use these for chips, soft badges, and secondary buttons instead of inventing new pastels.

## Tokens — Typography

### Inter — Default UI typeface (`$font-inter`), applied on `body` with `-webkit-font-smoothing: antialiased`. Body runs at 0.84rem (~13.4px) with 1.6 line-height — compact by design, so tables and lists stay dense without feeling cramped. Hierarchy is weight-driven: 400 body, 600 for links/`strong`/labels, 700 for all headings and card titles. Headings always take `$brand-dark` (#283c50), never the body gray. The customizer can swap the whole UI to any of ~20 preloaded Google families (Lato, Rubik, Poppins, Roboto, Montserrat, Nunito, etc.) via `app-font-family/*` body attributes, so never hardcode a family on a component — inherit from body. · `--font-inter`
- **Substitute:** system-ui stack (`$font-system-ui`)
- **Weights:** 100–900 available; 400, 500, 600, 700 in active use
- **Sizes:** 5–30px utility scale (`$font-5`…`$font-30`); body 0.84rem; inputs 0.845rem
- **Line height:** 1.6 (body)
- **Letter spacing:** utility scale `$text-spacing-xs` 0.15px → `$text-spacing-xxxl` 2px; normal elsewhere
- **Role:** Single inherited family for every UI context — navigation, tables, forms, charts, and headings all share Inter; only weight, size, and ink color differentiate them.

### Type Scale

| Role | Size | Weight | Color | Token |
|------|------|--------|-------|-------|
| h1 | 36px | 700 | `#283c50` | `$h1-font-size` |
| h2 | 28px | 700 | `#283c50` | `$h2-font-size` |
| h3 | 24px | 700 | `#283c50` | `$h3-font-size` |
| h4 | 20px | 700 | `#283c50` | `$h4-font-size` |
| h5 | 16px | 700 | `#283c50` | `$h5-font-size` |
| h6 | 15px | 700 | `#283c50` | `$h6-font-size` |
| card title | 16px | 700 | `#283c50` | `$font-16` |
| body | 0.84rem (~13.4px) | 400 | `#4b5563` | `$font-body` |
| input | 0.845rem | 400 | body | `$input-font-size` |
| caption / meta | 10–12px | 400–600 | `#4b5563` | `$font-10`–`$font-12` |

Font-weight utilities: `.fw-light` 200, `.fw-lighter` 300, `.fw-normal` 400, `.fw-medium` 500, `.fw-semibold` 600, `.fw-bold` 700, `.fw-bolder` 800, `.fw-black` 900.

## Tokens — Spacing & Shapes

**Base rhythm:** 5px increments; 25px is the structural constant (card/modal/list-group padding), 30px the content gutter.

**Density:** compact — this is a data dashboard, not a marketing site.

### Spacing Scale

| Name | Value | Where |
|------|-------|-------|
| card padding | 25px | `$card-spacer-y/x`, `$card-cap-padding-y/x`, modal inner & header, list-group x |
| content gutter | 30px 30px 5px | `.main-content` padding |
| card gap | 24px | `.card { margin-bottom }` |
| list-group y | 20px | `$list-group-item-padding-y` |
| content header | 75px tall, 25px 30px padding | `.content-sidebar-header`, `.content-area-header` |
| input padding-y | 0.5rem | `$input-btn-padding-y` |

### Border Radius

| Token | Value | Element |
|-------|-------|---------|
| `$border-radius-sm` | 2px | small controls |
| `$radius-xs` / `$border-radius` | 3–4px | images (`img` default 3px), inputs, buttons (4px) |
| `$radius-sm` / `$border-radius-lg` | 5–6px | large controls, dropdowns |
| `$radius-md` | 10px | **cards** — the signature surface radius |
| `$radius-lg`–`$radius-xxl` | 15–25px | feature blocks, banners |
| `$radius-pill` | 30px | pills, tags |
| `$radius-circle` / 100% | 50px / 100% | avatars, status dots |

### Shadows

| Token | Value |
|-------|-------|
| `$card-shadow` | `0 1px 3px 0 rgb(0 0 0 / .1), 0 1px 2px -1px rgb(0 0 0 / .1)` |
| `$shadow-sm` → `$shadow-xxl` | `0 1px 5px` → `0 20px 45px` of `rgba(#283c50, 0.15)` |
| `.btn-shadow` | `0 6px 7px -1px rgba(80, 86, 175, 0.3)` |
| dark theme | `0 0 20px rgb(0 0 0 / 50%)` |

### Layout Frame

- **Header height:** 80px (`$header-height`), background `#0f172a`, border `#1b2436`
- **Sidebar width:** 280px expanded / 100px collapsed (`$navigation-width` / `$navigation-collapsed-width`)
- **Horizontal menu (optional):** 60px topbar, background `#1b2335`
- **Content offset:** `.nxl-container { top: 80px; margin-left: 280px; }`
- **Responsive shell:** viewports `<= 1024px` use the offcanvas navigation; desktop layout starts strictly above `1024px`
- **Transitions:** `all 0.3s ease` on layout shells, links, cards

## Components

### Card
**Role:** The universal content surface — every widget, table, form, and chart lives in one

White background, 10px radius, `1px solid transparent` border, `$card-shadow` (feather-soft double shadow), 24px bottom margin. `.card-header` is a flex row (title left, actions right) with 25px padding and a `#e5e7eb` bottom hairline; `.card-title` is 16px/700 in `#283c50`. Hover reveals `.card-header-btn` actions (opacity + translateX transition). Body padding 25px.

### Primary Button
**Role:** Standard Bootstrap 5 buttons with a Duralux radius and soft variants

`.btn-primary` filled `#3454d1`, white text, 4px radius, 0.5rem vertical padding, 0.845rem text. Icons inside buttons render at 16px. `.btn-shadow` adds the indigo glow. Never place two filled primary buttons side by side in a card header — pair a filled primary with a `btn-light-*` or icon button.

### Soft Button (`btn-light-*`)
**Role:** Secondary action for every theme color

Tinted fill via `shift-color($value, -80%)` with matching text color; on hover/focus/active it flips to the solid theme color with white text. No box-shadow. This is the house style for secondary and per-row actions (e.g. `btn-light-brand`, `btn-light-danger`).

### Avatar
**Role:** User/entity identity across tables, chat, nav

`.avatar-image` / `.avatar-text`: 40px circle (border-radius 100%), white background, `1px solid #dcdee4` border, weight-700 initials. Size modifiers: `.avatar-xs` 12px, `.avatar-sm` 20px, `.avatar-md` 30px, up through xl/xxl. Status dots and stacked avatar groups compose on top.

### Sidebar Navigation (`.nxl-navigation`)
**Role:** Primary app navigation — the dark anchor of the layout

280px rail on `#0f172a`, borders `#1b2436`, hover fill `#1c2438`. Structure: `.nxl-item > .nxl-link` with `.nxl-micon` (Feather icon), `.nxl-mtext` (label), `.nxl-arrow` (chevron); submenus in `.nxl-submenu`; section labels via `.nxl-caption` in white. Link color `#b1b4c0`, active/caption white. Collapses to a 100px icon rail.

### Header (`.nxl-header`)
**Role:** Top bar — search, language, notifications, timesheets, profile

80px tall on `#0f172a`, link color `#6b7280`, dropdown panels are white cards with standard borders. Contains the global search modal trigger and customizer toggle.

### Badge
**Role:** Status and count labels

Solid theme-color badges plus soft variants using `bg-soft-*` (7.5% tint) with the theme color as text. Pill radius for counts, 4px for labels. Used heavily in tables (lead status, invoice state, task priority).

### Table / DataTable
**Role:** Core CRM data display (customers, leads, invoices, projects)

White card surface, `#e5e7eb` row hairlines, compact 0.84rem cells, muted `#4b5563` meta text, avatars + badges inline. Powered by DataTables (Bootstrap 5 skin) with pagination, search, and per-row `btn-light-*` action icons.

### Forms
**Role:** Create/edit flows (customers-create, invoice-create, settings)

Inputs at 0.845rem, 4px radius, `#dcdee4` borders, 0.5rem vertical padding. Enhanced controls come from the plugin set: Select2 (selects), Tagify (tags), daterangepicker/datepicker (dates), Quill (rich text), Cleave (masks), jQuery Steps (wizards) — all skinned to match the token set in `themes/plugins/*`.

### Charts (ApexCharts)
**Role:** Analytics and dashboard visualizations

ApexCharts everywhere, themed with the semantic palette (`#3454d1` primary series first, then success/warning/danger/info). Sparklines in stat widgets, area/bar/donut in reports; jVectorMap for geo, circle-progress for radial KPIs. Charts sit inside standard cards — never floating on the canvas.

### Content Split View (`.content-sidebar` + `.content-area`)
**Role:** Two-pane apps layout (email, chat, tasks, notes)

White inner sidebar with right hairline; both panes have a 75px header (25px 30px padding, bottom hairline). Bodies scroll independently (perfect-scrollbar).

### Customizer (`partials/customizer.html`)
**Role:** Runtime theme options panel

Offcanvas panel toggling: navigation/header color schemes, dark theme, and the UI font family (via `app-font-family/*`). Any new page must keep working under these options — that means: inherit fonts, use tokens, and never hardcode `#0f172a`-on-white assumptions outside the nav/header shells.

## Do's and Don'ts

### Do
- Build every content block inside a `.card` — white surface, 10px radius, `$card-shadow`, 25px padding, 24px bottom margin
- Use `#3454d1` (primary blue) exclusively for the main action per view; secondary actions use `btn-light-*` soft variants
- Keep body text at 0.84rem Inter weight 400 in `#4b5563`, and reserve `#283c50` at weight 700 for headings and card titles
- Use the semantic colors (success/danger/warning/info) for status meaning only — badges, deltas, alerts, chart series
- Use `bg-soft-*` (7.5% tints) and `shift-color(..., -80%)` fills for chips and soft buttons instead of new pastel hexes
- Use Feather icons (`.feather-*`) for navigation and inline actions, sized 16px inside buttons
- Apply `transition: all 0.3s ease` to interactive surfaces — it is the house motion signature
- Keep `#e5e7eb` hairlines as the only divider treatment; borders separate, shadows only lift cards
- Follow the naming convention: layout classes are prefixed `nxl-` (nxl-container, nxl-navigation, nxl-content); page init scripts are `<page>-init.min.js`

### Don't
- Do not use pure white as the page background — the canvas is `#f0f2f8`; white belongs to cards and panels only
- Do not use pure black text — ink is `#283c50` (headings) and `#4b5563` (body)
- Do not introduce a second saturated action color — blue acts, the rest of the palette reports status
- Do not enlarge body type — the compact 0.84rem scale is what keeps tables and widgets dense; use weight and ink color for emphasis
- Do not hardcode a font-family on components — the customizer swaps families at the `body` level via `app-font-family/*`
- Do not use heavy shadows on cards — only `$card-shadow`; the `$shadow-lg+` tiers are for popovers, modals, and floating panels
- Do not place content outside the `.nxl-container` / `.main-content` frame — the 80px header offset and 280px sidebar margin are structural
- Do not skin plugins ad hoc — Select2, DataTables, SweetAlert2, daterangepicker already have themed partials in `themes/plugins/`; extend those
- Do not break dark theme: any new hardcoded light color must have a `[app-skin="dark"]` counterpart (dark surfaces `#121a2d`/`#0f172a`, text `#b1b4c0`, borders `#1b2436`)

## Surfaces

| Level | Name | Value | Purpose |
|-------|------|-------|---------|
| 0 | Canvas | `#f0f2f8` | Page background behind all content |
| 1 | Card / Panel | `#ffffff` | Cards, sidebars, dropdowns, modals, tables |
| 1 | Soft Tint | `rgb(color, 0.075)` | Chips, soft badges, hover fills on white |
| 2 | Slate Shell | `#0f172a` | Navigation rail and header — the fixed dark frame |
| 2 | Slate Hover | `#1c2438` | Nav item hover/active fill |
| — | Dark Theme | `#121a2d` / `#0f172a` | Card / canvas surfaces when `app-skin="dark"` |

## Elevation

Elevation is minimal and purposeful: cards carry only the feather `$card-shadow`; dropdowns and popovers step up to `$shadow-sm`/`$shadow-md`; modals and offcanvas panels use `$shadow-lg` and above. All shadows derive from `rgba(#283c50, 0.15)` — cool, never warm gray. Hairline borders (`#e5e7eb`) do the separating work; shadows only communicate "floating above the canvas". In dark theme, shadows deepen to `0 0 20px rgb(0 0 0 / 50%)`.

## Imagery

Imagery is utilitarian: avatar photography (circle-cropped, hairline-bordered), brand/payment logos, flag icons for localization, and file-type icons in storage views. Illustrations appear only on auth/404/maintenance "creative" variants. All `img` elements default to a 3px radius. No hero art, no decorative gradients, no stock photography in the dashboard itself — data visualization (ApexCharts) is the visual interest.

## Layout

Fixed application frame: an 80px slate header spans the top, a 280px slate navigation rail pins the left (collapsible to 100px), and `.nxl-container` offsets content below/right of both. Content flows in `.main-content` with 30px gutters, composed as a Bootstrap 5 responsive grid of cards (`col-xxl-4`, `col-md-6`, etc.) with a uniform 24px vertical rhythm. Two-pane apps (email/chat/tasks/notes) split into `.content-sidebar` + `.content-area` with matching 75px headers. Every page is a standalone HTML file sharing the same shell; page behavior loads from `assets/js/<page>-init.min.js` plus `common-init.min.js`. Responsive behavior (`nxl-responsive.scss`) collapses the rail to an offcanvas drawer on mobile.

## Extending Duralux — Building a Standalone App (React)

> **For GranCRM apps: don't recreate anything below — `@duralux/ui` already exists.**
> It compiles the real Duralux SCSS directly and ships real React components
> (`AppLayout`, `AuthLayout`, `ShellHeader`, `ShellNav`, `PageHeader`, `Badge`,
> `StatusBadge`, `StatCard`, chart widgets, etc.) — see
> `docs/GUIA_APP_SATELITE_UI.md` in the `duralux-ui` repo. Import the package;
> do not hand-roll the frame from the tokens below. The section that follows
> is for the hypothetical case of a React product with **no** access to
> `@duralux/ui` (e.g. a completely separate, non-GranCRM product) that still
> wants to match Duralux's visual language from scratch.

Duralux ships as static HTML + SCSS. Absent `@duralux/ui`, extensions would be built on a standalone React stack recreating the frame and tokens from this document — no Duralux assets imported. Fidelity would come from three things: the **frame**, the **page-header pattern**, and the **tokens** (Quick Start section below).

### The frame

Fixed application shell, recreated in your layout component:

```jsx
// AppShell — the Duralux frame
// header: fixed, 80px tall, background #0f172a, borders #1b2436
// sidebar: fixed left, 280px wide (100px collapsed), background #0f172a
// content: margin-left 280px, padding-top 80px, background #f0f2f8, min-height 100vh
<div className="app">
  <aside className="shell-nav">   {/* #0f172a, links #b1b4c0, hover bg #1c2438 + white text */}
    {/* item: feather icon (16px) + label, 0.84rem; section captions 11px uppercase white */}
  </aside>
  <header className="shell-header"> {/* #0f172a, right side: search, notifications, avatar */} </header>
  <main className="content">      {/* 30px gutters; grid of cards, 24px vertical gap */}
    <PageHeader />
    {/* cards */}
  </main>
</div>
```

### The page-header pattern

Every content view opens with this — it is what makes a page read as Duralux, not just Bootstrap-ish:

- **Left:** title as h5 (16px / 700 / `#283c50`) + breadcrumb inline (`Home / Section / Page`, links `#283c50` weight 600, separator = 14px chevron-right in `#4b5563`, current item muted).
- **Right:** actions row with 8px gap — filters/secondary actions as soft buttons (`btn-light` style: primary at 7.5% tint background, primary text, hover flips to solid), and at most one filled `#3454d1` primary button.
- The block sits on the canvas (not inside a card), with the card grid starting below.

### Component mapping

| Duralux piece | React equivalent |
|---|---|
| Bootstrap grid of cards | CSS grid / flex; cards 10px radius, `--card-shadow`, 25px padding, 24px gap |
| Feather icons (`.feather-*`) | `react-feather` (same icon set), 16px inside buttons |
| ApexCharts inits | `react-apexcharts`, primary series `#3454d1`, then semantic colors |
| DataTables | Any table lib (e.g. TanStack Table) skinned per the Data Table Row prompt below |
| Select2 / Tagify / daterangepicker | Your stack's equivalents, styled with 4px radius, `#dcdee4` borders, `#3454d1` focus |
| SweetAlert2 toasts | Any toast lib using semantic colors + white card surface |

### Fidelity checklist (before shipping a view)

- Canvas is `#f0f2f8`, never white — white belongs to cards only
- Headings `#283c50` at 700; body 0.84rem Inter at 400 in `#4b5563`; no pure black anywhere
- Exactly one filled blue button per view; everything else soft-tinted or ghost
- Dividers are `#e5e7eb` hairlines; the only card shadow is `--card-shadow`
- Every view opens with the page-header (title + breadcrumb + actions)
- Interactive elements transition at `all 0.3s ease`
- Status is communicated with the semantic set (soft badges at 7.5% tint), never with new pastels

## Component Inventory — What Exists and Where to Extract It

**Source template:** `/home/pancho/duralux_plantilla/` — this is the extraction ground truth. Every component below has its markup in an HTML page and its styles in an SCSS partial inside that directory. To extract a piece: open the listed HTML page, locate the component by its class or card title, copy the markup as the JSX skeleton, and port only the styles from its SCSS partial (paths relative to `duralux-admin/`).

### Layer 1 — Shell / Layout

| Component | Key classes | Markup source | Styles |
|---|---|---|---|
| Sidebar navigation | `nxl-navigation`, `nxl-item`, `nxl-link`, `nxl-submenu`, `nxl-caption` | any page (duplicated verbatim in all) | `assets/scss/themes/layouts/_nxl-navigation.scss` |
| Header | `nxl-header`, `nxl-h-item` | any page | `layouts/_nxl-header.scss` |
| — Global search modal | `nxl-header-search` | any page | `components/_search.scss` |
| — Language modal | `nxl-header-language` | any page | `components/_language.scss` |
| — Notifications / timesheets / profile dropdowns | `nxl-h-item` + `dropdown-menu` | any page | `layouts/_nxl-header.scss` |
| Page header | `page-header`, `page-header-left/right`, `breadcrumb` | any content page, e.g. `index.html` | `layouts/_nxl-common.scss` |
| Two-pane split view | `content-sidebar`, `content-area`, `*-header` | `apps-email.html`, `apps-chat.html` | `layouts/_nxl-common.scss`, `_nxl-sidebar.scss` |
| Customizer panel | offcanvas + option groups | `partials/customizer.html` | `options/_theme-options-*.scss` |

### Layer 2 — Core UI

| Component | Key classes | Markup source | Styles |
|---|---|---|---|
| Card (+ hover header actions) | `card`, `card-title`, `card-header-btn`, `card-header-action` | everywhere; gallery in `widgets-*.html` | `components/_card.scss` |
| Buttons (solid / soft / outline / icon / shadow / Ladda) | `btn-*`, `btn-light-*`, `btn-shadow`, `ladda-button` | `index.html`, forms pages | `components/_button.scss`, `components/_miscellaneous.scss` |
| Badge (solid + soft) | `badge`, `bg-soft-*` | tables in `widgets-tables.html` | `components/_badge.scss` |
| Avatar (6 sizes, text/image, status dot) | `avatar-image`, `avatar-text`, `avatar-xs`…`avatar-xxl` | any table/list page | `components/_general.scss` |
| Stacked avatar group | `img-group` | `projects.html`, widgets | `components/_miscellaneous.scss` |
| Dropdown / Modal / Offcanvas / Tabs / Accordion / Alert | standard BS5 classes | `widgets-miscellaneous.html`, app pages | `components/_dropdown.scss`, `_modal.scss`, `_offcanvas.scss`, `_navs-tabs.scss`, `_accordion.scss`, `_alert.scss` |
| Table / list group | BS5 + theme overrides | `widgets-tables.html` | `components/_table.scss` |
| Forms (inputs, selectable card radios, file upload, password generator) | `form-control`, `card-input-element`, `custom-file-upload`, `generate-pass` | `customers-create.html`, `settings-*.html` | `components/_form.scss`, `_miscellaneous.scss` |
| Progress bar / scrollbar / fullscreen switcher | `progress`, custom scrollbar | widgets pages | `components/_general.scss` |

### Layer 3 — Widgets (~130 ready-made examples, the richest extraction source)

| Gallery | Contents | Markup source | Styles |
|---|---|---|---|
| Statistics (~71 KPI cards) | number + delta, sparkline, soft icon circle, circle-progress variants | `widgets-statistics.html` | `widgets/_widgets-statistics.scss` |
| Charts (18) | Visitors Overview, Sales Pipeline, Leads Overview, Earning & Expense, Payment Records, Inquiry Channel/Tracking, Session Device, Top Countries (map), Billable Time, Hours Spent, Performance Overview, Project Report, Website Analytics… | `widgets-charts.html` | `widgets/_widgets-charts.scss` |
| Lists (19) | Activity feeds (`activity-feed`, `feed-item-*` semantic), Notifications, Todos, Tickets, Users, Schedule, Meeting, Browsers, Social, Trending, Suggestions, Feedback, Invoice Overview, Project Leads | `widgets-lists.html` | `widgets/_widgets-lists.scss` |
| Tables (19) | Leads, Leads Status, Contact Leads, New Customers, Recent Orders, Invoice Summary, Project Progress/Tracker/Stats, Latest Tasks, Campaign, Top Selling, Traffic Reports, Visited Pages, Support Inbox, Countries, Store Overview, Remainders | `widgets-tables.html` | `widgets/_widgets-tables.scss` |
| Miscellaneous | Goal Progress, Tasks Progress, Revenue Forecast (`goal-progress-*`, `*-progress-chart` radial, color variants 1–4) | `widgets-miscellaneous.html` | `widgets/_widgets-miscellaneous.scss` |

### Layer 4 — Full app modules (composition reference, not copy-paste)

| Module | Markup source | Styles |
|---|---|---|
| Chat | `apps-chat.html` | `applications/_chat.scss` |
| Email (inbox + composer) | `apps-email.html` | `applications/_email.scss` |
| Tasks | `apps-tasks.html` | `applications/_tasks.scss` |
| Notes | `apps-notes.html` | `applications/_notes.scss` |
| Calendar | `apps-calendar.html` | `applications/_calendar.scss` |
| Storage (file manager) | `apps-storage.html` | `applications/_storage.scss` |

All share `applications/_apps-common.scss` on top of the split view.

### Layer 5 — Page patterns (view templates)

| Pattern | Markup source | Styles |
|---|---|---|
| Auth (login/register/reset/resetting/verify/404/maintenance × cover/creative/minimal) | `auth-*-{cover,creative,minimal}.html` | `pages/_authentication.scss`, `_maintaince.scss` |
| Customers (list / create / view with contact sidebar) | `customers*.html` | `pages/_customers-create.scss`, `_customers-view.scss` |
| Leads (list / create / view) | `leads*.html` | dashboard/report partials |
| Projects (list / create / view) | `projects*.html` | `pages/_projects.scss` |
| Proposal (list / create / edit / view) | `proposal*.html` | `pages/_proposal.scss` |
| Invoice (create / view) | `invoice-*.html` | `pages/_invoice-create.scss` |
| Payment | `payment.html` | — |
| Reports ×4 (sales / leads / projects / timesheets) | `reports-*.html` | `pages/_report-*.scss` |
| Settings master-detail shell (15 sections) | `settings-*.html` | shared layout partials |
| Help / knowledgebase | `help-knowledgebase.html` | `pages/_help-knowledgebase.scss`, `_help-desk.scss` |
| Dashboard / Analytics | `index.html`, `analytics.html` | `pages/_dashboard.scss`, `_analytics.scss` |

### Layer 6 — Plugin skins (port the skin, swap the lib for a React equivalent)

Themed partials in `assets/scss/themes/plugins/`: Select2, Tagify (`_tags-input.scss`), daterangepicker (`_daterange.scss`), datepicker, DataTables (`_dataTables.scss`), SweetAlert2, PNotify (`_pnotify.scss`, `_notification.scss`), jQuery Steps wizard (`_jauery-steps.scss`), circle-progress, bar-rating, lightbox, maxlength, perfect-scrollbar, Pace loader (`_pace.scss`). Rich text is Quill; input masks are Cleave. Map each to its React equivalent per the Component Mapping table above, keeping the Duralux skin values.

### Extraction workflow for the generator agent

1. Pick the piece from the tables above; open its markup source in `/home/pancho/duralux_plantilla/duralux-admin/`.
2. Copy the HTML block (locate by class or card title) → convert to JSX, parameterize the data.
3. Port only that component's rules from its SCSS partial, resolving SCSS variables with the Quick Start token block below.
4. Replace `feather-*` icons with `react-feather`, chart inits with `react-apexcharts`.
5. Validate against the Fidelity checklist.

## Agent Prompt Guide

## Quick Color Reference
- Text: #283c50 (headings, weight 700), #4b5563 (body), #4b5563 (muted/meta)
- Background: #f0f2f8 (canvas), #ffffff (cards), #0f172a (nav/header shell)
- Border: #e5e7eb (default hairline), #dcdee4 (inputs/avatars)
- Accent: #17c666 success, #ea4d4d danger, #ffa21d warning, #3dc7be info
- Primary action: #3454d1 (filled buttons, active states, first chart series)

## Example Component Prompts

1. **Stat Widget Card**: White card, 10px radius, shadow `0 1px 3px 0 rgb(0 0 0/.1), 0 1px 2px -1px rgb(0 0 0/.1)`, 25px padding. Top row: muted label 12px `#4b5563` uppercase + Feather icon in a 40px soft-primary circle (`rgba(52,84,209,0.075)`). Big number at 24px weight 700 `#283c50`. Delta badge: soft-success pill, `rgba(23,198,102,0.075)` background, `#17c666` text, 11px weight 600. Optional ApexCharts sparkline at the bottom in `#3454d1`.

2. **Data Table Row**: Inside a white card. 40px circle avatar with `1px solid #dcdee4` border, name at 0.84rem weight 600 `#283c50` with email below at 12px `#4b5563`. Status badge soft-tinted. Row hairline `#e5e7eb`. Right-aligned action icons as `btn-light-*` 16px Feather icons. Hover row fill: `#eff0f6`.

3. **Sidebar Nav Item**: On `#0f172a`. Flex row, 0.84rem Inter, color `#b1b4c0`, Feather icon left, chevron-right trailing for submenus. Hover: background `#1c2438`, text white. Active: text white. Section caption above in white, 11px uppercase.

4. **Form Section**: White card, header with 16px/700 `#283c50` title and `#e5e7eb` bottom hairline, 25px padding. Labels 0.84rem weight 600 `#283c50`. Inputs 0.845rem, 4px radius, `#dcdee4` border, 0.5rem vertical padding; focus ring in `#3454d1`. Primary submit `#3454d1` filled, cancel as `btn-light-brand`.

5. **Two-Pane App Shell**: Left `.content-sidebar` white, 1px `#e5e7eb` right border, 75px header (25px 30px padding, bottom hairline, title 16px/700). Right `.content-area` with matching 75px white header holding search + actions, body on `#f0f2f8` scrolling independently.

## Similar Brands

- **Salesforce Lightning** — Same functional-color philosophy: one action blue, semantic status colors, white cards on a cool light canvas, data density over decoration
- **Zoho CRM** — Compact type scale, hairline-bordered tables, soft status badges, and a fixed dark-accent navigation frame
- **Tabler / AdminKit (Bootstrap admin family)** — Direct kin: Bootstrap 5 tokens, Inter, feather-soft card shadows, `btn-light-*`-style soft buttons, Feather icons
- **Atlassian Jira** — Slate-dark navigation against light content, muted gray text hierarchy topped by a single blue, and utilitarian imagery limited to avatars and status

## Quick Start

### CSS Custom Properties

```css
:root {
  /* Colors — semantic */
  --bs-primary: #3454d1;
  --bs-secondary: #4b5563;
  --bs-success: #17c666;
  --bs-info: #3dc7be;
  --bs-warning: #ffa21d;
  --bs-danger: #ea4d4d;
  --bs-light: #eff0f6;
  --bs-dark: #283c50;
  --bs-darken: #001327;

  /* Colors — ink & surfaces */
  --bs-body-bg: #f0f2f8;
  --bs-body-color: #4b5563;
  --brand-dark: #283c50;
  --brand-body: #4b5563;
  --brand-muted: #7587a7;
  --text-muted: #4b5563;
  --surface-card: #ffffff;
  --surface-shell: #0f172a;
  --shell-hover: #1c2438;
  --shell-text: #b1b4c0;
  --shell-border: #1b2436;

  /* Borders */
  --bs-border-color: #e5e7eb;
  --border-color-2: #dcdee4;

  /* Typography */
  --font-inter: "Inter", sans-serif;
  --font-body-size: 0.84rem;
  --font-body-leading: 1.6;
  --input-font-size: 0.845rem;
  --h1: 36px; --h2: 28px; --h3: 24px;
  --h4: 20px; --h5: 16px; --h6: 15px;
  --fw-normal: 400; --fw-medium: 500;
  --fw-semibold: 600; --fw-bold: 700;

  /* Radius */
  --radius-xs: 3px;
  --radius-control: 4px;   /* buttons, inputs */
  --radius-sm: 5px;
  --radius-card: 10px;
  --radius-lg: 15px;
  --radius-pill: 30px;

  /* Shadows */
  --card-shadow: 0 1px 3px 0 rgb(0 0 0 / .1), 0 1px 2px -1px rgb(0 0 0 / .1);
  --shadow-sm: 0 1px 5px rgba(40, 60, 80, 0.15);
  --shadow-md: 0 5px 15px rgba(40, 60, 80, 0.15);
  --shadow-lg: 0 10px 25px rgba(40, 60, 80, 0.15);

  /* Layout frame */
  --header-height: 80px;
  --nav-width: 280px;
  --nav-width-collapsed: 100px;
  --content-gutter: 30px;
  --card-padding: 25px;
  --card-gap: 24px;
  --transition: all 0.3s ease;
}
```

### Tailwind v4

```css
@theme {
  /* Colors */
  --color-primary: #3454d1;
  --color-success: #17c666;
  --color-info: #3dc7be;
  --color-warning: #ffa21d;
  --color-danger: #ea4d4d;
  --color-canvas: #f0f2f8;
  --color-card: #ffffff;
  --color-shell: #0f172a;
  --color-shell-hover: #1c2438;
  --color-shell-text: #b1b4c0;
  --color-ink: #283c50;
  --color-body: #4b5563;
  --color-muted: #4b5563;
  --color-border: #e5e7eb;
  --color-border-strong: #dcdee4;

  /* Typography */
  --font-sans: "Inter", ui-sans-serif, system-ui, sans-serif;
  --text-body: 0.84rem;
  --leading-body: 1.6;

  /* Radius */
  --radius-control: 4px;
  --radius-card: 10px;
  --radius-pill: 30px;

  /* Shadows */
  --shadow-card: 0 1px 3px 0 rgb(0 0 0 / .1), 0 1px 2px -1px rgb(0 0 0 / .1);

  /* Layout */
  --spacing-card: 25px;
  --spacing-gutter: 30px;
  --spacing-header: 80px;
  --spacing-nav: 280px;
}
```
