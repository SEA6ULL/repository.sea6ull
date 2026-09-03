# Arctic Vibe — Mod Changelog

Ships as **Arctic Vibe** (`skin.arctic.vibe`, v1.0.9) by sea6ull.
A modified fork of **Arctic Fuse 2** (`skin.arctic.fuse.2`, v2.12.12) by jurialmunkey.

This document records every change made to the upstream skin, and — where it matters —
*why* a particular implementation was chosen. It is written to be handed to a future
assistant with no prior context, so it includes the traps and coupled behaviours that
are not obvious from reading the XML.

All modified lines are tagged with an `SKINMOD:` comment in the source, so
`grep -rn "SKINMOD" .` will list every touched location.

---

## Conventions used throughout

* The skin's coordinate space is **1920x1080** (`addon.xml` declares 1080i for all aspect ratios).
  1% of screen height = 10.8px.
* The skin uses a `-name` convention for negative constants (e.g. `hub_widgets_shift_y` /
  `-hub_widgets_shift_y`). **Both must be kept in sync.**
* Kodi constants **cannot do arithmetic**. Any "base + offset" value is pre-computed and
  stored as its own constant, with the arithmetic recorded in the comment.
* Files under `shortcuts/generator/` are **templates**. Editing them requires a
  menu/shortcut rebuild in Kodi before the change takes effect, because the real includes
  are generated at runtime by `script.skinvariables`.

---

## 1. Spotlight removed entirely

Spotlight was referenced in 313 places. Removal was done in two passes:

1. **Constant-folding.** Every `Skin.HasSetting(Hub.<X>.DisableSpotlight)` was replaced with
   `True` and every negated form with `False` (281 replacements). This makes the skin behave
   as though Spotlight were disabled on every hub, which is exactly the "no Spotlight" layout.
2. **Dead-code deletion.** The blocks that constant-folding made unreachable were removed.

Deleted include definitions (`1080i/Includes_Hubs.xml`):
`Hub_Spotlight`, `Hub_SpotlightFanart`, `Hub_Slide_Spotlight_OnFanart`,
`Hub_Slide_Spotlight_OnWidgets`, `Hub_Categories_Spotlight_Viewline`.

Other changes:
* `1080i/Includes_Spotlight.xml` replaced with **empty stubs** (`Spotlight_List`, `Spotlight_Main`,
  `Spotlight_Info`, `Spotlight_Fallback_Slideshow`, `Spotlight_Main_Button_Standard`). The names are
  kept so any residual reference still resolves at skin load instead of erroring.
* Spotlight options grouplist (176 lines) removed from `1080i/Includes_Shortcuts_Options.xml`.
* Spotlight label + options entry removed from `1080i/Custom_1115_Dialog_Shortcuts_Window.xml`.
* Spotlight trailer-autoplay setting removed from `1080i/Includes_SkinSettings.xml`.
* Spotlight menu-button setting removed from `1080i/Includes_Items.xml`, and its
  `Label_Setting_Spotlight_UseMenuButton` variable removed from `1080i/Includes_Labels.xml`.
* Generator stage `shortcuts/generator/data/base/category_spotlight.xml` unhooked from
  `shortcuts/generator/data/base/hubs_complex.xml`.
* Constants `hub_spotlight_*` and `hub_group_fanartspotlight_s` deleted.

### Trap that was hit and fixed
Removing the generator stage orphaned `<include>skinvariables-$PARAM[categories]-spotlight</include>`
in `Includes_Hubs.xml`. Left in place it is a **dangling include reference and a skin-load error**.
It was removed.

### Side effect worth knowing
`Hub_Slide_Group_OnWidgets` was gated on the Spotlight condition. With that condition now
constant-false, the menu group sits at a **permanent -440px offset** (`hub_group_widgetup_s`).
This is not a regression — it is exactly what Spotlight-disabled hubs did before — but it means
every hub now behaves like the old "no Spotlight" page.

### Left deliberately in place
`spotlight_label` / `spotlight_path` keys remain in the shortcut JSON defaults
(`shortcuts/skinvariables-shortcut-*.json`, `shortcuts/prebuilt/**`). They are inert data with no
consumer; editing JSON structure risks breaking shortcut config for no visual gain.

---

## 2. Footer settings section and bottom furniture removed

* The "Footer" section (label + Clock / Studio / Now Playing toggles) removed from
  `1080i/Includes_SkinSettings.xml`.
* In `1080i/Includes_Furniture.xml`, the definitions of `Furniture_Bottom_Right`,
  `Furniture_Bottom_Left`, `_Furniture_Bottom_Left` and `Furniture_Bottom` were **emptied**
  rather than deleting their ~30 call sites. Every existing
  `<include content="Furniture_Bottom_Right">` still resolves; it just renders nothing.
  Single point of change, trivially reversible.

### Left deliberately in place
`View_Furniture_Bottom_ViewLine` is **untouched**. Despite the name it is a generic label helper
also used for scrollbar page counts, sort indicators, weather and search headers. Emptying it
would break functional UI.

---

## 3. Widget rows moved down (more fanart space)

**Key file:** `1080i/Includes_Constants.xml`

```xml
<constant name="hub_widgets_shift_y">140</constant>
<constant name="-hub_widgets_shift_y">-140</constant>
<constant name="hub_widgets_shift_y_detailed">580</constant>   <!-- 440 + 140 -->
<constant name="-hub_widgets_shift_y_detailed">-580</constant>
```

Applied as `<top>` / `<bottom>` on the **wrapper group id 340** in both `Hub_Standard` and
`Hub_Widgets_Only` (`1080i/Includes_Hubs.xml`). Result: row 1 moved from ~55% to ~68% down the
screen, so the row below (title included) falls off the bottom edge.

### Why the wrapper and not the grouplist
`widget_grouplist_t` (880) and `widget_tbump_h` (640) feed a **scrolling** grouplist whose resting
position depends on runtime focus and a 640px top bumper. That math could not be derived reliably
from the XML. A `<top>` on the wrapper group translates the entire widget block by exactly N pixels
**regardless of the internal scroll math**, and provably cannot alter row-to-row spacing.

### FAILED APPROACH — do not repeat
An intermediate attempt also changed `widget_items_h` (1010 → 1064) to increase row *pitch*.
**This broke the widget titles**: they disappeared when scrolling to row 2. Reason —
`widget_items_h` is the height of each widget's row group and therefore feeds the grouplist's
item-to-item advance, but the row's *title* is positioned by a separate constant
(`widget_label_t`, -580) as an offset from its own layout slot. Growing the row height moved each
slot without moving the label offset with it, pushing titles outside the visible band.

**Pitch in this skin is a relationship between at least three constants, not one.**
`widget_items_h` is back at its stock 1010 and should not be used as a pitch lever.

---

## 4. Fanart (FlixArt background) enlarged

The FlixArt panel is anchored top-right and sized by `flixart_size_w` / `flixart_size_h`.
Height increased by **140px** (matching the widget row shift) so the original
fanart-to-widget relationship is preserved, just 140px lower.

Width was increased proportionally because `Background_FlixArt` uses `aspectratio scale`, which
preserves the source aspect and **crops** the overflow. Every original panel was exactly 16:9, so
adding height alone would have zoomed in and cropped the sides of every image. All variants are
held at 16:9 (1.7779 vs 16:9 = 1.7778).

`Background_Blur_Quadrants` reads the same two constants, so the blurred surround re-flows
automatically — no separate edit needed.

See section 9 for the final values after the Small/Medium/Large rework.

---

## 5. Setup wizard: widget mode step removed, Advanced forced

The wizard (`1080i/Custom_1180_Wizard.xml` + `Items_Wizard` in `1080i/Includes_Items.xml`) had three
steps: Themes → Dialog → Layout. The Layout step offered Basic / Detailed / Advanced.

**Advanced is now the only mode and the default.** Advanced is simply the "both settings unset"
state, so it is applied with `Skin.Reset(Hub.Home.DisableSubmenu)` +
`Skin.Reset(Hub.Home.EnableDetailedInformation)`.

### Critical trap — the Layout step was doing hidden work
Each of the three mode items also ran `Skin.Reset(Hub.Home.ReplaceWindow)`, triggered the shortcut
rebuild via `Skin.SetString(Shortcuts.RebuildDateTime,...)`, closed the wizard with `PreviousMenu`,
and **launched the menu-preset chooser** (`library-basic` vs `tmdb-basic`) that populates the home
menu. Deleting the step outright leaves the wizard stranded on the Dialog step with no way to
finish and **no home menu ever built.**

That work was redistributed:
* The **Dialog step is now the final step.** It applies Advanced, triggers the rebuild, then reloads.
* Because `ReloadSkin()` destroys the window mid-sequence, the close-out and preset chooser could
  not stay in the same `onclick` chain. They now live on a **new hidden button, id 8002**, in
  `Custom_1180_Wizard.xml`, reached via the skin's existing `AlarmClock(reset,SetFocus(8002),...)`
  pattern after the reload completes.
  **Superseded by §14** — 8002 still exists and still runs at the same point, but it no longer
  prompts; there is only one preset left and it is applied silently.
* Next / Back buttons and the back-nav hidden button (8001) were updated so none of them still
  point at the removed step.

Note: id 8002 is unique *within window 1180*. Other `8002` occurrences in `Includes_Items.xml` are
`Settings_Button` params in unrelated dialogs and do not conflict.

Also removed: the Mode selector (id 390) from `Custom_1115_Dialog_Shortcuts_Window.xml`; the widget
mode preview items in `Includes_Items.xml` (stubbed as `Items_Preview_WidgetsMode`, since
`Custom_1112_Dialog_PreviewSettings.xml` and `Items_Preview_CustomDialog` reference them);
`Shortcuts_Window_Var_Label_ClassicMode`, `Shortcuts_Window_Var_Label_ClassicMode_Preselect` and
`Shortcuts_Window_Var_Image_ClassicMode` from `Includes_Shortcuts_Window.xml`;
`Exp_Var_HomeLayout_IsBasic/IsDetailed/IsAdvanced` from `Includes_Expressions.xml`; and the
`extras/screenshots/layouts/` folder (Basic.jpg, Detailed.jpg, Advanced.jpg, ~1.7MB).

### Not migrated
Hubs previously set to Basic or Detailed by hand **keep their stored setting** — nothing migrates
them. A one-time reset across all hubs was offered but not implemented.

---

## 6. "Blur background — Basic (widgets)" setting removed

Removed from `Items_Settings_WidgetStyle` in `1080i/Includes_Items.xml`, along with
`Label_Settings_WidgetBlurBackground` in `1080i/Includes_Labels.xml`.

### This setting was NOT dead — important
The label read "Basic (widgets)" (`$LOCALIZE[10036] ($LOCALIZE[31103])`), naming the removed Basic
mode. But the underlying setting `Widgets.DontHideArtworkOnClassic` feeds `widget_hideartwork`,
which fires `SetProperty(Background.HideArtwork,True)` on widget focus — it **hides the fanart
outright**. On hub pages it is inert (it requires `widgets_only`, false in Advanced), but
`shortcuts/generator/data/base/home_widgets.xml` hardcodes `grouplist_widgets_only` to `True`,
so it was **still live on the Home window**.

Removing only the UI would have frozen it at its stored value — on default, hiding the (newly
enlarged) fanart with no way to switch it off. So:

* `shortcuts/generator/data/parts/widgets_row.xmltemplate` — `widget_hideartwork` forced to `False`.
  **Generator template: needs a menu/shortcut rebuild to take effect.**

### Still read the setting (deliberately left alone)
* `shortcuts/generator/data/parts/search_row.xmltemplate` — search result rows.
* `1080i/Includes_DialogInfo.xml` — widget rows inside the info dialog (~28 sites).
* `1080i/Includes_Lists.xml` — gated on the `hide_artwork` property, which is now never set for
  widget rows, so this path is inert for them.

Both remaining consumers freeze at the user's stored value. Forcing them to never-hide was offered
but not implemented.

---

## 7. Options menu opens on hover

`1080i/Includes_Hubs.xml`, `Hub_Categories_Options`, **button id 308**:

```xml
<onfocus condition="!Window.IsActive(1170)">ActivateWindow(1170)</onfocus>
```

The original `onclick` chain (`SetProperty(307Refocus,308)` + `SetFocus(307)`) was **kept** so mouse
clicks and the existing hidden-button-307 route still work.

### Known risks (verified working in practice, but worth knowing)
* **Reopen loop.** Closing dialog 1170 returns focus to 308. If Kodi re-fires `onfocus` on that
  return, the menu would reopen and trap the user. The `!Window.IsActive(1170)` guard does not
  cover this case (the dialog is closed by then). Fallback if it ever regresses: a latch property
  cleared via `<onunfocus>`.
* **Search reachability.** In the nav chain 308 sits between the category list and Search (309):
  `onleft` → categories (300), `onright` → 309. Search is therefore only reachable by passing
  *through* Options.

---

## 8. Row views moved down; Wall view rebuilt; default widget view changed

### 8a. Row views (`1080i/Includes_Views_Row.xml`)

```xml
<constant name="view_row">510</constant>                      <!-- unchanged, used by Combined -->
<constant name="view_row_shifted">650</constant>              <!-- 510 + 140 -->
<constant name="view_row_hitrect_y">586</constant>
<constant name="view_row_hitrect_y_shifted">726</constant>    <!-- 586 + 140 -->
```

All eight row-based views are shifted down 140px, matching the hub widget rows:
Square (50), Landscape (51), Poster (52), Circle (53), Card (54), Board (57), Quad (58), Disc (59).

Implemented by shifting the **defaults** of the shared `View_Row_Include` wrapper:

```xml
<param name="rowtop">view_row_shifted</param>
<param name="hitrect_y">view_row_hitrect_y_shifted</param>
```

The mouse hit rect was moved by the same 140 so targeting follows the artwork.

**Why not just change `view_row`:** that constant is also read by the **Combined** views
(502–592) in `1080i/Includes_Views_Combined.xml`, which must stay at their stock position.
Because the shift lives on the row wrapper's defaults and Combined positions its views with the
plain `view_row` constant directly, the two groups are structurally separate and Combined cannot
be caught up by accident.

Per-view override is still available — pass `rowtop` / `hitrect_y` to one view (useful if Quad,
the tallest of the eight, ever needs a smaller offset).

Card (54) and Board (57) each have two `View_Row_Include` calls behind their
`View.UseTextBasedCard` / `View.UseTextBasedBoard` toggles; both inherit the shift.

### 8b. Wall view — true grid (`1080i/Includes_Views_Wall.xml`)

**The AF2 skin already contained this layout.** `View_Wall_Include` had two branches; the one
gated behind `Exp_View_WallVert` sets the panel `orientation` to `vertical`, which fills
**row-major, 7 across**, wrapping to the next row. That branch is functionally identical to
Arctic Fuse 3's Wall view. AF3's only real change was deleting the other branch.

The default branch inherited `orientation: horizontal`, which fills **column-major** (item 1,
item 2 directly below it, item 3 in the next column) and scrolls sideways.

Geometry confirms 7 across was always intended: `view_poster_itemlayout_w` is `257.14` in **both**
AF2 and AF3 — exactly 1800 ÷ 7 — and the panel spans the full 1920.

Changes made:
* `1080i/Includes_Expressions.xml` — `Exp_View_WallVert` forced to `True`.
  The expression was **kept rather than deleted** so `Exp_View_Scrollbar_Maxi_V`
  (which references it) still resolves and the vertical scrollbar shows.
  This also lifts the original 16:9-only restriction, so the grid works on all aspect ratios.
* The column-major branch was deleted, and the redundant condition dropped from the survivor.
* The "Switch Wall Orientation" setting (`View.SwitchWallOrientation`) removed from
  `Items_Settings_Viewmodes` in `Includes_Items.xml`, and `Label_Settings_WallOrientation`
  removed from `Includes_Labels.xml`.

#### AF3 dependencies deliberately NOT copied
AF3's Wall block contains `<onleft>ActivateWindow(1171)</onleft>` and
`SetProperty(InfoPanel.FullSwitch,Wall,Home)`. Window **1171** (`Custom_1171_Dialog_Views.xml`)
does not exist in AF2, and `InfoPanel.FullSwitch` has zero references in AF2. Lifting AF3's block
wholesale would produce a dead left-navigation. AF2's `<onleft>600</onleft>` was kept.

#### Geometry difference left on the table
AF3 positions the grid at `top 290` / `bottom -40` (830px tall, deliberately running 40px past the
bottom edge so the last row is clipped as a scroll hint). AF2's values `top 200` / `bottom 140`
(740px) were **kept**, because they are tuned to AF2's header and 290 risks a gap or collision.

### 8c. Default widget viewtype: Landscape → Poster

`shortcuts/generator/data/setup/widgets_row.xml` — the final `<condition>True</condition>` fallback
in the `widget_include` rules changed from `List_Landscape_Row` to `List_Poster_Row`.
**Generator template: needs a menu/shortcut rebuild.**

This is the true default, used when a widget's style shows as "Default" in the widget options.

#### Scope limitation — expect this to look like it did nothing
Most widgets in the shipped presets do **not** rely on the default; they name Landscape
explicitly — 6 times in `shortcuts/skinvariables-shortcut-homemenu.json`, 10 in
`shortcuts/skinvariables-shortcut-sidemenu.json`, plus copies under `shortcuts/prebuilt/`.
Those keep their stored style, as does any already-configured widget. After a rebuild, Poster
appears only on widgets set to "Default". Rewriting those 30-odd explicit declarations was offered
but not implemented — they are deliberate per-widget choices (in-progress episodes, for instance,
may want landscape).

---

## 9. Background Style renamed to Small / Medium / Large, Medium default

Setting key is unchanged: `Skin.String(Skin.FlixArt.Size)`.

| Option | Stored value | Panel size | Old name |
|---|---|---|---|
| Small | `Small` | 1529x860 | Standard (was default) |
| **Medium** | *unset* (or `Medium`) | **1689x950** | Large |
| Large | `Large` | 1849x1040 | Extra Large |

All are 16:9 (see section 4).

**Medium is the default**, implemented by putting its values in the **base**
`1080i/Includes_Constants.xml`, so an unset setting falls through to Medium. Only Small and Large
need override files:

* **New file** `1080i/Includes_Constants_FlixArt_Small.xml` (1529x860).
* `1080i/Includes_Constants_FlixArt_Large.xml` now holds 1849x1040.
* `1080i/Includes_Constants_FlixArt_ExtraLarge.xml` **deleted**.
* `1080i/Includes.xml` conditions updated accordingly.

In `1080i/Includes_Items.xml` the Medium radio uses
`[String.IsEmpty(Skin.String(Skin.FlixArt.Size)) | Skin.String(Skin.FlixArt.Size,Medium)]` and its
`onclick` is `Skin.Reset(...)` rather than `SetString`, so the default state stays clean.
The `label2` dimension captions were also corrected — they still showed the pre-mod
1280x720 / 1440x810 / 1600x900 values.

