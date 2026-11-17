# VIGIL frontend redesign: plan

Reference: apple.com/macbook-pro/proled-xdr-display (studied 2026-10-30: screenshots at
1440 and 390 px, plus its HTML, `overview.built.css` and `main.built.js`).
**Status (2026-10-31): all 12 steps built.** Where the build differs from this plan:
- Guard Alert's dark mode (D4) came later, on 2026-10-31, through a `Palette` in `ui.dart`.
- `speedcam.py` has no speed-limit flag, so the Road legend uses the render's 30/40 bands.
- The hero film is a still card on phones (≤734 px), not the scroll-opening version.
- The RFID log's top row is 18 px shorter: one caption now fits on one line.
- App screenshots came from the iOS simulator (Xcode 27), not the Android emulator.

## 0. Ground rules

| Surface | What may change |
|---|---|
| VIGIL home (`web/home.html`) | Everything: layout, copy, media, motion |
| Shell header and slider (`web/index.html`) | Styling. The slider, its four tabs and the status dots stay |
| Parking, Speed and RFID dashboards | **Theme only**: colours, fonts, corner radius, borders, shadows, control styling. No panel moves, nothing added or removed, no behaviour changes |
| Campus Parking and Guard Alert apps | **Theme only**, same rule. Screens, navigation and widgets stay as they are |

Copy on the home page is written for a visitor, not an engineer. Words like LoRa,
AES, YOLO, homography and RLS are left out. Every number stays a real measurement.

---

## 1. What Apple's page does, measured

| Pattern | What Apple does | Measured values | VIGIL use |
|---|---|---|---|
| Stage | Black page; the closing part switches to light grey | `#000` stage; light zone `#F5F5F7`; tiles `#1D1D1F` | Same split: dark story, light closing |
| Type | SF Pro Display for headlines, SF Pro Text for body; tight tracking; semibold (600) everywhere | Hero 80/84 px −0.015em; section headline 64/68 −0.009em; "Get the highlights." 48/52; tile headline 28/32; stat 48/58; stat caption 14/18; body 17/25 −0.022em | Same scale via the Mac's own system font |
| Text colour | White leads into grey: the first sentence of a paragraph is bright, the rest is `#86868B` | Body grey `#86868B` (dark), `#6E6E73` (light) | Every intro paragraph |
| Section rhythm | Eyebrow (small, semibold) → huge centred headline → one centred paragraph → large framed media | Section padding 144 px; copy column 616–840 px; content 980 px wide | Gate, Road and Lot sections |
| Highlights | Horizontal gallery of tall rounded tiles that advances on its own; a dot nav whose active dot stretches into a progress bar; play/pause button | Tile radius 28 px | "Get the highlights." with 5 tiles |
| Stats | "Strength in numbers.": a 3-column grid of big numbers with a small caption, or a rounded card of 2 stats | Stats card radius 28 px, padding 72 px | One stat row per system |
| Feature trio | Three columns: line icon, then a white lead-in sentence and grey text ("Scroll. / Zoom. / Draw.") | 17 px body | "How it works" in three steps |
| Caption gallery | Big rounded image cards scrolling sideways, caption below each, round prev/next buttons | Buttons 36 px circles | Extra details per system |
| Split header | Headline on the left (two short lines), paragraph on the right | — | Apps section |
| Tab nav | Text tabs with an underline that switch the media above them ("Spotlight / Live Activities / Siri AI") | — | Switch between the two apps |
| Product viewer | Left column of round "+" buttons that expand into labelled pills; the media on the right changes with each | — | "A day on campus" |
| Light cards | White rounded cards on `#F5F5F7`, short headline and line of text | Card radius 28 px | Privacy and "Open a console" |
| Local nav | Sticky bar with a frosted, translucent background | 52–66 px tall; `rgba(…, .8)` + `saturate(1.8) blur(20px)` | In-page nav on the home page |
| Reveal motion | Items fade in and rise 30 px as their section comes into view, one after another | Rise 0.7 s, fade up to 0.9 s, 0.15 s between items; starts when the section top is 70% down the viewport | All home sections |
| Buttons | Blue pills | `#0071E3` fill; links `#2997FF` on dark, `#0066CC` on light | Primary actions |
| Breakpoints | Three sizes | ≥1069 px, 735–1068 px, ≤734 px | Same |

