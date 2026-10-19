# Themes

ANetBBS ships eighteen hand-picked themes, all WCAG-AA-readable on
body text:

- **Modern Dark** (default) — high-contrast Catppuccin-style palette
- **Classic Green** — green-on-black BBS terminal look
- **Amber Terminal** — warm amber on dark brown
- **Blue Ice** — cyan on deep navy
- **Matrix** — bright neon green on pure black
- **Synthwave** — hot pink + cyan on dark purple
- **Paper White** — light theme for daytime
- **VOID SIGNAL** — triple neon (green + cyan + magenta) on pure black,
  with scanlines, brand glitch, and animated card borders
- **HACKERS (1995)** — neon violet + lime + cyan rave cyberpunk, an
  homage to the 1995 film
- **Graphite Teal** — neutral graphite dark theme with a single refined
  teal accent and Manrope headings, built for long reading sessions
- **Ivory Editorial** — warm cream daytime theme with a deep
  forest-teal accent and serif headlines
- **Retro Web '99** — tiled pinstripe background, beveled Windows-95
  chrome, underlined blue/purple links, Times New Roman
- **Command Deck** — the one theme that changes the page shape, not
  just its colors: a fixed icon rail replaces the top navbar (hover to
  expand, flyout submenus), mission-control blue chrome with amber
  status-light badges, Sora + IBM Plex Mono. Real mobile treatment,
  not a squeezed-down desktop layout: a slide-in drawer plus a
  thumb-reachable bottom tab bar on phones.
- **Ceefax Teletext** — 1980s broadcast videotext: flat primary-colour
  blocks, chunky VT323 pixel type, hard right angles everywhere, no
  gradients.
- **Brass & Oscilloscope** — a ham-radio control room: brushed walnut
  panels, brass rivets on the navbar and cards, oscilloscope-green LCD
  readouts, Oswald + JetBrains Mono.
- **Mahogany Study** — a gentleman's reading room: deep mahogany,
  brass nameplates, leather-bound cards, Playfair Display + Lora, with
  a small illuminated drop-cap on each card's opening line.
- **Cyanotype Blueprint** — an engineering schematic: Prussian-blue
  grid paper, white/cyan line-art only, dashed part boxes with corner
  crosshairs, Big Shoulders Display + Space Mono.
- **Riso Pop-Art Arcade** — a pinball backglass poster: flat saturated
  red/blue/yellow on black ink, thick comic outlines with hard offset
  shadows, Anton + Archivo.

## Per-user

**Profile → Themes** lets the user pick. Choice is sticky.

## Custom themes

**Admin → Themes** has a builder where the sysop picks colors with
hex pickers. The full set of CSS custom properties:

```
--theme-bg            page background
--theme-bg-dark       darker bg (cards, modals)
--theme-primary       accent / borders
--theme-primary-dark  hover / active accent
--theme-text          body text
--theme-text-muted    secondary labels
--theme-card-bg       card surface
--theme-input-bg      form fields
--theme-input-focus   focused field
--theme-border        misc borders
```

The theme builder writes all 10 so you don't need to touch CSS.

## Toggle default

Mark one theme `is_default=True` to make it the choice for users
who haven't picked one yet.