New strings `#31598` ("Small") and `#31599` ("Medium") appended to all 13 language files.
`#31439` ("Large") is reused; `#31440` ("Extra Large") is now unused but left in place.

### Aspect-ratio interaction
`Includes_Constants_4x3.xml` and `Includes_Constants_3x2.xml` also define `flixart_size_*`
(1209x680) and are included **after** the Small/Large files but **before** the base constants.
Since the first definition wins in Kodi, 4:3 and 3:2 screens keep their own default, but an
explicit Small or Large selection overrides them and may overflow a narrower screen.
This ordering is inherited from upstream and was not changed.

---

## 10. Renamed to Arctic Vibe

* `addon.xml` — `name` changed to `Arctic Vibe`; `description` updated and now credits the
  upstream skin. **The first loading screen text comes from this `name` attribute** (Kodi's own
  skin-load splash), not from any skin XML — `Custom_1198_Window_Startup.xml` only shows the Kodi
  logo plus "Initialising Skin".
* All 13 `language/resource.language.*/strings.po` files — 38 occurrences of
  "Arctic Fuse 2" / "Arctic Fuse2" / "Arctic Fuse" replaced with "Arctic Vibe" (longest pattern
  first, so no stray `2` is left behind). `msgctxt` IDs are untouched.

### Addon id renamed to `skin.arctic.vibe`
The id was initially left as `skin.arctic.fuse.2` to preserve userdata; it has now been renamed.

* `addon.xml` — `id="skin.arctic.vibe"`.
* **Containing folder renamed to `skin.arctic.vibe`.** Kodi requires the addon directory name to
  match the id exactly; a mismatch makes Kodi register the addon under the folder name and can
  produce duplicate/ghost entries.
* 22 internal references updated across 11 files. These are **functional**, not cosmetic:
  * `shortcuts/skinvariables-generator.json` — `"skinid"`. `script.skinvariables` uses this to
    locate the skin and write generated includes. A stale value breaks menu/widget generation.
  * `1080i/Includes_Paths.xml` — `Path_SkinVariables_SkinFolder` (x2), used to build
    `addon_data` paths for skin users and prebuilt menu copying.
  * `1080i/Custom_1195_SkinUserLoginScreen.xml` — `skinid=` in the plugin:// content path.
  * `1080i/Includes_Labels.xml` (x2), `1080i/Includes_Settings.xml`, `1080i/DialogAddonInfo.xml` —
    `System.AddonVersion(<id>)` in the version line. A stale id renders an empty version.
  * `1080i/Settings.xml` (x8) — `<label2>` addon-id captions in the settings UI.
  * `.github/*` (CI metadata, not shipped), `language/resource.language.de_de/strings.po` (a header
    comment).

#### Userdata consequence — settings will NOT carry over
Skin settings, configured home/side menus and widget assignments live in
`userdata/addon_data/<addon id>/`. Because the id changed, Kodi now reads
`userdata/addon_data/skin.arctic.vibe/`, so the skin starts **unconfigured** and the setup wizard
runs again.

To preserve an existing configuration, copy the old directory across **before** first launch:

```
userdata/addon_data/skin.arctic.fuse.2/  ->  userdata/addon_data/skin.arctic.vibe/
```

Note `settings.xml` inside it may contain the old id in stored path strings (e.g. a customised
`Startup.ImageFolder` pointing at `special://skin/...` is fine, but absolute
`.../addons/skin.arctic.fuse.2/...` paths are not). Grep the copied file for `arctic.fuse` and fix
any absolute paths.