What we do **not** take: Apple's logo, images, videos, copy or font files. SF Pro is
used only as the Mac's own system font (`-apple-system`), which is how any site gets
it on a Mac. Headlines and text are ours.

---

## 2. Design system (one token set for every web surface)

### Colour

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | `#F5F5F7` | `#000000` | Page ground |
| `--surface` | `#FFFFFF` | `#1D1D1F` | Cards, panels |
| `--surface-2` | `#FAFAFC` | `#2C2C2E` | Raised or inset areas, table headers |
| `--ink` | `#1D1D1F` | `#F5F5F7` | Primary text |
| `--ink-2` | `#424245` | `#D2D2D7` | Secondary emphasis |
| `--muted` | `#6E6E73` | `#86868B` | Body grey, captions |
| `--line` | `#D2D2D7` | `#424245` | Borders, where kept |
| `--line-soft` | `#E8E8ED` | `#2C2C2E` | Row dividers |
| `--accent` | `#0071E3` | `#0071E3` | Buttons, selected states, focus ring |
| `--link` | `#0066CC` | `#2997FF` | Text links |
| `--green` | `#34C759` fill · `#248A3D` text | `#30D158` | Free bay, live, OK |
| `--red` | `#FF3B30` fill · `#D70015` text | `#FF453A` | Taken bay, speeding, failure |
| `--orange` | `#FF9500` fill · `#C93400` text | `#FF9F0A` | Warnings, "almost full" |
| `--yellow` | `#FFCC00` | `#FFD60A` | Approaching the limit, "filling" |
| `--teal` | `#30B0C7` | `#40C8E0` | "Plenty" lot status |

Status colours are only ever used for data. Controls use `--accent`.
In light mode, coloured **text** uses the darker text value so it passes 4.5:1.

### 2b. Thapar palette (replaces the neutrals and the blue above, 2026-11-07)

The user asked for Thapar's colours rather than Apple's: a darkish Thapar red where
blue was, warm dark greys instead of black, and a soothing beige instead of white.
The base red is `#ED2024`, sampled from the official logo
(`commons.wikimedia.org/wiki/File:Thapar_Logo.png`); Thapar publishes no brand guide.
The status colours in the table above are unchanged.

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | `#F2ECE3` beige | `#1B1A19` | Page ground |
| `--surface` | `#FBF8F3` cream | `#252322` | Cards, panels |
| `--surface-2` / sunken / hover | `#E9E2D7` | `#302D2B` | Raised or inset areas |
| `--ink` | `#24201D` | `#F3EFEA` | Primary text |
| `--ink-2` | `#4B443E` | `#D5CFC8` | Secondary emphasis |
| `--muted` | `#6E655C` | `#9C948C` | Body grey, captions (4.9:1 / 5.8:1 on `--bg`) |
| `--faint` | `#AAA096` | `#6E6862` | Placeholders, idle |
| `--line` | `#D4CABB` | `#4A4643` | Borders |
| `--line-soft` | `#E4DCD0` | `#302D2B`–`#3A3735` | Row dividers |
| `--accent` | `#B3141C` | `#D42F35` | Buttons, selected states, focus ring (white text 6.9:1 / 4.9:1) |
| `--accent-hover` | `#C21A22` | `#DE3E44` | Button hover |
| `--link` | `#B3141C` | `#F27A7E` | Text links (6.5:1 on the dark ground) |

Exceptions, on purpose:
- The accent is a darker, deeper red than the status red (`#D70015` / `#FF453A`), so a
  control never reads as an alert. Where red would mislead, the old blue became
  something else: the parking map's hover outline is white (red there means taken),
  and the speed console's "Playback" pill (`--info`) is neutral grey.
- Camera monitors, the parking entrance board and the phone bezel stay near-black,
  like the physical things they show.
- RFID's "on campus" series (`--inside`) takes the accent, as it took the blue.
- The apps (`mobile/`) still use the Apple palette below in section 6.

### Type