### Version set to 1.0
`addon.xml` `version` changed from `2.12.12` to **`1.0`** (this fork's own versioning).

**This is also what makes the rename actually appear.** Kodi caches addon metadata — including the
display name — in its addon database. Reinstalling a zip whose `version` is unchanged makes Kodi
skip the metadata refresh, so the old name persists on the skin-load splash even though `addon.xml`
is correct. Changing the version forces the re-read.

If the name is ever stale again after editing `addon.xml`, bump the version rather than hunting for
the string in the skin XML — it is not there.

### provider-name
Changed from `jurialmunkey` to **`sea6ull`**. Upstream credit is retained in the `<description>`.

### The startup wordmark — `Object_StartUp_Logo` (THE actual loading screen)
**This is the screen that shows "Arctic Fuse²" a moment after the Kodi splash.** It is
`Object_StartUp_Logo` in `1080i/Includes_Objects.xml`, drawn by `Hub_Widget_Splash` from
`Custom_1198_Window_Startup.xml`.

It was **not** findable with `grep "Arctic Fuse"`, because the name is split across inline markup
and two separate controls:

```xml
<label>[COLOR=main_logo]Arctic[/COLOR] Fuse</label>   <!-- font_title_huge, ColorHighlight -->
<label>$NUMBER[2]</label>                            <!-- separate control, font_head_bold -->
```

The literal text is `[COLOR=main_logo]Arctic[/COLOR] Fuse`, so the substring "Arctic Fuse" does not
exist in the file. The "2" is its own label in a horizontal grouplist with a smaller font, which is
why it renders raised like an exponent. `[COLOR=main_logo]` forces "Arctic" to the soft white text
colour while the rest of the label inherits `<textcolor>$VAR[ColorHighlight]</textcolor>`, which is
why only the second word tracked the user's chosen highlight colour.

**Fix:** the label is now `[COLOR=main_logo]Arctic[/COLOR] Vibe` and the `$NUMBER[2]` control has
been deleted.

**Lesson for future searches:** when hunting for on-screen text in this skin, grep for single words
(`Fuse`, `Arctic`) and remember that `[COLOR=...]`, `[B]`, `[UPPERCASE]` tags and multi-control
grouplists routinely split visible strings. Also note the `2` was `$NUMBER[2]`, not a literal `2`.

### icon.png rebranded (separate from the above)
`icon.png` (720x720) also had "arctic fuse 2" baked in as pixels — Kodi shows it as the addon icon
in the addon browser and skin settings. This was **not** the loading screen (an earlier diagnosis
wrongly claimed it was), but it did need rebranding anyway.

`icon.png` was rebranded in place:
* The word "arctic" (dark slate `#2D3E50`-ish, x 74-360, y 148-237) was **kept as-is**.
* "fuse" (light `#C9DEEF` = RGB 201,222,239, x 143-361, y 242-333) and the "2" (bottom right,
  x 649-688, y 612-675) were removed by diffusion inpainting — the rectangles were seeded with a
  vertical interpolation between the rows just outside them, iteratively Gaussian-blurred, then
  composited back through a feathered mask so no rectangular seam remains.
  *Note:* the "fuse" box top edge must sit at y>=240, because "arctic" descends to y=237 — seeding
  from y=229 drags dark glyph pixels into the fill and produces a visible dark patch.
* "vibe" was rendered in `fonts/Figtree-Bold.ttf` at size **122**, colour RGB(201,222,239), and
  positioned by measured glyph bbox so it lands exactly on the original's box
  (right edge x=361, top y=242, height 91px). The two words are right-aligned in the original
  layout, hence aligning to the right edge rather than the left.
* Saved back as RGBA to match the original mode.

`fanart.jpg` was checked and contains **no text** — no change needed.

If the icon ever needs redoing, the original is recoverable from the upstream repo; the plate and
the metrics above are enough to reproduce the edit.

---

## 11. Version / build line in Settings

Four places rendered `$INFO[System.BuildVersion,Kodi ,]$INFO[System.AddonVersion(<id>), • AF2 v,]`:
`1080i/Includes_Labels.xml` (x2), `1080i/Includes_Settings.xml`, `1080i/DialogAddonInfo.xml`.

All four now read:

```
$INFO[System.BuildVersionShort,Kodi ,]$INFO[System.AddonVersion(skin.arctic.vibe), • AV v,]
```

* `System.BuildVersion` → **`System.BuildVersionShort`** drops the `Git:<date>-<hash>` suffix.
* `AF2 v` → `AV v`.
* The version number itself is **not hardcoded** — it is read live from `addon.xml` via
  `System.AddonVersion`, which is why the `version` attribute there is what this line renders —
  currently `version="1.0.9"`, so it reads "AV v1.0.9". Keep it that way; hardcoding would let the
  two drift apart.

The info label targets the addon id, which is now `skin.arctic.vibe` (see section 10).

---

## 12. New setting: hide the profile picture on the idle "Now Playing" tile

**Setting key:** `Skin.HasSetting(ButtonMenu.ShowProfilePicture)` — **default off (unset)**.
**UI location:** Skin Settings → **Layout** → **Other**, last entry, labelled
`$LOCALIZE[33063] > $LOCALIZE[31603]` → "Options > Show profile picture".

### The behaviour being changed
The Options dialog (`Custom_1170_Dialog_HomeMenu.xml`) draws up to four configurable tiles. A tile
whose `OptionsTiles.NN.Include` string is `NowPlaying` renders `ButtonMenu_NowPlayingClock`
(`1080i/Includes_ButtonMenu.xml`). Tile 01 **defaults to `NowPlaying`**
(`Includes_Actions.xml`, line ~61), so this is the shipped configuration, not an edge case.

With nothing playing, that tile's two labels fall through to the date
(`Label_ButtonMenu_NowPlayingTitle` → `System.Date(DDD)`,
`Label_ButtonMenu_NowPlayingSubLabel` → `System.Date(d MMM YYYY)`) and its thumb falls through to
`Image_ProfileThumb` via the final unconditional `<value>` of
`Image_ButtonMenu_NowPlayingThumb` (`1080i/Includes_Images.xml`). The new setting suppresses that
thumb; the date labels are untouched.

### Why the icon is hidden by `<visible>`, not by emptying the texture
The obvious fix — making the profile-thumb fallback in `Image_ButtonMenu_NowPlayingThumb`
conditional so the variable resolves empty — **does not work**. `ButtonMenu_NowPlayingClock` passes
`icon_buttonstyle=true`, and in that branch `_Dialog_Special_Button`
(`1080i/Includes_Dialogs.xml`) draws the artwork *plus* a `circle_50.png` backdrop disc coloured
with `$PARAM[textcolor]`. Blanking the texture leaves a solid coloured circle floating on the tile.
The whole 80x80 icon group has to go.

So a new param was added:

* `_Dialog_Special_Button` — `<param name="icon_visible">true</param>` default, applied as
  `<visible>$PARAM[icon_visible]</visible>` on the 80x80 icon group (covers both the
  `icon_buttonstyle` and plain-image branches).
* `Dialog_Special_Button` — same `true` default, forwarded to **both** of its
  `_Dialog_Special_Button` calls (the unfocused and focused copies). Missing either one makes the
  picture reappear on focus.

`Dialog_Special_Button` has ~30 call sites across the dialogs; every one of them omits
`icon_visible` and therefore keeps the `true` default. Notably `ButtonMenu_Profile` — the *other*
tile that shows `Image_ProfileThumb` — is deliberately unaffected: that tile's whole purpose is the
profile, and it is not what the setting names.

### The visibility condition
`1080i/Includes_Expressions.xml`:

```xml
<expression name="Exp_ButtonMenu_ShowNowPlayingThumb">[Skin.HasSetting(ButtonMenu.ShowProfilePicture) | !String.IsEmpty(Pvr.EPGEventIcon) | !String.IsEmpty(Player.Art(poster)) | !String.IsEmpty(Player.Art(tvshow.poster)) | !String.IsEmpty(Player.Art(thumb))]</expression>
```

The four art checks are **exactly the conditions of the four real `<value>` entries in
`Image_ButtonMenu_NowPlayingThumb`, in the same order**. That is the point: the thumb is hidden
precisely when the variable would have fallen through to the profile picture, and never when there
is genuine artwork to show. `Player.HasMedia` was deliberately *not* used — something can be
playing with no artwork at all (a plain audio file, a stream), and in that case the profile picture
is still what appears, so it should still obey the setting.

**Keep the expression and the variable in sync.** If a future edit adds an art source to
`Image_ButtonMenu_NowPlayingThumb`, add the same test here or that artwork will be hidden.

No reload is needed after toggling — `<visible>` conditions are polled, and `Skin.HasSetting`
updates live.

### Layout consequence: none
Hiding the icon does not shift the labels and does not leave a gap. In `_Dialog_Special_Button` the
two labels are `left 40` / `right 100` and **left-aligned** (no `<align>`, so Kodi's default). The
`right 100` only reserves space; the text starts at the left edge either way. The tile simply loses
its circle.

### String
`#31603` "Show profile picture" appended to all 13 `language/resource.language.*/strings.po`
files with an empty `msgstr` (same convention as `#31598`/`#31599`). `#33063` ("Options") is a Kodi
core string already used as the dialog 1170 header, reused here for the prefix.

### Deliberately not done
* **No gating on whether a `NowPlaying` tile is actually configured.** The setting is always
  visible in Layout → Other. Gating would mean testing all four `OptionsTiles.NN.Include` strings
  in the entry's `<visible>`, which is noise for a setting that is inert when unused.
* **The setting is not mirrored into the Options-tile configuration dialog**
  (`Items_Settings_PowerMenuConfigure`). One home for it, per the request.
* `slevel` was left at the default `0`, so the entry shows at Basic settings level. The
library-section toggle added in section 13 sits directly beneath this one (id `011`) and follows
the same pattern.

## 13. Library section breadcrumb hidden in the viewline

The "viewline" is the small line of text above the list in every media view — `Info_Viewline`
in `1080i/Includes_Info.xml`. Its default label was the current section breadcrumb: **"Titles"**
in Movies, **"Artists"** in Music, and so on. That breadcrumb is now suppressed **while browsing
the local library only**.

### What was actually being rendered
`Info_Viewline`'s `label` param defaulted to:

```
$VAR[Label_Section_Main]$VAR[Label_Section_SortMethod, [COLOR=main_fg_30]&gt;[/COLOR] ,]
```

`Label_Section_Main` (`1080i/Includes_Labels.xml`) is a long fallback chain; in the library the
match is normally one of the *Content Labels* (`Container.Content(albums)` → "Albums",
`(songs)` → "Songs", `(seasons)` → "Seasons") or the *Category Label*
`$INFO[Container.FolderName]`, which is what produces "Titles" and "Artists" — those are the node
names Kodi reports for `videodb://movies/titles/` and `musicdb://artists/`.

`Label_Section_SortMethod` is a *different* thing and was deliberately kept: it only resolves when
the sort / filter / alphabet controls have focus, and it is functional feedback ("Sorted by: Title",
"Filtered > …"), not a section name.

### Implementation
Three small pieces, no deletions:

1. **`1080i/Includes_Expressions.xml`** — the gate:

```xml
<expression name="Exp_Viewline_HideLibrarySection">[String.IsEmpty(Container.PluginName) + [String.StartsWith(Container.FolderPath,videodb://) | String.StartsWith(Container.FolderPath,musicdb://) | String.StartsWith(Container.FolderPath,library://)]]</expression>
```

2. **`1080i/Includes_Labels.xml`** — a wrapper variable placed immediately after
   `Label_Section_Main`:

```xml
<variable name="Label_Section_Viewline">
    <value condition="$EXP[Exp_Viewline_HideLibrarySection]">$VAR[Label_Section_SortMethod]</value>
    <value>$VAR[Label_Section_Main]$VAR[Label_Section_SortMethod, [COLOR=main_fg_30]&gt;[/COLOR] ,]</value>
</variable>
```

3. **`1080i/Includes_Info.xml`** — `Info_Viewline`'s `label` **default** now reads
   `$VAR[Label_Section_Viewline]`. The stock string is preserved verbatim in a comment above it.

### User setting
**Setting key:** `Skin.HasSetting(Furniture.ShowLibrarySection)` — **default off (unset)**, i.e.
the breadcrumb is hidden out of the box.
**UI location:** Skin Settings → **Layout** → **Other**, directly below the profile-picture toggle
from section 12, labelled `$LOCALIZE[14022] > $LOCALIZE[31604]` → "Library > Show section name".
Control id `4011`. New string `#31604` "Show section name" appended to all 13
`language/resource.language.*/strings.po` files.

The setting is a *positive* opt-in, so it is the leading negated term of the gate rather than a
separate wrapper condition:

```xml
[!Skin.HasSetting(Furniture.ShowLibrarySection) + String.IsEmpty(Container.PluginName) + [ ...paths... ]]
```

The `Furniture.` namespace was chosen to match `Furniture.ViewLineColour`, the skin's existing
setting for this same line. No reload is needed after toggling — `<visible>` and label conditions
are polled live.

**To revert the whole feature** (removing the setting from the equation): set
`Exp_Viewline_HideLibrarySection` to `[False]`. Nothing else needs touching; the settings entry
becomes inert rather than broken.

### Why a wrapper variable and not an empty `<value>`
The obvious edit — adding `<value condition="...">` with empty content to `Label_Section_Main` —
relies on Kodi accepting a `<value>` node with no text child and returning an empty string for it.
That is not reliable across versions. Returning `$VAR[Label_Section_SortMethod]` instead is
guaranteed-safe: it is a real info label that simply resolves to nothing in the common case,
**and** it is exactly the piece that should survive.

### Why only the param *default* was changed
`Info_Viewline` has ~20 call sites. Most pass their own `label`:
widget row titles (`Includes_Widgets.xml`), the OSD panels, cast lists,
`Custom_1142_OSD_MusicTracks.xml` (hardcoded "Albums"), and every
`View_Furniture_Bottom_ViewLine` caller (page counters, `View_Scrollbar_Count`). Redirecting only
the default leaves all of those untouched — no need to audit them.

The call sites that *do* use the default are the library views themselves
(`Includes_Views.xml`, 6 sites), `MyFavourites.xml`, `MyPlaylist.xml`,
`Includes_Views_PVR.xml` and `DialogPVRChannelGuide.xml`. The last four are unaffected in practice
because their container paths are `favourites://` / `pvr://` / empty, so the expression is false
and they keep the stock label.

### Scope — what is and is not hidden
With the setting off (the default), hidden: everything under `videodb://`, `musicdb://` and `library://`. That includes movies, TV
shows, seasons, episodes, music videos, artists, albums, songs, and — worth knowing — **also the
more specific breadcrumbs**: a genre name, a set name, a show title, and the skin's own
`MediaFilter.BaseDir.Name`. If only the generic node words ("Titles"/"Artists") were meant to go
while genre and set names stayed, that is a different and much more fragile change: the two come
from the same `Container.FolderName` info label, so they can only be told apart by inspecting the
path shape, not by which variable value matched.

Not hidden: plugins (`plugin://`, and `Container.PluginName` non-empty), Files and Sources views,
Add-on browser, PVR, Favourites, playlist windows, and the video/music **Playlists** directories
(`special://videoplaylists/` etc., which still show their own folder name).

### Cosmetic side effect
Where `Info_Viewline` is called with `use_spinner=true` (the library views), the busy spinner lives
in a horizontal grouplist *after* the label. With the label empty the spinner shifts to the left
edge of the viewline instead of trailing the text. It is still visible and still animates.

---

## 14. Kodi-library prebuilt layout removed; TMDb Helper layout is now the install default

The skin shipped two prebuilt menu/widget presets under `shortcuts/prebuilt/`: `library-basic`
(built on `library://` nodes) and `tmdb-basic` (built on `plugin.video.themoviedb.helper`).
The wizard asked which one to install. **`library-basic` is gone and `tmdb-basic` is applied
unconditionally.**

### What was removed

* `shortcuts/prebuilt/library-basic/` — deleted (`skinvariables-shortcut-homemenu.json`,
  `skinvariables-shortcut-sidemenu.json`).
* The `run_dialog=select` chooser (`list=library-basic||tmdb-basic`) at both of its call sites.

### The two call sites, and why they ended up different

There were **two** places running that chooser. Only one was the wizard; missing the other leaves a
dead menu item pointing at a deleted folder.

1. **`1080i/Custom_1180_Wizard.xml`, hidden button 8002** — the wizard finish handler from §5.
   The select call is replaced with a direct, silent apply. No prompt: the wizard has nothing left
   to ask.
2. **`1080i/Custom_1115_Dialog_Shortcuts_Window.xml`, button id 9001 ("Prebuilt")** — the manual
   re-apply in the shortcut editor. This one **keeps a confirmation**, as `run_dialog=yesno`.
   The select dialog was previously doing double duty as the cancel path, and this action
   **overwrites the user's existing menu** — collapsing it to a one-click destructive button would
   have been a regression. Reuses existing strings only: heading `31102`, message `31079`,
   yeslabel `31456`, nolabel `222` (Cancel). No new strings, no `.po` edits.

### Single source of truth for the path

`shortcuts/builtins/skinvariables-prebuiltwidgets.json` was an **orphan** in the upstream skin —
present, correct, referenced by nothing. It is now the one definition of the copy action, with the
`{v}` placeholder folded to the literal folder:

```json
{
    "actions": [
        "route=copy_menufolder=special://skin/shortcuts/prebuilt/tmdb-basic/&skin={skin}"
    ]
}
```

Both call sites invoke it through the skin's established `run_executebuiltin` pattern, so the
prebuilt folder name lives in exactly one file. `{skin}` must still be passed as
`$VAR[Path_SkinVariables_SkinFolder]` — that variable resolves to `skin.arctic.vibe` **or**
`skin.arctic.vibe-<user>` when a skin user is set (`Includes_Paths.xml`), so hardcoding it would
break multi-user menu configs.

### Shipped defaults changed — this is what actually makes it "first install"

The root `shortcuts/skinvariables-shortcut-homemenu.json` and `-sidemenu.json` are the defaults
`script.skinvariables` seeds into `addon_data` when no user config exists. **They were the library
layout** (the root homemenu was `library-basic` with one entry reordered; the root sidemenu was
byte-identical to `library-basic`'s). Both are now copies of the `tmdb-basic` files.

> **Correction (see §15).** Only `-sidemenu.json` matters. The Home window's menu is
> `menu=sidemenu`; there is no `menu=homemenu` anywhere in the skin, so
> `skinvariables-shortcut-homemenu.json` is inert. Changing both was harmless, but the
> file that made the difference is the sidemenu one.

Without this, the first-run sequence would still have flashed the library menu: the shortcut
rebuild fires on the wizard's Dialog step, *before* the reload and before 8002 does the copy. If
the copy ever failed, the user would have been left on exactly the layout being removed. With the
defaults changed, the TMDb layout is correct from the first build and the 8002 copy is belt-and-braces.

Prior mods had **not** touched these two files (`SKINMOD` appears nowhere in `shortcuts/` outside
`generator/`), so overwriting them reverted nothing.

### Coupled behaviour worth knowing — interacts with §8c

§8c changed the default widget viewtype to Poster and noted the shipped presets mostly override it
by naming Landscape explicitly. Swapping the defaults changes those counts: homemenu **6 → 2**
explicit Landscape declarations, sidemenu **10 → 2**. The Poster default from §8c is therefore
visible on considerably more rows out of the box than that section describes.

### Left deliberately in place

* String `31395` ("Choose prebuilt widget configuration") is now unreferenced. Left in all 14
  `.po` files — inert, and editing 14 translation catalogues to delete one msgid risks more than
  it gains.
* Strings `31078` / `31456` / `31102` still read as though several presets exist ("various
  different default widget and menu sets"). Slightly inaccurate now; changing them means editing
  every `.po`. Not done.
* `spotlight_*` keys inside `prebuilt/tmdb-basic/*.json` — inert, same reasoning as §1.

### Validation

XML well-formedness (208 files), JSON validity (23 files), zero dangling includes, and unresolved
variable/expression count **identical to the original archive** — no regression. (Note: the
enumeration used here reports 35 rather than the 34 quoted above; the extra name is
`Exp_View_` from the same dynamic-construction family, and the figure is 35 both before and after
this change. What matters is that it did not move.)

---

## 15. First-run sidemenu flash removed; broken Trakt widget paths fixed

Two defects found from a `kodi.log` of a clean install.

### 15a. The vertical sidemenu flash before the wizard

**Symptom:** a layout with a vertical left-hand sidemenu appears for ~1 second immediately before
the setup wizard opens.

**Cause — `1080i/Home.xml`, first-run block.** Upstream forced Basic mode on the very first Home
load:

```xml
<onload condition="!Skin.HasSetting(Home.FirstRun)">Skin.SetBool(Hub.Home.DisableSubmenu)</onload>
```

`Hub.Home.DisableSubmenu` is the whole switch. In `Hub_Controls` (`Includes_Hubs.xml`) it feeds
`is_homemenu`, and that one param picks the layout:

| `is_homemenu` | Menu rendered | Where the items come from |
|---|---|---|
| **true** (Basic) | `Home_Menu` → `Home_Menu_Vert` → list 300, **vertical** | `skinvariables-sidemenu-staticitems` |
| **false** (Advanced) | `Hub_Categories`, `<orientation>horizontal</orientation>` | the same `sidemenu` items |

So the vertical sidemenu *is* Basic mode — the layout §5 removed from the UI but which Home.xml was
still switching on at first run. The wizard's Dialog step then ran `Skin.Reset(Hub.Home.DisableSubmenu)`
and reloaded, which is why it only flashed.

**Fix.** `Skin.SetBool` → `Skin.Reset`, plus an explicit reset of `EnableDetailedInformation`, so
first run lands on Advanced directly. Reset rather than simply deleting the line: skin settings live
in `userdata/addon_data/skin.arctic.vibe/settings.xml` and **survive a reinstall**, so a
`DisableSubmenu` stored by an earlier install would otherwise persist.

The other three first-run onloads (`Shortcuts.RebuildDateTime`, `Home.FirstRun`, `ReloadSkin`) are
untouched — they build the generated includes, which do not exist on the very first load.

### The trap here — do NOT delete the sidemenu shortcut config

The obvious reading is "the sidemenu is gone, so delete `skinvariables-shortcut-sidemenu.json` and
the sidemenu copy inside `prebuilt/tmdb-basic/`." **That would empty the home menu completely.**

`sidemenu` is the *name of the menu data*, not the name of the vertical layout. `Home.xml` passes
`categories=sidemenu`, and `Includes_Shortcuts_Window.xml` resolves the Home entry of the shortcut
editor to `menu=sidemenu`. Every generator stage that builds the home menu reads `menu="sidemenu"`
(`home_classic.xml`, `home_widgets.xml`, `home_listcheck.xml`, `side_items.xml`, `side_vars.xml`,
`hubs_complex.xml`). Advanced mode renders exactly the same items — just horizontally. The file is
the home menu.

Related finding: **`skinvariables-shortcut-homemenu.json` is the one that is actually inert.**
Grepping every `menu=` token in the skin yields `sidemenu`, `1101menu`–`1109menu`, `options-tray`,
`powermainmenu`, `powersidemenu`, `searchmenu` — no `homemenu`. Note the include named
`skinvariables-homemenu-widgets` is generated *from* `menu="sidemenu"` (see `home_widgets.xml`), so
its name is misleading. The dead `-homemenu.json` files (root and `prebuilt/tmdb-basic/`) are left
in place: they are harmless, and `script.skinvariables` seeding behaviour was not verifiable from
the skin source alone.

### 15b. The error notification after the wizard

**Symptom:** a notification flashes too fast to read once the wizard finishes.

**What the log shows.** At the moment described, the only errors are two failing widget rows:

```
error <general>: XFILE::CDirectory::GetDirectory - Error getting
  plugin://plugin.video.themoviedb.helper/?list_name=Best+Movies+Last+Decade
  &...&user_slug=jurialmunkey&...&owner=true&...&info=trakt_userlist&...&reload=&reload=
```

…and the same for `Highly+Rated+TV+Shows`. Both are Trakt lists owned by jurialmunkey, shipped in
the `tmdb-basic` preset.

**Why those two and not the others.** The preset contains four `trakt_userlist` widgets. The two
IMDb ones (`user_slug=justin`) load in the same pass and produce **no error** — public Trakt user
lists resolve without the user being signed in. The only difference between the pairs is
**`&owner=true`**, which tells TMDb Helper to resolve the list against the *authenticated* Trakt
account. On a fresh install there is no Trakt account, and the lookup fails. On an account that
*is* signed in it would still be wrong — the lists belong to someone else.

**Fix.** `&owner=true` stripped from both paths. Also collapsed a duplicated
`&reload=$INFO[Window(Home).Property(TMDbHelper.Widgets.Reload)]` that appeared **twice** on all six
TMDb Helper widget paths (visible in the log as the trailing `&reload=&reload=`; the property is
empty at first build). Applied to both `shortcuts/skinvariables-shortcut-sidemenu.json` and
`shortcuts/prebuilt/tmdb-basic/skinvariables-shortcut-sidemenu.json` — **they must stay identical**,
since the wizard copies the prebuilt over the seeded default. Verified byte-for-byte identical after
the edit.

**Confidence.** Kodi does not log the text of notification dialogs, so the popup cannot be read back
from the log directly. What can be said: these two errors are the only failures logged in that
window, and they land 2.4s after the post-wizard skin reload. `owner=true` is a plain defect
regardless.

**Not the cause, but present in the log:** `Skin has invalid include: skinvariables-options-tray-onload`
(≈9 per skin load, every load). `options_tray.xml` / `options_onload.xml` read `menu="options-tray"`,
and no `skinvariables-shortcut-options-tray.json` ships — the options tray is user-populated, so the
generator emits nothing and the include never exists. Upstream behaviour, log-only, never surfaced
to the user. Left alone. Likewise the `skinvariables-sidemenu-*` invalid-include warnings and
`Control 300 in window 10000 has been asked to focus` at 11:31:02 — those are the *first* load,
before the generator has run; the first-run rebuild-and-reload is what fixes them.

### Still Trakt-dependent by design

All four `trakt_userlist` widgets in the preset remain other people's Trakt lists, and the two
`trakt_trending` rows are Trakt endpoints. They resolve unauthenticated today, but they are outside
the skin's control. Swapping them for TMDb-native equivalents (`info=popular`, `info=trending`,
`info=top_rated`) would remove the Trakt dependency from the shipped default entirely. Offered, not
done — it changes what a fresh install actually shows, which is a content decision rather than a bug fix.

---

## 16. Preset trimmed to Movies / TV Shows; hub labels made specific

### 16a. Home hub shortcuts and widgets removed

Removed from the home menu (`menu=sidemenu`), whole shortcut plus its widget rows:

| Shortcut | Widgets removed with it |
|---|---|
| Discover (`ActivateWindow(1105)`) | IMDb: Top Rated Movies, IMDb: Top Rated TV Shows |
| Add-ons (`addons://sources/video/`) | Video add-ons, Music add-ons |
| Live TV (`ActivateWindow(TVGuide)`) | TV Channels, Recordings |

Individual widgets removed, shortcut kept:

* **Movies** → Best Movies Last Decade
* **TV Shows** → Highly Rated TV Shows

What remains is two shortcuts, one widget each: Movies → Trending, TV Shows → Trending.

Applied to **both** `shortcuts/skinvariables-shortcut-sidemenu.json` (the seeded default) and
`shortcuts/prebuilt/tmdb-basic/skinvariables-shortcut-sidemenu.json` (what the wizard copies).
Per §14 these must stay byte-identical; verified after the edit.

The `expression` and `forced` keys on the deleted items (`forced: True` on Discover) are inert —
`expression` is written into the item layout by `additional_sidemenu.xmltemplate` but never read
back, and nothing in the skin consumes `forced`. Deleting the whole item is safe.

**Side effect that resolves §15b.** All four `trakt_userlist` widgets are now gone from the preset,
including the two that were erroring. The `owner=true` fix in §15b stays anyway — it is still
wrong, and the widgets remain addable from the shortcut editor's TMDb Helper grouping.

**Discover is not orphaned.** Window 1105 is still reachable: `Includes_Actions.xml` swaps the
sidemenu search button for Discover under `Skin.HasSetting(SideMenu.SwapSearchForDiscover)`, and it
remains in `skinvariables-shortcut-config.json` as an addable shortcut.

The inert `-homemenu.json` pair (see §15a) was trimmed to match so the two files do not contradict
each other. It still has no consumer.

### 16b. Hub labels: generic "Hub" → per-window names

**Where it shows:** Settings → Shortcuts → Customise shortcuts (window 1115). The hub list built
`$NUMBER[1101]$VAR[Label_Header_Main_1101, (,)]`, and every one of those variables fell back to
`$LOCALIZE[31026]` ("Hub"), so the list read `1101 (Hub)`, `1102 (Hub)`, and so on.

**Fix — `1080i/Includes_Labels.xml`.** Only the *fallback* `<value>` of each
`Label_Header_Main_<id>` changed; the `Skin.String(Hub.<id>.Label)` branch above it is untouched, so
a user-set hub name still wins.

| Hub | Window file | New default |
|---|---|---|
| 1101 | `Custom_1101_Hub_Movies.xml` | `$LOCALIZE[20342]` Movies |
| 1102 | `Custom_1102_Hub_TVShows.xml` | `$LOCALIZE[20343]` TV Shows |
| 1103 | `Custom_1103_Hub_Music.xml` | `$LOCALIZE[249]` Music |
| 1104 | `Custom_1104_Hub_PVR.xml` | `$LOCALIZE[31085]` Live TV |
| 1106–1109 | `Custom_110<6-9>_Hub_Custom<1-4>.xml` | `$LOCALIZE[636]` Custom |

All five string ids were already in use elsewhere in the skin, so no `.po` edits were needed.

**Wider than the one list — by design.** `Label_Header_Main_<id>` also feeds `Label_Header_Main` and
`Label_Header_Hubs_Categories`, i.e. the hub page header itself. An unconfigured hub 1101 used to
show "Hub" at the top of the page and now shows "Movies". That is the same generic label, so it was
left consistent rather than special-cased.

`Label_Shortcut_SubHeader_GroupName` (24 values) was also switched from `$LOCALIZE[31026]` to
`$VAR[Label_Header_Main_<id>]`. That is the breadcrumb one screen deeper in the same flow — it read
"1101 Hub Categories" while the list it was reached from now says "1101 (Movies)". The numeric id is
deliberately kept in front: it is the only thing distinguishing 1106–1109, which all default to
"Custom".

### Deliberately left as "Hub" — do not "finish the job" here

The hub **window selector** still reads `Hub 1101`. Two files hold it:

* `1080i/Custom_1115_Dialog_Shortcuts_Window.xml:192` — the `list=` of a `run_dialog=select`.
* `1080i/Includes_Shortcuts_Window.xml:84-93` — `Shortcuts_Window_Var_HomeItem_Property_Current_Home_Hub`,
  passed as `preselect=` to that same dialog.

**They are a matched pair.** The preselect works by string-matching the current value against a list
entry, so changing one without the other silently breaks preselection. And the list is `||`
separated, so substituting `$VAR[Label_Header_Main_<id>]` would let a user-set hub label containing
`|` corrupt the list. Both were left alone.

Also untouched: the `CustomShortcut.Item0<n>` labels in `Includes_Labels.xml` (~16 values, e.g.
"Movies Hub"). Those name a *shortcut to* a hub, where "Hub" is the meaningful noun.

---

## 17. Hubs screen button tray removed; dead shortcut configs deleted

### 17a. Prebuilt / Rebuild buttons

`1080i/Custom_1115_Dialog_Shortcuts_Window.xml` — the bottom button tray, **grouplist 305** in its
entirety (56 lines), containing:

* **9001 "Prebuilt"** (`$LOCALIZE[31456]`) — the yes/no confirm added in §14. Only one prebuilt
  layout exists and the wizard installs it on first run, so there is nothing to choose or restore.
* **9002 "Rebuild"** (`$LOCALIZE[31494]`) — `RunPlugin(...&func=do_refresh...)` followed by
  `ActivateWindow(1115)`.

**Why removing Rebuild is safe.** The rebuild is already automatic, via two independent hooks:

1. Window 1115 has `<onunload>Skin.SetString(Shortcuts.RebuildDateTime,<timestamp>)</onunload>`
   (line 18). The shortcut editor 1116 has the identical onunload. Leaving either screen stamps a
   new build time.
2. `Action_Hubs_Onload` (`Includes_Actions.xml:289`) includes `Action_BuildShortcuts_OnLoad`, which
   runs `shortcuts/skinvariables-build-templates.json` on **every** Home/hub window load. That file
   passes `lastbuildtime={skinstring_lastbuildtime}` — read from `Shortcuts.RebuildDateTime` — into
   `route=action=buildtemplate`, so a changed timestamp forces regeneration.

So: edit shortcuts → leave 1115 → land on Home → rebuild runs. The button only ever saved the trip
back to Home.

**The trap — three dangling nav targets.** Grouplist 3000 and two of its buttons pointed down into
the deleted tray:

* line 175 — `<ondown>305</ondown>` on grouplist 3000 itself
* lines 273, 298 — on buttons 398 ("Edit global widgets") and 395 ("Edit widgets")

Left in place these produce `Control 305 in window 1115 has been asked to focus, but it can't`
on every down-press at the bottom of the list — the same error class already visible in the install
log. All three were removed; down now simply stops at the last button, which is Kodi's default
grouplist behaviour.

Strings `31456`, `31494`, `31104`, `31080`, `31081` are now unreferenced. Left in the `.po` files,
per the §14 convention.

### 17b. Dead shortcut config files deleted

```
shortcuts/skinvariables-shortcut-homemenu.json
shortcuts/prebuilt/tmdb-basic/skinvariables-shortcut-homemenu.json
```

These are the files identified as inert in §15a: the home menu is `menu=sidemenu`, and there is no
`menu=homemenu` token anywhere in the skin. They were the last place still describing widgets that
no longer exist in the shipped config (`Random Movies Genre`, `Shut Up, And Watch`), which is
exactly why they looked like stale prebuilt configs.

Confirmed before deleting: zero references remain to `homemenu` outside the generator's own
`skinvariables-homemenu-widgets` / `is_homemenu` / `widget_homemenu` / `listcheck_homemenu` names —
none of which read these files. The include `skinvariables-homemenu-widgets` is generated from
`menu="sidemenu"` (`home_widgets.xml`), so it is unaffected.

### Kept deliberately — every other json under `shortcuts/`

Checked one by one; none is a stale prebuilt config:

| File | Why it stays |
|---|---|
| `skinvariables-shortcut-sidemenu.json` | **The home menu.** See the §15a warning. |
| `prebuilt/tmdb-basic/skinvariables-shortcut-sidemenu.json` | What the wizard copies on first run. |
| `skinvariables-shortcut-powermainmenu.json` | 3 live `menu=powermainmenu` references. |
| `skinvariables-shortcut-searchmenu.json` | 5 live `menu=searchmenu` references. |
| `skinvariables-shortcut-config.json` (46KB) | **Not a menu config** — the catalogue of shortcuts the editor offers when you add an item. It lists library nodes, add-ons, PVR and TMDb Helper paths *by design*; that breadth is the point. Deleting it empties the "add shortcut" browser. |
| `skinvariables-shortcut-context.json` | 58 bytes, all-empty (`mainmenu`/`widgets`/`basic`). Not a menu list and has no `menu=context` consumer, but its structure suggests `script.skinvariables` reads it by filename convention. Empty either way — removing it risks a missing-file path for no gain. |
| `builtins/skinvariables-prebuiltwidgets.json` | Still the wizard's copy action (§14). The 1115 button is gone; hidden button 8002 in the wizard is not. |
| `skinvariables-build-*.json`, `skinvariables-generator*.json`, `skinvariables-startup.json`, `skinviewtypes.json` | Build/generator/startup/viewtype machinery, unrelated to menus. |

`menu=powersidemenu` is referenced by the skin with no shipped json — that menu is user-created and
the file appears in `addon_data` only once populated. Not a missing file.

---

## 18. Combine Widgets no longer collides with the widget row

**Symptom.** Turning on **Combine Widgets** for a category (shortcut editor → *Category: Combine
Widgets*) made the category's submenu button strip overlap the first widget row.

**Cause.** Two separate positioning systems that section 3 pulled apart.

The button strip is grouplist **3002** (`Hub_Submenu_Group`, `1080i/Includes_Hubs.xml`), pinned at
`hub_submenuwidget_t` (1300) and nested inside `Hub_Menu_Group` — which carries the permanent
**-440** offset described in section 1. It therefore rests at **~860px** and has no relationship
to `hub_widgets_shift_y`. Section 3 moved widget wrapper group **340** down 140px; the strip stayed
where it was, and the row landed on top of it.

The strip only exists for combined categories, so this was invisible until Combine Widgets was
switched on. Its generated visibility condition
(`shortcuts/generator/data/base/category_submenu.xml`) is:

```
!String.IsEmpty(Container(300).ListItem.Property(use_as_widget))
```

**Fix.** New include `Hub_Slide_Widgets_OnCombined` (`1080i/Includes_Hubs.xml`), added to group 340
in `Hub_Standard`. It slides the wrapper back up by exactly `hub_widgets_shift_y` when the focused
category has Combine Widgets on, so **that category renders at the original upstream spacing** and
every other category keeps the mod's lowered rows.

```xml
<include name="Hub_Slide_Widgets_OnCombined">
    <param name="posy">-hub_widgets_shift_y</param>
    <param name="condition">[String.IsEmpty(Container(300).ListItem.Property(use_as_widget))]</param>
    <param name="time">1</param>
    ...
</include>
```

### Why an animation and not `<top>`
`use_as_widget` is a **per-category runtime property** — it changes as the user moves along the
categories list. `<top>` / `<bottom>` and `<include content="X" condition="...">` are both resolved
**once at skin load**, so neither can react to it. A conditional slide on the wrapper is the skin's
own idiom for this (`Hub_Slide_Group_OnWidgets`), and — like the `<top>` in section 3 — it
translates the entire block, so it **cannot alter row-to-row pitch**. The section 3 warning about
`widget_items_h` still stands and is not touched here.

### Inverted condition — read carefully before editing
Following `Hub_Slide_Group_OnWidgets`, `posy` is the resting offset applied when `condition` is
**FALSE**. So `condition` holds the ***un*-combined** case (`String.IsEmpty(...)`), where the group
rests at 0 and the section 3 shift stays intact. Negating the condition inverts the whole feature.

### Only `Hub_Standard` needs this
`Hub_Widgets_Only` also has a group 340, but it is the `Hub.<id>.DisableSubmenu` path, and
`category_submenu.xml` is gated on `!Skin.HasSetting(Hub.{window_id}.DisableSubmenu)`. The strip
can never render there, so its `_detailed` (580) shift is left alone.

### `time` defaults to 1 (instant)
A 400ms slide would leave a window where the row is mid-travel while the strip fades in — briefly
reproducing the collision the fix exists to remove. Raise it to 400 for a slide matching
`Hub_Slide_Group_OnWidgets` if the snap reads as abrupt.

### Not changed: fanart size
`flixart_size_h` (section 4) was raised 140px to match the row shift, but it is a **constant**, and
Kodi constants cannot vary per category. Combined categories therefore sit at stock row height
against a slightly taller fanart panel. This is cosmetic — the panel is top-right anchored with a
gradient falloff — and reverting it would need a second `Background_FlixArt` variant keyed on the
same condition. Not done; noted here in case the overlap is ever judged too heavy.

**No rebuild required.** The change is in `1080i/`, not under `shortcuts/generator/`.

---

## 19. "Hide widget initialisation behind splash" made to work in Advanced mode

**Symptom.** Skin Settings → Other → Startup → *Hide widget initialisation behind splash*
(`Skin.HasSetting(Startup.WaitForLoad)`, string `#31428`) had no observable effect. Kodi splash →
skin splash → Home appears immediately with empty widget rows that fill in over several seconds.

### What the setting actually does

It is **not** an extension of window 1198. It draws `Hub_Widget_Splash` as a full-screen overlay
*inside* the Home/hub window — `Background_StartUp` (which is what cycles the
`Skin.String(Startup.ImageFolder)` launch images), `Object_StartUp_Logo`, an "initialising" label and
a busy spinner. Because it reuses the same furniture, it reads as the skin splash lingering.

`Hub_Controls` (`1080i/Includes_Hubs.xml`) contains **three** copies of that include. Which one is
compiled is decided at **skin load** from two params:

| Variant | Compiled when | Watched container |
|---|---|---|
| 1 | `[$PARAM[is_homemenu]]` | `Container(100601)` — first widget row |
| 2 | `![is_homemenu]` + `![widgets_only]` + `!DisableForHubs` | **`Container(300)`** — category menu |
| 3 | `![is_homemenu]` + `$PARAM[widgets_only]` + `!DisableForHubs` | `Container(100601)` |

### The defect

Variant 2's condition was:

```
System.HasAlarm(SplashTimeOut) + Integer.IsEqual(Container(300).NumItems,0) + Container(300).IsUpdating
```

Container 300 is the category selector — a `fixedlist` fed by the generator's
`skinvariables-<menu>-staticitems` (`Categories_Selector` in `1080i/Includes_Categories.xml`). It is
**static**: populated the instant the window opens, and it never reports `IsUpdating`, because that
flag only goes true for background directory fetches. Both of the last two terms are permanently
false, so the overlay could never draw.

**And variant 2 is the one Home compiles.** `Home.xml` passes *both*
`is_homemenu` and `widgets_only` as `Skin.HasSetting(Hub.Home.DisableSubmenu)`, which §15b resets so
that first run lands on Advanced — so both are false. Every hub with a submenu compiles it too. The
setting therefore worked **only** on a `widgets_only` hub (variant 3).

This is an upstream hole — Advanced-mode users never had the feature in AF2 either. Forcing Advanced
(§5, §15b) is what made it universal.

Not the cause: the `SplashTimeOut` alarm. `Action_Hubs_Onload` restarts it on every Home/hub load
(visible in a debug log as `started alarm with name: splashtimeout`), so it is always live.

### Fix

Variant 2 now watches the **first widget row of the first category**, using the same empty-or-
placeholder + `IsUpdating` test the other two variants use.

Getting the right container id is the whole difficulty, because there is no single widget id space.
`setup/widgets_row.xml` assigns `widget_id = grouplist_item_x * 1000 + 600 + enum_x` (`enum_x`
1-based), but `grouplist_item_x` comes from whichever datafile the path pulls in:

| Path | Datafile | `grouplist_item_x` | First row |
|---|---|---|---|
| Classic / `widgets_only` hub | `setup/widgets_hubsmenu.xml` | `100` (constant) | 100601 |
| Classic Home (sidemenu) | `base/home_widgets.xml` | `item_x + 100` | 100601 |
| Global widgets | `setup/widgets_constant.xml` | `200` (constant) | 200601 |
| **Advanced (this variant)** | `setup/widgets_standard.xml` | `{item_x}` — **no offset** | **601** |

Advanced Home and Advanced hubs reach `Hub_Standard` → `skinvariables-<categories>-widgets` →
`base/category_widgets.xml` → `setup/widgets_standard.xml`. So **100601 does not exist in Advanced
mode at all**. That is exactly why `Includes_Actions.xml` only ever references 100601 from
`Action_Hubs_Classic_SetFocus` (line ~318) and from the `onload` gated on
`is_homemenu | DisableSubmenu` (line ~272) — upstream never assumes it on the Complex path.

`item_x` is 0-based: `home_widgets.xml` uses `item_x + 100` for the same role that
`widgets_hubsmenu.xml` fills with the constant `100`, so the two classic paths only share the 100601
id space if the first item is 0. That gives **601**.

**Do not add an OR arm on another category's row as a safety net.** A `CDirectoryProvider` that has
never fetched reports `IsUpdating` **true** indefinitely — it is not "idle", it is "not yet run". Only
the focused category's rows fetch on window init (confirmed in debug logs: four providers refresh on
Home and nothing else ever does, even after navigating). So a second arm on, say, `Container(1601)`
sits permanently at `NumItems==0 + IsUpdating`, which latches the splash on forever.

A wrong id fails *safe*: a control that does not exist reports `NumItems` 0 and `IsUpdating` false, so
the splash simply never appears. A plausible-but-unfetched id fails *stuck*. Only single-arm
conditions on the focused category's first row are correct here.

The stock condition is preserved verbatim in the comment above the edit, along with the full id
derivation.

**To confirm the id directly**, read the generated
`1080i/script-skinvariables-generator-includes-<skinuser>.xml` on the device and find the first
`<control type="list" id="...">` under the `Hub_Menu_Group` include. That number is the first widget
row of the first category and is what this condition must name.

**No rebuild required.** The change is in `1080i/`, not under `shortcuts/generator/`.

### Correction history

**1.0.1** used `Container(100601)` for variant 2 and did nothing. The id was derived from
`base/side_vars.xml`/`base/home_widgets.xml`, both of which carry the `+ 100` offset, without checking
which datafile the Advanced path actually loads — it is `setup/widgets_standard.xml`, which has no
offset. Recorded because the four-way split above is not obvious from the templates and is easy to
get wrong the same way twice.

**1.0.2** used `Container(601) | Container(1601)`, the second arm hedging the 0-vs-1-based reading of
`item_x`. Result: the splash never cleared — it held past 30s and only dropped on a remote keypress.
That behaviour identifies the mechanism above (never-fetched provider ⇒ `IsUpdating` true) and, in
doing so, confirms `item_x` is 0-based: under the 1-based reading 1601 would have been a focused-
category row, fetched at startup, and the splash would have cleared on its own at ~8s.

**1.0.3** drops the hedge. Single arm on `Container(601)`.

### Two user settings this still depends on

* **`Startup.WaitForLoad.DisableForHubs` must stay off** (the "- Hubs" radio, string `#31114`,
  shown as *selected* when the setting is unset). Because Home now evaluates as a non-homemenu
  window, this sub-toggle silently governs **Home** as well as the hubs. With it on, variant 2 is not
  compiled at all and the fix is inert.
* **`Startup.ImageFolder` must point at a folder**, or the overlay shows the wordmark and spinner
  with no cycling launch images.

### Scope — what this does and does not hide

The overlay clears when the **first** widget row reports items, not when all of them finish. On a
measured cold start (Home window init 12:21:19.607, first row populated 12:21:29.223) that is ~9.6s
of splash, after which Home appears with row 1 filled and later rows still arriving. It hides the
worst of the wait, not all of it.

Widget rows on Home are slow mainly because Home is the window that loads *during add-on startup*:
the same TMDb Helper widget takes ~1.3s alone in a hub, ~2.8–3.7s in a quiet hub alongside four
others, and ~9–10s on Home while nine add-on services boot. Kodi also serves directory fetches from
**three** job worker threads, so a fourth and fifth provider queue — including cheap native ones. A
`library://music/recentlyplayedalbums.xml/` row measured a **6 ms** query that waited 9.4–11.9s for a
slot behind a plugin fetch. None of that is skin-side; it is noted here because it is what the splash
is covering up.

### Deliberately not changed

* **The `Hub.Home.ReplaceWindow` variants in `Home.xml`** (hub 1101–1109 rendered *as* Home) pass no
  `is_homemenu`, so they inherit `false` and also compile variant 2 — they are fixed by the same
  edit, but they remain gated on `DisableForHubs`. Arguably correct: the window really is a hub.
* **Variant 1 was left gated on `is_homemenu`.** It is unreachable while Advanced is the only mode,
  but it is stock, harmless, and the honest record of what the Basic path did. Decoupling Home from
  the `DisableForHubs` sub-toggle would need a new `is_home` param on `Hub_Controls` set from
  `Home.xml`; offered, not done.

### Version bumped to 1.0.3

`addon.xml` `version` `1.0` → `1.0.1` → `1.0.2` → **`1.0.3`**. See *Correction history* above for what
changed at each step.

Kodi's version comparison is per-component and numeric, so `1.0.3` > `1.0.2` > `1.0.1` > `1.0` and an install
over the top is treated as an upgrade rather than a reinstall.

**Why not `1.01`.** Because the comparison is numeric per component, `1.01` and `1.1` parse to the
same `[1, 1]` and compare **equal** — a later `1.1` release could not be installed over a `1.01` one.
Three-component patch numbering avoids that trap entirely. For the same reason `1.01` sorts *above*
`1.0.1` (`[1,1]` vs `[1,0,1]`), so if a `1.01` build was ever installed, Kodi will see this one as a
downgrade and refuse it — uninstall first, or bump past it.

Per §10, the bump is also what forces Kodi to re-read cached addon metadata; skin XML under `1080i/`
is re-read on every skin load regardless, so the bump is not what makes this fix take effect — but it
does mean the addon browser and the settings version line update. The version line needs no edit: it
reads live via `System.AddonVersion(skin.arctic.vibe)` and now renders "AV v1.0.3" (§11).

---

## 20. TMDb Helper error notifications suppressible from skin settings

**Files:** `1080i/DialogNotification.xml`, `1080i/Includes_SkinSettings.xml`
**Setting:** `TMDbHelper.SuppressErrorNotifications` (default off — notifications shown)
**Where:** Settings > Other > Expert (`slevel` 3), id `025`

### Why it is done this way
There is no add-on-side hook to gate this. `jurialmunkey.logger.kodi_traceback()` calls
`Dialog().notification()` unconditionally — it reads no setting and no skin property, so
`Skin.HasSetting(...)` cannot be consulted by the add-on. The add-on's own
`startup_notifications` / `sync_notifications` / `connection_notifications` settings cover Trakt
auth, sync dialogs and HTTP connection errors respectively; none of them touch the
`kodi_try_except` traceback path, which is the catch-all that fires for *any* unhandled exception
in the TMDb Helper service.

What the skin *does* own is `DialogNotification.xml` — Kodi renders every toast through it.
Kodi populates control `400` (icon), `401` (header) and `402` (message) before the window draws,
and this skin already reads 401/402 to build the toast. The suppression therefore works by
refusing to draw the dialog when the header identifies TMDb Helper.

### Match string
`kodi_traceback` builds its header as `f'TheMovieDb Helper {get_localized(257)}'`. String 257 is a
Kodi core string, so its value is locale-dependent — the match therefore uses
`String.StartsWith(Control.GetLabel(401),TheMovieDb Helper)` on the add-on-name prefix only, which
is locale-independent and catches **every** traceback notification the add-on can raise, present
and future. That breadth is the point: it is the coverage the add-on's own settings cannot give.

### Change
`DialogNotification.xml` — `<visible>` added to the outer group, plus a zero-time fade animation
on the inverse condition. The animation is belt-and-braces: `<visible>` is re-evaluated
continuously and there is a theoretical window where the group renders for a frame before Kodi has
populated control 401. The animation collapses it to alpha 0 in that frame.

`Includes_SkinSettings.xml` — radiobutton appended to the Expert section of
`SkinSettings_Items_Other`, after the existing `TMDbHelper.DisablePVR` entry (id `024`, so the new
entry takes `025`). Label is literal text rather than `$LOCALIZE[...]`; swap it for a string in
`language/resource.language.en_gb/strings.po` if translation is ever wanted.

### Toggle polarity — deliberately NOT matching the neighbouring entries
The button reads **"Suppress TMDb Helper error notifications"** and uses a *positive* selected
condition:

```xml
<selected>Skin.HasSetting(TMDbHelper.SuppressErrorNotifications)</selected>
```

Selected = suppression active = no toasts. The setting name, the label and the radio state all move
in the same direction.

This is inconsistent with `TMDbHelper.DisablePVR` (id `024`) and several others in this file, which
name the setting for the thing being *disabled* and then invert with `!Skin.HasSetting(...)` so the
button can be labelled for the feature. That pattern reads well in the settings list and badly
everywhere else: the first build of this entry used it, and the inverted state made a test run
unreadable — "setting off" meant the radio was *on*, because the radio described notifications while
the setting described suppression. Two things called "off" in the same sentence.

If a future entry here is a `Suppress`/`Disable`/`Hide` setting, prefer this polarity over the
neighbours'. Consistency with a confusing local convention is not worth the ambiguity.

### Trap
The `<visible>` must sit on the window's top-level **control**, not on the window. Kodi has no
`<visible>` on `<window>`, and putting the condition on the individual includes instead would
leave the `Furniture_Busy_Base` backing plate drawn — an empty toast panel with no text.

### Coupled behaviour worth knowing
`Furniture_Busy_Main` reads `Control.GetLabel(401)` / `(402)` as its `mainlabel` / `minilabel`
params. Those controls are declared *after* the group in the file and are hidden via
`Object_Hidden_Item_Definition`. Do not reorder or remove them — they are Kodi's data carriers for
this window, not decoration.

### Version bumped to 1.0.4

`addon.xml` `version` **`1.0.3` → `1.0.4`**. Same numeric-comparison reasoning as §19: three
component parts, so `1.0.4` > `1.0.3` and an install over the top is an upgrade, not a reinstall.

Nothing here needed the bump to take effect — `1080i/` XML is re-read on every skin load — but it
refreshes cached addon metadata and the settings version line, which reads live via
`System.AddonVersion(skin.arctic.vibe)` and now renders "AV v1.0.4" (§11).

### What this does not do
The dialog still opens and closes on schedule (`Window Init` / `Window Deinit` still appear in
`kodi.log`, roughly 5.4s per toast) — it is drawn transparent, not prevented. The underlying error
still occurs, is still logged, and still costs the wasted API round trip and the stale item panel.
This hides a symptom.

It also hides TMDb Helper errors that *are* worth seeing — expired Trakt tokens, bad API keys,
Fanart.tv failures. Default is off for that reason; turn it on only while a known upstream bug is
outstanding. `kodi.log` remains complete either way.

---

## 21. Wall views run full-screen; wall header removed

### 21a. The wall band was sized for chrome that no longer exists

`View_Wall_Include` positioned the grid at `top 200` / `bottom 140` — a 740px band. At the
350px row pitch shared by nine of the eleven wall views that fits exactly **two rows**
(2 x 350 = 700). Ring was the visible exception at four rows, because it is the one wall view
with its own pitch (175).

The 200 reserved space for the wall-only header. The 140 reserved space for a footer that
**this mod had already emptied** in section 5 — `Furniture_Bottom_Left` and
`Furniture_Bottom_Right` are stubs, and every remaining bottom control (sort buttons 8000,
page counter, scrollbar strip, alphabet filter, hint text) is focus-gated behind
`Exp_View_ScrollFilter_HasFocus` or `allowhiddenfocus` visibility. Nothing persistent lived in
that 140px. It was dead reserve.

Both are now zero, so the grid is full-bleed and shows **three rows**. Artwork rows land at
y40-350, y390-700 and y740-1050, leaving a 40px top and 30px bottom margin.

### 21c. Header removed without waking the row-view info panel

`Exp_View_HasHeader` is `Exp_View_IsMedia + Exp_View_WallMode` — true only for wall views in
media windows. In `View_Row_Info` it gated a group containing `Furniture_Top_Left` (the section
title, ~y70-130) and an `Info_Viewline` breadcrumb at `top 170`. That group is now empty.

**The obvious approach — forcing the expression to `False` — is wrong.** The third branch of
`View_Row_Info` is gated on `!$EXP[Exp_View_HasHeader]` and draws the row-view `Info_Panel`
(plot / details). Flipping the expression would switch that branch on and drop a details panel
over the wall grid. The expression is left intact and the group left in place, emptied.

### 21d. Total scope

Vertical only. Three files, two of them one-liners:

| File | Change |
|---|---|
| `Includes_Constants.xml` | 2 new constants: `wall_top` 0, `wall_bottom` 0 |
| `Includes_Views_Wall.xml` | `<top>` / `<bottom>` swapped for those constants |
| `Includes_Views.xml` | wall header group emptied |

`Includes_Items.xml` and `Includes_Lists.xml` are byte-identical to the pre-mod originals.

### 21e. REVERTED — two failed attempts, recorded so they are not retried

Both shipped broken, both were caught on hardware, both are fully backed out.

**(1) Threading dimension params through `View_Wall_Include`.** An earlier revision passed
`item_w` / `item_h` / `itemlayout_w` / `itemlayout_h` through the wall include to make row pitch
tunable and to offer an optional 8-column "dense" wall behind `Skin.HasSetting(View.WallDense)`.
The params were declared empty (`<param name="itemlayout_w" />`) and forwarded unconditionally,
on the assumption that a passed-but-empty param falls through to the callee's declared default.

**It does not — the empty value wins.** With `itemlayout_w` empty, Kodi fell back to the panel
width and laid out a single 1800px-wide column, stretching every poster across the screen.

The reasoning that produced it: `Widget_Content` writes
`<param name="item_h">$PARAM[item_h]</param>` with `item_h` undefined (its caller `_Widget_Row`
never sets it) and passes that into `List_Square_Row`, which declares defaults; square widgets
render correctly, so empty appeared to fall through. That inference was wrong and was never
tested on hardware before shipping.

The same revision promoted `List_Poster_Row` and `List_Overview_Row` from hardcoded dimensions
to params. Since `Widget_Content` passes those four params empty to whatever list include a
widget uses, this also broke **home-screen poster widgets**, not just the wall. Both includes
have been restored to their original hardcoded form.

*Consequence:* row pitch is **not** adjustable from `View_Wall_Include`. Changing it means
editing `view_poster_itemlayout_h` (350), which is shared with the row views, hub widgets and
combined views, and would move all of those too.

**(2) Insetting the panel horizontally.** A second revision added `left 60` / `right 60` on the
theory that every wall view's `itemlayout_w` divides 1800 exactly (257.14 x 7, 450 x 4,
600 x 3, 360 x 5), so a full-width 1920 panel must be leaving a dead ~120px column on the right.

**That analysis missed `offset_x`.** `List_Core` gives each itemlayout
`<left>$PARAM[offset_x]</left>` — `view_offx`, 80 — so artwork is already shifted 80px right
inside its slot. In the original full-width panel the first poster sits at x80 and the last runs
1622.86-1840: **80px margins on both sides, already centred.** The surplus 120 is not dead space
at the right, it is the 80px inset plus the 40px trailing gutter.

Insetting the panel double-counted it. The grid shifted to 140-1900, giving a visibly wide left
margin and a right margin of only 20px, which TV overscan then ate into — the last column
rendered about three-quarters visible.

*Rule for both:* **the wall panel takes `top` and `bottom` and nothing else.** Its horizontal
placement is already correct and is governed by `offset_x`, not by the panel bounds.

### 21f. Why there is no peek at row four

Three rows at 350 pitch is 1050 of 1080. The remaining 30px is **smaller than the 40px cell
gutter**, so row four's artwork begins at y1090 — ten pixels below the bottom edge. No `top` /
`bottom` tuning produces a peek:

* `top 0` — artwork rows at 40-350, 390-700, 740-1050. Nothing left.
* `top -40` — artwork flush to the top edge, 30px peek. Cramped top row for a sliver.
* `top -50` — 40px peek, but the top row is clipped by 10px.

`Layout_Poster` uses `aspectratio=scale` against a fixed `poster_w217_h310.png` diffuse mask, so
artwork height cannot move without width moving with it. With gutters held at a uniform 40px and
7 columns, `item_w` is pinned at 217.14 and therefore `item_h` at 310. A peek requires giving up
either gutter uniformity or column count. **Three rows, no peek, is the accepted final layout.**

### 21g. Pre-existing issue noted, not changed

`View_Wall_Include` passes `<preloaditems>0</preloaditems>` as nested content, but `List_Core`
writes `<preloaditems>$PARAM[preloaditems]</preloaditems>` (default 2) *after* its `<nested />`,
so the later element wins and the wall preloads 2. This predates the mod. Left alone — changing
it has scroll-performance implications worth measuring separately.

---

## 22. Dialog backdrop behind the bottom furniture strip

### 22a. The problem

The sort/view button row and the A-Z jump letters are drawn straight onto the view with nothing
behind them. That was survivable while the row views sat at `view_row` 510; after section 8a
moved them to 650 — and after section 21 made the wall grid full-bleed, putting artwork at
y740-1050 — both strips landed on top of artwork and became hard to read. The controls
themselves work correctly; this is purely a legibility fix.

Neither strip is a real Kodi window. Both live in `View_Furniture_Bottom`
(`1080i/Includes_Views.xml`), inside `Dimension_Bottombar` → `Dimension_Furniture_Gutters`:

* `View_Sorting_Buttons` — grouplist `8000` (View / Sort / Order / Watched / Filter /
  Update Library / Addon Settings / Fullscreen, plus the MyPlaylist set).
* `View_Alphabet_Filter` — edit `19`, letter panel `600`, autocomplete `601`.

### 22b. What was added

A new `View_Furniture_Bottom_DialogPanel` include, placed **first** in the gutters group so it
draws behind everything else. Nothing was repositioned.

It reuses `Dialog_Background_Blur` from `Includes_Dialogs.xml` — the same include every real
dialog window resolves through — so it follows `Skin.String(Background.DialogImage)`, the user's
Dialog Colour setting, with **no new setting of its own**. That covers all nine themes including
Adaptive's blurred-artwork fill, plus `$VAR[Color_DialogBorder]` for the outline and
`Skin.HasSetting(Glass.DarkPanels)` / `Glass.EnableBorders`.

Wired into two call sites:

* `Includes_Views.xml` — `View_Furniture_Bottom` (all library views).
* `Includes_Views_PVR.xml` — `View_PVR_Menu`, which carries its own copy of the same strip.

### 22c. Geometry

```xml
<constant name="view_furniture_dialog_t">-40</constant>
<constant name="view_furniture_dialog_h">130</constant>
<constant name="view_furniture_dialog_x">60</constant>
```

`_t` and `_h` are relative to the `Dimension_Furniture_Gutters` group: 60px tall at y950-1010
(`Dimension_Bottombar` sets it 80px off the bottom at 40px tall, then the gutters bleed 10px each
way). `_x` insets from that group's edges. So the panel spans **y910-1040, screen x140-1780**.

The selectable ink of **both** strips is y940-1020, centre **980**:

| Strip | Nominal box | Actual ink |
|---|---|---|
| Sort buttons | grouplist 160 tall on `centertop 50%` → y900-1060 | **y940-1020** |
| Alphabet jump | panel `600`, two rows of 40px letters | **y940-1020** |

The sort row's nominal box is misleading. Its focus texture is
`Texture_CircleButtonDialog_Highlight_Focus_V` → `common/circlebutton.png`, a 160x160 texture
whose alpha bounding box is only `(40, 40, 120, 120)` — 40px of transparent padding on every
side. So the visible pill inks 40px inside the 200x160 button rect, not the full rect.

**First pass got this wrong.** It sized the panel y870-1050 on the assumption that the alphabet
block's ink started at y900, giving a centre of 960 against ink centred on 980 — the buttons and
letters visibly sat ~20px below the midpoint. The panel now centres on 975.

#### The typed-letter echo
`View_Alphabet_Filter` draws a faint `main_fg_30` label above the letters showing the first
character of edit `19` (`top -40`, height 40). At `font_main` 30px it inks **y900-930**, so it
now overhangs the panel top by ~10px. It is decorative and duplicates the bold + underlined
highlight already on the selected letter in the panel, so it was left alone rather than moved.
There is no room to tuck it inside: the only clear band is y910-940, and a 30px glyph there
would collide with the first row of letters. Set `_t -50` / `_h 150` to enclose it instead, at
the cost of 20px of height and 5px of centring.

### 22d. Why the panel is not sized to the buttons

It cannot hug them. Confirmed against `CGUIControlGroupList::Process`, which positions children
with `SetOrigin(m_posX + pos, m_posY)` — children keep their own `posX` as an *additional* offset,
and the list advances by `Size(control)`. Each `DialogInfo_Button` emits **two** controls, a 200px
button (`back_width`, which wins over `back_size` because Kodi's `GetDimension` uses
`FirstChildElement`) and a 40px icon group, so each button advances the list 240px while its
visible pill is only 120px wide.

Visible button count then swings with context, and the ink width with it:

| Buttons | Ink (screen x) | Width | Context |
|---|---|---|---|
| 3 | 640-1220 | 580 | MyPlaylist subset |
| 4 | 520-1340 | 820 | plugin / basic list |
| 5 | 400-1460 | 1060 | library, no advanced filter |
| 6 | 280-1580 | 1300 | library — View/Sort/Order/Watched/Filter/Update |
| 7 | 160-1700 | 1540 | + Fullscreen (`Player.HasMedia`) |

Kodi will not accept a `$VAR` in a `<width>` tag, so a content-following width is not
expressible; the panel has to be fixed at the worst case. A per-count stack of fixed-width images
gated on the same visibility conditions was considered and rejected as too fragile.

**`_x` therefore cannot go much past 60.** At 60 the panel is x140-1780, which still clears the
seven-button case with 20px to spare. At 100 it clips it. `_x` 0 (the original full-gutter width)
through 60 are all safe; anything above 60 trades the `Player.HasMedia` case for a smaller panel.

`left`/`right` insets were kept in preference to a centred fixed `<width>` so the panel continues
to inherit the per-aspect-ratio constant overrides.

### 22e. Visibility gate

```xml
<expression name="Exp_View_FurnitureDialog_IsVisible">[$EXP[Exp_View_SortModeButtons_HasFocus] | Control.HasFocus(19) | Control.HasFocus(600) | Control.HasFocus(601)]</expression>
```

Deliberately narrower than the existing `Exp_View_ScrollFilter_HasFocus`, which was the obvious
candidate but wrong twice over:

* It includes the **vertical** scrollbars `64`/`65`/`66`/`611`, which are on the right edge and
  have nothing to do with this strip.
* It includes `610`. In `View_PVR_Menu` that control bounces focus straight to `8000`, but the
  library views' copy in `View_Hidden_Buttons` has three *conditional* `onfocus` branches and
  **none of them fire** when `Exp_IsAlphabetStrip` is true and `Container.NumPages` is not 1 — so
  `610` retains focus as the left/right letter-scrubbing state. `View_Alphabet_Filter` does not
  show for `610`, so including it would raise an empty panel.

The horizontal scrollbar `60` is also excluded: it is thin, self-contained furniture that reads
acceptably against artwork.

### 22f. The `diffuse` parameter

`diffuse/dialog_w960_h200.png` is passed through. It is consumed **only** by the Adaptive branch
(`Dialog_Background_Blur_Adaptive`), where it masks the blurred-artwork layer; the eight fixed
colour themes go through `Dialog_Background_Blur_Standard` and ignore it. 960x200 (4.8:1) is the
closest aspect available in `media/Textures.xbt` to this ~9.8:1 bar — the corner curves stretch
horizontally, which is not visible at this size. The base `common/dialog.png` is a 64x64 texture
drawn with `border="20"`, so the rounded corners are texture-space and stay correct at any size.

`transparency` is intentionally not passed. Kodi's `ResolveParametersForNode` removes a
`<param>` that forwards an undefined `$PARAM[...]` to a nested include, precisely so the nested
default (`false`) is picked up rather than being overridden with an empty string.

### 22g. Tuning

The panel is top-anchored, so `_h` alone extends it *downward* toward the screen edge. To grow it
upward, lower `_t` and raise `_h` by the same amount. To shift it without resizing, change `_t`
alone. `_x` is symmetric; see the ceiling in 22d.

### 22h. Page counter suppressed while the panel is up

`View_Scrollbar_Count` was gated on `Exp_View_ScrollFilter_HasFocus`, which also fires for the
sort buttons and the A-Z jump — so "Page 1 of 3" appeared alongside them. It is furniture that is
otherwise never on screen, and it does not belong in a selection dialog.

Shrinking the panel does not hide it. It renders through `Info_Viewline` at `top 10` / height 40,
right-aligned inside the same gutters group — **y960-1000**, i.e. dead centre of the panel's own
vertical band, hard against its right edge. It needed an explicit gate:

```xml
<param name="visible">[$EXP[Exp_View_ScrollFilter_HasFocus] + !$EXP[Exp_View_FurnitureDialog_IsVisible]]</param>
```

It still shows for genuine scrollbar focus (`60`, `64`/`65`/`66`, `611`), which is the case where
a page number is actually useful.

#### Left alone
`View_Hint_Text` ("press left/right to jump") is gated on `Exp_View_ScrollFilterHorz_HasFocus`,
which includes `19`/`600`/`601`, so it accompanies the A-Z strip. It sits at `centerbottom -50`,
below the panel. It was left as-is — it is guidance rather than stray furniture — but it is
outside the panel and can be suppressed the same way if it reads as clutter.

---

## 23. Wall Kaleido — new viewtype 521 with a sliding info panel

A second poster wall that shows Title / Plot / Ratings / Duration in a panel that slides out
of the focused poster after three seconds. **Wall Poster (520) is untouched** — Kaleido is an
additional view, not a replacement, and both appear in the viewtype picker.

### 23a. Why the panel lives inside the focusedlayout

The obvious build is an overlay group placed beside the container, the way `Info_Panel` is
used by the row views. That was rejected. An overlay would have to derive the focused cell's
screen position itself — 3 rows x 7 columns = **21 positional variants**, all of which would
have to be kept in sync with `view_poster_itemlayout_w/h` by hand — and it would draw *above*
the whole container, including the poster it is supposed to emerge from.

Declaring the panel inside the container's own `<focusedlayout>` solves three problems at once:

1. **Position.** The layout is already drawn at the focused cell's origin. The panel needs no
   row/column arithmetic at all, only a left/right direction choice.
2. **Hover delay.** `CGUIBaseContainer::ProcessItem` calls `ResetAnimation(ANIM_TYPE_FOCUS)`
   on the focused layout whenever the focused **item** changes. So an
   `<animation ...>Focus</animation>` carrying `delay="3000"` restarts its countdown on every
   d-pad move, for free. No `AlarmClock`, no window property, no script, no polling.
3. **Z-order.** Containers render the focused item **last**, so the panel covers its
   neighbours. Within the layout, controls render in document order, so declaring the panel
   *before* `Layout_Poster` puts the origin poster on top of it.

### 23b. "Dies into the poster"

`common/dialog.png` is drawn with `border="20"` and rounds all four corners. Squaring off only
the poster-side edge is not practical: for eight of the nine dialog themes the fill is a
blurred PNG, so an overlaid patch would sample a visibly different colour.

Instead the panel is drawn **40px wider than it is meant to look** (`kaleido_tuck`) and that
surplus runs *underneath* the poster. Its poster-side rounded corners are hidden by the
artwork; what remains visible is a straight cut emerging from behind the poster. The two
exposed corners stay rounded, matching the rest of the skin. No new texture was needed.

`kaleido_tuck` must stay **>= 20** (the dialog border, or the corner curve peeks out) and
**< 217.14** (`view_poster_item_w`, or the panel would emerge from the poster's far side).
40 sits in the middle of that range.

#### Cosmetic note, not a defect
`Layout_Poster` masks its artwork with `diffuse/poster_w217_h310.png`, which has rounded
corners of its own, and `Object_ItemBack` behind it is only `main_fg_12` — semi-transparent.
So a sliver of panel is visible through the poster's own corner curves on the tucked side.
It reads as depth (the panel genuinely *is* behind the poster) and was left alone.

### 23c. Geometry

All in `Includes_Constants.xml`, all derived from the existing poster grid. Coordinates are
relative to the artwork box that `List_Core` insets by `offset_x` / `offset_y`, in which the
poster runs x 0-217.14 and y 0-310.

| Constant | Value | Derivation |
|---|---|---|
| `kaleido_panel_w` | 771.42 | 3 x `view_poster_itemlayout_w` (257.14) — three column pitches |
| `kaleido_panel_h` | 310 | = `view_poster_item_h`, i.e. exactly poster height |
| `kaleido_tuck` | 40 | overlap hidden under the origin poster |
| `kaleido_ext_w` | 811.42 | 771.42 + 40 |
| `kaleido_right_x` | 177.14 | 217.14 - 40 |
| `kaleido_left_x` | -771.42 | -(3 x 257.14) |
| `kaleido_pad` | 40 | content inset, exposed side |
| `kaleido_pad_tucked` | 80 | content inset, tucked side (40 pad + 40 tuck) |

Three column pitches is what makes the far edge land *exactly* on the third poster's outer
edge rather than in a gutter. Verified across all seven columns:

| Column | Poster x | Panel x | |
|---|---|---|---|
| 0 | 80.00–297.14 | 297.14–1068.56 | right |
| 1 | 337.14–554.28 | 554.28–1325.70 | right |
| 2 | 594.28–811.42 | 811.42–1582.84 | right |
| 3 | 851.42–1068.56 | 1068.56–1839.98 | right |
| 4 | 1108.56–1325.70 | 337.14–1108.56 | left |
| 5 | 1365.70–1582.84 | 594.28–1365.70 | left |
| 6 | 1622.84–1839.98 | 851.42–1622.84 | left |

Column 3 — the worst case — ends at 1839.98, which is the grid's own right margin (section 21e:
artwork runs x80–1840). Nothing overhangs, on either side.

**These constants are a set.** If `view_poster_item_w`, `view_poster_itemlayout_w` or
`view_poster_item_h` ever move, all eight must be recomputed together. Kodi constants cannot do
arithmetic (see Conventions), so the sums live only in the comments.

### 23d. Direction

Columns 0–3 slide right, columns 4–6 slide left, chosen by `Container(id).Column(n)` in the
panel's `<visible>`. Both variants are always instantiated and are mutually exclusive.

`Container(id).Column(n)` is a stock boolean condition, documented specifically for use inside
item layouts. **It is used nowhere else in this skin**, so it is the one thing worth verifying
on hardware before trusting the rest. Two fallbacks, in order of preference:

1. Drop the id and use the bare `Container.Column(n)`.
2. Enumerate `Container(id).Position(n)`. With 7 across and 3 rows visible the cursor runs
   0–20, so column 0 is `Position(0) | Position(7) | Position(14)`, column 1 is
   `Position(1) | Position(8) | Position(15)`, and so on. Verbose, but depends only on
   `Container.Position`.

### 23e. The animations

```xml
<animation effect="fade"  start="0"   end="100" delay="3000" time="200">Focus</animation>
<animation effect="slide" start="±160,0" end="0,0" delay="3000" time="320" tween="quadratic" easing="out">Focus</animation>
```

`start` always points back toward the poster, so the panel emerges from behind it. The fade
finishes before the slide does, which is why the panel is invisible during the part of the
travel where it would otherwise be seen poking out from under the poster.

**`delay` and `time` are written as literals on purpose.** Kodi resolves `<constant>`
substitutions on element *values*, not on attributes — a constant name in `delay=""` would be
read as 0. Both animations must carry the **same** delay or the panel fades in before it moves.
To change the hover delay, change both numbers in `View_Kaleido_Panel`; it is the only place
they appear.

### 23f. CONTROL TYPE RESTRICTION — read before editing the panel

Kodi only accepts `group` / `image` / `label` / `textbox` / `progress` / `multiimage` inside a
container layout. **No `grouplist` and no `button`.** `Includes_Layouts.xml` contains zero
grouplists, which is the same constraint showing up in the original skin.

This rules out reusing `Info_Panel` and `Info_RatingsLine` wholesale: `Info_RatingsLine` is a
`grouplist` of `button` controls (`Info_RatingsLine_Object` emits a button so the label can be
`width auto`). Dragging either into the layout will silently drop controls at load.

Consequences:

* Title and Plot are plain textboxes fed by `$VAR[Label_Title]` / `$VAR[Label_Plot]` — the same
  variables the row views use, and both read a bare `ListItem.`, which is exactly right inside
  an item layout.
* **Title and Plot are fixed-height boxes, not auto-height.** Without a grouplist nothing can
  reflow below them, so the title must not be allowed to change anything else's position.
  Title is 90px (two lines of `font_head_bold`), plot 112px (`font_main_plot` is quantised to
  the skin's 40px line grid, so 112 clips the third line cleanly rather than half-drawing it).
* The ratings row cannot auto-flow, so it is **three fixed slots at a 150px pitch**, fed by
  cascading variables that left-pack the available ratings — see below.

### 23g. Ratings, without a grouplist

Four candidates in priority order: Kodi (`ListItem.Rating`), TMDb, IMDb, Rotten Tomatoes
critic. Presence of each is an expression; `Var_Kaleido_RatingN_Icon` / `_Label` then answer
"what is the Nth *present* candidate", so the slots pack left with no gaps:

* slot 1 = first present;
* slot 2 = candidate *k* where **exactly one** candidate before *k* is present;
* slot 3 = candidate *k* where **exactly two** are.

The `Exp_Kaleido_Rating_One12` / `_One123` / `_Two123` expressions are those "exactly N of the
first M" tests written out longhand. Slot visibility is the OR of that slot's own value
conditions, so visibility and content can never disagree.

Icons resolve through the existing `$VAR[Info_RatingsLine_Object_Style]`, so the panel honours
the current **Colour ratings** setting (`Furniture.EnableColourRatings`) with no setting of its
own. The icon box is a fixed 44x32 with `aspectratio keep`; `Info_RatingsLine_Object` gives
IMDb `icon_w 52` because it can auto-flow, so here the wordmark scales down inside the box
instead.

**Note the mixed data sources.** `ListItem.Rating` is item-local; the other three are
`Window(Home).Property(TMDbHelper.ListItem.*)` values that describe whatever is *currently*
focused. That is correct here only because the panel is drawn for the focused item and nothing
else. **Do not reuse these variables in an itemlayout** — every unfocused poster would show the
focused item's ratings.

### 23h. Duration

`Var_Kaleido_Duration`, bottom-right. Same source order and the same `31074` "hr" / `31073`
"mins" strings as the Duration block of `Info_Line`, so it reads identically to the row views.
Resolves to empty for tvshows, seasons and folders, which is intended — the label just renders
blank rather than showing a placeholder.

### 23i. Panel colour — dialog background, not window background

The panel resolves through `Dialog_Background_Blur`, the same include every real dialog window
uses, so it follows `Skin.String(Background.DialogImage)` — the user's **Dialog Colour**
setting — with no setting of its own. Section 22 already set this precedent for the bottom
furniture strip.

The window background was considered first and rejected. Three reasons:

1. Both settings are **images**, not colours (`Background.Image` is a full-screen blurred JPG,
   `Background.DialogImage` a dialog-sized blurred PNG). The wall grid already sits on the
   window background, so a panel filled with the same image would be near-invisible against
   its own backdrop.
2. `Dialog_Background_Blur` is the skin's ready-made panel surface: rounded corners via
   `common/dialog.png` at `border="20"`, outline via `$VAR[Color_DialogBorder]`, and
   `Glass.DarkPanels` / `Glass.EnableBorders` handling. It is built purely from `image`
   controls, which is *why* it is legal inside a container layout where `Info_Panel` is not.
3. Dialog backgrounds have matched `dialog_fg_*` foreground colours tuned for contrast,
   including under the Light-dialogs colour theme. The window background has no such pairing,
   so text legibility would have been guesswork.

Side benefit: under the **Adaptive** dialog theme the fill comes from blurred artwork, so the
panel tints to whatever poster is being hovered.

`diffuse/dialog_w1120_h400.png` (2.8:1) is passed as the diffuse — closest available aspect to
the panel's 811x310 (2.6:1). Per section 22f it is consumed **only** by the Adaptive branch;
the eight fixed themes ignore it. `transparency` is deliberately not passed, for the same
reason given in 22f.

### 23j. Navigation and select — deliberately no work done

The requirement was that the panel must not trap focus: pressing select should behave exactly
as it does on any other poster, and the d-pad should keep navigating without a Back press
first.

**This required no code.** The panel is decoration drawn inside the container's own layout. It
is not a window, not a dialog, not a focusable control, and it never receives focus — the
container keeps it throughout. So select, d-pad, context menu, `onleft` 600, `onright` 611 and
`Action_View_Movement_OnBack` all remain stock, inherited unchanged from `View_Wall_Include`.

Resist any temptation to "improve" this with `ActivateWindow` or a modal: that is precisely
what would introduce the Back-press the requirement rules out. (Compare section 8b, where
AF3's `<onleft>ActivateWindow(1171)</onleft>` was deliberately not copied.)

### 23k. Layout param relay — do not add defaults

`Layout_Kaleido` relays every param `List_Core` passes through to `Layout_Poster` **by name,
without declaring defaults for them**. Unresolved `$PARAM[...]` forwards are dropped by Kodi's
`ResolveParametersForNode`, so `Layout_Poster`'s own defaults apply — the same mechanism that
already makes `icon` (`$VAR[Image_Poster]`) work through `List_Poster_Row` today.

**Declaring empty defaults here instead would break the layout**, exactly as it broke the wall
in section 21e: a passed-but-empty param wins over the callee's declared default.

The two params `Layout_Kaleido` *does* declare — `focusedlayout` (false) and `selected` (false)
— are the panel's gate, and neither is forwarded to `Layout_Poster` in a way that changes it.

#### Why the gate is `focusedlayout + selected`
`List_Core` emits the itemlayout include three times:

| Copy | `focusedlayout` | `selected` | Shown when |
|---|---|---|---|
| plain `<itemlayout>` | unset → default false | false | item not focused |
| `<focusedlayout>` copy A | true | false | container does **not** have focus |
| `<focusedlayout>` copy B | true | true | container **has** focus |

Only copy B should carry a panel. A second `<visible>Control.HasFocus(id)</visible>` on the
panel group is belt-and-braces for the same thing.

### 23l. Registration — the five places a new view has to be wired

| File | Change |
|---|---|
| `Includes_Views_Wall.xml` | `View_521_Kaleido_Wall`, via the shared `View_Wall_Include` |
| `Includes_Views_Wall_Kaleido.xml` | **new file** — panel, rating variables, direction chooser |
| `Includes.xml` | loads the new file, immediately after `Includes_Views_Wall.xml` |
| `Includes_Views.xml` | `521` added to `<views>`, and to `View_Row_Items_StandardViews` |
| `Includes_Views_Fallbacks.xml` | `Exp_View_521` / `_Include`, both False |
| `Includes_Expressions.xml` | `521` added to `Exp_View_WallMode` |
| `Includes_Actions.xml` | `521` added to `Action_View_ContentID` / `_ContainerID` |
| `Includes_Lists.xml` | `List_Kaleido_Row` |
| `Includes_Layouts.xml` | `Layout_Kaleido` |
| `shortcuts/skinviewtypes.json` | viewtypes map, icons map, and 13 `rules` arrays |
| `language/.../strings.po` | `31605` "Wall Kaleido" |
| `extras/viewtypes/wall-kaleido.jpg` | picker thumbnail |

Adding `521` to `Exp_View_WallMode` is what gives Kaleido the vertical scrollbar
(`Exp_View_Scrollbar_Maxi_V`) and the suppressed wall header (section 21c) for free.

`Exp_View_521` ships as `False` in the fallbacks, matching every other wall view: the real
expressions are generated from `skinviewtypes.json` by the `buildviews` generator. **Kaleido
will not appear in the viewtype picker until a viewtype rebuild has run**, which is the same
condition Wall Poster is already under.

In `skinviewtypes.json`, `521` was inserted immediately after `520` in all 13 `rules` arrays
that offer Wall Poster, so Kaleido is available for exactly the same content types and sits
next to Wall Poster in the picker.

#### On the id
`521` is unused everywhere in the skin (verified by sweep). The `IDs` file nominally reserves
`5X1` for "Wall View Control" — a convention `561` (Small Banner Wall) already breaks, and no
`5X1` control exists for any wall view.

#### Placeholder artwork
`extras/viewtypes/wall-kaleido.jpg` is currently a **copy of `wall-poster.jpg`**. It is
functional but does not show the panel. Replace it with a real screenshot when convenient.

### 23m. Left deliberately in place

* **Wall Poster (520)** — unchanged, still offered, still the default poster wall.
* `List_Poster_Row` and `Layout_Poster` — untouched. `List_Kaleido_Row` is a sibling that
  differs only in `itemlayout_include`, deliberately reusing the identical geometry constants
  so the two cannot drift apart (the panel's placement is derived from them).
* `preloaditems` — still the pre-existing 2 described in section 21g. The panel adds per-item
  controls to the focusedlayout only, which Kodi clones on demand for the focused item, so the
  preload cost is unchanged.

### 23n. Version bumped to 1.0.5

`addon.xml`. The Settings version line reads `System.AddonVersion(skin.arctic.vibe)`
(section 11), so it follows automatically.

---

## 24. New setting: disable fanart for wall viewtypes

**Settings → Skin Settings → Behaviour → Background → "Disable fanart for wall viewtypes"**
(`Skin.HasSetting(Background.DisableWallFanart)`, string `#31606`, id `5025`).

Off by default — unset is the stock behaviour, fanart behind the wall grid. When on, the eleven
wall viewtypes render against the plain window background instead. Every other window, and every
non-wall viewtype, is untouched.

The motivation is section 21: the wall grid is now full-bleed, three rows of artwork from y40 to
y1050. Fanart behind it competes with the posters rather than framing them.

### 24a. Why this is a visibility gate and not a cover layer

The first design painted an opaque layer over the background. It was rejected: the textures would
still be fetched, decoded and held in VRAM on every focus change, with the cost paid on exactly
the low-power devices where a "disable" setting is worth having. Masking hides a symptom.

The skin already had the right idiom. `Background_NotVideo` drops the background artwork during
background video playback using `<visible>` plus `Hidden` / `Visible` fade animations — not a
conditional fade to zero opacity — so Kodi releases the control instead of drawing it invisibly.
It is pulled into `Background_FlixArt`, `Background_Fanart`, `Background_Blur` and
`Background_Image`: the same controls this setting needs to reach.

So the wall gate is a second `<visible>` on those same controls. Kodi ANDs multiple `<visible>`
tags, so the two conditions compose, and **the crossfade is inherited from `Background_NotVideo`
at no cost** — no new animation was needed on the two artwork controls.

### 24b. Why the obvious one-line edit was wrong

`Exp_BackgroundArtwork_IsHidden` is the skin's existing "hide the fanart" switch, and adding
`[Skin.HasSetting(Background.DisableWallFanart) + $EXP[Exp_View_WallMode]]` to it is a one-line
change. **It does not work, for two independent reasons.**

**1. It only reaches the sharp panel.** That expression gates `Background_FlixArt` and
`Background_Fanart`. It does *not* gate `Background_Blur_Quadrants`, the blurred fanart surround,
which has no hide condition of any kind. With `TMDbHelper.EnableBlur` on, the blur survives every
"hide artwork" route in the skin.

The two mask layers that draw above it are not opaque enough to cover for that. Measured from
`media/Textures.xbt` (alpha channel, composited as `1-(1-a1)(1-a2)`):

| | x=40 | x=800 | x=1500 | x=1900 |
|---|---|---|---|---|
| **y=40** | 65% | 60% | 62% | 62% |
| **y=400** | 80% | 69% | 76% | 77% |
| **y=800** | 100% | 98% | 80% | 77% |
| **y=1060** | 100% | 100% | 98% | 97% |

`combined_flixart.png` is opaque bottom-left and alpha 3–5 top-right; `combined_overlay.png` peaks
at 195/255 and is the complement. Around 60% coverage across the top band means blurred fanart
reads through at roughly 35–40% behind the wall grid's top row. That is fanart dimmed, not
disabled.

**2. It has side effects outside the background stack.** `Exp_BackgroundArtwork_IsHidden` feeds
`Exp_BackgroundArtwork_IsOverlay`, which is consumed elsewhere. A term added for wall views would
switch the overlay wash on as a by-product.

Hence a separate expression, `Exp_BackgroundArtwork_WallIsDisabled`, in `Includes_Expressions.xml`
next to its neighbours. **Do not fold it into `Exp_BackgroundArtwork_IsHidden` later.**

### 24c. The load-time / runtime trap

Which viewtype is showing is a **runtime** condition. Kodi evaluates `<include condition="...">`
when the window is *loaded*, so the existing pattern in `Background_Blur` —

```xml
<include content="Background_Blur_Quadrants" condition="Skin.HasSetting(TMDbHelper.EnableBlur)">
```

— cannot be extended with a view test. That is why every gate added here is a `<visible>` on a
control, matching how `Exp_View_HasHeader` is consumed in `Includes_Views.xml`. The pre-existing
`Skin.HasSetting` include conditions are left exactly as they were.

### 24d. Why a replacement base layer was needed

Hiding all three artwork layers leaves nothing behind them. The only always-on layer above that
point is `combined_flixart.png`, alpha 3–5 in the top-right — so the wall would have fallen
through to black up there.

`Background_Wall_NoFanart` (new include) supplies the base. It draws `Background_Image`, which
resolves `Image_SimpleBackground` — the user's "Customise Window Background" image, else
`purple_blur.jpg` — over a solid `ColorBackground` fill.

* **The solid fill is a guarantee, not decoration.** `Background.Image` accepts any path; a user
  PNG with alpha would otherwise let the view show through.
* **It is called from `Background_Main_Standard`, not from `Background_Blur`.** `Background_Blur`
  is skipped entirely when `Background.ArtworkStyle` is `Simple`, which is precisely the style
  with no other background layer — a base added there would be missing where it is most needed.
  At the `Background_Main_Standard` level it covers every artwork style and both blur states.
* **It is the first child**, so `Background_Main_Overlay`'s masks still draw on top. The theme
  gradient is preserved and the result matches the skin's ordinary "no fanart available" look
  rather than a flat fill.

`Background_Main_Plain` was deliberately left alone: `Exp_PlainBackgroundWindows` lists only
settings-type windows, none of which can host a wall view.

### 24e. Both blur modes are covered

| `TMDbHelper.EnableBlur` | Surround | Sharp panel |
|---|---|---|
| On | `Background_Blur_Quadrants` — blurred fanart, **gated** | `Background_FlixArt`, **gated** |
| Off | `Background_Image` — static, not fanart, left alone | `Background_Fanart`, **gated** |

With blur off, `Includes_Images_Background_FakeBlur.xml` redefines `Image_Foreground` to the raw
ListItem fanart, so `Background_Fanart` still needs the gate even though the surround does not.
`Background_FlixArt` is also the control used by the `Simple` artwork style, so one gate on it
covers both of its call sites.

### 24f. Total scope

| File | Change |
|---|---|
| `Includes_Expressions.xml` | new `Exp_BackgroundArtwork_WallIsDisabled` |
| `Includes_Background.xml` | 3 `<visible>` gates; new `Background_Wall_NoFanart`; called from `Background_Main_Standard` |
| `Includes_SkinSettings.xml` | new radiobutton, id `025`, slevel 2 |
| `language/*/strings.po` | `#31606` added to all 13 |

`Background_FlixArt`, `Background_Fanart` and `Background_Blur_Quadrants` are each referenced only
within `Includes_Background.xml`, so the gates cannot leak into another consumer.

`Background_Blur_Quadrants` needed its own `Hidden` / `Visible` fade animations (400ms, matching
`Background_NotVideo`) because, unlike the two artwork controls, it does not pull in
`Background_NotVideo` and so had none to inherit. Without them the switch into a wall view
hard-cut.

**Do not add `<param>` declarations to `Background_Blur_Quadrants`.** It has no `<definition>`
block, so its `$PARAM[flixart_size_w]` / `_h` values resolve from the calling include
(`Background_Blur`). Declaring them locally would shadow the caller with empty values — the same
class of failure recorded in section 21e.

### 24g. id and polarity choices

`025`: ids `001`–`018` and `020`–`024` are taken in `SkinSettings_Items_Behaviour`. `019` remains
the deliberately-skipped gap from the removed Spotlight trailer setting (see the note on id `024`)
so it is not read as belonging to the Trailers group. baseid is `5`, so the control is `5025`.

`slevel 2` matches the "Background" section label. Anything lower would leave the entry visible at
settings level 0/1 with its own header hidden, orphaning it under the Playback group.

Polarity follows section 20 rather than the neighbouring entries: a `Disable`-named setting with a
*positive* `<selected>`, so the setting name, the label and the radio state all move in the same
direction.

### 24h. What this does not do

It stops the **skin** from loading fanart on wall views. It does not stop **TMDb Helper**: the
monitor still populates `TMDbHelper.ListItem.BlurImage` and still generates blurred image files as
the grid is scrolled, because that work is driven by the container, not by what the skin renders.
Eliminating it means turning off `TMDbHelper.EnableBlur`, which is a different setting with
skin-wide effects.

It also does not affect background video. `Background_Image` carries `Background_NotVideo`, and
`Background_Video` sits outside the gated group, so video backgrounds behave exactly as before.

### 24i. Pre-existing gap fixed in passing

String `#31605` ("Wall Kaleido", section 23) had only been added to `en_gb`; the other 12 language
files lacked it. Kodi falls back to English for a missing string, so nothing was broken, but it was
inconsistent with `#31602`–`#31604`, which are in all 13. `#31605` has been backfilled to the
remaining 12 alongside `#31606`.

### 24j. Version bumped to 1.0.6

`addon.xml` `1.0.5` → `1.0.6`. Same reasoning as sections 19, 20 and 23n. The Settings version line
reads `System.AddonVersion(skin.arctic.vibe)` (section 11), so it follows automatically.

---

## 25. Aspect ratio tag on the info line

**Key files:** `1080i/Includes_Info.xml`, `1080i/Includes_OSD.xml`, `1080i/Includes_Items.xml`,
`1080i/Includes_Labels.xml`, `language/resource.language.en_gb/strings.po`

A new pill on the info line, immediately after the resolution / `WEB` pill, showing the video
aspect ratio as e.g. `2.40:1`. Appears in every viewtype that has an info panel (Wall viewtypes
have none), in the information dialog, and in the OSD. Controlled by a new entry in
*Skin Settings → Layout → Customise style → Video Info - Widgets*.

### Where the data comes from — and where it does not

The pill reads Kodi's `ListItem.VideoAspect` (`VideoPlayer.VideoAspect` in the OSD). That value
comes from `streamdetails` in `MyVideos.db`, which Kodi populates by probing the file itself during
a library scan when *Settings → Media → Videos → Extract video information from files* is enabled,
or on first playback of each file. **No NFO is required.**

**TMDb Helper cannot supply this and never will.** The full TMDbHelper Detailed Item property list
contains no aspect ratio field, because the TMDb API does not carry one. The
`TMDbHelper.ListItem.base_*` properties are not an alternative source either — they mirror the
underlying Kodi listitem, so if `ListItem.VideoAspect` is empty for an addon item,
`base_videoaspect` would be empty too.

Consequence: **the pill renders on library items and does not render on addon / Web sources.**
This is by design and reads coherently, because the pill next to it already says `WEB` in exactly
that case. Getting aspect for a stream would require ffprobe on the resolved URL (a scrape/debrid
resolve per item, wholly impractical during navigation) or an external IMDb/Wikidata scrape with
its own cache. Both were rejected.

### The two branches — and the bug this caused in the first attempt

`Info_Line_VideoQuality` is instantiated **twice** inside `Info_Line`, and exactly one of the two
is ever visible:

| Branch | Visible when | Reads |
|---|---|---|
| 1 | `Integer.IsEqual(Window.Property(TMDBHelper.WidgetContainer),System.CurrentControlID)` | `$PARAM[container]ListItem.*` |
| 2 | the negation of the above | `Window(Home).Property(TMDbHelper.ListItem.base_*)` |

**Branch 2 is the one that renders in ordinary library views and in the information dialog.** In
those contexts `TMDBHelper.WidgetContainer` is empty — `Includes_DialogInfo.xml` explicitly clears
it on load, and `Includes_Views.xml` only shows that info panel when the property *is* empty — so
`Integer.IsEqual(<empty>,System.CurrentControlID)` evaluates false and branch 2 wins. Branch 1 is
for the widget-focused case on Home and in combined views.

The first implementation passed `base_videoaspect` on branch 2, on the assumption that the `base_*`
set mirrors the listitem the way `base_videoresolution` does. **It does not exist.** TMDbHelper
does not expose it. The symptom was diagnostic: the resolution pill appeared everywhere (because
`base_videoresolution` *is* populated) while the aspect pill appeared only in the OSD (which reads
`VideoPlayer.VideoAspect` directly and bypasses both branches entirely).

The fix gives branch 2 a fallback chain — `base_videoaspect` first, so a future TMDbHelper that
adds it is picked up automatically, then the real `ListItem.VideoAspect`.

**The fallback is guarded on `String.IsEmpty(Window.Property(TMDBHelper.WidgetContainer))`, and the
guard is not optional.** That condition is precisely the case where no other container is
registered, so `ListItem` is guaranteed to be the item the panel is describing. Without it, a
combined view — which registers a *different* container id — could draw this pill from one list
while the resolution and HDR pills beside it come from another. Two pills describing two different
films, side by side, is worse than one pill missing.

### Why the params were split

The pill originally took one param, an infolabel name, used for both the label and the
`String.IsEmpty()` visibility test. The fallback chain broke that: Kodi resolves variables at
runtime to **strings**, not to infolabel references, so `$INFO[$VAR[...]]` cannot work and the
label had to become a variable (`Label_Info_VideoAspect_Base`) while the visible condition stayed
an explicit boolean. `Info_Line_VideoQuality` therefore takes `videoaspect_label` and
`videoaspect_visible` as separate params.

**These two must be kept in sync.** The variable's `<value>` conditions and the call site's
visible expression encode the same fallback logic in two places; changing one without the other
produces either an invisible pill or an empty one.

| Call site | Label | Visible |
|---|---|---|
| `Info_Line` branch 1 | `$INFO[$PARAM[container]ListItem.VideoAspect,,:1]` | `!String.IsEmpty($PARAM[container]ListItem.VideoAspect)` |
| `Info_Line` branch 2 | `$VAR[Label_Info_VideoAspect_Base]` | base_ present **or** [WidgetContainer empty **and** ListItem present] |
| `OSD_Info_Line` | `$INFO[VideoPlayer.VideoAspect,,:1]` | `!String.IsEmpty(VideoPlayer.VideoAspect)` |

### Implementation: one control, not eleven

`Info_Line_VideoQuality_Resolution`, `_HDRType` and `Info_Line_AudioChannels` all work by
instantiating **one control per possible value** with a `<visible>` condition on each. That pattern
was deliberately **not** copied here.

Kodi normalises `VideoAspect` to a fixed set of ~11 ratio strings (1.33, 1.37, 1.66, 1.78, 1.85,
2.00, 2.20, 2.35, 2.40, 2.55, 2.76 — see `xbmc/utils/StreamDetails.cpp`,
`VideoAspectToAspectDescription`). Bucketing would mean 11 controls × 2 branches × every
`Info_Panel` instance Kodi loads in a window, each carrying a compound boolean evaluated per frame.
A single `Info_Line_VideoQuality_Object` prints the value directly instead. The `,,:1` suffix
formatting matches existing usage in `Info_CodecsLine`.

Net cost: **two controls per info panel**, reading an infolabel that the resolution / HDR / audio
pills beside it already read on the same focus change. No disk, no DB, no network at render time.

### Setting

Follows the existing **inverted** convention of this dialog (`Infoline.DisableSource`,
`Infoline.DisableHDR`, …), so `Infoline.DisableAspect` **unset means the tag is ON**, matching the
"everything on by default" behaviour of every other entry there. Three places enumerate the tags
explicitly and had to be kept in sync:

* `Items_Settings_InfolineAdditionalTags` — the radiobutton (id `8007`) **and** the "None" button,
  which sets every `Disable` flag and would otherwise leave this one on.
* `Label_Setting_AdditionalTags` — both the `NONE` test (an AND of every flag) and the summary
  string, plus a new `Label_Setting_AdditionalTags_Aspect` variable.

**Adding a future tag means touching all three.** Missing the `NONE` condition is the easy one to
overlook: the label would never read "None" again.

New string `#31607` "Aspect ratio".

### Accuracy caveats (not fixable in the skin)

1. The value is a **bucket, not a measurement**. A 2.39:1 film reports `2.40`.
2. It is derived from the **stored frame dimensions**. A scope film encoded into a 1920×1080 frame
   with baked-in black bars reports `1.78`. Most modern rips are cropped, so this is uncommon, but
   there is no infolabel that would reveal the true framing.
3. If the pill appears in the OSD but nowhere else, that is now a wiring bug, not a data problem —
   the OSD reads the live decoder while everything else reads `streamdetails`. If it appears in
   *neither*, `streamdetails` were never extracted: enable the extraction setting and rescan, or
   play each file once. That scan is slower over SMB/NFS — a one-time library cost, not a
   navigation cost.

---

## 26. Match OSD to content — keep the OSD out of the letterbox bars

**Key files:** `1080i/Includes_Animations.xml`, `1080i/Includes_Expressions.xml`,
`1080i/Includes_OSD.xml`, `1080i/Includes_Items.xml`, plus a one-line include in 14 OSD windows.

For constant-image-height (CIH) projector setups. When a projector zooms scope content to fill a
2.35/2.40 screen, the GUI zooms with it, so OSD furniture sitting in the letterbox bands is thrown
off the top and bottom of the screen. This setting pulls the OSD inward by the bar depth, **sized
automatically from the aspect ratio of whatever is playing.**

Off by default — most people are not using a zoom-capable projector.
*Skin Settings → Layout → Customise style → Video OSD → Overlay → Match OSD to content.*
Positive-sense setting (`OSD.MatchToContent`), so unset means off.

### Offsets

Bar depth in a 1080-line frame is `(1080 - 1920/aspect) / 2`; the dim layer's zoom is the active
image height as a fraction of 1080, `(1920/aspect) / 1080`.

| VideoAspect | Active height | Slide offset | Dim zoom |
|---|---|---|---|
| 1.85 | 1039 | 21 | 96.2% |
| 2.00 | 960 | 60 | 88.9% |
| 2.20 | 873 | 104 | 80.8% |
| 2.35 | 817 | 132 | 75.7% |
| 2.40 | 800 | 140 | 74.1% |
| 2.55 | 753 | 164 | 69.7% |
| 2.76 | 696 | 192 | 64.4% |

**1.78 and below are deliberately absent.** 1.78 has zero bar depth, and 1.66 and narrower are
pillarboxed — the image already fills the frame height, so there is nothing to move away from.

Kodi normalises `VideoPlayer.VideoAspect` to this fixed set of strings, so exact `String.IsEqual`
matching is safe and no range logic is needed.

### The mutual-exclusivity requirement

**Kodi applies conditional animations cumulatively.** Every animation in these includes is keyed to
one exact `VideoAspect` string, so at most one can ever be true. If these conditions were ever
widened to ranges and two could match at once, the slide offsets would **add** and the dim zooms
would **multiply**. Keep them exact.

### Mechanism

Kodi cannot make `<top>` conditional, so this uses conditional slide animations, which the skin
already relies on heavily (`Animation_OSD_Seekbar_Slide`, `Animation_OSD_MusicArtwork_Slide`).
`Animation_OSD_MatchContent_Slide` is the parameterised primitive; `_Bottom` and `_Top` are the
bucket tables that instantiate it.

**Direction matters and one include cannot serve both.** Bottom-anchored furniture must move up
(negative offset) and top-anchored furniture must move down (positive); applying the wrong include
pushes a panel further into the bar it was supposed to escape.

Gated by `Exp_OSD_MatchContent`, which ANDs the setting with `Player.HasVideo` so the music
visualisation screen — which has no letterbox bars to escape — is never affected.

### Applied per window, deliberately

The include is attached to the outermost group of each OSD window rather than to a shared geometry
include such as `OSD_Info_Dimensions`.

**This is the important design decision.** `OSD_Info_Dimensions` and `OSD_List_Dimensions` are
frequently **nested** — in `Custom_1140` and `Custom_1141` the list carrying `OSD_List_Dimensions`
sits inside the group carrying `OSD_Info_Dimensions`. Putting the animation in a shared geometry
include would translate nested controls **twice**, cumulatively, and the bug would appear only in
some windows. Attaching it once per window makes the translation unambiguous.

Because the whole window root moves as a rigid body, **internal spacing is untouched and collisions
are impossible by construction.** The scroll-down panels (cast, playlist, stream selectors) keep
their existing relationships to each other.

### Direction is decided by the content's pinned edge, not the outermost `<top>`

Two windows were classified wrongly on the first pass, both because the outermost group's `<top>`
was taken as the anchor.

**`Custom_1153_OSD_VideoInfoOverlayTop`** — named "Top", and its outer group opens with
`<top>view_pad</top>`, but that only establishes a bounding box running to the bottom of the
screen. What is actually pinned is the content's *bottom* edge: the inner group is
`<bottom>240</bottom>`, and `OSD_Progress_Details_Extended` is a grouplist with `<bottom>40</bottom>`
and `align=bottom`. The panel's baseline therefore sits at y=800 and grows upward, 140px clear of
the VideoOSD button row at 940..1060.

Insetting it downward pushed the baseline to 940 while the button row moved up to 800 — a 140px
overlap, which is the collision seen when hovering the Info button in the player. It takes
`_Bottom`.

```
_Top    (wrong): content bottom 940, row 800..920  -> OVERLAP 140px
_Bottom (right): content bottom 660, row 800..920  -> gap 140px
```

**When adding this to a window, resolve the content's actual pixel position.** A group carrying
both `<top>` and `<bottom>` is a stretched box, and an outer `<top>` says nothing about which edge
the content is pinned to. The remaining `_Top` attachments were re-checked for any `bottom`,
`centerbottom` or `align=bottom` in their subtrees; both are clean.

### Custom_1143 is a mixed-anchor window — the exception to the rule below

The original audit assumed each OSD window is anchored uniformly, top **or** bottom.
`Custom_1143_OSD_NextOverlay` breaks that: the up-next card is anchored to the top
(`top=view_pad`) while directly below it sits an `OSD_CustomDialog_GroupList` at `centerbottom 80`
— a replica of the VideoOSD button row that exists so focus can move between the two windows.

With a single `_Top` include on the window root, that replica moved **down** by the inset while the
real VideoOSD row moved **up** by it, separating the two rows by twice the offset. Both were drawn,
which is what surfaced as a duplicate skip-chapter button on 1.85:1 content.

The two children are therefore insetted independently: `_Top` on the card, `_Bottom` on the button
row. The window root carries no include at all.

**Before adding this to another window, check the window for mixed anchoring first.** All fifteen
attachment points were re-audited after this was found; `Custom_1143` is the only one. The trap is
that a replica button row can look like incidental furniture, and that the bug only shows on
content whose inset is non-zero.

| Direction | Windows |
|---|---|
| Bottom | `VideoOSD.xml`, `DialogSeekBar.xml`, `VideoOSDBookmarks.xml`, `DialogPVRChannelsOSD.xml`, `Custom_1140`, `Custom_1141`, `Custom_1145`, `Custom_1146`, `Custom_1147`, `Custom_1148`, `Custom_1152`, `Custom_1153`, `Custom_1143` (button row only) |
| Top | `VideoFullScreen.xml` (codec overlay grouplist), `Custom_1143` (up-next card only) |

Music windows (`Custom_1142`, `Custom_1151`, `MusicVisualisation.xml`) are **untouched**.

### The full-frame dim layer — the trap that made this necessary

`OSD_Background_Dim` (included by `VideoFullScreen.xml`) has two layers. The second is
`background/combined_flixart.png` tinted at `panel_bg_70` with **no top, bottom or height set**, so
it covers the entire 1920×1080 frame. It becomes visible whenever any OSD sub-window is open:
playlist, cast, info panel, stream selectors, up-next.

On a CIH setup that **paints the letterbox bars grey**. Beyond looking wrong, it can defeat a
projector's automatic black-bar detection and cause it to re-zoom to 16:9 mid-film. Moving the OSD
without fixing this would have left the more disruptive half of the problem in place.

A slide cannot fix it, because the layer needs to **shrink**, not move. The wrapper group therefore
carries a **zoom** animation (`Animation_OSD_MatchContent_Dim`) instead, scaled about the frame
centre. Distortion is irrelevant on a flat tint and a vertical gradient, and centre-scaling also
lands the bottom-anchored gradient layer (`bottom 0`, `height 240`) correctly — so one animation
handles both layers.

The condition is additionally gated with `Window.IsActive(VideoFullScreen.xml)` because
`MusicVisualisation.xml` includes the same block and must not be affected.

### The teardown artefact — two separate causes

Stopping playback originally showed the OSD sliding back down **three times in succession**, and
after the first fix still showed it drawn once at the un-inset position.

**Cause one: the tween.** Five windows draw OSD furniture — `DialogSeekBar`, `VideoOSD`,
`Custom_1152`, `Custom_1153` and `VideoFullScreen` — and each fades out on its own schedule when
the player tears down. Each carries its own copy of the slide, so a reversible 200ms tween played
back *during* those fades and read as three separate redraws. Fixed with `time="0"`.

**Cause two: live player conditions.** That left one redraw, because every condition the
animations tested evaporated the moment playback stopped while the windows were still visible:
`Player.HasVideo` in the gate, `String.IsEqual(VideoPlayer.VideoAspect,...)` in each bucket, and
`Window.IsActive(VideoFullScreen.xml)` on the dim layer. The controls snapped to rest and were then
drawn there for the length of the fades.

The aspect is now **latched** into `Window(Home).Property(SkinMod.OSDAspect)` by an `<onload>` in
`VideoFullScreen.xml` (playback start), `VideoOSD.xml` and `DialogSeekBar.xml` (every time the OSD
appears). A window property survives teardown, so the inset holds until the windows are gone. Three
write points because the stream may not have reported its aspect yet at `VideoFullScreen` onload —
the property then lands empty and the first OSD open corrects it.

`Exp_OSD_MatchContent` consequently contains **no live player infolabel at all**. `Player.HasVideo`
was replaced by `!Window.IsVisible(MusicVisualisation.xml)`, which does the same job — keeping the
inset off the shared `DialogSeekBar` during music — without dying at stop.

The property is deliberately never cleared. A stale value is only ever read while an OSD window is
on screen, and the music guard covers the one case where it could leak.

**Anything added to these conditions later must also survive teardown.**

### Why the transitions are instant (`time="0"`)

The first working build used a 200ms tween. On stopping playback the OSD was seen sliding back
down **three times in succession**.

Cause: five separate windows draw OSD furniture — `DialogSeekBar`, `VideoOSD`, `Custom_1152`,
`Custom_1153` and `VideoFullScreen` — and each fades out on its own schedule when the player tears
down. Each carries its own copy of the slide, so a reversible tween plays back **during** those
fades, and the eye reads the same furniture un-sliding at three different moments as three
redraws.

Setting `time="0"` puts every control at its rest position before the fades begin. The residual
triple fade on stop is the stock close sequence of those stacked windows and is unrelated to this
feature — it is visible on content with no inset at all (1.78 and below), and with
`OSD.MatchToContent` switched off.

**Do not add a tween back.** The inset only ever changes at playback start or stop, when the OSD
is not on screen, so an animated transition can never be seen *except* as this artefact.

### Known limits

* **A file with baked-in black bars reports 1.78**, so the skin will not inset while the projector
  zooms anyway. Nothing in Kodi exposes the true framing of such a file. Rips cropped to the
  active image — the normal case — work correctly.
* **Subtitles are not skin-controlled.** Kodi places them itself; adjust subtitle position in
  Kodi's own settings separately.
* **Test the projector.** Play a scope film and open the cast list. If the projector re-zooms even
  with this setting on, its detection is reacting to something else being drawn, and the fix is on
  the projector side (a trigger or a manual lens memory), not in the skin.
* If the projector uses a **fixed scope lens memory** rather than zooming per content ratio, the
  per-content offsets above will slightly overshoot or undershoot on ratios that are not the
  screen's own. In that case, set every bucket in `_Bottom` / `_Top` to the same value.

New string `#31608` "Match OSD to content".

---

## 27. OSD flicker on stop

**Key files:** `1080i/Includes_Animations.xml`, `1080i/VideoOSD.xml`,
`1080i/Custom_1152_OSD_VideoInfoOverlay.xml`

Stopping playback produced repeated full-screen flashes. Two independent causes, found by
comparing debug logs.

### Cause one: display refresh rate switching (not a skin issue)

With a 144Hz desktop and 24.000fps content, Kodi's whitelist found no exact 24Hz and no double-rate
48Hz match and fell back to a 3:2 pulldown rate of 60Hz, switching the display on every play and
stop:

```
38.547  SetFullScreen: 1920x1080, refresh 144.000000
40.819  OnDisplayLost:  notify display lost event
40.820  OnDisplayReset: notify display reset event
```

A 2.3-second monitor renegotiation, entirely outside the skin. Resolved by the user setting
*Settings → Player → Videos → Adjust display refresh rate → Off*. Worth noting that 144 is an exact
6x multiple of 24, so leaving the display alone also gives better motion than the 60Hz 3:2 pulldown
Kodi was switching to.

**Nothing in this skin can cause or fix that.** If flashing on stop is ever reported again, check
for `SetFullScreen` / `OnDisplayLost` in the log before looking at any XML.

### Cause two: close-fade animations outliving the renderer

With refresh switching off, the logs showed the real problem. Teardown, same file, same machine:

| | Estuary | Arctic Vibe |
|---|---|---|
| `OnPlayBackStopped` → last `Window Deinit` | **12ms** | **233ms** |

The entire Arctic Vibe delay sat between `DialogSeekBar` deinitialising and `VideoOSD` /
`VideoFullScreen` deinitialising — a 226ms gap. That is Kodi **deferring `Window Deinit` until each
window's `WindowClose` animation completes**, and these windows carry 300ms fades.

The critical detail is ordering: `CRenderManager::DeleteRenderer` runs at 46.313, roughly 70ms
*before* the first window even begins closing. So the video plane is already gone and those fades
play out over nothing, staggered across three windows, followed by `MyVideoNav` initialising and
its `skinvariables-blurfallback.json` background regenerating 440ms later. That sequence of
discrete visual steps is what was seen as flashing. Estuary has no such fades and collapses
everything in one frame.

### Fix

Each `WindowClose` fade is now paired: the 300ms fade carries `condition="Player.HasMedia"`, and a
`time="0"` twin carries `!Player.HasMedia`. Closing the OSD during playback fades exactly as
before; stopping playback tears down immediately, like Estuary.

Applied to `VideoOSD.xml`, `Custom_1152`, and `Animation_OSD_Fullscreen_Change` (which covers
`VideoFullScreen`, `DialogSeekBar`, `DialogFullScreenInfo`, `MusicVisualisation` and the
`OSD_Background_Dim` layers). The condition is correct everywhere it lands — none of those has
anything to fade over once playback has ended.

**Two conditional animations, not one with a shortened time.** Removing the fade outright would
lose it during normal OSD dismissal, which is the case it exists for.

### Reverted: the `Player.HasMedia` visibility guard on DialogSeekBar

An earlier attempt added `<visible>Player.HasMedia</visible>` to `DialogSeekBar.xml`, on the theory
that transient player states were re-triggering its fade-in during teardown. It reduced the flashing
from three to two, which seemed to confirm it.

**That theory was wrong** — or at least unproven. Kodi logs window init/deinit but never
control-level `<visible>` changes, so no log could confirm it, and the real cause turned out to be
animation timing. The guard also cost the seekbar during PVR channel switching. It has been removed
now that the actual cause is addressed.

If flashing returns, re-adding that line is the first thing to try, and it would mean the guard was
doing real work after all.

---

## 28. Library artwork lost after playing from the info screen

**Key files:** `1080i/Includes_Actions.xml`

Playing a movie from the info screen and then stopping it returned to the library with the played
item showing a fallback image instead of its poster. The item kept its identity — `DBID` and title
intact — but every art field came back empty and `ListItem.Icon` had fallen back to
`DefaultVideo.png`. Backing out of the window and re-entering restored it.

### Reproduction

| Route | Result |
|---|---|
| Arctic Vibe, play from the list | poster survives |
| Arctic Vibe, play from the info screen | **poster lost** |
| Estuary, play from the info screen | poster survives |

Only the info-screen route was affected, in any viewtype.

### Cause

Arctic Fuse's info-screen play button routes through `$VAR[Action_DialogInfo_PlayMedia]`, which
hands TMDb Helper a **path string**. TMDb Helper does not parse it — `manager.py`,
`get_playmedia_builtin()` wraps the value in `PlayMedia(...)` — so it reaches Kodi's builtin as a
bare path.

`PlayOrQueueMedia` constructs a `CFileItem` from that string and calls `item.LoadDetails()`. For a
video with no info tag yet, `LoadDetails` does:

```cpp
if (db.LoadVideoInfo(GetDynPath(), *tag))
{
  const CFileItem loadedItem{*tag};
  UpdateInfo(loadedItem);
```

`CFileItem(const CVideoInfoTag&)` calls `SetFromVideoInfoTag`, which copies the title, path and info
tag, **sets no artwork at all**, and ends with `FillInDefaultIcon()`:

```cpp
SetArt("icon", "DefaultVideo.png");
```

`icon` is not a separate field. `CGUIListItem::SetArt(type, url)` writes it into `m_art` — the same
map that holds poster, fanart, thumb and landscape. The item handed to the player therefore has an
art map of exactly one entry: `{icon: DefaultVideo.png}`.

On stop, `CSaveFileState::DoWork` queues `GUI_MSG_UPDATE_ITEM` carrying a copy of that item.
`CGUIMediaWindow` passes it to `CFileItemList::UpdateItem`, which calls `CFileItem::UpdateInfo`:

```cpp
if (!item.GetArt().empty())
  SetArt(item.GetArt());        // CGUIListItem::SetArt(map) → m_art = art;
```

The guard is against an *empty* map. One default-icon entry is not empty, so the assignment runs and
**replaces the list item's entire art map with that single entry**. Poster, thumb, fanart and
landscape are gone; `Icon` reads `DefaultVideo.png`. `DBID` and the title survive because
`UpdateInfo` copies the video info tag separately.

The timing explains why the return's own directory read does not help:

```
57.203  CSaveFileState::DoWork          <- queues GUI_MSG_UPDATE_ITEM
57.263  Window Init (MyVideoNav.xml)
57.267  CGUIMediaWindow::GetDirectory   <- list loads WITH full art
```

`SendThreadMessage` queues, so the message is processed *after* the window has re-initialised and
the directory has loaded. The list comes back correct and is then overwritten.

**Why playing from the list is safe, and why Estuary never breaks.** Both hand the player the actual
list item, which already satisfies `if (HasVideoInfoTag()) return true;` at the top of
`LoadDetails`. No synthetic item is built, `FillInDefaultIcon` never runs, and the full art map
survives the round trip. Estuary's info screen uses Kodi's native control 8, which calls
`CGUIDialogVideoInfo::Play()` on `m_movieItem` directly. It is not avoiding the bug; it never enters
the code path.

### Fix

A new first value in `Action_DialogInfo_PlayMedia`, handing library items to Kodi's native handler
instead of a path string:

```xml
<value condition="Window.IsVisible(movieinformation) + !String.IsEmpty(ListItem.DBID) + !$EXP[Exp_IsFolder]">SendClick(movieinformation,8)</value>
```

`movieinformation` is `WINDOW_DIALOG_VIDEO_INFO` (12003) and 8 is `CONTROL_BTN_PLAY`.

**The skin defines no control 8 — that does not matter.** The `SendClick` builtin addresses the
*window*, passing the control id only as the message sender:

```cpp
CGUIMessage message(GUI_MSG_CLICKED, atoi(params[1].c_str()), windowID);
```

`CGUIDialogVideoInfo::OnMessage` reads `message.GetSenderId()` and dispatches to `Play()` on a
match, so the handler runs whether or not a control with that id exists in the XML.

`Play()` runs `CVideoPlayActionProcessor` on `m_movieItem` and calls `Close(true)` itself, so the
`close_dialog=1190` handling of the old route is not needed. Because the skin defines no
`CONTROL_BTN_RESUME` (id 9) either, `Play()` takes the `ProcessDefaultAction()` branch, which is the
normal resume prompt.

### Scope of the guard

Three conditions, each doing real work:

* `Window.IsVisible(movieinformation)` — `DialogInfo_Button_Expansion` is shared across several
  windows, including TMDb Helper's own window 1190. Only Kodi's native dialog has the handler.
* `!String.IsEmpty(ListItem.DBID)` — plugin items and anything unidentified have no `m_movieItem`
  worth playing natively and must keep the old route.
* `!$EXP[Exp_IsFolder]` — for tvshow and season items, `Play()` *navigates into* them rather than
  playing, which is not what this button means in this skin.

Everything failing those conditions falls through to the untouched upstream values below.

### Notes

* **Behaviour change:** the native button honours *Settings → Player → Videos → Default select
  action*, where the old path-based route did not. If playback starts without a resume prompt, that
  setting is the reason.
* **Not fixed:** `Action_DialogInfo_Panel_PlayMedia` (`Includes_Items.xml:1344`) is the equivalent
  action for the info *panel*. It reads `window.property(filenameandpath)` rather than `ListItem`
  and is not covered here. If artwork loss appears after playing from the panel, this is why.
* **Upstream.** `Action_DialogInfo_PlayMedia` is byte-identical in Arctic Fuse 2 v2.12.12, so this
  reproduces on the unmodified skin. It is arguably a Kodi bug as well — `CFileItem::MergeInfo`
  exists directly below `UpdateInfo` and performs exactly the merge that would prevent it.

### Diagnostic note for future work

Several plausible theories were tested and disproved before the cause was found: refresh-rate
switching, asynchronous art loading, the wall/row/Kaleido view work, `Container.Content` emptying,
focus landing on the wrong container, and the `library://` versus `videodb://` reload asymmetry. All
were ruled out by evidence rather than inspection.

The measurement that settled it was a temporary debug overlay in `MyVideoNav.xml` reading
`ListItem.Art(poster)`, `Art(thumb)`, `Icon`, `DBID` and `Container.Content`, plus a second line
reading `Container(51)` explicitly to bypass focus. Two things are worth remembering if this kind of
problem recurs:

* **Kodi never writes control text to `kodi.log`.** Label values must be read off the screen.
* **Inside a container control, unqualified `ListItem` and `Container.*` resolve against that
  control**, not the window's focused list. A hidden probe container reported empty for everything
  until the infolabels were qualified as `Container(51).ListItem...`, and `Container(id)`
  cross-references do not resolve at all inside a directory-provider `<content>` URL.

---

## 29. Regression in 1.0.8: two Match-OSD-to-content fixes lost

**Key files:** `1080i/Custom_1143_OSD_NextOverlay.xml`,
`1080i/Custom_1153_OSD_VideoInfoOverlayTop.xml`

The 1.0.8 package was assembled from a tree that predated the last two fixes of section 26. Both
files reverted to the **first-pass** classification — a single `Animation_OSD_MatchContent_Top` on
the window root — and the two subsections of section 26 that document them
("Direction is decided by the content's pinned edge" and "Custom_1143 is a mixed-anchor window")
were absent from the 1.0.8 changelog, along with the corrected direction table. Nothing else
regressed; `Includes_Actions.xml` (section 28) was the only intended change in 1.0.8.

Reverting the code and the documentation together is what made this invisible: the changelog in the
package described the reverted state as correct, so the two bugs looked like new reports rather than
returning ones.

### Symptoms as reported

| Symptom | File | Cause |
|---|---|---|
| Info panel does not slide up when the OSD is inset | `Custom_1153` | `_Top` moved the panel *down* into the VideoOSD button row instead of up |
| Duplicate skip-chapter button in the OSD on non-16:9 content | `Custom_1143` | `_Top` on the root moved the replica button row down while the real VideoOSD row moved up, separating the two so both read as distinct |

Both are exactly the failures described in section 26. Restoring the 1.0.7 state of the two files
fixes them; no new code was written for this release.

### Restored state

* `Custom_1153` — root include is `Animation_OSD_MatchContent_Bottom`. The window is named "Top"
  and its outer group opens with `<top>view_pad</top>`, but the content is pinned by its *bottom*
  edge. See section 26.
* `Custom_1143` — **no include on the window root.** `_Top` on the up-next card,
  `_Bottom` on the `OSD_CustomDialog_GroupList` button row. This is the only mixed-anchor window of
  the fifteen attachment points.

Both files carry the full reasoning as `SKINMOD:` block comments at the attachment points, so the
classification now survives independently of this document.

### Guard against a repeat

The direction of every attachment is checkable from the source without reasoning about geometry:

```
grep -rn "Animation_OSD_MatchContent" 1080i/
```

Expect **fifteen** attachments — thirteen `_Bottom`, two `_Top` — matching the direction table in
section 26. `Custom_1143` must show one of each and none on its root; `Custom_1153` must show
`_Bottom`. Any release where that grep disagrees with the table has the 1.0.8 regression.

Because the reverted state is well-formed XML that resolves every include, none of the five
validation steps below could have caught this. **A manifest diff against the previous release is
now part of packaging**: any file differing from the last version that is not named in that
release's changelog section is treated as an unintended revert until confirmed.

---

## Validation performed after every change

1. **XML well-formedness** across all files in `1080i/` (258 files at last count).
2. **No dangling include references** — every `<include content="X">` / `<include>X</include>`
   resolves to a defined `<include name="X">`, excluding runtime-substituted (`$PARAM`/`$VAR`)
   and generated (`skinvariables-*`) names.
3. **No dangling variable / expression references** — unresolved count held at the
   **pre-existing baseline of 34**. Those 34 are upstream dynamic name constructions
   (`Exp_View_<id>`, `Exp_View_<id>_Include`, `Color_PC_to_Hex_`, `View_`, `Label_Title_C`,
   `Label_Plot_C`, `Exp_1105Hub_IsVisible`, `Exp_IsPersonInfo`) and are **not** errors.
   Any number above 34 means something was broken.
4. **JSON validity** for all files under `shortcuts/`.
5. **Zip integrity** plus a file-manifest diff against the original archive to confirm nothing was
   dropped unintentionally (only `.git*` / `.github` CI metadata is excluded from the package).
6. **Manifest diff against the previous release** — every file that differs must be accounted for by
   a changelog section in this release. Added in 1.0.9 after the section 29 regression, which was
   valid XML and therefore passed steps 1-5.

---

## Quick reference — the tuning knobs

| What | Constant / file | Current |
|---|---|---|
| Hub widget row vertical position | `hub_widgets_shift_y` (+ `_detailed`) | 140 / 580 |
| Combine Widgets un-shift | `Hub_Slide_Widgets_OnCombined` (`time` param) | `-hub_widgets_shift_y`, instant |
| Row view vertical position | `view_row_shifted`, `view_row_hitrect_y_shifted` | 650 / 726 |
| Fanart panel size | `flixart_size_w` / `_h` (base = Medium) | 1689 x 950 |
| Wall grid vertical position | `wall_top` / `wall_bottom` (horizontal: do not set) | 0 / 0 |
| Wall row pitch | `view_poster_itemlayout_h` — shared, moves row views too | 350 |
| Default widget view | `widgets_row.xml` fallback rule | `List_Poster_Row` |
| Bottom furniture backdrop | `view_furniture_dialog_t` / `_h` / `_x` (top-anchored; `_x` max 60) | -40 / 130 / 60 |
| Kaleido panel size | `kaleido_panel_w` / `_h` (a set — see 23c) | 771.42 x 310 |
| Kaleido tuck under poster | `kaleido_tuck` (+ `kaleido_ext_w`, `kaleido_right_x`, `kaleido_pad_tucked`) | 40 |
| Kaleido hover delay | `delay="3000"` on **both** animations in `View_Kaleido_Panel` (literal — not a constant) | 3000ms |
| Wall fanart on/off | `Background.DisableWallFanart` → `Exp_BackgroundArtwork_WallIsDisabled` | off (fanart shown) |
| Aspect ratio pill on/off | `Infoline.DisableAspect` (inverted sense) | on |
| Match OSD to content on/off | `OSD.MatchToContent` (positive sense) | off |
| OSD inset per aspect ratio | bucket tables in `Animation_OSD_MatchContent_Bottom` / `_Top` / `_Dim` | 21-192px |

10.8px = 1% of screen height. Remember to update `-name` negative twins.