```
--sans:  -apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", Helvetica, Arial, sans-serif
--mono:  ui-monospace, "SF Mono", Menlo, monospace
```
System fonts only, so every page also works offline. This matters most for the speed
console, which runs on the drone's Wi-Fi.

| Role | ≥1069 | 735–1068 | ≤734 | Weight / tracking |
|---|---|---|---|---|
| Hero headline | 80/84 | 64/68 | 48/52 | 600 / −0.015em |
| Section headline (XL) | 64/68 | 48/52 | 40/44 | 600 / −0.009em |
| Headline | 48/52 | 40/44 | 32/36 | 600 / −0.003em |
| Eyebrow | 21/25 | 19/23 | 17/21 | 600 |
| Tile headline | 28/32 | 24/28 | 21/25 | 600 / +0.007em |
| Intro paragraph | 21/30 | 19/27 | 17/25 | 400 |
| Body | 17/25 | 17/25 | 17/25 | 400 / −0.022em |
| Stat value | 48/58 | 40/48 | 32/40 | 600 |
| Caption | 14/18 | 14/18 | 12/16 | 400 |

Dashboards keep their own compact sizes (13–15 px). They only take the font, the
tracking and the weights.

### Shape, space, motion

- Radius: home tiles and cards 28 px · dashboard panels 18 px · inputs 12 px · buttons and chips are pills.
- Borders: none on cards. The surface colour separates them (white on `#F5F5F7`, `#1D1D1F` on black). Hairlines stay only inside tables and lists.
- Shadows: none, except floating things (tooltips, the slider thumb).
- Home spacing: section padding 144 / 100 / 80 px by breakpoint; content 980 px; copy column 692 px.
- Easing `cubic-bezier(.25,.1,.25,1)`. Hover and press take 200 ms. The slider thumb and tab swaps take 300 ms.
- `prefers-reduced-motion`: nothing rises or scales, the scroll-linked hero is static, videos don't autoplay and show a play button instead.
- Focus: a 2 px `--accent` outline at 3 px offset, everywhere.

---

## 3. Shell: header and slider (`web/index.html`)

```
┌────────────────────────────────────────────────────────────────────────────┐
│ VIGIL                         ( VIGIL │ ● Parking │ ● Speed │ ● RFID gate ) │  48 px
└────────────────────────────────────────────────────────────────────────────┘
```
- The bar is `--surface` at 80% with `saturate(1.8) blur(20px)`, and a hairline at the bottom. It follows the system light or dark setting.
- "VIGIL" is set in 21 px semibold. The "Campus vehicle monitoring" subtitle is removed; the home page says it instead.
- The slider becomes an Apple-style segmented control:
  - Track: `rgba(118,118,128,.12)` in light mode, `.24` in dark.
  - Thumb: white in light mode, `#636366` in dark, with a soft shadow.
  - Labels: 13 px semibold.
  - Status dots: `--green` when the system is up, grey when it's down.
- Switching tabs crossfades the iframes (opacity, 300 ms) instead of cutting.
- The "isn't running" message uses the new type and a pill-shaped code chip.

---

## 4. VIGIL home page (`web/home.html`), section by section

The page is always dark from the hero to the apps, then switches to light grey for the
closing sections, as Apple's page does.

### 4.0 Local nav (sticky, frosted)
```
VIGIL                                   Overview  Gate  Road  Lot  Apps     [ Open consoles ]
```
- The link for the section in view gets an underline.
- "Open consoles" is a blue pill that scrolls to 4.8.
- At ≤734 px the links fold into a chevron menu.

### 4.1 Hero
```
                              VIGIL
              Campus traffic, in plain sight.
     Smart gates, a drone overhead and parking that counts itself.

        ╭──────────────────────────────────────────────────────╮
        │      overhead street film (rounded, 80% width)        │  ← as you scroll it grows
        │                                                      │    edge to edge, and the
        ╰──────────────────────────────────────────────────────╯    speed tags fade in
               Same street. Now every car has a speed.
 ┌──────────────────────────────────────────────────────────────────────┐
 │ Three live consoles, one tap away.     ( Parking ) ( Speed ) ( Gate ) │
 └──────────────────────────────────────────────────────────────────────┘
```
- **Signature moment.** Two copies of the same 12 s overhead clip play stacked and in sync: plain footage, and the same footage with VIGIL's speed tags. Scroll position drives the frame's size and the tagged layer's opacity, using CSS `animation-timeline: view()`. Browsers without it get the plain clip with the tags shown.
- The strip at the bottom mirrors Apple's buy strip. Its pills switch the top slider, through `target="_top"` links to `/#parking` and the others.

### 4.2 Get the highlights.
A horizontal scroll-snap gallery with 5 tall tiles. Each tile has one bold sentence at
the top and media below it. The gallery advances on its own; under it sit a dot nav
whose active dot fills as a progress bar, and a play/pause button.

| Tile | Copy | Media |
|---|---|---|
| Smart gate | **Smart gate.** Every car signs in and out with a card. | Gate illustration (4.3) |
| Speed watch | **Speed watch.** A drone reads the speed of every car below it. | Tagged overhead clip |
| Live parking | **Live parking.** Every bay, marked free or taken as it happens. | Parking clip |
| Campus Parking | **Campus Parking.** Check for a space before you set off. | Lots screen in a phone frame |
| Guard Alert | **Guard Alert.** The nearest guard hears about speeding first. | Alert screen in a phone frame |

### 4.3 Gate: "Tap in. Drive on."
```
                              Smart gate
                          Tap in. Drive on.
   Every registered car carries two cards, one for coming in and one for going
   out. Hold one to the reader and the visit shows up in the security office's
   log a moment later, with the time and direction.            (white lead → grey)

        ╭──────────────────── animated illustration ───────────────────╮
        │  card ▸ reader   ))) radio )))   log row slides in: In · 09:42 │
        ╰────────────────────────────────────────────────────────────────╯

   ☐ Tap.                    ☐ Send.                    ☐ Logged.
   Hold the card to the      The gate radios it to      It appears on the gate
   reader for a moment.      the office. No Wi-Fi.      log within a second.

                         The gate, in numbers.
        0.6 s                    Locked                      0 lost
   typical time from        every message from the    scans, through a two-
   tap to the log           gate is encrypted         minute network outage
```
- **Illustration, not a screenshot:** the RFID tables are empty. It's inline SVG and CSS, about 4 KB, and it plays once each time it comes into view. Its labels make plain it's a sketch, with no fake plates.
- A light card under the stats says: "No card? Guards count visitors with two spare cards of their own."

### 4.4 Road: "Speeding, spotted from above."
```
                              Speed watch
                    Speeding, spotted from above.
   A drone hovers over the road and reads the speed of every car beneath it.
   When a car stays over 40 km/h, VIGIL saves a photo and tells the nearest guard.

        ╭──────────────── tagged film, second clip (12 s) ─────────────╮
        ╰────────────────────────────────────────────────────────────────╯
              ● under 30 km/h   ● 30–40   ● over 40      (matches the render)

   ┌──────────────┐ ┌──────────────┐ ┌──────────────┐        caption gallery, ‹ ›
   │ still frame  │ │ "UNRELIABLE" │ │ guard alert  │
   └──────────────┘ └──────────────┘ └──────────────┘
   Steady when the     Honest when unsure.    Proof kept. Each
   drone isn't. Wind   A shaky reading is     speeding car is saved
   shakes the camera;  marked "unreliable",   with a photo, the time
   VIGIL holds the     never guessed.         and its top speed.
   picture still first.

                         Speed watch, in numbers.
      Within 1 km/h                0.23 s                       0.7 s
   shaky footage reads      for an alert to reach       a car must stay over
   the same as steady       a guard's phone             the limit before it counts
```

### 4.5 Lot: "Every space, at a glance."
```
                              Live parking
                       Every space, at a glance.
   A camera above the lot watches every bay. Free bays show green, taken ones
   red, and the count at the entrance changes within seconds of a car leaving.

        ╭──────────────────── parking clip with bay overlay ──────────╮
        ╰────────────────────────────────────────────────────────────────╯
                             ● free   ● taken

   ╭─────── the entrance board ───────╮   The entrance board.
   │   crop of the real LED counter   │   The same count drivers see at the gate,
   ╰──────────────────────────────────╯   shown big and bright.      (split layout)

                        Live parking, in numbers.
          165                       Instantly                      2 min
   bays watched in            the board updates when a     without fresh pictures, the
   Parking A                  bay changes, and every 20 s  board says "No live data"
```

### 4.6 Apps: "Two apps. One for drivers, one for guards."
```
   Two apps.                        │  Drivers find a space before they leave.
   One for drivers, one for guards. │  Guards hear about speeding in seconds.

                ╭────────╮   ╭────────╮     ← two phones per app, rising in on scroll
                │ screen │   │ screen │
                ╰────────╯   ╰────────╯
                  Campus Parking   ·   Guard Alert            ← tab nav, underline
   See how full every lot is, then open a live map of free bays.      ← changes per tab
```
- **Campus Parking:** "See how full each lot is, from 'Wide open' to 'Full', then open a live map of every free bay."
- **Guard Alert:** "When a car is speeding, the three nearest guards on duty get an alert with the place, the speed and a photo. The first to accept takes it, and the others see who's on it."
- Phone frames are drawn in CSS: rounded black bezel, no device brand.

### 4.7 A day on campus (product-viewer pattern)
```
   A day on campus.                         ╭────────────────────────────╮
   Five parts, working as one.              │                            │
                                            │   media for the selected   │
   (+) A car arrives                        │   step (crossfade)         │
   (+) It drives across campus              │                            │
   (+) It goes too fast                     │                            │
   (+) It parks                             ╰────────────────────────────╯
   (+) A driver checks the app
```
The selected step expands into a pill with one plain sentence:

| Step | Sentence |
|---|---|
| A car arrives | "The gate logs it with a tap of its card." |
| It drives across campus | "The drone overhead reads its speed." |
| It goes too fast | "The nearest guard gets an alert with a photo." |
| It parks | "Its bay turns red and the free count drops." |
| A driver checks the app | "Campus Parking shows the spaces left." |

This replaces the old database diagram. It ends with one line: "Each part runs on its
own, and they share a single, secure record."

### 4.8 Light zone: privacy, consoles, footer (`#F5F5F7`)
```
   Built with privacy in mind.
   ┌───────────────┐ ┌───────────────┐ ┌───────────────┐
   │ No plates     │ │ Kept apart    │ │ Campus only   │
   │ from the air. │ │ The gate log  │ │ Guard accounts│
   │ The drone     │ │ and the drone │ │ need a        │
   │ never reads   │ │ never mix.    │ │ thapar.edu    │
   │ number plates.│ │               │ │ address.      │
   └───────────────┘ └───────────────┘ └───────────────┘

   Open a console.
   ┌───────────────┐ ┌───────────────┐ ┌───────────────┐
   │ ◻ Parking     │ │ ◻ Speed       │ │ ◻ Gate log    │   white cards, icon,
   │ Live bays and │ │ Drone feed    │ │ Every visit,  │   "Open ›" link (blue)
   │ the board     │ │ and speeds    │ │ in and out    │
   └───────────────┘ └───────────────┘ └───────────────┘

   VIGIL · A capstone project at Thapar Institute of Engineering and Technology, Patiala
```

### 4.9 Motion summary (home)
| Element | Motion | Trigger |
|---|---|---|
| Headlines, copy, stats, cards | Fade in and rise 30 px, 0.7 s, 0.15 s between items | Section top reaches 70% of the viewport (IntersectionObserver), once |
| Hero film | Grows from 80% width with 28 px corners to full-bleed with square corners; tags fade in | Scroll-linked (CSS) |
| Highlights | Auto-advance; the progress dot fills over each tile's duration | In view; pauses off-screen or on pause |
| Phones | Rise 60 px with a slight scale, 0.15 s apart | In view |
| Gate illustration | Card slides to the reader, three radio arcs pulse, log row slides in | In view, once per entry |
| Tabs, product viewer | Media crossfades in 300 ms | Click or keyboard |
| Videos | Play only while on screen; at most two at once (the hero pair) | IntersectionObserver |

---

## 5. Dashboards: theme only

Each dashboard runs and launches on its own, on its own port. So the token block from
section 2 is **copied into each file's `:root`** rather than linked from port 8070;
linking would break the dashboards when started alone. A before/after check of panel
positions confirms the layout didn't move (section 10).

### Parking (`parking/dashboard.html`, plus `annotate_page.html`)
| Now | Becomes |
|---|---|
| Overpass, Overpass Mono (Google Fonts) | System SF Pro and SF Mono; the Google Fonts request is removed |
| Doto, the LED font on the entrance sign | **Kept.** It is the physical sign's look (decision D3) |
| Yellow `--marking` for controls and focus | `--accent` blue |
| Asphalt darks `#111317` / `#191C21` | `#000` / `#1D1D1F` (dark stays the default) |
| Concrete light theme | `#F5F5F7` / `#FFF` |
| free `#2FCB7D`, taken `#E5484D` | `--green`, `--red` |
| Five lot levels (wide, plenty, filling, almost, full) | green, teal, yellow, orange, red |
| Radius 14 / 10 / 8 | 18 panels · pill buttons and switch · 12 inputs |
| Panel borders | Removed in dark; kept as hairlines in light |
| Camera monitor and sign | Stay near-black; the note overlay gets the frosted chip look |

### Speed (`speed/dashboard.html`)
| Now | Becomes |
|---|---|
| Paper `#F3F1EC`, ink `#1C1B1A` | `#F5F5F7` / `#FFF`, ink `#1D1D1F` |
| Dark red `#8B1E1E` for attention | `--red` (text value in light mode) |
| ok, warn, info greens, ambers and blues | `--green`, `--orange`, `--link` |
| Black "primary" button | Blue pill |
| Radius 6 px | 12 px panels and monitor · pill buttons and chips |
| Chips on video `rgba(10,10,11,.72)` | Frosted: `rgba(29,29,31,.72)` + blur 20 px |
| Calibration step bars | Accent blue progress |
| Speed tags drawn on the video | Unchanged: they are pipeline output, not page styling |

### RFID gate log (`rfid/dashboard/index.html`)
| Now | Becomes |
|---|---|
| Warm neutrals `#F9F9F7`, `#FCFCFB` | `#F5F5F7` / `#FFF` |
| Inside `#2A78D6`, outside `#008300` (donut and pills) | system blue, `--green` |
| critical, warning | `--red`, `--orange` |
| Uppercase tracked labels (KPIs, table headers) | Sentence case, 12 px, `--muted` |
| Radius 10 / 7 | 18 cards · 12 inputs · pill buttons |
| Card border | None in dark; hairline in light |

---

## 6. Apps: theme only

### Campus Parking (`mobile/campus_parking/lib/theme.dart`, plus 12 hard-coded colours in other files)
| Now | Becomes |
|---|---|
| Concrete `#EEF0F2` / night `#121418` | iOS grouped `#F2F2F7` / `#000000` |
| Cards `#FFF` / `#1C1F24`, with outline | `#FFFFFF` / `#1C1C1E`, no outline |
| Yellow primary (buttons, focus) | System blue `#007AFF` / `#0A84FF`, white text |
| Free green `#2FCB7D` | `#34C759` / `#30D158` |
| Asphalt lot map `#3A3F47` | `#2C2C2E`; the white stall lines stay |
| Filled button: rounded 14, height 52 | Pill, height 50, 17 pt semibold |
| Headline weights 800 | 700 with tighter tracking (Apple large title) |
| Status pill | Same shape, new colours |

### Guard Alert (`mobile/guard_app/lib/ui.dart`)
| Now | Becomes |
|---|---|
| Off-white `#F7F5F0`, paper `#FFFDF9` | `#F2F2F7`, `#FFFFFF` |
| Dark red `#8B1E1E` as primary | Blue for everyday actions (sign in, duty toggle). **Red only for the alert** and its accept button: set explicitly in `alert.dart`, as styling only |
| Line `#E7E3DC` | `#D1D1D6` |
| Light theme only | Add a matching `darkTheme` (decision D4) |

Fonts: iOS already uses SF. Android stays Roboto (decision D5).

---

## 7. Media to produce

| ID | File | Source | Notes |
|---|---|---|---|
| M1 | `hero-plain.mp4` + `hero-tagged.mp4` | `speed/data/drone_video.mp4` 2–14 s: plain cut, and the same cut of a fresh `speedcam.py` render | **Done.** 1600×900, 2.4 and 3.1 MB, 288 frames each, offset 0 frames |
| M2 | `road.mp4` | The same render, a second 12 s cut | Gives the Road section different footage from the hero |
| M3 | Posters (`*.jpg`) | First frame of each clip | Fast first paint; shown under reduced motion |
| M4 | Gate illustration | Inline SVG + CSS | No file |
| M5 | `parking-sign.webp` | Screenshot of the parking dashboard's entrance board in Recorded mode | Real output |
| M6 | New app screenshots | Android emulator, after the apps are re-themed | **You sign in on the emulator**; I don't enter passwords. Until then the current screenshots stay |

- One ML job at a time for M1/M2, with the MPS memory limits from CLAUDE.md. Nothing else heavy runs alongside it.
- Media budget: about 10 MB for the whole page.
- `speedcam.py`'s offline runner has no speed-limit option, so renders use its fixed 30/40 colour bands. The live console uses 32/40 (80% of a 40 km/h limit). The Road legend follows the render: under 30 · 30–40 · over 40.

---

## 8. What stays the same

- Every dashboard's HTML structure, IDs, JavaScript and endpoints.
- Every Flutter widget tree, route and backend call.
- The slider's tabs, hash routing, `localStorage` memory and status checks.
- Port numbers and launchers.

---

## 9. Build steps

1. **Tokens and shell:** restyle `web/index.html`: header, segmented slider, crossfade, offline message.
2. **Home skeleton:** new `home.html` with the design system CSS, local nav, hero (M1, M3) and the reveal motion.
3. **Highlights:** the auto-advancing gallery with dot progress and pause.
4. **Story sections:** Gate (with the illustration), Road (M2), Lot (M5), with stats, trios and galleries.
5. **Closing sections:** the apps tab nav, "A day on campus", the light zone and the footer.
6. **Parking dashboard theme** (and the annotator page).
7. **Speed dashboard theme.**
8. **RFID dashboard theme.**
9. **Campus Parking app theme.**
10. **Guard Alert app theme.**
11. **New app screenshots** (M6, needs your sign-in) swapped into the home page.
12. **Verify everything** and update the CLAUDE.md files.

Steps 6 to 10 don't depend on the home page and can run in any order.

---

## 10. Verification

- **Home:**
  - Checked at 1440, 1024, 768 and 375 px, with no sideways scroll.
  - Reduced motion: nothing moves, and videos show a play button.
  - Keyboard alone can work the nav, gallery, tabs and product viewer.
  - No external requests, so it works offline.
- **Dashboards:**
  - Record the size and position of the main regions before and after each restyle.
  - Pass if nothing moves by more than a few pixels. Small font-metric shifts from Overpass to SF are expected.
  - Screenshots in light and dark.
- **Speed:** check the console works with the network off.
- **Apps:** `flutter analyze` and the existing tests pass. Screenshots on the emulator in light and dark.
- **Contrast:** every text and background pair checked at AA.

---

## 11. Decisions for you (my recommendation first)

| # | Question | Recommendation |
|---|---|---|
| D1 | Accent colour for controls everywhere | **Apple blue `#0071E3`.** The alternative is keeping road-marking yellow as VIGIL's own accent, but yellow can't be used for text on light grounds |
| D2 | Gate visual while the RFID tables are empty | **Animated illustration now**, swapped for a real gate-log screenshot once there are scans |
| D3 | The LED font on the parking entrance board | **Keep it.** It's what makes the board read as a physical sign |
| D4 | Dark mode for Guard Alert | **Add it.** Guards work nights, and Campus Parking already has one |
| D5 | Android font | **Leave Roboto.** Bundling Inter adds an asset for one platform |
| D6 | Team names in the footer | Your call: names (yours, Ishita Vaid, Dr. Sudhanshu Tyagi) or only the institute |
| D7 | Footage rights | The parking clip is an iStock preview with a watermark. The overhead street clip (`drone_video.mp4`, used for the hero and Road) is from an unrecorded source. Both are fine locally; confirm the source before showing either publicly |
